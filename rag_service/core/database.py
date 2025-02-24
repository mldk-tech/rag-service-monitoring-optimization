"""Database management module for comprehensive database interactions."""

from typing import Dict, Any, Optional, List, Union
import logging
import time
import asyncio
from datetime import datetime
from prometheus_client import Counter, Histogram, Gauge
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
import elasticsearch
from motor.motor_asyncio import AsyncIOMotorClient
import aioredis
from pydantic import BaseModel
import pybreaker

class DatabaseError(Exception):
    """Base class for database-related errors."""
    pass

class ConnectionError(DatabaseError):
    """Database connection errors."""
    pass

class QueryError(DatabaseError):
    """Database query errors."""
    pass

class Document(BaseModel):
    """Base document model."""
    id: str
    content: str
    metadata: Dict[str, Any]
    embedding: Optional[List[float]] = None
    created_at: datetime
    updated_at: datetime

class DatabaseManager:
    """Comprehensive database management for the RAG service."""

    def __init__(self, config: Dict[str, Any]):
        """Initialize database manager.
        
        Args:
            config: Database configuration
        """
        self.config = config
        self.tracer = trace.get_tracer(__name__)

        # Circuit breakers
        self.es_breaker = pybreaker.CircuitBreaker(
            fail_max=5,
            reset_timeout=60,
            name="elasticsearch"
        )
        self.mongo_breaker = pybreaker.CircuitBreaker(
            fail_max=5,
            reset_timeout=60,
            name="mongodb"
        )
        self.redis_breaker = pybreaker.CircuitBreaker(
            fail_max=5,
            reset_timeout=60,
            name="redis"
        )

        # Metrics
        self._init_metrics()

        # Initialize connections
        self.es_client = None
        self.mongo_client = None
        self.redis_client = None

    def _init_metrics(self):
        """Initialize database metrics."""
        # Query metrics
        self.query_counter = Counter(
            'rag_db_queries_total',
            'Total number of database queries',
            ['database', 'operation', 'status']
        )
        self.query_latency = Histogram(
            'rag_db_query_duration_seconds',
            'Database query duration in seconds',
            ['database', 'operation'],
            buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5)
        )

        # Connection metrics
        self.connection_gauge = Gauge(
            'rag_db_connections',
            'Number of active database connections',
            ['database']
        )
        self.connection_errors = Counter(
            'rag_db_connection_errors_total',
            'Total number of database connection errors',
            ['database']
        )

        # Circuit breaker metrics
        self.circuit_breaks = Counter(
            'rag_db_circuit_breaks_total',
            'Total number of circuit breaker trips',
            ['database']
        )
        self.circuit_state = Gauge(
            'rag_db_circuit_state',
            'Circuit breaker state (0=open, 1=closed)',
            ['database']
        )

    async def connect(self):
        """Initialize all database connections with retries."""
        try:
            await asyncio.gather(
                self._connect_elasticsearch(),
                self._connect_mongodb(),
                self._connect_redis()
            )
        except Exception as e:
            logging.error("Failed to initialize database connections: %s", str(e))
            raise ConnectionError("Database initialization failed") from e

    @pybreaker.CircuitBreakerError
    async def _connect_elasticsearch(self):
        """Initialize Elasticsearch connection with retry logic."""
        retries = 3
        for attempt in range(retries):
            try:
                self.es_client = elasticsearch.AsyncElasticsearch(
                    hosts=self.config["elasticsearch"]["hosts"],
                    timeout=self.config["elasticsearch"]["timeout"],
                    maxsize=self.config["elasticsearch"]["max_connections"]
                )
                # Test connection
                await self.es_client.info()
                self.connection_gauge.labels(database="elasticsearch").set(1)
                logging.info("Successfully connected to Elasticsearch")
                return

            except Exception as e:
                self.connection_errors.labels(database="elasticsearch").inc()
                if attempt == retries - 1:
                    logging.error("Failed to connect to Elasticsearch: %s", str(e))
                    raise ConnectionError("Elasticsearch connection failed") from e
                await asyncio.sleep(2 ** attempt)  # Exponential backoff

    @pybreaker.CircuitBreakerError
    async def _connect_mongodb(self):
        """Initialize MongoDB connection with retry logic."""
        retries = 3
        for attempt in range(retries):
            try:
                self.mongo_client = AsyncIOMotorClient(
                    self.config["mongodb"]["uri"],
                    maxPoolSize=self.config["mongodb"]["max_connections"]
                )
                # Test connection
                await self.mongo_client.admin.command('ping')
                self.connection_gauge.labels(database="mongodb").set(1)
                logging.info("Successfully connected to MongoDB")
                return

            except Exception as e:
                self.connection_errors.labels(database="mongodb").inc()
                if attempt == retries - 1:
                    logging.error("Failed to connect to MongoDB: %s", str(e))
                    raise ConnectionError("MongoDB connection failed") from e
                await asyncio.sleep(2 ** attempt)  # Exponential backoff

    @pybreaker.CircuitBreakerError
    async def _connect_redis(self):
        """Initialize Redis connection with retry logic."""
        retries = 3
        for attempt in range(retries):
            try:
                self.redis_client = aioredis.Redis(
                    host=self.config["redis"]["host"],
                    port=self.config["redis"]["port"],
                    db=self.config["redis"]["db"],
                    max_connections=self.config["redis"]["max_connections"]
                )
                # Test connection
                await self.redis_client.ping()
                self.connection_gauge.labels(database="redis").set(1)
                logging.info("Successfully connected to Redis")
                return

            except Exception as e:
                self.connection_errors.labels(database="redis").inc()
                if attempt == retries - 1:
                    logging.error("Failed to connect to Redis: %s", str(e))
                    raise ConnectionError("Redis connection failed") from e
                await asyncio.sleep(2 ** attempt)  # Exponential backoff

    async def close(self):
        """Close all database connections."""
        try:
            await asyncio.gather(
                self._close_elasticsearch(),
                self._close_mongodb(),
                self._close_redis()
            )
        except Exception as e:
            logging.error("Error closing database connections: %s", str(e))
            raise ConnectionError("Failed to close database connections") from e

    async def _close_elasticsearch(self):
        """Close Elasticsearch connection."""
        if self.es_client:
            await self.es_client.close()
            self.connection_gauge.labels(database="elasticsearch").set(0)

    async def _close_mongodb(self):
        """Close MongoDB connection."""
        if self.mongo_client:
            self.mongo_client.close()
            self.connection_gauge.labels(database="mongodb").set(0)

    async def _close_redis(self):
        """Close Redis connection."""
        if self.redis_client:
            await self.redis_client.close()
            self.connection_gauge.labels(database="redis").set(0)

    @es_breaker
    async def search_documents(
        self,
        query: str,
        filters: Optional[Dict[str, Any]] = None,
        size: int = 10
    ) -> List[Document]:
        """Search documents in Elasticsearch.
        
        Args:
            query: Search query
            filters: Optional filters
            size: Number of results to return
            
        Returns:
            List of matching documents
            
        Raises:
            QueryError: If search fails
        """
        start_time = time.time()
        
        with self.tracer.start_span("elasticsearch_search") as span:
            try:
                # Build search query
                search_query = {
                    "query": {
                        "bool": {
                            "must": [
                                {"match": {"content": query}}
                            ]
                        }
                    },
                    "size": size
                }

                if filters:
                    search_query["query"]["bool"]["filter"] = filters

                # Execute search
                response = await self.es_client.search(
                    index=self.config["elasticsearch"]["index_name"],
                    body=search_query
                )

                # Process results
                documents = []
                for hit in response["hits"]["hits"]:
                    doc = hit["_source"]
                    doc["id"] = hit["_id"]
                    documents.append(Document(**doc))

                # Record metrics
                duration = time.time() - start_time
                self.query_counter.labels(
                    database="elasticsearch",
                    operation="search",
                    status="success"
                ).inc()
                self.query_latency.labels(
                    database="elasticsearch",
                    operation="search"
                ).observe(duration)

                span.set_attribute("hits", len(documents))
                span.set_status(Status(StatusCode.OK))

                return documents

            except Exception as e:
                duration = time.time() - start_time
                self.query_counter.labels(
                    database="elasticsearch",
                    operation="search",
                    status="error"
                ).inc()
                self.query_latency.labels(
                    database="elasticsearch",
                    operation="search"
                ).observe(duration)

                span.set_status(Status(StatusCode.ERROR))
                logging.error("Elasticsearch search failed: %s", str(e))
                raise QueryError("Search failed") from e

    @mongo_breaker
    async def store_document(
        self,
        document: Document
    ) -> str:
        """Store document in MongoDB.
        
        Args:
            document: Document to store
            
        Returns:
            Stored document ID
            
        Raises:
            QueryError: If storage fails
        """
        start_time = time.time()
        
        with self.tracer.start_span("mongodb_store") as span:
            try:
                # Store document
                result = await self.mongo_client[
                    self.config["mongodb"]["database"]
                ][self.config["mongodb"]["collection"]].insert_one(
                    document.dict()
                )

                # Record metrics
                duration = time.time() - start_time
                self.query_counter.labels(
                    database="mongodb",
                    operation="insert",
                    status="success"
                ).inc()
                self.query_latency.labels(
                    database="mongodb",
                    operation="insert"
                ).observe(duration)

                span.set_status(Status(StatusCode.OK))
                return str(result.inserted_id)

            except Exception as e:
                duration = time.time() - start_time
                self.query_counter.labels(
                    database="mongodb",
                    operation="insert",
                    status="error"
                ).inc()
                self.query_latency.labels(
                    database="mongodb",
                    operation="insert"
                ).observe(duration)

                span.set_status(Status(StatusCode.ERROR))
                logging.error("MongoDB insert failed: %s", str(e))
                raise QueryError("Document storage failed") from e

    @redis_breaker
    async def get_cache(
        self,
        key: str
    ) -> Optional[str]:
        """Get value from Redis cache.
        
        Args:
            key: Cache key
            
        Returns:
            Cached value if exists, None otherwise
            
        Raises:
            QueryError: If cache operation fails
        """
        start_time = time.time()
        
        with self.tracer.start_span("redis_get") as span:
            try:
                # Get from cache
                value = await self.redis_client.get(key)

                # Record metrics
                duration = time.time() - start_time
                self.query_counter.labels(
                    database="redis",
                    operation="get",
                    status="success"
                ).inc()
                self.query_latency.labels(
                    database="redis",
                    operation="get"
                ).observe(duration)

                span.set_status(Status(StatusCode.OK))
                return value

            except Exception as e:
                duration = time.time() - start_time
                self.query_counter.labels(
                    database="redis",
                    operation="get",
                    status="error"
                ).inc()
                self.query_latency.labels(
                    database="redis",
                    operation="get"
                ).observe(duration)

                span.set_status(Status(StatusCode.ERROR))
                logging.error("Redis get failed: %s", str(e))
                raise QueryError("Cache get failed") from e

    @redis_breaker
    async def set_cache(
        self,
        key: str,
        value: str,
        ttl: Optional[int] = None
    ) -> bool:
        """Set value in Redis cache.
        
        Args:
            key: Cache key
            value: Value to cache
            ttl: Optional TTL in seconds
            
        Returns:
            Whether operation succeeded
            
        Raises:
            QueryError: If cache operation fails
        """
        start_time = time.time()
        
        with self.tracer.start_span("redis_set") as span:
            try:
                # Set in cache
                if ttl:
                    success = await self.redis_client.setex(key, ttl, value)
                else:
                    success = await self.redis_client.set(key, value)

                # Record metrics
                duration = time.time() - start_time
                self.query_counter.labels(
                    database="redis",
                    operation="set",
                    status="success" if success else "error"
                ).inc()
                self.query_latency.labels(
                    database="redis",
                    operation="set"
                ).observe(duration)

                span.set_status(Status(StatusCode.OK if success else StatusCode.ERROR))
                return bool(success)

            except Exception as e:
                duration = time.time() - start_time
                self.query_counter.labels(
                    database="redis",
                    operation="set",
                    status="error"
                ).inc()
                self.query_latency.labels(
                    database="redis",
                    operation="set"
                ).observe(duration)

                span.set_status(Status(StatusCode.ERROR))
                logging.error("Redis set failed: %s", str(e))
                raise QueryError("Cache set failed") from e

    async def health_check(self) -> Dict[str, Any]:
        """Check health of all database connections.
        
        Returns:
            Health status for each database
        """
        results = {}
        
        # Check Elasticsearch
        try:
            await self.es_client.info()
            results["elasticsearch"] = {
                "status": "healthy",
                "message": "Connection OK"
            }
        except Exception as e:
            results["elasticsearch"] = {
                "status": "unhealthy",
                "message": str(e)
            }

        # Check MongoDB
        try:
            await self.mongo_client.admin.command('ping')
            results["mongodb"] = {
                "status": "healthy",
                "message": "Connection OK"
            }
        except Exception as e:
            results["mongodb"] = {
                "status": "unhealthy",
                "message": str(e)
            }

        # Check Redis
        try:
            await self.redis_client.ping()
            results["redis"] = {
                "status": "healthy",
                "message": "Connection OK"
            }
        except Exception as e:
            results["redis"] = {
                "status": "unhealthy",
                "message": str(e)
            }

        # Update connection gauges
        for db, status in results.items():
            self.connection_gauge.labels(database=db).set(
                1 if status["status"] == "healthy" else 0
            )

 