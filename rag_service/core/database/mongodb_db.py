"""MongoDB database client implementation."""

import asyncio
from typing import Dict, Any, Optional, List
import structlog
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import (
    ConnectionFailure,
    OperationFailure,
    ServerSelectionTimeoutError,
    NetworkTimeout
)
from opentelemetry import trace
import datetime

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

class MongoDBClient(DatabaseClient):
    """Client for MongoDB database operations."""
    
    def __init__(self, config: Dict[str, Any], metrics_manager):
        """Initialize the MongoDB client with configuration."""
        self.config = config
        self.metrics = metrics_manager
        self.client = None
        self.db = None
        self.collection = None
        self.connection_state = ConnectionState.DISCONNECTED
        self.database_name = config.get("database_name", "rag_service")
        self.collection_name = config.get("collection_name", "documents")
        
    async def connect(self):
        """Connect to MongoDB."""
        if self.connection_state == ConnectionState.CONNECTED:
            return
            
        self.connection_state = ConnectionState.CONNECTING
        logger.info(
            "connecting_to_mongodb", 
            uri=self.config.get("uri", "mongodb://localhost:27017"),
            database=self.database_name,
            collection=self.collection_name
        )
        
        try:
            # Create MongoDB client
            self.client = AsyncIOMotorClient(
                self.config.get("uri", "mongodb://localhost:27017"),
                serverSelectionTimeoutMS=self.config.get("server_selection_timeout_ms", 5000),
                connectTimeoutMS=self.config.get("connect_timeout_ms", 10000),
                socketTimeoutMS=self.config.get("socket_timeout_ms", 30000),
                maxPoolSize=self.config.get("max_pool_size", 100),
                minPoolSize=self.config.get("min_pool_size", 10)
            )
            
            # Check connection
            await self.client.admin.command("ping")
            
            # Get database and collection
            self.db = self.client[self.database_name]
            self.collection = self.db[self.collection_name]
            
            # Create indexes if needed
            await self._create_indexes()
            
            self.connection_state = ConnectionState.CONNECTED
            logger.info("mongodb_connected")
        except (ConnectionFailure, ServerSelectionTimeoutError) as e:
            self.connection_state = ConnectionState.FAILED
            logger.error(
                "mongodb_connection_failed",
                error=str(e),
                error_type=type(e).__name__
            )
            raise ConnectionError(f"Failed to connect to MongoDB: {str(e)}")
        except Exception as e:
            self.connection_state = ConnectionState.FAILED
            logger.error(
                "mongodb_initialization_failed",
                error=str(e),
                error_type=type(e).__name__
            )
            raise DatabaseError(f"Failed to initialize MongoDB: {str(e)}")
            
    async def _create_indexes(self):
        """Create necessary indexes for the MongoDB collection."""
        try:
            # Create text index on content field
            await self.collection.create_index([("content", "text")])
            
            # Create index on id field
            await self.collection.create_index("id", unique=True)
            
            # Create index on created_at field
            await self.collection.create_index("created_at")
            
            # Create index on common metadata fields
            await self.collection.create_index([("metadata.source", 1)])
            await self.collection.create_index([("metadata.type", 1)])
            
            logger.info("mongodb_indexes_created")
        except Exception as e:
            logger.error(
                "mongodb_index_creation_failed",
                error=str(e),
                error_type=type(e).__name__
            )
            raise DatabaseError(f"Failed to create MongoDB indexes: {str(e)}")
            
    async def disconnect(self):
        """Disconnect from MongoDB."""
        if self.client is not None:
            try:
                self.client.close()
                self.connection_state = ConnectionState.DISCONNECTED
                logger.info("mongodb_disconnected")
            except Exception as e:
                logger.error(
                    "mongodb_disconnect_failed",
                    error=str(e),
                    error_type=type(e).__name__
                )
                
    async def get_status(self) -> ConnectionState:
        """Get the current connection status."""
        if self.connection_state != ConnectionState.CONNECTED:
            return self.connection_state
            
        try:
            # Ping to check if connection is still alive
            await self.client.admin.command("ping")
            return ConnectionState.CONNECTED
        except Exception:
            self.connection_state = ConnectionState.FAILED
            return ConnectionState.FAILED
            
    async def health_check(self) -> bool:
        """Check if MongoDB is healthy."""
        try:
            await self.client.admin.command("ping")
            return True
        except Exception as e:
            logger.error(
                "mongodb_health_check_failed",
                error=str(e),
                error_type=type(e).__name__
            )
            return False
            
    async def store_document(self, document: Document) -> str:
        """Store a document in MongoDB."""
        if self.connection_state != ConnectionState.CONNECTED:
            await self.connect()
            
        doc_data = document.dict()
        
        try:
            # Use upsert to handle both insert and update
            result = await self.collection.replace_one(
                {"id": document.id},
                doc_data,
                upsert=True
            )
            
            logger.info(
                "mongodb_document_stored",
                document_id=document.id,
                upserted=result.upserted_id is not None
            )
            return document.id
        except NetworkTimeout as e:
            logger.error(
                "mongodb_store_document_timeout",
                document_id=document.id,
                error=str(e),
                error_type=type(e).__name__
            )
            raise QueryTimeout(f"Timeout storing document in MongoDB: {str(e)}")
        except Exception as e:
            logger.error(
                "mongodb_store_document_failed",
                document_id=document.id,
                error=str(e),
                error_type=type(e).__name__
            )
            raise QueryError(f"Error storing document in MongoDB: {str(e)}")
            
    async def get_document(self, document_id: str) -> Optional[Document]:
        """Get a document from MongoDB by ID."""
        if self.connection_state != ConnectionState.CONNECTED:
            await self.connect()
            
        try:
            doc_data = await self.collection.find_one({"id": document_id})
            
            if doc_data is None:
                logger.info(
                    "mongodb_document_not_found",
                    document_id=document_id
                )
                return None
                
            # Remove MongoDB's _id field
            if "_id" in doc_data:
                doc_data.pop("_id")
                
            logger.info(
                "mongodb_document_retrieved",
                document_id=document_id
            )
            return Document(**doc_data)
        except NetworkTimeout as e:
            logger.error(
                "mongodb_get_document_timeout",
                document_id=document_id,
                error=str(e),
                error_type=type(e).__name__
            )
            raise QueryTimeout(f"Timeout retrieving document from MongoDB: {str(e)}")
        except Exception as e:
            logger.error(
                "mongodb_get_document_failed",
                document_id=document_id,
                error=str(e),
                error_type=type(e).__name__
            )
            raise QueryError(f"Error retrieving document from MongoDB: {str(e)}")
            
    async def search_documents(
        self, 
        query: str, 
        limit: int = 10,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[Document]:
        """Search for documents in MongoDB."""
        if self.connection_state != ConnectionState.CONNECTED:
            await self.connect()
            
        # Build the search query
        search_query = {
            "$text": {
                "$search": query
            }
        }
        
        # Add filters if provided
        if filters:
            for field, value in filters.items():
                if isinstance(value, list):
                    search_query[f"metadata.{field}"] = {"$in": value}
                else:
                    search_query[f"metadata.{field}"] = value
                    
        try:
            # Execute the search with text score sorting
            cursor = self.collection.find(
                search_query,
                {"score": {"$meta": "textScore"}}
            ).sort([("score", {"$meta": "textScore"})]).limit(limit)
            
            documents = []
            async for doc in cursor:
                # Remove MongoDB's _id field
                if "_id" in doc:
                    doc.pop("_id")
                    
                # Add score to metadata
                if "metadata" not in doc:
                    doc["metadata"] = {}
                doc["metadata"]["score"] = doc.get("score", 0)
                
                documents.append(Document(**doc))
                
            logger.info(
                "mongodb_search_completed",
                query=query,
                result_count=len(documents)
            )
            return documents
        except NetworkTimeout as e:
            logger.error(
                "mongodb_search_timeout",
                query=query,
                error=str(e),
                error_type=type(e).__name__
            )
            raise QueryTimeout(f"Timeout searching documents in MongoDB: {str(e)}")
        except Exception as e:
            logger.error(
                "mongodb_search_failed",
                query=query,
                error=str(e),
                error_type=type(e).__name__
            )
            raise QueryError(f"Error searching documents in MongoDB: {str(e)}")
            
    async def delete_document(self, document_id: str) -> bool:
        """Delete a document from MongoDB by ID."""
        if self.connection_state != ConnectionState.CONNECTED:
            await self.connect()
            
        try:
            result = await self.collection.delete_one({"id": document_id})
            
            success = result.deleted_count > 0
            logger.info(
                "mongodb_document_deleted" if success else "mongodb_document_not_found_for_deletion",
                document_id=document_id,
                deleted_count=result.deleted_count
            )
            return success
        except NetworkTimeout as e:
            logger.error(
                "mongodb_delete_document_timeout",
                document_id=document_id,
                error=str(e),
                error_type=type(e).__name__
            )
            raise QueryTimeout(f"Timeout deleting document from MongoDB: {str(e)}")
        except Exception as e:
            logger.error(
                "mongodb_delete_document_failed",
                document_id=document_id,
                error=str(e),
                error_type=type(e).__name__
            )
            raise QueryError(f"Error deleting document from MongoDB: {str(e)}")
            
    async def update_document(self, document: Document) -> bool:
        """Update a document in MongoDB."""
        if self.connection_state != ConnectionState.CONNECTED:
            await self.connect()
            
        doc_data = document.dict()
        
        try:
            result = await self.collection.update_one(
                {"id": document.id},
                {"$set": doc_data}
            )
            
            success = result.matched_count > 0
            logger.info(
                "mongodb_document_updated" if success else "mongodb_document_not_found_for_update",
                document_id=document.id,
                matched_count=result.matched_count,
                modified_count=result.modified_count
            )
            return success
        except NetworkTimeout as e:
            logger.error(
                "mongodb_update_document_timeout",
                document_id=document.id,
                error=str(e),
                error_type=type(e).__name__
            )
            raise QueryTimeout(f"Timeout updating document in MongoDB: {str(e)}")
        except Exception as e:
            logger.error(
                "mongodb_update_document_failed",
                document_id=document.id,
                error=str(e),
                error_type=type(e).__name__
            )
            raise QueryError(f"Error updating document in MongoDB: {str(e)}") 