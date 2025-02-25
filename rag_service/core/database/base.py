"""Base classes and interfaces for database implementations."""

from typing import Dict, Any, Optional, List, Union
from enum import Enum
from datetime import datetime
from abc import ABC, abstractmethod
from pydantic import BaseModel, Field, field_validator

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
    
    class WeaviateConfig(BaseModel):
        url: str
        api_key: Optional[str] = None
        timeout: int = Field(default=30, ge=1)
        batch_size: int = Field(default=100, ge=1)
        
    class MilvusConfig(BaseModel):
        host: str
        port: int = Field(default=19530, ge=1, le=65535)
        collection: str
        dimension: int = Field(default=768, ge=1)
        index_type: str = Field(default="IVF_FLAT")
        metric_type: str = Field(default="L2")
        
    class Neo4jConfig(BaseModel):
        uri: str
        username: str
        password: str
        database: Optional[str] = None
        max_connection_lifetime: int = Field(default=3600, ge=1)
        max_connection_pool_size: int = Field(default=100, ge=1)
        
    elasticsearch: Optional[ElasticsearchConfig] = None
    mongodb: Optional[MongoDBConfig] = None
    redis: Optional[RedisConfig] = None
    weaviate: Optional[WeaviateConfig] = None
    milvus: Optional[MilvusConfig] = None
    neo4j: Optional[Neo4jConfig] = None
    connection_timeout: int = Field(default=30, ge=1)
    retry_attempts: int = Field(default=3, ge=1)
    circuit_breaker_threshold: int = Field(default=5, ge=1)
    circuit_breaker_reset_timeout: int = Field(default=60, ge=1)
    
    @field_validator("elasticsearch")
    def validate_es_config(cls, v):
        if v and not v.hosts:
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
    """Enhanced document model with metadata."""
    id: str
    content: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    embedding: Optional[List[float]] = None
    source: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = None
    
    class Config:
        arbitrary_types_allowed = True

class DatabaseClient(ABC):
    """Abstract base class for database clients."""
    
    @abstractmethod
    async def connect(self) -> None:
        """Connect to the database."""
        pass
    
    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from the database."""
        pass
    
    @abstractmethod
    async def get_status(self) -> ConnectionState:
        """Get the current connection status."""
        pass
    
    @abstractmethod
    async def health_check(self) -> bool:
        """Check if the database is healthy."""
        pass
    
    @abstractmethod
    async def store_document(self, document: Document) -> str:
        """Store a document in the database."""
        pass
    
    @abstractmethod
    async def get_document(self, document_id: str) -> Optional[Document]:
        """Retrieve a document by ID."""
        pass
    
    @abstractmethod
    async def search_documents(
        self, 
        query: str, 
        limit: int = 10, 
        filters: Optional[Dict[str, Any]] = None
    ) -> List[Document]:
        """Search for documents based on a query."""
        pass
    
    @abstractmethod
    async def delete_document(self, document_id: str) -> bool:
        """Delete a document by ID."""
        pass
    
    @abstractmethod
    async def update_document(self, document: Document) -> bool:
        """Update a document."""
        pass 