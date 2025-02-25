"""Elasticsearch database client implementation."""

import asyncio
from typing import Dict, Any, Optional, List
import structlog
from elasticsearch import AsyncElasticsearch
from elasticsearch.exceptions import (
    ConnectionError as ESConnectionError,
    RequestError,
    NotFoundError,
    TransportError
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

class ElasticsearchClient(DatabaseClient):
    """Client for Elasticsearch database operations."""
    
    def __init__(self, config: Dict[str, Any], metrics_manager):
        """Initialize the Elasticsearch client with configuration."""
        self.config = config
        self.metrics = metrics_manager
        self.client = None
        self.connection_state = ConnectionState.DISCONNECTED
        self.index_name = config.get("index_name", "documents")
        
    async def connect(self):
        """Connect to Elasticsearch."""
        if self.connection_state == ConnectionState.CONNECTED:
            return
            
        self.connection_state = ConnectionState.CONNECTING
        logger.info("connecting_to_elasticsearch", hosts=self.config.get("hosts"))
        
        try:
            # Create Elasticsearch client
            self.client = AsyncElasticsearch(
                hosts=self.config.get("hosts"),
                timeout=self.config.get("timeout", 30),
                retry_on_timeout=self.config.get("retry_on_timeout", True),
                max_retries=self.config.get("max_retries", 3)
            )
            
            # Check if index exists, create if not
            if not await self.client.indices.exists(index=self.index_name):
                await self._create_index()
                
            self.connection_state = ConnectionState.CONNECTED
            logger.info("elasticsearch_connected")
        except ESConnectionError as e:
            self.connection_state = ConnectionState.FAILED
            logger.error(
                "elasticsearch_connection_failed",
                error=str(e),
                error_type=type(e).__name__
            )
            raise ConnectionError(f"Failed to connect to Elasticsearch: {str(e)}")
        except Exception as e:
            self.connection_state = ConnectionState.FAILED
            logger.error(
                "elasticsearch_connection_failed",
                error=str(e),
                error_type=type(e).__name__
            )
            raise DatabaseError(f"Failed to initialize Elasticsearch: {str(e)}")
            
    async def _create_index(self):
        """Create the Elasticsearch index with appropriate mappings."""
        mappings = {
            "mappings": {
                "properties": {
                    "content": {
                        "type": "text",
                        "analyzer": "standard"
                    },
                    "metadata": {
                        "type": "object",
                        "dynamic": True
                    },
                    "embedding": {
                        "type": "dense_vector",
                        "dims": self.config.get("embedding_dimensions", 768)
                    },
                    "created_at": {
                        "type": "date"
                    },
                    "updated_at": {
                        "type": "date"
                    }
                }
            },
            "settings": {
                "number_of_shards": self.config.get("shards", 1),
                "number_of_replicas": self.config.get("replicas", 1)
            }
        }
        
        try:
            await self.client.indices.create(
                index=self.index_name,
                body=mappings
            )
            logger.info("elasticsearch_index_created", index=self.index_name)
        except Exception as e:
            logger.error(
                "elasticsearch_index_creation_failed",
                index=self.index_name,
                error=str(e),
                error_type=type(e).__name__
            )
            raise DatabaseError(f"Failed to create Elasticsearch index: {str(e)}")
            
    async def disconnect(self):
        """Disconnect from Elasticsearch."""
        if self.client is not None:
            try:
                await self.client.close()
                self.connection_state = ConnectionState.DISCONNECTED
                logger.info("elasticsearch_disconnected")
            except Exception as e:
                logger.error(
                    "elasticsearch_disconnect_failed",
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
        """Check if Elasticsearch is healthy."""
        try:
            response = await self.client.cluster.health()
            status = response.get("status")
            # Consider yellow and green as healthy
            return status in ["yellow", "green"]
        except Exception as e:
            logger.error(
                "elasticsearch_health_check_failed",
                error=str(e),
                error_type=type(e).__name__
            )
            return False
            
    async def store_document(self, document: Document) -> str:
        """Store a document in Elasticsearch."""
        if self.connection_state != ConnectionState.CONNECTED:
            await self.connect()
            
        doc_data = document.dict()
        
        try:
            response = await self.client.index(
                index=self.index_name,
                id=document.id,
                body=doc_data,
                refresh=self.config.get("refresh_on_write", True)
            )
            logger.info(
                "elasticsearch_document_stored",
                document_id=document.id,
                index=self.index_name
            )
            return response["_id"]
        except TransportError as e:
            logger.error(
                "elasticsearch_store_document_failed",
                document_id=document.id,
                error=str(e),
                error_type=type(e).__name__
            )
            if e.status_code == 408:
                raise QueryTimeout(f"Timeout storing document in Elasticsearch: {str(e)}")
            raise QueryError(f"Error storing document in Elasticsearch: {str(e)}")
            
    async def get_document(self, document_id: str) -> Optional[Document]:
        """Get a document from Elasticsearch by ID."""
        if self.connection_state != ConnectionState.CONNECTED:
            await self.connect()
            
        try:
            response = await self.client.get(
                index=self.index_name,
                id=document_id
            )
            source = response["_source"]
            logger.info(
                "elasticsearch_document_retrieved",
                document_id=document_id,
                index=self.index_name
            )
            return Document(**source)
        except NotFoundError:
            logger.info(
                "elasticsearch_document_not_found",
                document_id=document_id,
                index=self.index_name
            )
            return None
        except TransportError as e:
            logger.error(
                "elasticsearch_get_document_failed",
                document_id=document_id,
                error=str(e),
                error_type=type(e).__name__
            )
            if e.status_code == 408:
                raise QueryTimeout(f"Timeout retrieving document from Elasticsearch: {str(e)}")
            raise QueryError(f"Error retrieving document from Elasticsearch: {str(e)}")
            
    async def search_documents(
        self, 
        query: str, 
        limit: int = 10,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[Document]:
        """Search for documents in Elasticsearch."""
        if self.connection_state != ConnectionState.CONNECTED:
            await self.connect()
            
        # Build the search query
        search_query = {
            "query": {
                "bool": {
                    "must": [
                        {
                            "match": {
                                "content": {
                                    "query": query,
                                    "operator": "and"
                                }
                            }
                        }
                    ]
                }
            },
            "size": limit
        }
        
        # Add filters if provided
        if filters:
            for field, value in filters.items():
                if isinstance(value, list):
                    search_query["query"]["bool"]["filter"] = search_query["query"]["bool"].get("filter", [])
                    search_query["query"]["bool"]["filter"].append({
                        "terms": {
                            f"metadata.{field}": value
                        }
                    })
                else:
                    search_query["query"]["bool"]["filter"] = search_query["query"]["bool"].get("filter", [])
                    search_query["query"]["bool"]["filter"].append({
                        "term": {
                            f"metadata.{field}": value
                        }
                    })
                    
        try:
            response = await self.client.search(
                index=self.index_name,
                body=search_query
            )
            
            documents = []
            for hit in response["hits"]["hits"]:
                doc_data = hit["_source"]
                # Add score to metadata
                if "metadata" not in doc_data:
                    doc_data["metadata"] = {}
                doc_data["metadata"]["score"] = hit["_score"]
                documents.append(Document(**doc_data))
                
            logger.info(
                "elasticsearch_search_completed",
                query=query,
                result_count=len(documents),
                index=self.index_name
            )
            return documents
        except TransportError as e:
            logger.error(
                "elasticsearch_search_failed",
                query=query,
                error=str(e),
                error_type=type(e).__name__
            )
            if e.status_code == 408:
                raise QueryTimeout(f"Timeout searching documents in Elasticsearch: {str(e)}")
            raise QueryError(f"Error searching documents in Elasticsearch: {str(e)}")
            
    async def delete_document(self, document_id: str) -> bool:
        """Delete a document from Elasticsearch by ID."""
        if self.connection_state != ConnectionState.CONNECTED:
            await self.connect()
            
        try:
            response = await self.client.delete(
                index=self.index_name,
                id=document_id,
                refresh=self.config.get("refresh_on_write", True)
            )
            logger.info(
                "elasticsearch_document_deleted",
                document_id=document_id,
                index=self.index_name
            )
            return response["result"] == "deleted"
        except NotFoundError:
            logger.info(
                "elasticsearch_document_not_found_for_deletion",
                document_id=document_id,
                index=self.index_name
            )
            return False
        except TransportError as e:
            logger.error(
                "elasticsearch_delete_document_failed",
                document_id=document_id,
                error=str(e),
                error_type=type(e).__name__
            )
            if e.status_code == 408:
                raise QueryTimeout(f"Timeout deleting document from Elasticsearch: {str(e)}")
            raise QueryError(f"Error deleting document from Elasticsearch: {str(e)}")
            
    async def update_document(self, document: Document) -> bool:
        """Update a document in Elasticsearch."""
        if self.connection_state != ConnectionState.CONNECTED:
            await self.connect()
            
        doc_data = document.dict()
        
        try:
            response = await self.client.update(
                index=self.index_name,
                id=document.id,
                body={"doc": doc_data},
                refresh=self.config.get("refresh_on_write", True)
            )
            logger.info(
                "elasticsearch_document_updated",
                document_id=document.id,
                index=self.index_name
            )
            return response["result"] == "updated"
        except NotFoundError:
            logger.info(
                "elasticsearch_document_not_found_for_update",
                document_id=document.id,
                index=self.index_name
            )
            return False
        except TransportError as e:
            logger.error(
                "elasticsearch_update_document_failed",
                document_id=document.id,
                error=str(e),
                error_type=type(e).__name__
            )
            if e.status_code == 408:
                raise QueryTimeout(f"Timeout updating document in Elasticsearch: {str(e)}")
            raise QueryError(f"Error updating document in Elasticsearch: {str(e)}") 