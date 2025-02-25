"""Database manager for coordinating multiple database backends."""

import asyncio
from typing import Dict, Any, Optional, List, Type, Union
import structlog
from opentelemetry import trace
from prometheus_client import Counter, Histogram, Summary

from rag_service.core.database.base import (
    DatabaseClient, 
    Document, 
    DatabaseConfig,
    ConnectionState,
    DatabaseError
)
from rag_service.core.database.elasticsearch_db import ElasticsearchClient
from rag_service.core.database.mongodb_db import MongoDBClient
from rag_service.core.database.redis_db import RedisClient
from rag_service.core.database.weaviate_db import WeaviateClient
from rag_service.core.database.milvus_db import MilvusClient
from rag_service.core.database.neo4j_db import Neo4jClient
from rag_service.monitoring.metrics import MetricsManager

# Get a logger
logger = structlog.get_logger(__name__)

# Get a tracer
tracer = trace.get_tracer(__name__)

class DatabaseManager:
    """Manager for coordinating multiple database backends."""
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize the database manager with configuration."""
        self.config = DatabaseConfig(**config)
        self.metrics = MetricsManager.get_instance()
        self.clients: Dict[str, DatabaseClient] = {}
        self._setup_metrics()
        
    def _setup_metrics(self):
        """Set up metrics for database operations."""
        self.db_operation_duration = self.metrics.create_histogram(
            "db_operation_duration_seconds",
            "Duration of database operations in seconds",
            ["operation", "database"]
        )
        self.db_operation_count = self.metrics.create_counter(
            "db_operation_count",
            "Count of database operations",
            ["operation", "database", "status"]
        )
        self.db_connection_status = self.metrics.create_gauge(
            "db_connection_status",
            "Database connection status (1=connected, 0=disconnected)",
            ["database"]
        )
        
    async def connect(self):
        """Connect to all configured databases."""
        tasks = []
        
        # Initialize Elasticsearch if configured
        if self.config.elasticsearch:
            es_client = ElasticsearchClient(self.config.elasticsearch, self.metrics)
            self.clients["elasticsearch"] = es_client
            tasks.append(self._connect_client("elasticsearch", es_client))
            
        # Initialize MongoDB if configured
        if self.config.mongodb:
            mongo_client = MongoDBClient(self.config.mongodb, self.metrics)
            self.clients["mongodb"] = mongo_client
            tasks.append(self._connect_client("mongodb", mongo_client))
            
        # Initialize Redis if configured
        if self.config.redis:
            redis_client = RedisClient(self.config.redis, self.metrics)
            self.clients["redis"] = redis_client
            tasks.append(self._connect_client("redis", redis_client))
            
        # Initialize Weaviate if configured
        if self.config.weaviate:
            weaviate_client = WeaviateClient(self.config.weaviate, self.metrics)
            self.clients["weaviate"] = weaviate_client
            tasks.append(self._connect_client("weaviate", weaviate_client))
            
        # Initialize Milvus if configured
        if self.config.milvus:
            milvus_client = MilvusClient(self.config.milvus, self.metrics)
            self.clients["milvus"] = milvus_client
            tasks.append(self._connect_client("milvus", milvus_client))
            
        # Initialize Neo4j if configured
        if self.config.neo4j:
            neo4j_client = Neo4jClient(self.config.neo4j, self.metrics)
            self.clients["neo4j"] = neo4j_client
            tasks.append(self._connect_client("neo4j", neo4j_client))
            
        # Wait for all connections to complete
        if tasks:
            await asyncio.gather(*tasks)
            
    async def _connect_client(self, name: str, client: DatabaseClient):
        """Connect to a specific database client with error handling."""
        try:
            await client.connect()
            status = await client.get_status()
            self.db_connection_status.labels(database=name).set(
                1 if status == ConnectionState.CONNECTED else 0
            )
            logger.info(f"{name}_connected", status=status.value)
        except Exception as e:
            self.db_connection_status.labels(database=name).set(0)
            logger.error(
                f"{name}_connection_failed",
                error=str(e),
                error_type=type(e).__name__
            )
            raise
            
    async def disconnect(self):
        """Disconnect from all databases."""
        tasks = []
        for name, client in self.clients.items():
            tasks.append(self._disconnect_client(name, client))
            
        if tasks:
            await asyncio.gather(*tasks)
            
    async def _disconnect_client(self, name: str, client: DatabaseClient):
        """Disconnect from a specific database client with error handling."""
        try:
            await client.disconnect()
            self.db_connection_status.labels(database=name).set(0)
            logger.info(f"{name}_disconnected")
        except Exception as e:
            logger.error(
                f"{name}_disconnect_failed",
                error=str(e),
                error_type=type(e).__name__
            )
            
    async def health_check(self) -> Dict[str, bool]:
        """Check the health of all database connections."""
        results = {}
        for name, client in self.clients.items():
            try:
                results[name] = await client.health_check()
            except Exception:
                results[name] = False
                
        return results
        
    async def store_document(self, document: Document, database: Optional[str] = None) -> str:
        """Store a document in the specified database or all databases."""
        if database and database in self.clients:
            return await self._store_in_database(database, document)
            
        # If no specific database is specified, store in all databases
        document_ids = {}
        for db_name, client in self.clients.items():
            document_ids[db_name] = await self._store_in_database(db_name, document)
            
        # Return the ID from the primary database (elasticsearch or the first available)
        primary_db = "elasticsearch" if "elasticsearch" in document_ids else list(document_ids.keys())[0]
        return document_ids[primary_db]
        
    async def _store_in_database(self, database: str, document: Document) -> str:
        """Store a document in a specific database with metrics and tracing."""
        client = self.clients[database]
        with tracer.start_as_current_span(f"{database}_store_document") as span:
            span.set_attribute("document.id", document.id)
            span.set_attribute("database", database)
            
            with self.db_operation_duration.labels(
                operation="store_document", database=database
            ).time():
                try:
                    document_id = await client.store_document(document)
                    self.db_operation_count.labels(
                        operation="store_document", database=database, status="success"
                    ).inc()
                    return document_id
                except Exception as e:
                    self.db_operation_count.labels(
                        operation="store_document", database=database, status="error"
                    ).inc()
                    span.record_exception(e)
                    logger.error(
                        f"{database}_store_document_failed",
                        document_id=document.id,
                        error=str(e),
                        error_type=type(e).__name__
                    )
                    raise
                    
    async def get_document(self, document_id: str, database: Optional[str] = None) -> Optional[Document]:
        """Get a document from the specified database or try all databases."""
        if database and database in self.clients:
            return await self._get_from_database(database, document_id)
            
        # If no specific database is specified, try all databases
        for db_name, client in self.clients.items():
            document = await self._get_from_database(db_name, document_id)
            if document:
                return document
                
        return None
        
    async def _get_from_database(self, database: str, document_id: str) -> Optional[Document]:
        """Get a document from a specific database with metrics and tracing."""
        client = self.clients[database]
        with tracer.start_as_current_span(f"{database}_get_document") as span:
            span.set_attribute("document.id", document_id)
            span.set_attribute("database", database)
            
            with self.db_operation_duration.labels(
                operation="get_document", database=database
            ).time():
                try:
                    document = await client.get_document(document_id)
                    self.db_operation_count.labels(
                        operation="get_document", database=database, status="success"
                    ).inc()
                    return document
                except Exception as e:
                    self.db_operation_count.labels(
                        operation="get_document", database=database, status="error"
                    ).inc()
                    span.record_exception(e)
                    logger.error(
                        f"{database}_get_document_failed",
                        document_id=document_id,
                        error=str(e),
                        error_type=type(e).__name__
                    )
                    return None
                    
    async def search_documents(
        self, 
        query: str, 
        database: Optional[str] = None,
        limit: int = 10,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[Document]:
        """Search for documents in the specified database or all databases."""
        if database and database in self.clients:
            return await self._search_in_database(database, query, limit, filters)
            
        # If no specific database is specified, search in all databases and combine results
        all_results = []
        for db_name, client in self.clients.items():
            results = await self._search_in_database(db_name, query, limit, filters)
            all_results.extend(results)
            
        # Sort by relevance and limit
        all_results.sort(key=lambda doc: doc.metadata.get("score", 0), reverse=True)
        return all_results[:limit]
        
    async def _search_in_database(
        self, 
        database: str, 
        query: str, 
        limit: int,
        filters: Optional[Dict[str, Any]]
    ) -> List[Document]:
        """Search for documents in a specific database with metrics and tracing."""
        client = self.clients[database]
        with tracer.start_as_current_span(f"{database}_search_documents") as span:
            span.set_attribute("query", query)
            span.set_attribute("database", database)
            span.set_attribute("limit", limit)
            
            with self.db_operation_duration.labels(
                operation="search_documents", database=database
            ).time():
                try:
                    documents = await client.search_documents(query, limit, filters)
                    self.db_operation_count.labels(
                        operation="search_documents", database=database, status="success"
                    ).inc()
                    span.set_attribute("result_count", len(documents))
                    return documents
                except Exception as e:
                    self.db_operation_count.labels(
                        operation="search_documents", database=database, status="error"
                    ).inc()
                    span.record_exception(e)
                    logger.error(
                        f"{database}_search_documents_failed",
                        query=query,
                        error=str(e),
                        error_type=type(e).__name__
                    )
                    return []
                    
    async def delete_document(self, document_id: str, database: Optional[str] = None) -> bool:
        """Delete a document from the specified database or all databases."""
        if database and database in self.clients:
            return await self._delete_from_database(database, document_id)
            
        # If no specific database is specified, delete from all databases
        success = True
        for db_name, client in self.clients.items():
            result = await self._delete_from_database(db_name, document_id)
            success = success and result
            
        return success
        
    async def _delete_from_database(self, database: str, document_id: str) -> bool:
        """Delete a document from a specific database with metrics and tracing."""
        client = self.clients[database]
        with tracer.start_as_current_span(f"{database}_delete_document") as span:
            span.set_attribute("document.id", document_id)
            span.set_attribute("database", database)
            
            with self.db_operation_duration.labels(
                operation="delete_document", database=database
            ).time():
                try:
                    success = await client.delete_document(document_id)
                    self.db_operation_count.labels(
                        operation="delete_document", database=database, status="success"
                    ).inc()
                    return success
                except Exception as e:
                    self.db_operation_count.labels(
                        operation="delete_document", database=database, status="error"
                    ).inc()
                    span.record_exception(e)
                    logger.error(
                        f"{database}_delete_document_failed",
                        document_id=document_id,
                        error=str(e),
                        error_type=type(e).__name__
                    )
                    return False
                    
    async def update_document(self, document: Document, database: Optional[str] = None) -> bool:
        """Update a document in the specified database or all databases."""
        if database and database in self.clients:
            return await self._update_in_database(database, document)
            
        # If no specific database is specified, update in all databases
        success = True
        for db_name, client in self.clients.items():
            result = await self._update_in_database(db_name, document)
            success = success and result
            
        return success
        
    async def _update_in_database(self, database: str, document: Document) -> bool:
        """Update a document in a specific database with metrics and tracing."""
        client = self.clients[database]
        with tracer.start_as_current_span(f"{database}_update_document") as span:
            span.set_attribute("document.id", document.id)
            span.set_attribute("database", database)
            
            with self.db_operation_duration.labels(
                operation="update_document", database=database
            ).time():
                try:
                    success = await client.update_document(document)
                    self.db_operation_count.labels(
                        operation="update_document", database=database, status="success"
                    ).inc()
                    return success
                except Exception as e:
                    self.db_operation_count.labels(
                        operation="update_document", database=database, status="error"
                    ).inc()
                    span.record_exception(e)
                    logger.error(
                        f"{database}_update_document_failed",
                        document_id=document.id,
                        error=str(e),
                        error_type=type(e).__name__
                    )
                    return False 