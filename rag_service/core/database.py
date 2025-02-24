"""Enhanced database management module with comprehensive features and safety mechanisms."""

from typing import Dict, Any, Optional, List, Union, Type
from enum import Enum
import logging
import time
import uuid
import asyncio
from datetime import datetime
from dataclasses import dataclass
from prometheus_client import Counter, Histogram, Gauge, Summary
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
import elasticsearch
from motor.motor_asyncio import AsyncIOMotorClient
import aioredis
from pydantic import BaseModel, Field, validator
import pybreaker
from functools import wraps
import structlog
from deepdiff import DeepDiff
from collections import defaultdict
import atexit
from rag_service.monitoring.metrics import MetricsManager

# Enhanced logging setup
logger = structlog.get_logger(__name__)

class ConnectionState(Enum):
    """Database connection states."""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    FAILED = "failed"
    RECONNECTING = "reconnecting"

class DatabaseConfig(BaseModel):
    """Enhanced database configuration with validation."""
    class ElasticsearchConfig(BaseModel):
        hosts: List[str] = Field(..., min_items=1)
        timeout: int = Field(default=30, ge=1)
        max_connections: int = Field(default=10, ge=1)
        index: str
        sniff_on_start: bool = Field(default=True)
        retry_on_timeout: bool = Field(default=True)
        max_retries: int = Field(default=3, ge=1)
        
    class MongoDBConfig(BaseModel):
        uri: str
        database: str
        collection: str
        max_pool_size: int = Field(default=100, ge=1)
        min_pool_size: int = Field(default=10, ge=1)
        max_idle_time_ms: int = Field(default=10000, ge=0)
        
    class RedisConfig(BaseModel):
        host: str
        port: int = Field(default=6379, ge=1, le=65535)
        db: int = Field(default=0, ge=0)
        max_connections: int = Field(default=10, ge=1)
        socket_timeout: int = Field(default=5, ge=1)
        
    elasticsearch: ElasticsearchConfig
    mongodb: MongoDBConfig
    redis: RedisConfig
    connection_timeout: int = Field(default=30, ge=1)
    retry_attempts: int = Field(default=3, ge=1)
    circuit_breaker_threshold: int = Field(default=5, ge=1)
    circuit_breaker_reset_timeout: int = Field(default=60, ge=1)
    
    @validator("elasticsearch")
    def validate_es_config(cls, v):
        if not v.hosts:
            raise ValueError("At least one Elasticsearch host must be specified")
        return v

class DatabaseError(Exception):
    """Enhanced base class for database-related errors."""
    def __init__(self, message: str, error_type: str, retryable: bool = False):
        super().__init__(message)
        self.error_type = error_type
        self.retryable = retryable
        self.timestamp = datetime.utcnow()

class ConnectionError(DatabaseError):
    """Enhanced database connection errors."""
    def __init__(self, message: str, service: str):
        super().__init__(message, "connection", retryable=True)
        self.service = service

class QueryError(DatabaseError):
    """Enhanced database query errors."""
    def __init__(self, message: str, query_type: str, query_id: str):
        super().__init__(message, "query", retryable=False)
        self.query_type = query_type
        self.query_id = query_id

class QueryTimeout(DatabaseError):
    """Query timeout errors."""
    def __init__(self, query_id: str, timeout: float):
        super().__init__(
            f"Query {query_id} timed out after {timeout} seconds",
            "timeout",
            retryable=True
        )
        self.query_id = query_id
        self.timeout = timeout

class Document(BaseModel):
    """Enhanced document model with validation."""
    id: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    embedding: Optional[List[float]] = Field(None, min_items=1)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    @validator("embedding")
    def validate_embedding(cls, v):
        if v is not None and not all(isinstance(x, (int, float)) for x in v):
            raise ValueError("Embedding must contain only numbers")
        return v

class ConnectionPool:
    """Enhanced connection pool with monitoring and auto-scaling."""
    
    def __init__(
        self,
        name: str,
        create_connection,
        min_size: int,
        max_size: int,
        metrics_manager: MetricsManager
    ):
        self.name = name
        self._create_connection = create_connection
        self._min_size = min_size
        self._max_size = max_size
        self._metrics = metrics_manager
        self._pool = asyncio.Queue(maxsize=max_size)
        self._in_use = set()
        self._lock = asyncio.Lock()
        self._maintenance_task = None
        self._closed = False
        
    async def initialize(self):
        """Initialize the connection pool with minimum connections."""
        try:
            for _ in range(self._min_size):
                conn = await self._create_connection()
                await self._pool.put(conn)
            
            self._maintenance_task = asyncio.create_task(self._maintain_pool())
            logger.info(
                "connection_pool_initialized",
                name=self.name,
                min_size=self._min_size,
                max_size=self._max_size
            )
        except Exception as e:
            logger.error(
                "connection_pool_initialization_failed",
                name=self.name,
                error=str(e)
            )
            raise ConnectionError(f"Failed to initialize pool: {str(e)}", self.name)
    
    async def acquire(self, timeout: Optional[float] = None) -> Any:
        """Acquire a connection from the pool."""
        if self._closed:
            raise ConnectionError("Connection pool is closed", self.name)
            
        try:
            async with asyncio.timeout(timeout or 30):
                async with self._lock:
                    if self._pool.empty() and len(self._in_use) < self._max_size:
                        conn = await self._create_connection()
                    else:
                        conn = await self._pool.get()
                    
                    self._in_use.add(conn)
                    self._update_metrics()
                    return conn
                    
        except asyncio.TimeoutError:
            self._metrics.connection_timeouts.labels(pool=self.name).inc()
            raise ConnectionError(
                f"Timeout acquiring connection from pool {self.name}",
                self.name
            )
    
    async def release(self, conn: Any):
        """Release a connection back to the pool."""
        async with self._lock:
            if conn in self._in_use:
                self._in_use.remove(conn)
                if not self._closed:
                    await self._pool.put(conn)
                self._update_metrics()
    
    async def _maintain_pool(self):
        """Background task to maintain pool health."""
        while not self._closed:
            try:
                await asyncio.sleep(60)  # Check every minute
                async with self._lock:
                    # Clean up dead connections
                    dead_conns = set()
                    for conn in self._in_use:
                        if not await self._is_connection_alive(conn):
                            dead_conns.add(conn)
                    
                    for conn in dead_conns:
                        self._in_use.remove(conn)
                        self._metrics.connection_errors.labels(
                            pool=self.name,
                            error="dead_connection"
                        ).inc()
                    
                    # Ensure minimum connections
                    while (
                        self._pool.qsize() + len(self._in_use) < self._min_size
                    ):
                        try:
                            conn = await self._create_connection()
                            await self._pool.put(conn)
                        except Exception as e:
                            logger.error(
                                "pool_maintenance_failed",
                                name=self.name,
                                error=str(e)
                            )
                            break
                            
            except Exception as e:
                logger.error(
                    "pool_maintenance_error",
                    name=self.name,
                    error=str(e)
                )
    
    def _update_metrics(self):
        """Update pool metrics."""
        self._metrics.connection_pool_size.labels(
            pool=self.name
        ).set(self._pool.qsize())
        self._metrics.active_connections.labels(
            pool=self.name
        ).set(len(self._in_use))
    
    async def close(self):
        """Close the connection pool."""
        self._closed = True
        if self._maintenance_task:
            self._maintenance_task.cancel()
            
        async with self._lock:
            # Close all connections
            while not self._pool.empty():
                conn = await self._pool.get()
                await self._close_connection(conn)
            
            for conn in self._in_use:
                await self._close_connection(conn)
            
            self._in_use.clear()
            self._update_metrics()
    
    async def _is_connection_alive(self, conn: Any) -> bool:
        """Check if a connection is still alive."""
        try:
            if hasattr(conn, "ping"):
                await conn.ping()
            return True
        except Exception:
            return False
    
    async def _close_connection(self, conn: Any):
        """Close a single connection."""
        try:
            if hasattr(conn, "close"):
                await conn.close()
        except Exception as e:
            logger.warning(
                "connection_close_failed",
                name=self.name,
                error=str(e)
            )

class EnhancedCircuitBreaker(pybreaker.CircuitBreaker):
    """Enhanced circuit breaker with metrics and partial failure detection."""
    
    def __init__(
        self,
        name: str,
        fail_max: int = 5,
        reset_timeout: int = 60,
        metrics_manager: MetricsManager = None
    ):
        super().__init__(
            fail_max=fail_max,
            reset_timeout=reset_timeout,
            name=name
        )
        self._metrics = metrics_manager
        self._partial_failure_count = 0
        self._last_failure_time = None
        self._failure_window = 60  # 1 minute window for partial failures
        self._partial_failure_threshold = fail_max * 2
        
    def _on_failure(self, exc):
        """Handle circuit breaker failure."""
        super()._on_failure(exc)
        
        current_time = time.monotonic()
        self._partial_failure_count += 1
        self._last_failure_time = current_time
        
        # Record metrics
        if self._metrics:
            self._metrics.circuit_breaker_failures.labels(
                name=self.name,
                type=type(exc).__name__
            ).inc()
        
        # Check for partial failures
        if self._partial_failure_count >= self._partial_failure_threshold:
            if (
                self._last_failure_time and 
                current_time - self._last_failure_time <= self._failure_window
            ):
                self._state = self.STATE_OPEN
                if self._metrics:
                    self._metrics.circuit_breaker_state.labels(
                        name=self.name
                    ).set(0)  # 0 for open
                logger.warning(
                    "circuit_breaker_opened",
                    name=self.name,
                    failures=self._partial_failure_count
                )
    
    def _on_success(self):
        """Handle circuit breaker success."""
        super()._on_success()
        self._partial_failure_count = max(0, self._partial_failure_count - 1)
        
        if self._metrics:
            self._metrics.circuit_breaker_state.labels(
                name=self.name
            ).set(1)  # 1 for closed
    
    def _on_state_change(self, old_state, new_state):
        """Handle circuit breaker state change."""
        super()._on_state_change(old_state, new_state)
        
        if self._metrics:
            self._metrics.circuit_breaker_state_changes.labels(
                name=self.name,
                from_state=old_state,
                to_state=new_state
            ).inc()
            
        logger.info(
            "circuit_breaker_state_changed",
            name=self.name,
            old_state=old_state,
            new_state=new_state
        )

class QueryExecutor:
    """Execute database queries with timeout and monitoring."""
    
    def __init__(self, timeout: float = 30.0):
        self._timeout = timeout
        self._active_queries = {}
        self._query_lock = asyncio.Lock()
        self._metrics = MetricsManager.get_instance().collector
        
    async def execute(
        self,
        query_func,
        *args,
        database: str,
        operation: str,
        timeout: Optional[float] = None,
        **kwargs
    ):
        """Execute a database query with monitoring and timeout.
        
        Args:
            query_func: Async function to execute
            *args: Positional arguments for query_func
            database: Database name for metrics
            operation: Operation name for metrics
            timeout: Optional timeout override
            **kwargs: Keyword arguments for query_func
            
        Returns:
            Query result
            
        Raises:
            QueryTimeout: If query exceeds timeout
            QueryError: If query fails
        """
        query_id = str(uuid.uuid4())
        start_time = time.monotonic()
        
        # Record query start
        self._metrics.db_queries_active.labels(
            database=database,
            operation=operation
        ).inc()
        
        try:
            async with asyncio.timeout(timeout or self._timeout):
                async with self._query_lock:
                    self._active_queries[query_id] = {
                        "database": database,
                        "operation": operation,
                        "start_time": start_time
                    }
                    try:
                        result = await query_func(*args, **kwargs)
                        duration = time.monotonic() - start_time
                        
                        # Record success metrics
                        self._metrics.db_query_duration_seconds.labels(
                            database=database,
                            operation=operation,
                            status="success"
                        ).observe(duration)
                        
                        return result
                        
                    finally:
                        del self._active_queries[query_id]
                        
        except asyncio.TimeoutError:
            duration = time.monotonic() - start_time
            
            # Record timeout metrics
            self._metrics.db_query_duration_seconds.labels(
                database=database,
                operation=operation,
                status="timeout"
            ).observe(duration)
            
            self._metrics.db_query_timeouts.labels(
                database=database,
                operation=operation
            ).inc()
            
            raise QueryTimeout(query_id, self._timeout)
            
        except Exception as e:
            duration = time.monotonic() - start_time
            
            # Record error metrics
            self._metrics.db_query_duration_seconds.labels(
                database=database,
                operation=operation,
                status="error"
            ).observe(duration)
            
            self._metrics.db_query_errors.labels(
                database=database,
                operation=operation,
                error_type=type(e).__name__
            ).inc()
            
            raise QueryError(
                f"Query failed: {str(e)}",
                operation,
                query_id
            ) from e
            
        finally:
            # Decrement active queries
            self._metrics.db_queries_active.labels(
                database=database,
                operation=operation
            ).dec()
    
    def get_active_queries(self) -> Dict[str, Dict[str, Any]]:
        """Get information about currently executing queries."""
        current_time = time.monotonic()
        return {
            query_id: {
                **query_info,
                "duration": current_time - query_info["start_time"]
            }
            for query_id, query_info in self._active_queries.items()
        }

class HealthMonitor:
    """Monitor database health and maintain connection states."""
    
    def __init__(self, db_manager: 'DatabaseManager'):
        self._db = db_manager
        self._stop_event = asyncio.Event()
        self._monitor_task = None
        self._check_interval = 60  # Check every minute
        self._metrics = MetricsManager.get_instance().collector
        
    def start(self):
        """Start health monitoring."""
        if not self._monitor_task:
            self._monitor_task = asyncio.create_task(self._monitor_loop())
            logger.info("health_monitoring_started")
    
    def stop(self):
        """Stop health monitoring."""
        if self._monitor_task:
            self._stop_event.set()
            self._monitor_task.cancel()
            self._monitor_task = None
            logger.info("health_monitoring_stopped")
    
    async def _monitor_loop(self):
        """Main monitoring loop."""
        while not self._stop_event.is_set():
            try:
                health_status = await self._check_health()
                self._update_metrics(health_status)
                self._handle_status_changes(health_status)
                
                await asyncio.sleep(self._check_interval)
                
            except Exception as e:
                logger.error(
                    "health_check_failed",
                    error=str(e)
                )
                await asyncio.sleep(5)  # Short delay on error
    
    async def _check_health(self) -> Dict[str, Dict[str, Any]]:
        """Check health of all database connections."""
        results = {}
        
        # Check Elasticsearch
        try:
            es_pool = self._db._pools.get("elasticsearch")
            if es_pool:
                conn = await es_pool.acquire()
                try:
                    await conn.info()
                    results["elasticsearch"] = {
                        "status": "healthy",
                        "message": "Connection OK",
                        "pool_size": es_pool._pool.qsize(),
                        "active_connections": len(es_pool._in_use)
                    }
                finally:
                    await es_pool.release(conn)
        except Exception as e:
            results["elasticsearch"] = {
                "status": "unhealthy",
                "message": str(e)
            }
        
        # Check MongoDB
        try:
            mongo_pool = self._db._pools.get("mongodb")
            if mongo_pool:
                conn = await mongo_pool.acquire()
                try:
                    await conn[self._db.config.mongodb.database].command("ping")
                    results["mongodb"] = {
                        "status": "healthy",
                        "message": "Connection OK",
                        "pool_size": mongo_pool._pool.qsize(),
                        "active_connections": len(mongo_pool._in_use)
                    }
                finally:
                    await mongo_pool.release(conn)
        except Exception as e:
            results["mongodb"] = {
                "status": "unhealthy",
                "message": str(e)
            }
        
        # Check Redis
        try:
            redis_pool = self._db._pools.get("redis")
            if redis_pool:
                conn = await redis_pool.acquire()
                try:
                    await conn.ping()
                    results["redis"] = {
                        "status": "healthy",
                        "message": "Connection OK",
                        "pool_size": redis_pool._pool.qsize(),
                        "active_connections": len(redis_pool._in_use)
                    }
                finally:
                    await redis_pool.release(conn)
        except Exception as e:
            results["redis"] = {
                "status": "unhealthy",
                "message": str(e)
            }
        
        return results
    
    def _update_metrics(self, health_status: Dict[str, Dict[str, Any]]):
        """Update health metrics."""
        for service, status in health_status.items():
            # Update health status
            self._metrics.db_health_status.labels(
                database=service
            ).set(1 if status["status"] == "healthy" else 0)
            
            # Update pool metrics if available
            if "pool_size" in status:
                self._metrics.db_connection_pool_size.labels(
                    database=service
                ).set(status["pool_size"])
                
            if "active_connections" in status:
                self._metrics.db_active_connections.labels(
                    database=service
                ).set(status["active_connections"])
    
    def _handle_status_changes(self, health_status: Dict[str, Dict[str, Any]]):
        """Handle changes in service health status."""
        for service, status in health_status.items():
            current_state = self._db._connection_states[service]
            new_state = (
                ConnectionState.CONNECTED
                if status["status"] == "healthy"
                else ConnectionState.FAILED
            )
            
            if current_state != new_state:
                self._db._connection_states[service] = new_state
                logger.info(
                    "service_state_changed",
                    service=service,
                    old_state=current_state.value,
                    new_state=new_state.value,
                    message=status["message"]
                )
                
                # Trigger reconnection if needed
                if (
                    new_state == ConnectionState.FAILED and
                    not self._stop_event.is_set()
                ):
                    asyncio.create_task(self._attempt_reconnection(service))
    
    async def _attempt_reconnection(self, service: str):
        """Attempt to reconnect to a failed service."""
        logger.info(
            "attempting_reconnection",
            service=service
        )
        
        try:
            if service == "elasticsearch":
                self._db._pools[service] = await self._db._create_elasticsearch_pool()
            elif service == "mongodb":
                self._db._pools[service] = await self._db._create_mongodb_pool()
            elif service == "redis":
                self._db._pools[service] = await self._db._create_redis_pool()
                
            logger.info(
                "reconnection_successful",
                service=service
            )
            
        except Exception as e:
            logger.error(
                "reconnection_failed",
                service=service,
                error=str(e)
            )

class DatabaseManager:
    """Enhanced database management system with comprehensive features."""

    def __init__(self, config: Dict[str, Any]):
        """Initialize the database manager with enhanced configuration.
        
        Args:
            config: Database configuration dictionary
        """
        # Validate and store configuration
        self.config = DatabaseConfig(**config)
        self.tracer = trace.get_tracer(__name__)
        self.metrics = MetricsManager.get_instance().collector
        
        # Initialize connection state tracking
        self._connection_states = {
            "elasticsearch": ConnectionState.DISCONNECTED,
            "mongodb": ConnectionState.DISCONNECTED,
            "redis": ConnectionState.DISCONNECTED
        }
        
        # Initialize connection pools
        self._pools = {}
        
        # Initialize circuit breakers
        self._circuit_breakers = {
            "elasticsearch": EnhancedCircuitBreaker(
                name="elasticsearch",
                fail_max=self.config.circuit_breaker_threshold,
                reset_timeout=self.config.circuit_breaker_reset_timeout,
                metrics_manager=self.metrics
            ),
            "mongodb": EnhancedCircuitBreaker(
                name="mongodb",
                fail_max=self.config.circuit_breaker_threshold,
                reset_timeout=self.config.circuit_breaker_reset_timeout,
                metrics_manager=self.metrics
            ),
            "redis": EnhancedCircuitBreaker(
                name="redis",
                fail_max=self.config.circuit_breaker_threshold,
                reset_timeout=self.config.circuit_breaker_reset_timeout,
                metrics_manager=self.metrics
            )
        }
        
        # Initialize query executor
        self._query_executor = QueryExecutor(timeout=30.0)
        
        # Initialize health monitor
        self._health_monitor = HealthMonitor(self)
        
        # Register cleanup
        self._register_cleanup()
        
        logger.info(
            "database_manager_initialized",
            elasticsearch_hosts=self.config.elasticsearch.hosts,
            mongodb_uri=self.config.mongodb.uri,
            redis_host=self.config.redis.host
        )

    def _register_cleanup(self):
        """Register cleanup handlers."""
        atexit.register(
            lambda: asyncio.run(self.close())
        )

    async def connect(self):
        """Initialize all database connections with enhanced error handling."""
        try:
            # Initialize connection pools
            self._pools["elasticsearch"] = await self._create_elasticsearch_pool()
            self._pools["mongodb"] = await self._create_mongodb_pool()
            self._pools["redis"] = await self._create_redis_pool()
            
            # Start health monitoring
            self._health_monitor.start()
            
            logger.info("all_database_connections_established")
            
        except Exception as e:
            logger.error(
                "database_connection_failed",
                error=str(e)
            )
            await self.close()
            raise ConnectionError(
                "Failed to initialize database connections",
                "all"
            ) from e

    async def _create_elasticsearch_pool(self) -> ConnectionPool:
        """Create and initialize Elasticsearch connection pool."""
        self._connection_states["elasticsearch"] = ConnectionState.CONNECTING
        
        try:
            pool = ConnectionPool(
                name="elasticsearch",
                create_connection=self._create_elasticsearch_client,
                min_size=1,
                max_size=self.config.elasticsearch.max_connections,
                metrics_manager=self.metrics
            )
            await pool.initialize()
            self._connection_states["elasticsearch"] = ConnectionState.CONNECTED
            return pool
            
        except Exception as e:
            self._connection_states["elasticsearch"] = ConnectionState.FAILED
            raise ConnectionError(
                f"Failed to create Elasticsearch pool: {str(e)}",
                "elasticsearch"
            )

    async def _create_mongodb_pool(self) -> ConnectionPool:
        """Create and initialize MongoDB connection pool."""
        self._connection_states["mongodb"] = ConnectionState.CONNECTING
        
        try:
            pool = ConnectionPool(
                name="mongodb",
                create_connection=self._create_mongodb_client,
                min_size=self.config.mongodb.min_pool_size,
                max_size=self.config.mongodb.max_pool_size,
                metrics_manager=self.metrics
            )
            await pool.initialize()
            self._connection_states["mongodb"] = ConnectionState.CONNECTED
            return pool
            
        except Exception as e:
            self._connection_states["mongodb"] = ConnectionState.FAILED
            raise ConnectionError(
                f"Failed to create MongoDB pool: {str(e)}",
                "mongodb"
            )

    async def _create_redis_pool(self) -> ConnectionPool:
        """Create and initialize Redis connection pool."""
        self._connection_states["redis"] = ConnectionState.CONNECTING
        
        try:
            pool = ConnectionPool(
                name="redis",
                create_connection=self._create_redis_client,
                min_size=1,
                max_size=self.config.redis.max_connections,
                metrics_manager=self.metrics
            )
            await pool.initialize()
            self._connection_states["redis"] = ConnectionState.CONNECTED
            return pool
            
        except Exception as e:
            self._connection_states["redis"] = ConnectionState.FAILED
            raise ConnectionError(
                f"Failed to create Redis pool: {str(e)}",
                "redis"
            )

    async def _create_elasticsearch_client(self):
        """Create a new Elasticsearch client."""
        return elasticsearch.AsyncElasticsearch(
            hosts=self.config.elasticsearch.hosts,
            timeout=self.config.elasticsearch.timeout,
            maxsize=1,  # Individual connection
            sniff_on_start=self.config.elasticsearch.sniff_on_start,
            retry_on_timeout=self.config.elasticsearch.retry_on_timeout
        )

    async def _create_mongodb_client(self):
        """Create a new MongoDB client."""
        return AsyncIOMotorClient(
            self.config.mongodb.uri,
            maxPoolSize=1,  # Individual connection
            minPoolSize=1,
            maxIdleTimeMS=self.config.mongodb.max_idle_time_ms
        )

    async def _create_redis_client(self):
        """Create a new Redis client."""
        return aioredis.Redis(
            host=self.config.redis.host,
            port=self.config.redis.port,
            db=self.config.redis.db,
            max_connections=1,  # Individual connection
            socket_timeout=self.config.redis.socket_timeout
        )

    @property
    def es_client(self):
        """Get Elasticsearch client from pool."""
        return self._pools["elasticsearch"]

    @property
    def mongo_client(self):
        """Get MongoDB client from pool."""
        return self._pools["mongodb"]

    @property
    def redis_client(self):
        """Get Redis client from pool."""
        return self._pools["redis"]

    async def close(self):
        """Close all database connections and cleanup resources."""
        try:
            # Stop health monitoring
            if hasattr(self, '_health_monitor'):
                self._health_monitor.stop()
            
            # Close all connection pools
            for name, pool in self._pools.items():
                try:
                    await pool.close()
                    self._connection_states[name] = ConnectionState.DISCONNECTED
                except Exception as e:
                    logger.error(
                        "pool_close_failed",
                        pool=name,
                        error=str(e)
                    )
            
            logger.info("database_manager_closed")
            
        except Exception as e:
            logger.error(
                "database_close_failed",
                error=str(e)
            )
            raise ConnectionError(
                "Failed to close database connections",
                "all"
            ) from e

    @es_breaker
    async def search_documents(
        self,
        query: str,
        filters: Optional[Dict[str, Any]] = None,
        size: int = 10,
        timeout: Optional[float] = None
    ) -> List[Document]:
        """Search documents in Elasticsearch with enhanced error handling and monitoring.
        
        Args:
            query: Search query
            filters: Optional filters
            size: Number of results to return
            timeout: Optional query timeout override
            
        Returns:
            List of matching documents
            
        Raises:
            QueryError: If search fails
            QueryTimeout: If search times out
        """
        # Build search query
        search_query = {
            "query": {
                "multi_match": {
                    "query": query,
                    "fields": ["content", "metadata.*"],
                    "type": "best_fields",
                    "tie_breaker": 0.3
                }
            },
            "size": min(size, 100),  # Limit maximum size
            "_source": True,
            "track_total_hits": True
        }

        if filters:
            search_query["query"] = {
                "bool": {
                    "must": [search_query["query"]],
                    "filter": filters
                }
            }

        async def _execute_search():
            # Get connection from pool
            es_pool = self._pools["elasticsearch"]
            conn = await es_pool.acquire()
            try:
                # Execute search with tracing
                with self.tracer.start_span("elasticsearch_search") as span:
                    span.set_attribute("query", str(search_query))
                    response = await conn.search(
                        index=self.config.elasticsearch.index,
                        body=search_query
                    )
                    
                    # Process results
                    documents = []
                    for hit in response["hits"]["hits"]:
                        doc = hit["_source"]
                        doc["id"] = hit["_id"]
                        doc["score"] = hit["_score"]
                        documents.append(Document(**doc))
                    
                    # Add search metadata to span
                    span.set_attribute("total_hits", response["hits"]["total"]["value"])
                    span.set_attribute("returned_hits", len(documents))
                    span.set_status(Status(StatusCode.OK))
                    
                    return documents
            finally:
                # Always release the connection
                await es_pool.release(conn)

        try:
            # Execute search with monitoring
            documents = await self._query_executor.execute(
                _execute_search,
                database="elasticsearch",
                operation="search",
                timeout=timeout
            )
            
            # Record cache metrics if documents found
            if documents:
                self.metrics.search_cache_miss.labels(
                    index=self.config.elasticsearch.index
                ).inc()
            
            return documents
            
        except QueryTimeout as e:
            logger.error(
                "search_timeout",
                query=query,
                timeout=e.timeout
            )
            raise
            
        except Exception as e:
            logger.error(
                "search_failed",
                query=query,
                error=str(e)
            )
            if isinstance(e, QueryError):
                raise
            raise QueryError(
                f"Search failed: {str(e)}",
                "search",
                str(uuid.uuid4())
            ) from e

    @mongo_breaker
    async def store_document(
        self,
        document: Document,
        timeout: Optional[float] = None
    ) -> str:
        """Store document in MongoDB with enhanced error handling and monitoring.
        
        Args:
            document: Document to store
            timeout: Optional operation timeout
            
        Returns:
            Stored document ID
            
        Raises:
            QueryError: If storage fails
            QueryTimeout: If operation times out
        """
        async def _execute_store():
            # Get connection from pool
            mongo_pool = self._pools["mongodb"]
            conn = await mongo_pool.acquire()
            try:
                # Store document with tracing
                with self.tracer.start_span("mongodb_store") as span:
                    span.set_attribute("document_id", document.id)
                    result = await conn[
                        self.config.mongodb.database
                    ][self.config.mongodb.collection].insert_one(
                        document.dict()
                    )
                    span.set_status(Status(StatusCode.OK))
                    return str(result.inserted_id)
            finally:
                await mongo_pool.release(conn)

        try:
            # Execute storage with monitoring
            doc_id = await self._query_executor.execute(
                _execute_store,
                database="mongodb",
                operation="insert",
                timeout=timeout
            )
            
            # Update cache metrics
            self.metrics.document_inserts.labels(
                collection=self.config.mongodb.collection
            ).inc()
            
            return doc_id
            
        except QueryTimeout as e:
            logger.error(
                "document_store_timeout",
                document_id=document.id,
                timeout=e.timeout
            )
            raise
            
        except Exception as e:
            logger.error(
                "document_store_failed",
                document_id=document.id,
                error=str(e)
            )
            if isinstance(e, QueryError):
                raise
            raise QueryError(
                f"Document storage failed: {str(e)}",
                "insert",
                str(uuid.uuid4())
            ) from e

    @redis_breaker
    async def get_cache(
        self,
        key: str,
        timeout: Optional[float] = None
    ) -> Optional[str]:
        """Get value from Redis cache with enhanced error handling and monitoring.
        
        Args:
            key: Cache key
            timeout: Optional operation timeout
            
        Returns:
            Cached value if exists, None otherwise
            
        Raises:
            QueryError: If cache operation fails
            QueryTimeout: If operation times out
        """
        async def _execute_get():
            # Get connection from pool
            redis_pool = self._pools["redis"]
            conn = await redis_pool.acquire()
            try:
                # Get from cache with tracing
                with self.tracer.start_span("redis_get") as span:
                    span.set_attribute("key", key)
                    value = await conn.get(key)
                    span.set_status(Status(StatusCode.OK))
                    return value
            finally:
                await redis_pool.release(conn)

        try:
            # Execute get with monitoring
            value = await self._query_executor.execute(
                _execute_get,
                database="redis",
                operation="get",
                timeout=timeout
            )
            
            # Update cache metrics
            if value is not None:
                self.metrics.cache_hits.labels(
                    operation="get"
                ).inc()
            else:
                self.metrics.cache_misses.labels(
                    operation="get"
                ).inc()
            
            return value
            
        except QueryTimeout as e:
            logger.error(
                "cache_get_timeout",
                key=key,
                timeout=e.timeout
            )
            raise
            
        except Exception as e:
            logger.error(
                "cache_get_failed",
                key=key,
                error=str(e)
            )
            if isinstance(e, QueryError):
                raise
            raise QueryError(
                f"Cache get failed: {str(e)}",
                "get",
                str(uuid.uuid4())
            ) from e

    @redis_breaker
    async def set_cache(
        self,
        key: str,
        value: str,
        ttl: Optional[int] = None,
        timeout: Optional[float] = None
    ) -> bool:
        """Set value in Redis cache with enhanced error handling and monitoring.
        
        Args:
            key: Cache key
            value: Value to cache
            ttl: Optional TTL in seconds
            timeout: Optional operation timeout
            
        Returns:
            Whether operation succeeded
            
        Raises:
            QueryError: If cache operation fails
            QueryTimeout: If operation times out
        """
        async def _execute_set():
            # Get connection from pool
            redis_pool = self._pools["redis"]
            conn = await redis_pool.acquire()
            try:
                # Set in cache with tracing
                with self.tracer.start_span("redis_set") as span:
                    span.set_attribute("key", key)
                    span.set_attribute("ttl", ttl)
                    if ttl:
                        success = await conn.setex(key, ttl, value)
                    else:
                        success = await conn.set(key, value)
                    span.set_status(
                        Status(StatusCode.OK if success else StatusCode.ERROR)
                    )
                    return bool(success)
            finally:
                await redis_pool.release(conn)

        try:
            # Execute set with monitoring
            success = await self._query_executor.execute(
                _execute_set,
                database="redis",
                operation="set",
                timeout=timeout
            )
            
            # Update cache metrics
            if success:
                self.metrics.cache_sets.labels(
                    operation="set",
                    status="success"
                ).inc()
            else:
                self.metrics.cache_sets.labels(
                    operation="set",
                    status="error"
                ).inc()
            
            return success
            
        except QueryTimeout as e:
            logger.error(
                "cache_set_timeout",
                key=key,
                timeout=e.timeout
            )
            raise
            
        except Exception as e:
            logger.error(
                "cache_set_failed",
                key=key,
                error=str(e)
            )
            if isinstance(e, QueryError):
                raise
            raise QueryError(
                f"Cache set failed: {str(e)}",
                "set",
                str(uuid.uuid4())
            ) from e

    async def health_check(self) -> Dict[str, Dict[str, Any]]:
        """Check health of all database connections with enhanced monitoring."""
        return await self._health_monitor._check_health()

 