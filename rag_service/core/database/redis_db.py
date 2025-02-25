"""Redis database client implementation."""

import asyncio
import json
from typing import Dict, Any, Optional, List
import structlog
import redis.asyncio as redis
from redis.exceptions import (
    ConnectionError as RedisConnectionError,
    TimeoutError as RedisTimeoutError,
    RedisError
)
from opentelemetry import trace

from rag_service.core.database.base import (
    DatabaseClient,
    Document,
    ConnectionState,
    DatabaseError,
    ConnectionError,
    QueryError,
    QueryTimeout
)

# Get a logger
logger = structlog.get_logger(__name__)

# Get a tracer
tracer = trace.get_tracer(__name__)

class RedisClient(DatabaseClient):
    """Client for Redis database operations."""
    
    def __init__(self, config: Dict[str, Any], metrics_manager):
        """Initialize the Redis client with configuration."""
        self.config = config
        self.metrics = metrics_manager
        self.client = None
        self.connection_state = ConnectionState.DISCONNECTED
        self.prefix = config.get("key_prefix", "doc:")
        self.ttl = config.get("ttl", None)  # Default to no expiration
        
    async def connect(self):
        """Connect to Redis."""
        if self.connection_state == ConnectionState.CONNECTED:
            return
            
        self.connection_state = ConnectionState.CONNECTING
        logger.info(
            "connecting_to_redis", 
            host=self.config.get("host", "localhost"),
            port=self.config.get("port", 6379),
            db=self.config.get("db", 0)
        )
        
        try:
            # Create Redis client
            self.client = redis.Redis(
                host=self.config.get("host", "localhost"),
                port=self.config.get("port", 6379),
                db=self.config.get("db", 0),
                password=self.config.get("password", None),
                socket_timeout=self.config.get("socket_timeout", 5.0),
                socket_connect_timeout=self.config.get("socket_connect_timeout", 10.0),
                socket_keepalive=self.config.get("socket_keepalive", True),
                health_check_interval=self.config.get("health_check_interval", 30),
                retry_on_timeout=self.config.get("retry_on_timeout", True),
                max_connections=self.config.get("max_connections", 100),
                decode_responses=False  # We'll handle decoding ourselves
            )
            
            # Check connection
            await self.client.ping()
            
            self.connection_state = ConnectionState.CONNECTED
            logger.info("redis_connected")
        except RedisConnectionError as e:
            self.connection_state = ConnectionState.FAILED
            logger.error(
                "redis_connection_failed",
                error=str(e),
                error_type=type(e).__name__
            )
            raise ConnectionError(f"Failed to connect to Redis: {str(e)}")
        except Exception as e:
            self.connection_state = ConnectionState.FAILED
            logger.error(
                "redis_initialization_failed",
                error=str(e),
                error_type=type(e).__name__
            )
            raise DatabaseError(f"Failed to initialize Redis: {str(e)}")
            
    async def disconnect(self):
        """Disconnect from Redis."""
        if self.client is not None:
            try:
                await self.client.close()
                self.connection_state = ConnectionState.DISCONNECTED
                logger.info("redis_disconnected")
            except Exception as e:
                logger.error(
                    "redis_disconnect_failed",
                    error=str(e),
                    error_type=type(e).__name__
                )
                
    async def get_status(self) -> ConnectionState:
        """Get the current connection status."""
        if self.connection_state != ConnectionState.CONNECTED:
            return self.connection_state
            
        try:
            # Ping to check if connection is still alive
            await self.client.ping()
            return ConnectionState.CONNECTED
        except Exception:
            self.connection_state = ConnectionState.FAILED
            return ConnectionState.FAILED
            
    async def health_check(self) -> bool:
        """Check if Redis is healthy."""
        try:
            await self.client.ping()
            return True
        except Exception as e:
            logger.error(
                "redis_health_check_failed",
                error=str(e),
                error_type=type(e).__name__
            )
            return False
            
    def _get_key(self, document_id: str) -> str:
        """Get the Redis key for a document ID."""
        return f"{self.prefix}{document_id}"
            
    async def store_document(self, document: Document) -> str:
        """Store a document in Redis."""
        if self.connection_state != ConnectionState.CONNECTED:
            await self.connect()
            
        key = self._get_key(document.id)
        doc_data = document.json()
        
        try:
            if self.ttl:
                await self.client.setex(key, self.ttl, doc_data)
            else:
                await self.client.set(key, doc_data)
                
            # Store in search index if using RediSearch
            if self.config.get("use_redisearch", False):
                await self._index_document(document)
                
            logger.info(
                "redis_document_stored",
                document_id=document.id,
                key=key
            )
            return document.id
        except RedisTimeoutError as e:
            logger.error(
                "redis_store_document_timeout",
                document_id=document.id,
                error=str(e),
                error_type=type(e).__name__
            )
            raise QueryTimeout(f"Timeout storing document in Redis: {str(e)}")
        except Exception as e:
            logger.error(
                "redis_store_document_failed",
                document_id=document.id,
                error=str(e),
                error_type=type(e).__name__
            )
            raise QueryError(f"Error storing document in Redis: {str(e)}")
            
    async def _index_document(self, document: Document):
        """Index a document for search if RediSearch is enabled."""
        # This is a placeholder for RediSearch integration
        # Actual implementation would depend on RediSearch module availability
        pass
            
    async def get_document(self, document_id: str) -> Optional[Document]:
        """Get a document from Redis by ID."""
        if self.connection_state != ConnectionState.CONNECTED:
            await self.connect()
            
        key = self._get_key(document_id)
        
        try:
            doc_data = await self.client.get(key)
            
            if doc_data is None:
                logger.info(
                    "redis_document_not_found",
                    document_id=document_id,
                    key=key
                )
                return None
                
            # Parse the JSON data
            try:
                doc_json = json.loads(doc_data)
                logger.info(
                    "redis_document_retrieved",
                    document_id=document_id,
                    key=key
                )
                return Document(**doc_json)
            except json.JSONDecodeError as e:
                logger.error(
                    "redis_document_json_decode_error",
                    document_id=document_id,
                    error=str(e)
                )
                return None
        except RedisTimeoutError as e:
            logger.error(
                "redis_get_document_timeout",
                document_id=document_id,
                error=str(e),
                error_type=type(e).__name__
            )
            raise QueryTimeout(f"Timeout retrieving document from Redis: {str(e)}")
        except Exception as e:
            logger.error(
                "redis_get_document_failed",
                document_id=document_id,
                error=str(e),
                error_type=type(e).__name__
            )
            raise QueryError(f"Error retrieving document from Redis: {str(e)}")
            
    async def search_documents(
        self, 
        query: str, 
        limit: int = 10,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[Document]:
        """Search for documents in Redis."""
        if self.connection_state != ConnectionState.CONNECTED:
            await self.connect()
            
        # If RediSearch is not enabled, we can't perform a proper search
        if not self.config.get("use_redisearch", False):
            logger.warning(
                "redis_search_not_supported",
                message="RediSearch is not enabled, returning empty results"
            )
            return []
            
        # This is a placeholder for RediSearch integration
        # Actual implementation would depend on RediSearch module availability
        logger.info(
            "redis_search_completed",
            query=query,
            result_count=0
        )
        return []
            
    async def delete_document(self, document_id: str) -> bool:
        """Delete a document from Redis by ID."""
        if self.connection_state != ConnectionState.CONNECTED:
            await self.connect()
            
        key = self._get_key(document_id)
        
        try:
            result = await self.client.delete(key)
            
            success = result > 0
            logger.info(
                "redis_document_deleted" if success else "redis_document_not_found_for_deletion",
                document_id=document_id,
                key=key,
                deleted_count=result
            )
            return success
        except RedisTimeoutError as e:
            logger.error(
                "redis_delete_document_timeout",
                document_id=document_id,
                error=str(e),
                error_type=type(e).__name__
            )
            raise QueryTimeout(f"Timeout deleting document from Redis: {str(e)}")
        except Exception as e:
            logger.error(
                "redis_delete_document_failed",
                document_id=document_id,
                error=str(e),
                error_type=type(e).__name__
            )
            raise QueryError(f"Error deleting document from Redis: {str(e)}")
            
    async def update_document(self, document: Document) -> bool:
        """Update a document in Redis."""
        if self.connection_state != ConnectionState.CONNECTED:
            await self.connect()
            
        key = self._get_key(document.id)
        
        try:
            # Check if document exists
            exists = await self.client.exists(key)
            if not exists:
                logger.info(
                    "redis_document_not_found_for_update",
                    document_id=document.id,
                    key=key
                )
                return False
                
            # Update the document (same as store)
            doc_data = document.json()
            if self.ttl:
                await self.client.setex(key, self.ttl, doc_data)
            else:
                await self.client.set(key, doc_data)
                
            # Update in search index if using RediSearch
            if self.config.get("use_redisearch", False):
                await self._index_document(document)
                
            logger.info(
                "redis_document_updated",
                document_id=document.id,
                key=key
            )
            return True
        except RedisTimeoutError as e:
            logger.error(
                "redis_update_document_timeout",
                document_id=document.id,
                error=str(e),
                error_type=type(e).__name__
            )
            raise QueryTimeout(f"Timeout updating document in Redis: {str(e)}")
        except Exception as e:
            logger.error(
                "redis_update_document_failed",
                document_id=document.id,
                error=str(e),
                error_type=type(e).__name__
            )
            raise QueryError(f"Error updating document in Redis: {str(e)}") 