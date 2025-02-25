"""Database package for RAG service with multiple backend implementations."""

from rag_service.core.database.base import (
    Document, 
    DatabaseError,
    ConnectionError,
    QueryError,
    QueryTimeout,
    ConnectionState,
    DatabaseConfig
)
from rag_service.core.database.manager import DatabaseManager
from rag_service.core.database.elasticsearch_db import ElasticsearchClient
from rag_service.core.database.mongodb_db import MongoDBClient
from rag_service.core.database.redis_db import RedisClient
from rag_service.core.database.weaviate_db import WeaviateClient
from rag_service.core.database.milvus_db import MilvusClient
from rag_service.core.database.neo4j_db import Neo4jClient

__all__ = [
    'Document',
    'DatabaseError',
    'ConnectionError',
    'QueryError',
    'QueryTimeout',
    'ConnectionState',
    'DatabaseConfig',
    'DatabaseManager',
    'ElasticsearchClient',
    'MongoDBClient',
    'RedisClient',
    'WeaviateClient',
    'MilvusClient',
    'Neo4jClient',
] 