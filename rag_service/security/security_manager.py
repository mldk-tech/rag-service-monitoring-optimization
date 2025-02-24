"""Security module for comprehensive authentication, authorization, and rate limiting."""

from typing import Dict, Optional, Any, List
import time
import asyncio
import logging
from datetime import datetime, timedelta
import re
from dataclasses import dataclass
from prometheus_client import Counter, Gauge, Histogram
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
import aioredis
from pydantic import BaseModel

@dataclass
class SecurityContext:
    """Security context for request processing."""
    user_id: str
    roles: List[str]
    scopes: List[str]
    authenticated: bool
    rate_limited: bool
    request_count: int
    last_request_time: datetime

class AuthenticationResult(BaseModel):
    """Authentication result data."""
    authenticated: bool
    user_id: Optional[str] = None
    roles: List[str] = []
    scopes: List[str] = []
    error: Optional[str] = None

class SecurityManager:
    """Comprehensive security management for the RAG service."""

    def __init__(
        self,
        config: Dict[str, Any],
        redis_client: aioredis.Redis
    ):
        """Initialize security manager.
        
        Args:
            config: Security configuration
            redis_client: Redis client for rate limiting and caching
        """
        self.config = config
        self.redis = redis_client
        self.tracer = trace.get_tracer(__name__)

        # Authentication metrics
        self.auth_requests = Counter(
            'rag_auth_requests_total',
            'Total authentication requests',
            ['status']
        )
        self.auth_latency = Histogram(
            'rag_auth_duration_seconds',
            'Authentication duration in seconds',
            ['status'],
            buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25)
        )
        self.auth_cache_hits = Counter(
            'rag_auth_cache_hits_total',
            'Total authentication cache hits'
        )

        # Rate limiting metrics
        self.rate_limit_exceeded = Counter(
            'rag_rate_limit_exceeded_total',
            'Total number of rate limit exceeded events',
            ['user_id']
        )
        self.request_rate = Gauge(
            'rag_request_rate',
            'Current request rate per user',
            ['user_id']
        )

        # Query validation metrics
        self.query_validation_failures = Counter(
            'rag_query_validation_failures_total',
            'Total number of query validation failures',
            ['reason']
        )

        # Security event metrics
        self.security_events = Counter(
            'rag_security_events_total',
            'Total number of security events',
            ['type', 'severity']
        )

    async def authenticate_request(
        self,
        auth_token: str
    ) -> AuthenticationResult:
        """Authenticate request with caching and metrics.
        
        Args:
            auth_token: Authentication token
            
        Returns:
            Authentication result
            
        Raises:
            SecurityError: If authentication fails
        """
        start_time = time.time()
        
        with self.tracer.start_span("authenticate_request") as span:
            try:
                # Check cache first
                cache_key = f"auth:{auth_token}"
                cached_result = await self.redis.get(cache_key)
                
                if cached_result:
                    self.auth_cache_hits.inc()
                    auth_result = AuthenticationResult.parse_raw(cached_result)
                    duration = time.time() - start_time
                    self.auth_latency.labels(status="cache_hit").observe(duration)
                    span.set_attribute("cache_hit", True)
                    return auth_result

                # Authenticate with external service
                auth_result = await self._authenticate_with_service(auth_token)
                
                # Cache successful authentications
                if auth_result.authenticated:
                    await self.redis.setex(
                        cache_key,
                        self.config["auth"]["cache_ttl"],
                        auth_result.json()
                    )

                duration = time.time() - start_time
                status = "success" if auth_result.authenticated else "failure"
                self.auth_latency.labels(status=status).observe(duration)
                self.auth_requests.labels(status=status).inc()

                if not auth_result.authenticated:
                    self.security_events.labels(
                        type="authentication_failure",
                        severity="warning"
                    ).inc()
                    span.set_status(Status(StatusCode.ERROR))
                    logging.warning(
                        "Authentication failed for token: %s",
                        auth_token[:8] + "..."
                    )
                else:
                    span.set_status(Status(StatusCode.OK))

                return auth_result

            except Exception as e:
                duration = time.time() - start_time
                self.auth_latency.labels(status="error").observe(duration)
                self.auth_requests.labels(status="error").inc()
                self.security_events.labels(
                    type="authentication_error",
                    severity="error"
                ).inc()
                span.set_status(Status(StatusCode.ERROR))
                logging.error("Authentication error: %s", str(e))
                raise SecurityError(f"Authentication failed: {str(e)}")

    async def _authenticate_with_service(
        self,
        auth_token: str
    ) -> AuthenticationResult:
        """Authenticate with external service.
        
        Args:
            auth_token: Authentication token
            
        Returns:
            Authentication result
        """
        # Placeholder - implement actual authentication service integration
        # This should integrate with your organization's authentication service
        # (e.g., OAuth2, OpenID Connect, custom auth service)
        await asyncio.sleep(0.1)  # Simulate external service call
        return AuthenticationResult(
            authenticated=True,
            user_id="test_user",
            roles=["user"],
            scopes=["read"]
        )

    async def check_rate_limit(
        self,
        user_id: str
    ) -> bool:
        """Check if request is within rate limits.
        
        Args:
            user_id: User ID
            
        Returns:
            Whether request is allowed
        """
        rate_key = f"rate:{user_id}"
        
        try:
            # Use Redis to track request count
            current = await self.redis.incr(rate_key)
            if current == 1:
                await self.redis.expire(rate_key, 60)  # Reset after 1 minute

            # Update metrics
            self.request_rate.labels(user_id=user_id).set(current)

            # Check against limit
            if current > self.config["rate_limit"]["requests_per_minute"]:
                self.rate_limit_exceeded.labels(user_id=user_id).inc()
                self.security_events.labels(
                    type="rate_limit_exceeded",
                    severity="warning"
                ).inc()
                logging.warning(
                    "Rate limit exceeded for user %s: %d requests/minute",
                    user_id,
                    current
                )
                return False

            return True

        except Exception as e:
            logging.error("Rate limiting error: %s", str(e))
            # Fail open if rate limiting fails
            return True

    def validate_query(
        self,
        query: str
    ) -> bool:
        """Validate query against security rules.
        
        Args:
            query: Query string
            
        Returns:
            Whether query is valid
        """
        # Check query length
        if len(query) > self.config["query_validation"]["max_length"]:
            self.query_validation_failures.labels(reason="length").inc()
            return False

        # Check blocked patterns
        for pattern in self.config["query_validation"]["blocked_patterns"]:
            if re.search(pattern, query, re.IGNORECASE):
                self.query_validation_failures.labels(reason="pattern").inc()
                self.security_events.labels(
                    type="blocked_query_pattern",
                    severity="warning"
                ).inc()
                return False

        return True

    async def create_security_context(
        self,
        auth_token: str
    ) -> SecurityContext:
        """Create security context for request processing.
        
        Args:
            auth_token: Authentication token
            
        Returns:
            Security context
            
        Raises:
            SecurityError: If context creation fails
        """
        try:
            # Authenticate
            auth_result = await self.authenticate_request(auth_token)
            if not auth_result.authenticated:
                raise SecurityError("Authentication failed")

            # Check rate limit
            rate_limited = not await self.check_rate_limit(auth_result.user_id)

            # Create context
            return SecurityContext(
                user_id=auth_result.user_id,
                roles=auth_result.roles,
                scopes=auth_result.scopes,
                authenticated=True,
                rate_limited=rate_limited,
                request_count=0,
                last_request_time=datetime.utcnow()
            )

        except Exception as e:
            self.security_events.labels(
                type="context_creation_error",
                severity="error"
            ).inc()
            logging.error("Security context creation failed: %s", str(e))
            raise SecurityError(f"Failed to create security context: {str(e)}")

class SecurityError(Exception):
    """Security-related errors."""
    pass 