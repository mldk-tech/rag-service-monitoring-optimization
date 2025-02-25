"""Weaviate database client implementation."""

import asyncio
import uuid
from typing import Dict, Any, Optional, List
import structlog
import weaviate
from weaviate.exceptions import (
    WeaviateBaseError,
    WeaviateConnectionError,
    WeaviateQueryError,
    WeaviateTimeoutError
)
from opentelemetry import trace
import datetime
import json

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

class WeaviateClient(DatabaseClient):
    """Client for Weaviate vector database operations."""
    
    def __init__(self, config: Dict[str, Any], metrics_manager):
        """Initialize the Weaviate client with configuration."""
        self.config = config
        self.metrics = metrics_manager
        self.client = None
        self.connection_state = ConnectionState.DISCONNECTED
        self.class_name = config.get("class_name", "Document")
        self.batch_size = config.get("batch_size", 100)
        self.batch_timeout_retries = config.get("batch_timeout_retries", 3)
        self.batch_num_workers = config.get("batch_num_workers", 1)
        
    async def connect(self):
        """Connect to Weaviate."""
        if self.connection_state == ConnectionState.CONNECTED:
            return
            
        self.connection_state = ConnectionState.CONNECTING
        logger.info(
            "connecting_to_weaviate", 
            url=self.config.get("url", "http://localhost:8080"),
            class_name=self.class_name
        )
        
        try:
            # Create Weaviate client
            auth_config = None
            if self.config.get("api_key"):
                auth_config = weaviate.auth.AuthApiKey(api_key=self.config.get("api_key"))
                
            self.client = weaviate.Client(
                url=self.config.get("url", "http://localhost:8080"),
                auth_client_secret=auth_config,
                timeout_config=(
                    self.config.get("timeout", 10)
                )
            )
            
            # Check connection
            if not self.client.is_ready():
                raise ConnectionError("Weaviate server is not ready")
                
            # Create schema if it doesn't exist
            await self._ensure_schema()
            
            self.connection_state = ConnectionState.CONNECTED
            logger.info("weaviate_connected")
        except WeaviateConnectionError as e:
            self.connection_state = ConnectionState.FAILED
            logger.error(
                "weaviate_connection_failed",
                error=str(e),
                error_type=type(e).__name__
            )
            raise ConnectionError(f"Failed to connect to Weaviate: {str(e)}")
        except Exception as e:
            self.connection_state = ConnectionState.FAILED
            logger.error(
                "weaviate_initialization_failed",
                error=str(e),
                error_type=type(e).__name__
            )
            raise DatabaseError(f"Failed to initialize Weaviate: {str(e)}")
            
    async def _ensure_schema(self):
        """Ensure the Weaviate schema exists."""
        try:
            # Check if class exists
            schema = self.client.schema.get()
            classes = [c["class"] for c in schema["classes"]] if "classes" in schema else []
            
            if self.class_name not in classes:
                # Define class properties
                properties = [
                    {
                        "name": "content",
                        "dataType": ["text"],
                        "description": "The content of the document",
                        "indexSearchable": True,
                        "tokenization": "word"
                    },
                    {
                        "name": "metadata",
                        "dataType": ["object"],
                        "description": "Metadata associated with the document",
                        "indexSearchable": True
                    },
                    {
                        "name": "created_at",
                        "dataType": ["date"],
                        "description": "When the document was created",
                        "indexSearchable": True
                    },
                    {
                        "name": "updated_at",
                        "dataType": ["date"],
                        "description": "When the document was last updated",
                        "indexSearchable": True
                    },
                    {
                        "name": "document_id",
                        "dataType": ["string"],
                        "description": "The unique identifier of the document",
                        "indexSearchable": True,
                        "tokenization": "field"
                    }
                ]
                
                # Create class
                class_obj = {
                    "class": self.class_name,
                    "description": "RAG document class",
                    "vectorizer": self.config.get("vectorizer", "text2vec-transformers"),
                    "properties": properties
                }
                
                self.client.schema.create_class(class_obj)
                logger.info("weaviate_schema_created", class_name=self.class_name)
        except Exception as e:
            logger.error(
                "weaviate_schema_creation_failed",
                error=str(e),
                error_type=type(e).__name__
            )
            raise DatabaseError(f"Failed to create Weaviate schema: {str(e)}")
            
    async def disconnect(self):
        """Disconnect from Weaviate."""
        if self.client is not None:
            try:
                # Weaviate client doesn't have a close method, but we'll set it to None
                self.client = None
                self.connection_state = ConnectionState.DISCONNECTED
                logger.info("weaviate_disconnected")
            except Exception as e:
                logger.error(
                    "weaviate_disconnect_failed",
                    error=str(e),
                    error_type=type(e).__name__
                )
                
    async def get_status(self) -> ConnectionState:
        """Get the current connection status."""
        if self.connection_state != ConnectionState.CONNECTED:
            return self.connection_state
            
        try:
            # Check if connection is still alive
            if not self.client.is_ready():
                self.connection_state = ConnectionState.FAILED
                return ConnectionState.FAILED
            return ConnectionState.CONNECTED
        except Exception:
            self.connection_state = ConnectionState.FAILED
            return ConnectionState.FAILED
            
    async def health_check(self) -> bool:
        """Check if Weaviate is healthy."""
        try:
            return self.client.is_ready()
        except Exception as e:
            logger.error(
                "weaviate_health_check_failed",
                error=str(e),
                error_type=type(e).__name__
            )
            return False
            
    async def store_document(self, document: Document) -> str:
        """Store a document in Weaviate."""
        if self.connection_state != ConnectionState.CONNECTED:
            await self.connect()
            
        try:
            # Convert document to Weaviate object
            weaviate_object = {
                "content": document.content,
                "metadata": document.metadata,
                "created_at": document.created_at.isoformat() if document.created_at else datetime.datetime.now().isoformat(),
                "updated_at": document.updated_at.isoformat() if document.updated_at else datetime.datetime.now().isoformat(),
                "document_id": document.id
            }
            
            # Generate UUID based on document ID for consistency
            uuid_obj = uuid.uuid5(uuid.NAMESPACE_DNS, document.id)
            
            # Store the object
            result = self.client.data_object.create(
                weaviate_object,
                self.class_name,
                uuid_obj
            )
            
            logger.info(
                "weaviate_document_stored",
                document_id=document.id,
                weaviate_id=str(uuid_obj)
            )
            return document.id
        except WeaviateTimeoutError as e:
            logger.error(
                "weaviate_store_document_timeout",
                document_id=document.id,
                error=str(e),
                error_type=type(e).__name__
            )
            raise QueryTimeout(f"Timeout storing document in Weaviate: {str(e)}")
        except Exception as e:
            logger.error(
                "weaviate_store_document_failed",
                document_id=document.id,
                error=str(e),
                error_type=type(e).__name__
            )
            raise QueryError(f"Error storing document in Weaviate: {str(e)}")
            
    async def get_document(self, document_id: str) -> Optional[Document]:
        """Get a document from Weaviate by ID."""
        if self.connection_state != ConnectionState.CONNECTED:
            await self.connect()
            
        try:
            # Query by document_id property
            query = {
                "operator": "Equal",
                "path": ["document_id"],
                "valueString": document_id
            }
            
            result = self.client.query.get(
                self.class_name, 
                ["content", "metadata", "created_at", "updated_at", "document_id"]
            ).with_where(query).do()
            
            # Check if we got results
            if not result or "data" not in result or "Get" not in result["data"] or not result["data"]["Get"][self.class_name]:
                logger.info(
                    "weaviate_document_not_found",
                    document_id=document_id
                )
                return None
                
            # Extract the document data
            doc_data = result["data"]["Get"][self.class_name][0]
            
            # Parse metadata if it's a string
            metadata = doc_data.get("metadata", {})
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
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
                "weaviate_document_retrieved",
                document_id=document_id
            )
            return document
        except WeaviateTimeoutError as e:
            logger.error(
                "weaviate_get_document_timeout",
                document_id=document_id,
                error=str(e),
                error_type=type(e).__name__
            )
            raise QueryTimeout(f"Timeout retrieving document from Weaviate: {str(e)}")
        except Exception as e:
            logger.error(
                "weaviate_get_document_failed",
                document_id=document_id,
                error=str(e),
                error_type=type(e).__name__
            )
            raise QueryError(f"Error retrieving document from Weaviate: {str(e)}")
            
    async def search_documents(
        self, 
        query: str, 
        limit: int = 10,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[Document]:
        """Search for documents in Weaviate."""
        if self.connection_state != ConnectionState.CONNECTED:
            await self.connect()
            
        try:
            # Build the query
            weaviate_query = self.client.query.get(
                self.class_name, 
                ["content", "metadata", "created_at", "updated_at", "document_id", "_additional {certainty}"]
            )
            
            # Add vector search
            weaviate_query = weaviate_query.with_near_text({
                "concepts": [query]
            })
            
            # Add filters if provided
            if filters:
                filter_query = self._build_filter_query(filters)
                if filter_query:
                    weaviate_query = weaviate_query.with_where(filter_query)
                    
            # Add limit
            weaviate_query = weaviate_query.with_limit(limit)
            
            # Execute query
            result = weaviate_query.do()
            
            # Process results
            documents = []
            if result and "data" in result and "Get" in result["data"] and result["data"]["Get"][self.class_name]:
                for item in result["data"]["Get"][self.class_name]:
                    # Parse metadata if it's a string
                    metadata = item.get("metadata", {})
                    if isinstance(metadata, str):
                        try:
                            metadata = json.loads(metadata)
                        except json.JSONDecodeError:
                            metadata = {}
                            
                    # Add certainty score to metadata
                    if "_additional" in item and "certainty" in item["_additional"]:
                        if not metadata:
                            metadata = {}
                        metadata["score"] = item["_additional"]["certainty"]
                        
                    # Create Document object
                    document = Document(
                        id=item.get("document_id", str(uuid.uuid4())),
                        content=item.get("content", ""),
                        metadata=metadata,
                        created_at=datetime.datetime.fromisoformat(item.get("created_at")) if item.get("created_at") else None,
                        updated_at=datetime.datetime.fromisoformat(item.get("updated_at")) if item.get("updated_at") else None
                    )
                    documents.append(document)
                    
            logger.info(
                "weaviate_search_completed",
                query=query,
                result_count=len(documents)
            )
            return documents
        except WeaviateTimeoutError as e:
            logger.error(
                "weaviate_search_timeout",
                query=query,
                error=str(e),
                error_type=type(e).__name__
            )
            raise QueryTimeout(f"Timeout searching documents in Weaviate: {str(e)}")
        except Exception as e:
            logger.error(
                "weaviate_search_failed",
                query=query,
                error=str(e),
                error_type=type(e).__name__
            )
            raise QueryError(f"Error searching documents in Weaviate: {str(e)}")
            
    def _build_filter_query(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        """Build a Weaviate filter query from filters dict."""
        if not filters:
            return {}
            
        # For simple cases, we'll just use the first filter
        if len(filters) == 1:
            key, value = next(iter(filters.items()))
            
            # Handle list values
            if isinstance(value, list):
                if len(value) == 1:
                    return {
                        "path": [f"metadata.{key}"],
                        "operator": "Equal",
                        "valueString": str(value[0])
                    }
                else:
                    # For multiple values, use OR operator
                    operands = []
                    for v in value:
                        operands.append({
                            "path": [f"metadata.{key}"],
                            "operator": "Equal",
                            "valueString": str(v)
                        })
                    return {
                        "operator": "Or",
                        "operands": operands
                    }
            else:
                return {
                    "path": [f"metadata.{key}"],
                    "operator": "Equal",
                    "valueString": str(value)
                }
                
        # For multiple filters, use AND operator
        operands = []
        for key, value in filters.items():
            if isinstance(value, list):
                if len(value) == 1:
                    operands.append({
                        "path": [f"metadata.{key}"],
                        "operator": "Equal",
                        "valueString": str(value[0])
                    })
                else:
                    # For multiple values, use OR operator
                    or_operands = []
                    for v in value:
                        or_operands.append({
                            "path": [f"metadata.{key}"],
                            "operator": "Equal",
                            "valueString": str(v)
                        })
                    operands.append({
                        "operator": "Or",
                        "operands": or_operands
                    })
            else:
                operands.append({
                    "path": [f"metadata.{key}"],
                    "operator": "Equal",
                    "valueString": str(value)
                })
                
        return {
            "operator": "And",
            "operands": operands
        }
            
    async def delete_document(self, document_id: str) -> bool:
        """Delete a document from Weaviate by ID."""
        if self.connection_state != ConnectionState.CONNECTED:
            await self.connect()
            
        try:
            # First, find the Weaviate UUID by document_id
            query = {
                "operator": "Equal",
                "path": ["document_id"],
                "valueString": document_id
            }
            
            result = self.client.query.get(
                self.class_name, 
                ["_additional {id}"]
            ).with_where(query).do()
            
            # Check if we got results
            if not result or "data" not in result or "Get" not in result["data"] or not result["data"]["Get"][self.class_name]:
                logger.info(
                    "weaviate_document_not_found_for_deletion",
                    document_id=document_id
                )
                return False
                
            # Extract the Weaviate UUID
            weaviate_id = result["data"]["Get"][self.class_name][0]["_additional"]["id"]
            
            # Delete the object
            self.client.data_object.delete(
                weaviate_id,
                self.class_name
            )
            
            logger.info(
                "weaviate_document_deleted",
                document_id=document_id,
                weaviate_id=weaviate_id
            )
            return True
        except WeaviateTimeoutError as e:
            logger.error(
                "weaviate_delete_document_timeout",
                document_id=document_id,
                error=str(e),
                error_type=type(e).__name__
            )
            raise QueryTimeout(f"Timeout deleting document from Weaviate: {str(e)}")
        except Exception as e:
            logger.error(
                "weaviate_delete_document_failed",
                document_id=document_id,
                error=str(e),
                error_type=type(e).__name__
            )
            raise QueryError(f"Error deleting document from Weaviate: {str(e)}")
            
    async def update_document(self, document: Document) -> bool:
        """Update a document in Weaviate."""
        if self.connection_state != ConnectionState.CONNECTED:
            await self.connect()
            
        try:
            # First, find the Weaviate UUID by document_id
            query = {
                "operator": "Equal",
                "path": ["document_id"],
                "valueString": document.id
            }
            
            result = self.client.query.get(
                self.class_name, 
                ["_additional {id}"]
            ).with_where(query).do()
            
            # Check if we got results
            if not result or "data" not in result or "Get" not in result["data"] or not result["data"]["Get"][self.class_name]:
                logger.info(
                    "weaviate_document_not_found_for_update",
                    document_id=document.id
                )
                return False
                
            # Extract the Weaviate UUID
            weaviate_id = result["data"]["Get"][self.class_name][0]["_additional"]["id"]
            
            # Convert document to Weaviate object
            weaviate_object = {
                "content": document.content,
                "metadata": document.metadata,
                "updated_at": document.updated_at.isoformat() if document.updated_at else datetime.datetime.now().isoformat(),
                "document_id": document.id
            }
            
            # Update the object
            self.client.data_object.update(
                weaviate_object,
                self.class_name,
                weaviate_id
            )
            
            logger.info(
                "weaviate_document_updated",
                document_id=document.id,
                weaviate_id=weaviate_id
            )
            return True
        except WeaviateTimeoutError as e:
            logger.error(
                "weaviate_update_document_timeout",
                document_id=document.id,
                error=str(e),
                error_type=type(e).__name__
            )
            raise QueryTimeout(f"Timeout updating document in Weaviate: {str(e)}")
        except Exception as e:
            logger.error(
                "weaviate_update_document_failed",
                document_id=document.id,
                error=str(e),
                error_type=type(e).__name__
            )
            raise QueryError(f"Error updating document in Weaviate: {str(e)}") 