"""Milvus database client implementation."""

import asyncio
import json
from typing import Dict, Any, Optional, List
import structlog
import numpy as np
from pymilvus import (
    connections,
    Collection,
    FieldSchema,
    CollectionSchema,
    DataType,
    utility
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

class MilvusClient(DatabaseClient):
    """Client for Milvus vector database operations."""
    
    def __init__(self, config: Dict[str, Any], metrics_manager):
        """Initialize the Milvus client with configuration."""
        self.config = config
        self.metrics = metrics_manager
        self.connection_alias = config.get("connection_alias", "default")
        self.collection = None
        self.connection_state = ConnectionState.DISCONNECTED
        self.collection_name = config.get("collection_name", "documents")
        self.embedding_dim = config.get("embedding_dim", 768)
        self.index_params = config.get("index_params", {
            "index_type": "IVF_FLAT",
            "metric_type": "L2",
            "params": {"nlist": 1024}
        })
        self.search_params = config.get("search_params", {
            "metric_type": "L2",
            "params": {"nprobe": 10}
        })
        
    async def connect(self):
        """Connect to Milvus."""
        if self.connection_state == ConnectionState.CONNECTED:
            return
            
        self.connection_state = ConnectionState.CONNECTING
        logger.info(
            "connecting_to_milvus", 
            host=self.config.get("host", "localhost"),
            port=self.config.get("port", 19530),
            collection=self.collection_name
        )
        
        try:
            # Connect to Milvus
            connections.connect(
                alias=self.connection_alias,
                host=self.config.get("host", "localhost"),
                port=self.config.get("port", 19530),
                user=self.config.get("user", ""),
                password=self.config.get("password", ""),
                secure=self.config.get("secure", False)
            )
            
            # Check if collection exists, create if not
            if not utility.has_collection(self.collection_name):
                await self._create_collection()
            
            # Get collection
            self.collection = Collection(self.collection_name)
            
            # Load collection into memory
            self.collection.load()
            
            self.connection_state = ConnectionState.CONNECTED
            logger.info("milvus_connected")
        except Exception as e:
            self.connection_state = ConnectionState.FAILED
            logger.error(
                "milvus_connection_failed",
                error=str(e),
                error_type=type(e).__name__
            )
            raise ConnectionError(f"Failed to connect to Milvus: {str(e)}")
            
    async def _create_collection(self):
        """Create Milvus collection with appropriate schema."""
        try:
            # Define fields
            fields = [
                FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=100),
                FieldSchema(name="document_id", dtype=DataType.VARCHAR, max_length=100),
                FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=65535),
                FieldSchema(name="metadata", dtype=DataType.JSON),
                FieldSchema(name="created_at", dtype=DataType.VARCHAR, max_length=30),
                FieldSchema(name="updated_at", dtype=DataType.VARCHAR, max_length=30),
                FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=self.embedding_dim)
            ]
            
            # Create schema
            schema = CollectionSchema(fields)
            
            # Create collection
            Collection(name=self.collection_name, schema=schema)
            
            # Create index on embedding field
            collection = Collection(self.collection_name)
            collection.create_index(
                field_name="embedding",
                index_params=self.index_params
            )
            
            logger.info("milvus_collection_created", collection=self.collection_name)
        except Exception as e:
            logger.error(
                "milvus_collection_creation_failed",
                error=str(e),
                error_type=type(e).__name__
            )
            raise DatabaseError(f"Failed to create Milvus collection: {str(e)}")
            
    async def disconnect(self):
        """Disconnect from Milvus."""
        try:
            if self.collection:
                self.collection.release()
                
            connections.disconnect(self.connection_alias)
            self.connection_state = ConnectionState.DISCONNECTED
            logger.info("milvus_disconnected")
        except Exception as e:
            logger.error(
                "milvus_disconnect_failed",
                error=str(e),
                error_type=type(e).__name__
            )
                
    async def get_status(self) -> ConnectionState:
        """Get the current connection status."""
        if self.connection_state != ConnectionState.CONNECTED:
            return self.connection_state
            
        try:
            # Check if connection is still alive
            if not connections.has_connection(self.connection_alias):
                self.connection_state = ConnectionState.FAILED
                return ConnectionState.FAILED
            return ConnectionState.CONNECTED
        except Exception:
            self.connection_state = ConnectionState.FAILED
            return ConnectionState.FAILED
            
    async def health_check(self) -> bool:
        """Check if Milvus is healthy."""
        try:
            return connections.has_connection(self.connection_alias)
        except Exception as e:
            logger.error(
                "milvus_health_check_failed",
                error=str(e),
                error_type=type(e).__name__
            )
            return False
            
    def _get_embedding(self, document: Document) -> List[float]:
        """Get embedding from document or generate a random one if not present."""
        if "embedding" in document.metadata and isinstance(document.metadata["embedding"], list):
            return document.metadata["embedding"]
            
        # For testing purposes, generate a random embedding
        # In a real implementation, this would use a text embedding model
        return list(np.random.rand(self.embedding_dim).astype(np.float32))
            
    async def store_document(self, document: Document) -> str:
        """Store a document in Milvus."""
        if self.connection_state != ConnectionState.CONNECTED:
            await self.connect()
            
        try:
            # Get embedding
            embedding = self._get_embedding(document)
            
            # Prepare data
            data = [
                [document.id],  # id
                [document.id],  # document_id (same as id for simplicity)
                [document.content],  # content
                [json.dumps(document.metadata)],  # metadata
                [document.created_at.isoformat() if document.created_at else datetime.datetime.now().isoformat()],  # created_at
                [document.updated_at.isoformat() if document.updated_at else datetime.datetime.now().isoformat()],  # updated_at
                [embedding]  # embedding
            ]
            
            # Insert data
            self.collection.insert(data)
            
            logger.info(
                "milvus_document_stored",
                document_id=document.id,
                collection=self.collection_name
            )
            return document.id
        except Exception as e:
            logger.error(
                "milvus_store_document_failed",
                document_id=document.id,
                error=str(e),
                error_type=type(e).__name__
            )
            raise QueryError(f"Error storing document in Milvus: {str(e)}")
            
    async def get_document(self, document_id: str) -> Optional[Document]:
        """Get a document from Milvus by ID."""
        if self.connection_state != ConnectionState.CONNECTED:
            await self.connect()
            
        try:
            # Query by document_id
            results = self.collection.query(
                expr=f'document_id == "{document_id}"',
                output_fields=["id", "document_id", "content", "metadata", "created_at", "updated_at"]
            )
            
            if not results:
                logger.info(
                    "milvus_document_not_found",
                    document_id=document_id
                )
                return None
                
            # Extract the document data
            doc_data = results[0]
            
            # Parse metadata
            metadata = {}
            if doc_data.get("metadata"):
                try:
                    metadata = json.loads(doc_data["metadata"])
                except json.JSONDecodeError:
                    metadata = {}
                    
            # Create Document object
            document = Document(
                id=doc_data.get("document_id", document_id),
                content=doc_data.get("content", ""),
                metadata=metadata,
                created_at=datetime.datetime.fromisoformat(doc_data.get("created_at")) if doc_data.get("created_at") else None,
                updated_at=datetime.datetime.fromisoformat(doc_data.get("updated_at")) if doc_data.get("updated_at") else None
            )
            
            logger.info(
                "milvus_document_retrieved",
                document_id=document_id
            )
            return document
        except Exception as e:
            logger.error(
                "milvus_get_document_failed",
                document_id=document_id,
                error=str(e),
                error_type=type(e).__name__
            )
            raise QueryError(f"Error retrieving document from Milvus: {str(e)}")
            
    async def search_documents(
        self, 
        query: str, 
        limit: int = 10,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[Document]:
        """Search for documents in Milvus."""
        if self.connection_state != ConnectionState.CONNECTED:
            await self.connect()
            
        try:
            # For a real implementation, we would generate an embedding from the query
            # Here we'll use a random vector for demonstration
            query_embedding = list(np.random.rand(self.embedding_dim).astype(np.float32))
            
            # Build filter expression if filters provided
            expr = None
            if filters:
                filter_expressions = []
                for key, value in filters.items():
                    if isinstance(value, list):
                        # For lists, we need to check if the value is in the list
                        # This is a simplification, as JSON filtering in Milvus is more complex
                        or_expressions = []
                        for v in value:
                            or_expressions.append(f'metadata LIKE "%"{key}": "{v}"%"')
                        filter_expressions.append("(" + " OR ".join(or_expressions) + ")")
                    else:
                        filter_expressions.append(f'metadata LIKE "%"{key}": "{value}"%"')
                
                if filter_expressions:
                    expr = " AND ".join(filter_expressions)
            
            # Search
            search_results = self.collection.search(
                data=[query_embedding],
                anns_field="embedding",
                param=self.search_params,
                limit=limit,
                expr=expr,
                output_fields=["id", "document_id", "content", "metadata", "created_at", "updated_at"]
            )
            
            # Process results
            documents = []
            for hits in search_results:
                for hit in hits:
                    # Extract data
                    entity = hit.entity
                    
                    # Parse metadata
                    metadata = {}
                    if entity.get("metadata"):
                        try:
                            metadata = json.loads(entity["metadata"])
                        except json.JSONDecodeError:
                            metadata = {}
                            
                    # Add score to metadata
                    if not metadata:
                        metadata = {}
                    metadata["score"] = hit.score
                    
                    # Create Document object
                    document = Document(
                        id=entity.get("document_id", entity.get("id", str(len(documents)))),
                        content=entity.get("content", ""),
                        metadata=metadata,
                        created_at=datetime.datetime.fromisoformat(entity.get("created_at")) if entity.get("created_at") else None,
                        updated_at=datetime.datetime.fromisoformat(entity.get("updated_at")) if entity.get("updated_at") else None
                    )
                    documents.append(document)
                    
            logger.info(
                "milvus_search_completed",
                query=query,
                result_count=len(documents)
            )
            return documents
        except Exception as e:
            logger.error(
                "milvus_search_failed",
                query=query,
                error=str(e),
                error_type=type(e).__name__
            )
            raise QueryError(f"Error searching documents in Milvus: {str(e)}")
            
    async def delete_document(self, document_id: str) -> bool:
        """Delete a document from Milvus by ID."""
        if self.connection_state != ConnectionState.CONNECTED:
            await self.connect()
            
        try:
            # Delete by document_id
            expr = f'document_id == "{document_id}"'
            self.collection.delete(expr)
            
            logger.info(
                "milvus_document_deleted",
                document_id=document_id
            )
            return True
        except Exception as e:
            logger.error(
                "milvus_delete_document_failed",
                document_id=document_id,
                error=str(e),
                error_type=type(e).__name__
            )
            raise QueryError(f"Error deleting document from Milvus: {str(e)}")
            
    async def update_document(self, document: Document) -> bool:
        """Update a document in Milvus."""
        if self.connection_state != ConnectionState.CONNECTED:
            await self.connect()
            
        try:
            # Milvus doesn't support direct updates, so we need to delete and re-insert
            # First, check if document exists
            results = self.collection.query(
                expr=f'document_id == "{document.id}"',
                output_fields=["id"]
            )
            
            if not results:
                logger.info(
                    "milvus_document_not_found_for_update",
                    document_id=document.id
                )
                return False
                
            # Delete existing document
            self.collection.delete(f'document_id == "{document.id}"')
            
            # Get embedding
            embedding = self._get_embedding(document)
            
            # Prepare data for re-insertion
            data = [
                [document.id],  # id
                [document.id],  # document_id (same as id for simplicity)
                [document.content],  # content
                [json.dumps(document.metadata)],  # metadata
                [document.created_at.isoformat() if document.created_at else datetime.datetime.now().isoformat()],  # created_at
                [document.updated_at.isoformat() if document.updated_at else datetime.datetime.now().isoformat()],  # updated_at
                [embedding]  # embedding
            ]
            
            # Insert data
            self.collection.insert(data)
            
            logger.info(
                "milvus_document_updated",
                document_id=document.id
            )
            return True
        except Exception as e:
            logger.error(
                "milvus_update_document_failed",
                document_id=document.id,
                error=str(e),
                error_type=type(e).__name__
            )
            raise QueryError(f"Error updating document in Milvus: {str(e)}") 