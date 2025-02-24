"""Enhanced security module for comprehensive authentication, authorization, and rate limiting."""

from typing import Dict, Optional, Any, List, Set, Union
import time
import asyncio
from datetime import datetime, timedelta
import re
import hashlib
import secrets
from dataclasses import dataclass, field
from enum import Enum
import jwt
from jwt.exceptions import PyJWTError
from pydantic import BaseModel, Field, validator, SecretStr
from prometheus_client import Counter, Gauge, Histogram, Summary
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
import aioredis
import structlog
from functools import wraps
from abc import ABC, abstractmethod

# Enhanced logging setup
logger = structlog.get_logger(__name__)

class SecurityLevel(Enum):
    """Security levels for different operations."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class SecurityEventType(Enum):
    """Types of security events."""
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    RATE_LIMIT = "rate_limit"
    VALIDATION = "validation"
    ATTACK = "attack"
    SYSTEM = "system"

class SecurityEventSeverity(Enum):
    """Severity levels for security events."""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

@dataclass
class SecurityEvent:
    """Enhanced security event data."""
    type: SecurityEventType
    severity: SecurityEventSeverity
    timestamp: datetime = field(default_factory=datetime.utcnow)
    user_id: Optional[str] = None
    ip_address: Optional[str] = None
    event_id: str = field(default_factory=lambda: secrets.token_hex(16))
    details: Dict[str, Any] = field(default_factory=dict)
    source: str = field(default="security_manager")

@dataclass
class SecurityContext:
    """Enhanced security context for request processing."""
    user_id: str
    roles: Set[str]
    scopes: Set[str]
    authenticated: bool
    rate_limited: bool
    request_count: int
    last_request_time: datetime
    security_level: SecurityLevel
    ip_address: Optional[str] = None
    session_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def has_permission(self, required_scope: str) -> bool:
        """Check if context has required scope."""
        return (
            "admin" in self.roles or
            required_scope in self.scopes or
            "*" in self.scopes
        )

    def has_role(self, required_role: str) -> bool:
        """Check if context has required role."""
        return "admin" in self.roles or required_role in self.roles

    def has_security_level(self, required_level: SecurityLevel) -> bool:
        """Check if context has required security level."""
        levels = {
            SecurityLevel.LOW: 0,
            SecurityLevel.MEDIUM: 1,
            SecurityLevel.HIGH: 2,
            SecurityLevel.CRITICAL: 3
        }
        return levels[self.security_level] >= levels[required_level]

class AuthenticationResult(BaseModel):
    """Enhanced authentication result data."""
    authenticated: bool
    user_id: Optional[str] = None
    roles: Set[str] = Field(default_factory=set)
    scopes: Set[str] = Field(default_factory=set)
    security_level: SecurityLevel = Field(default=SecurityLevel.LOW)
    token_type: Optional[str] = None
    expires_at: Optional[datetime] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    error_code: Optional[str] = None

    @validator("roles", "scopes", pre=True)
    def convert_to_set(cls, v):
        """Convert lists to sets."""
        if isinstance(v, list):
            return set(v)
        return v

class RateLimitConfig(BaseModel):
    """Rate limit configuration."""
    enabled: bool = True
    requests_per_minute: int = Field(default=60, gt=0)
    burst_size: int = Field(default=10, gt=0)
    window_size: int = Field(default=60, gt=0)  # seconds
    by_ip: bool = True
    by_user: bool = True
    by_endpoint: bool = True
    whitelist: Set[str] = Field(default_factory=set)

class AuthenticationConfig(BaseModel):
    """Authentication configuration."""
    enabled: bool = True
    token_expiry: int = Field(default=3600, gt=0)  # seconds
    refresh_token_expiry: int = Field(default=86400, gt=0)  # seconds
    jwt_secret: SecretStr
    jwt_algorithm: str = "HS256"
    cache_ttl: int = Field(default=300, gt=0)  # seconds
    required_claims: Set[str] = Field(default_factory=lambda: {"sub", "exp"})
    allowed_issuers: Set[str] = Field(default_factory=set)

class ValidationConfig(BaseModel):
    """Input validation configuration."""
    max_query_length: int = Field(default=1000, gt=0)
    min_query_length: int = Field(default=1, ge=1)
    blocked_patterns: Set[str] = Field(default_factory=set)
    sanitize_input: bool = True
    validate_unicode: bool = True

class SecurityConfig(BaseModel):
    """Enhanced security configuration."""
    authentication: AuthenticationConfig
    rate_limit: RateLimitConfig
    validation: ValidationConfig
    security_log_path: str = "logs/security.log"
    event_retention_days: int = Field(default=90, gt=0)
    max_failed_attempts: int = Field(default=5, gt=0)
    lockout_duration: int = Field(default=900, gt=0)  # seconds
    session_timeout: int = Field(default=3600, gt=0)  # seconds

class SecurityError(Exception):
    """Enhanced base security error."""
    def __init__(
        self,
        message: str,
        error_code: str,
        details: Optional[Dict[str, Any]] = None
    ):
        super().__init__(message)
        self.error_code = error_code
        self.details = details or {}
        self.timestamp = datetime.utcnow()

class AuthenticationError(SecurityError):
    """Authentication-specific error."""
    def __init__(
        self,
        message: str,
        details: Optional[Dict[str, Any]] = None
    ):
        super().__init__(
            message,
            "AUTHENTICATION_ERROR",
            details
        )

class AuthorizationError(SecurityError):
    """Authorization-specific error."""
    def __init__(
        self,
        message: str,
        details: Optional[Dict[str, Any]] = None
    ):
        super().__init__(
            message,
            "AUTHORIZATION_ERROR",
            details
        )

class RateLimitError(SecurityError):
    """Rate limit specific error."""
    def __init__(
        self,
        message: str,
        details: Optional[Dict[str, Any]] = None
    ):
        super().__init__(
            message,
            "RATE_LIMIT_ERROR",
            details
        )

class ValidationError(SecurityError):
    """Validation-specific error."""
    def __init__(
        self,
        message: str,
        details: Optional[Dict[str, Any]] = None
    ):
        super().__init__(
            message,
            "VALIDATION_ERROR",
            details
        )

class SecurityManager:
    """Enhanced security management for the RAG service."""

    def __init__(
        self,
        config: Dict[str, Any],
        redis_client: aioredis.Redis
    ):
        """Initialize enhanced security manager.
        
        Args:
            config: Security configuration
            redis_client: Redis client for rate limiting and caching
        """
        self.config = SecurityConfig(**config)
        self.redis = redis_client
        self.tracer = trace.get_tracer(__name__)

        # Enhanced metrics
        self._setup_metrics()

        # Compile regex patterns
        self.blocked_patterns = [
            re.compile(pattern)
            for pattern in self.config.validation.blocked_patterns
        ]

        # Initialize cache for failed attempts
        self.failed_attempts = {}

        logger.info(
            "security_manager_initialized",
            config=config
        )

    def _setup_metrics(self):
        """Setup enhanced security metrics."""
        # Authentication metrics
        self.auth_requests = Counter(
            'rag_auth_requests_total',
            'Total authentication requests',
            ['status', 'token_type']
        )
        self.auth_latency = Histogram(
            'rag_auth_duration_seconds',
            'Authentication duration in seconds',
            ['status', 'token_type'],
            buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25)
        )
        self.auth_cache_hits = Counter(
            'rag_auth_cache_hits_total',
            'Total authentication cache hits',
            ['token_type']
        )
        self.auth_failures = Counter(
            'rag_auth_failures_total',
            'Total authentication failures',
            ['reason', 'token_type']
        )
        self.active_sessions = Gauge(
            'rag_active_sessions',
            'Number of active sessions',
            ['security_level']
        )

        # Rate limiting metrics
        self.rate_limit_exceeded = Counter(
            'rag_rate_limit_exceeded_total',
            'Total number of rate limit exceeded events',
            ['user_id', 'ip', 'endpoint']
        )
        self.request_rate = Gauge(
            'rag_request_rate',
            'Current request rate',
            ['user_id', 'ip', 'endpoint']
        )
        self.rate_limit_remaining = Gauge(
            'rag_rate_limit_remaining',
            'Remaining requests before rate limit',
            ['user_id', 'ip', 'endpoint']
        )

        # Security event metrics
        self.security_events = Counter(
            'rag_security_events_total',
            'Total number of security events',
            ['type', 'severity']
        )
        self.blocked_requests = Counter(
            'rag_blocked_requests_total',
            'Total number of blocked requests',
            ['reason']
        )
        self.suspicious_activities = Counter(
            'rag_suspicious_activities_total',
            'Total number of suspicious activities',
            ['type', 'severity']
        )

    async def authenticate_request(
        self,
        auth_token: str,
        token_type: str = "bearer",
        ip_address: Optional[str] = None
    ) -> AuthenticationResult:
        """Enhanced request authentication with comprehensive security checks.
        
        Args:
            auth_token: Authentication token
            token_type: Type of token (bearer, api_key, etc.)
            ip_address: Client IP address
            
        Returns:
            Authentication result
            
        Raises:
            AuthenticationError: If authentication fails
        """
        start_time = time.time()
        
        with self.tracer.start_span("authenticate_request") as span:
            try:
                span.set_attribute("token_type", token_type)
                if ip_address:
                    span.set_attribute("ip_address", ip_address)

                # Check for previous failures
                if await self._is_blocked(ip_address):
                    raise AuthenticationError(
                        "Too many failed attempts",
                        {
                            "ip_address": ip_address,
                            "lockout_duration": self.config.lockout_duration
                        }
                    )

                # Check cache first
                cache_key = f"auth:{token_type}:{self._hash_token(auth_token)}"
                cached_result = await self.redis.get(cache_key)
                
                if cached_result:
                    self.auth_cache_hits.labels(
                        token_type=token_type
                    ).inc()
                    auth_result = AuthenticationResult.parse_raw(cached_result)
                    
                    # Verify token hasn't expired
                    if (
                        auth_result.expires_at and
                        auth_result.expires_at < datetime.utcnow()
                    ):
                        await self.redis.delete(cache_key)
                        raise AuthenticationError("Token expired")
                    
                    duration = time.time() - start_time
                    self.auth_latency.labels(
                        status="cache_hit",
                        token_type=token_type
                    ).observe(duration)
                    span.set_attribute("cache_hit", True)
                    return auth_result

                # Authenticate with service
                auth_result = await self._authenticate_with_service(
                    auth_token,
                    token_type
                )
                
                if auth_result.authenticated:
                    # Cache successful authentications
                    await self.redis.setex(
                        cache_key,
                        self.config.authentication.cache_ttl,
                        auth_result.json()
                    )
                    
                    # Reset failed attempts
                    if ip_address:
                        await self._reset_failed_attempts(ip_address)
                    
                    # Update active sessions
                    self.active_sessions.labels(
                        security_level=auth_result.security_level.value
                    ).inc()
                else:
                    # Record failed attempt
                    if ip_address:
                        await self._record_failed_attempt(ip_address)
                    
                    self.auth_failures.labels(
                        reason=auth_result.error_code or "unknown",
                        token_type=token_type
                    ).inc()

                duration = time.time() - start_time
                status = "success" if auth_result.authenticated else "failure"
                self.auth_latency.labels(
                    status=status,
                    token_type=token_type
                ).observe(duration)
                self.auth_requests.labels(
                    status=status,
                    token_type=token_type
                ).inc()

                if not auth_result.authenticated:
                    self._record_security_event(
                        SecurityEvent(
                            type=SecurityEventType.AUTHENTICATION,
                            severity=SecurityEventSeverity.WARNING,
                            user_id=auth_result.user_id,
                            ip_address=ip_address,
                            details={
                                "error": auth_result.error,
                                "error_code": auth_result.error_code,
                                "token_type": token_type
                            }
                        )
                    )
                    span.set_status(Status(StatusCode.ERROR))
                    logger.warning(
                        "authentication_failed",
                        token_type=token_type,
                        error=auth_result.error,
                        ip_address=ip_address
                    )
                else:
                    span.set_status(Status(StatusCode.OK))

                return auth_result

            except Exception as e:
                duration = time.time() - start_time
                self.auth_latency.labels(
                    status="error",
                    token_type=token_type
                ).observe(duration)
                self.auth_requests.labels(
                    status="error",
                    token_type=token_type
                ).inc()
                
                self._record_security_event(
                    SecurityEvent(
                        type=SecurityEventType.AUTHENTICATION,
                        severity=SecurityEventSeverity.ERROR,
                        ip_address=ip_address,
                        details={
                            "error": str(e),
                            "token_type": token_type
                        }
                    )
                )
                
                span.set_status(Status(StatusCode.ERROR))
                span.set_attribute("error", str(e))
                
                logger.error(
                    "authentication_error",
                    error=str(e),
                    token_type=token_type,
                    ip_address=ip_address
                )
                
                if isinstance(e, SecurityError):
                    raise
                raise AuthenticationError(
                    f"Authentication failed: {str(e)}",
                    {"token_type": token_type}
                )

    async def _authenticate_with_service(
        self,
        auth_token: str,
        token_type: str
    ) -> AuthenticationResult:
        """Enhanced service authentication with JWT support.
        
        Args:
            auth_token: Authentication token
            token_type: Type of token
            
        Returns:
            Authentication result
        """
        try:
            if token_type == "bearer":
                # Verify JWT token
                try:
                    payload = jwt.decode(
                        auth_token,
                        self.config.authentication.jwt_secret.get_secret_value(),
                        algorithms=[self.config.authentication.jwt_algorithm]
                    )
                    
                    # Validate required claims
                    missing_claims = self.config.authentication.required_claims - set(payload.keys())
                    if missing_claims:
                        raise AuthenticationError(
                            f"Missing required claims: {missing_claims}"
                        )
                    
                    # Validate issuer if configured
                    if (
                        self.config.authentication.allowed_issuers and
                        payload.get("iss") not in self.config.authentication.allowed_issuers
                    ):
                        raise AuthenticationError("Invalid token issuer")
                    
                    # Check expiration
                    exp = datetime.fromtimestamp(payload["exp"])
                    if exp < datetime.utcnow():
                        raise AuthenticationError("Token expired")
                    
                    return AuthenticationResult(
                        authenticated=True,
                        user_id=payload["sub"],
                        roles=set(payload.get("roles", [])),
                        scopes=set(payload.get("scopes", [])),
                        security_level=SecurityLevel(
                            payload.get("security_level", "low")
                        ),
                        token_type="bearer",
                        expires_at=exp,
                        metadata=payload.get("metadata", {})
                    )
                    
                except PyJWTError as e:
                    return AuthenticationResult(
                        authenticated=False,
                        error="Invalid JWT token",
                        error_code="INVALID_JWT"
                    )
                    
            elif token_type == "api_key":
                # Implement API key validation
                # This should integrate with your API key management system
                pass
                
            else:
                return AuthenticationResult(
                    authenticated=False,
                    error=f"Unsupported token type: {token_type}",
                    error_code="UNSUPPORTED_TOKEN_TYPE"
                )
                
        except Exception as e:
            logger.error(
                "service_authentication_failed",
                error=str(e),
                token_type=token_type
            )
            return AuthenticationResult(
                authenticated=False,
                error=str(e),
                error_code="AUTHENTICATION_ERROR"
            )

    async def check_rate_limit(
        self,
        context: SecurityContext,
        endpoint: str
    ) -> bool:
        """Enhanced rate limiting with multiple strategies.
        
        Args:
            context: Security context
            endpoint: API endpoint
            
        Returns:
            Whether request is allowed
            
        Raises:
            RateLimitError: If rate limit is exceeded
        """
        if not self.config.rate_limit.enabled:
            return True
            
        # Check whitelist
        if (
            context.user_id in self.config.rate_limit.whitelist or
            context.ip_address in self.config.rate_limit.whitelist
        ):
            return True

        try:
            keys = []
            
            # Build rate limit keys based on configuration
            if self.config.rate_limit.by_user and context.user_id:
                keys.append(f"rate:user:{context.user_id}")
            if self.config.rate_limit.by_ip and context.ip_address:
                keys.append(f"rate:ip:{context.ip_address}")
            if self.config.rate_limit.by_endpoint:
                keys.append(f"rate:endpoint:{endpoint}")
                if context.user_id:
                    keys.append(f"rate:user:{context.user_id}:endpoint:{endpoint}")
                if context.ip_address:
                    keys.append(f"rate:ip:{context.ip_address}:endpoint:{endpoint}")

            # Check all rate limits
            async with self.redis.pipeline() as pipe:
                for key in keys:
                    pipe.incr(key)
                    pipe.expire(key, self.config.rate_limit.window_size)
                results = await pipe.execute()

            # Process results
            for key, current in zip(keys, results[::2]):
                # Update metrics
                key_parts = key.split(":")
                labels = {
                    "user_id": context.user_id or "anonymous",
                    "ip": context.ip_address or "unknown",
                    "endpoint": endpoint
                }
                
                self.request_rate.labels(**labels).set(current)
                remaining = max(
                    0,
                    self.config.rate_limit.requests_per_minute - current
                )
                self.rate_limit_remaining.labels(**labels).set(remaining)

                # Check against limit
                if current > self.config.rate_limit.requests_per_minute:
                    self.rate_limit_exceeded.labels(**labels).inc()
                    self._record_security_event(
                        SecurityEvent(
                            type=SecurityEventType.RATE_LIMIT,
                            severity=SecurityEventSeverity.WARNING,
                            user_id=context.user_id,
                            ip_address=context.ip_address,
                            details={
                                "endpoint": endpoint,
                                "current_rate": current,
                                "limit": self.config.rate_limit.requests_per_minute
                            }
                        )
                    )
                    logger.warning(
                        "rate_limit_exceeded",
                        user_id=context.user_id,
                        ip_address=context.ip_address,
                        endpoint=endpoint,
                        current_rate=current
                    )
                    raise RateLimitError(
                        "Rate limit exceeded",
                        {
                            "limit": self.config.rate_limit.requests_per_minute,
                            "window": self.config.rate_limit.window_size,
                            "current": current
                        }
                    )

            return True

        except RateLimitError:
            raise
        except Exception as e:
            logger.error(
                "rate_limit_error",
                error=str(e),
                user_id=context.user_id,
                ip_address=context.ip_address,
                endpoint=endpoint
            )
            # Fail open if rate limiting fails
            return True

    async def validate_query(
        self,
        query: str,
        context: SecurityContext
    ) -> bool:
        """Enhanced query validation with security checks.
        
        Args:
            query: Query string
            context: Security context
            
        Returns:
            Whether query is valid
            
        Raises:
            ValidationError: If query is invalid
        """
        try:
            # Check query length
            if len(query) > self.config.validation.max_query_length:
                raise ValidationError(
                    "Query too long",
                    {
                        "max_length": self.config.validation.max_query_length,
                        "actual_length": len(query)
                    }
                )
                
            if len(query) < self.config.validation.min_query_length:
                raise ValidationError(
                    "Query too short",
                    {
                        "min_length": self.config.validation.min_query_length,
                        "actual_length": len(query)
                    }
                )

            # Check for blocked patterns
            for pattern in self.blocked_patterns:
                if pattern.search(query):
                    self.blocked_requests.labels(
                        reason="blocked_pattern"
                    ).inc()
                    self._record_security_event(
                        SecurityEvent(
                            type=SecurityEventType.VALIDATION,
                            severity=SecurityEventSeverity.WARNING,
                            user_id=context.user_id,
                            ip_address=context.ip_address,
                            details={
                                "query": query,
                                "pattern": pattern.pattern
                            }
                        )
                    )
                    raise ValidationError(
                        "Query contains blocked pattern",
                        {"pattern": pattern.pattern}
                    )

            # Validate Unicode if enabled
            if self.config.validation.validate_unicode:
                try:
                    query.encode("ascii")
                except UnicodeEncodeError:
                    self.blocked_requests.labels(
                        reason="invalid_unicode"
                    ).inc()
                    raise ValidationError(
                        "Query contains invalid Unicode characters"
                    )

            # Sanitize input if enabled
            if self.config.validation.sanitize_input:
                # Implement input sanitization logic
                pass

            return True

        except ValidationError:
            raise
        except Exception as e:
            logger.error(
                "query_validation_error",
                error=str(e),
                query=query
            )
            raise ValidationError(
                f"Query validation failed: {str(e)}"
            )

    async def create_security_context(
        self,
        auth_token: str,
        ip_address: Optional[str] = None
    ) -> SecurityContext:
        """Create enhanced security context from authentication token.
        
        Args:
            auth_token: Authentication token
            ip_address: Client IP address
            
        Returns:
            Security context
            
        Raises:
            SecurityError: If context creation fails
        """
        try:
            # Authenticate request
            auth_result = await self.authenticate_request(
                auth_token,
                "bearer",
                ip_address
            )
            
            if not auth_result.authenticated:
                raise AuthenticationError(
                    auth_result.error or "Authentication failed"
                )

            # Create context
            context = SecurityContext(
                user_id=auth_result.user_id,
                roles=auth_result.roles,
                scopes=auth_result.scopes,
                authenticated=True,
                rate_limited=False,
                request_count=0,
                last_request_time=datetime.utcnow(),
                security_level=auth_result.security_level,
                ip_address=ip_address,
                session_id=secrets.token_hex(16),
                metadata=auth_result.metadata
            )

            # Store session
            session_key = f"session:{context.session_id}"
            await self.redis.setex(
                session_key,
                self.config.session_timeout,
                context.json()
            )

            return context

        except SecurityError:
            raise
        except Exception as e:
            logger.error(
                "context_creation_failed",
                error=str(e),
                ip_address=ip_address
            )
            raise SecurityError(
                f"Failed to create security context: {str(e)}",
                "CONTEXT_CREATION_ERROR"
            )

    async def _is_blocked(self, ip_address: Optional[str]) -> bool:
        """Check if IP is blocked due to failed attempts."""
        if not ip_address:
            return False
            
        key = f"failed_attempts:{ip_address}"
        attempts = await self.redis.get(key)
        
        if not attempts:
            return False
            
        return int(attempts) >= self.config.max_failed_attempts

    async def _record_failed_attempt(self, ip_address: str):
        """Record failed authentication attempt."""
        key = f"failed_attempts:{ip_address}"
        attempts = await self.redis.incr(key)
        
        if attempts == 1:
            await self.redis.expire(
                key,
                self.config.lockout_duration
            )
            
        if attempts >= self.config.max_failed_attempts:
            self._record_security_event(
                SecurityEvent(
                    type=SecurityEventType.AUTHENTICATION,
                    severity=SecurityEventSeverity.WARNING,
                    ip_address=ip_address,
                    details={
                        "attempts": attempts,
                        "lockout_duration": self.config.lockout_duration
                    }
                )
            )

    async def _reset_failed_attempts(self, ip_address: str):
        """Reset failed authentication attempts."""
        key = f"failed_attempts:{ip_address}"
        await self.redis.delete(key)

    def _hash_token(self, token: str) -> str:
        """Securely hash authentication token."""
        return hashlib.sha256(token.encode()).hexdigest()

    def _record_security_event(self, event: SecurityEvent):
        """Record security event with metrics."""
        self.security_events.labels(
            type=event.type.value,
            severity=event.severity.value
        ).inc()
        
        if event.severity in {
            SecurityEventSeverity.ERROR,
            SecurityEventSeverity.CRITICAL
        }:
            self.suspicious_activities.labels(
                type=event.type.value,
                severity=event.severity.value
            ).inc()
            
        logger.info(
            "security_event_recorded",
            event_id=event.event_id,
            type=event.type.value,
            severity=event.severity.value,
            user_id=event.user_id,
            ip_address=event.ip_address,
            details=event.details
        ) 