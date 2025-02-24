"""Configuration management for the RAG service with comprehensive validation and type safety."""

from typing import Dict, List, Optional, Union, Any
import os
from pathlib import Path
import logging
from pydantic import BaseModel, Field, field_validator, model_validator
from omegaconf import OmegaConf
from dotenv import load_dotenv
import yaml
import structlog

logger = structlog.get_logger(__name__)

# --- Configuration Models ---

class ServerConfig(BaseModel):
    """Server configuration settings."""
    host: str = Field("0.0.0.0", description="Server host address")
    port: int = Field(8000, ge=1024, le=65535, description="Server port")
    workers: int = Field(4, ge=1, description="Number of worker processes")
    timeout: int = Field(30, ge=1, description="Request timeout in seconds")
    cors_origins: List[str] = Field(["*"], description="CORS allowed origins")

class RateLimitConfig(BaseModel):
    """Rate limiting configuration."""
    requests_per_minute: int = Field(..., gt=0, description="Maximum requests per minute")
    burst: int = Field(..., gt=0, description="Maximum burst size")

class QueryValidationConfig(BaseModel):
    """Query validation configuration."""
    max_length: int = Field(..., gt=0, description="Maximum query length")
    blocked_patterns: List[str] = Field(default_factory=list, description="Blocked query patterns")

class AuthConfig(BaseModel):
    """Authentication configuration."""
    enabled: bool = Field(True, description="Whether authentication is enabled")
    service_url: str = Field(..., description="Authentication service URL")
    cache_ttl: int = Field(300, ge=0, description="Authentication cache TTL in seconds")

class SecurityConfig(BaseModel):
    """Security configuration."""
    api_key_header: str = Field(..., description="API key header name")
    rate_limit: RateLimitConfig
    query_validation: QueryValidationConfig
    auth: AuthConfig

class ElasticsearchConfig(BaseModel):
    """Elasticsearch configuration."""
    hosts: List[str] = Field(..., min_items=1, description="Elasticsearch hosts")
    timeout: int = Field(..., gt=0, description="Connection timeout in seconds")
    max_connections: int = Field(..., gt=0, description="Maximum connections")
    index_name: str = Field(..., description="Index name")
    settings: Dict[str, Any] = Field(default_factory=dict, description="Index settings")

    @field_validator('hosts')
    def validate_hosts(cls, v):
        """Validate Elasticsearch hosts."""
        for host in v:
            if not host.startswith(('http://', 'https://')):
                raise ValueError(f"Invalid Elasticsearch host: {host}. Must start with http:// or https://")
        return v

class MongoDBConfig(BaseModel):
    """MongoDB configuration."""
    uri: str = Field(..., description="MongoDB connection URI")
    database: str = Field(..., description="Database name")
    collection: str = Field(..., description="Collection name")
    max_connections: int = Field(..., gt=0, description="Maximum connections")
    write_concern: Dict[str, Any] = Field(default_factory=dict, description="Write concern settings")

class RedisConfig(BaseModel):
    """Redis configuration."""
    host: str = Field(..., description="Redis host")
    port: int = Field(..., gt=0, le=65535, description="Redis port")
    db: int = Field(0, ge=0, description="Redis database number")
    max_connections: int = Field(..., gt=0, description="Maximum connections")
    ssl: bool = Field(False, description="Whether to use SSL")

class DatabasesConfig(BaseModel):
    """Database configurations."""
    elasticsearch: ElasticsearchConfig
    mongodb: MongoDBConfig
    redis: RedisConfig

class RetrievalStrategyConfig(BaseModel):
    """Retrieval strategy configuration."""
    name: str = Field(..., description="Strategy name")
    weight: float = Field(..., ge=0, le=1, description="Strategy weight")
    params: Dict[str, Any] = Field(default_factory=dict, description="Strategy parameters")

class RetrievalConfig(BaseModel):
    """Retrieval configuration."""
    default_top_k: int = Field(..., gt=0, description="Default number of results to return")
    timeout: int = Field(..., gt=0, description="Retrieval timeout in seconds")
    strategies: List[RetrievalStrategyConfig]

    @field_validator('strategies')
    def validate_strategy_weights(cls, v):
        """Validate that strategy weights sum to 1."""
        total_weight = sum(strategy.weight for strategy in v)
        if not 0.99 <= total_weight <= 1.01:  # Allow for small floating point errors
            raise ValueError(f"Strategy weights must sum to 1, got {total_weight}")
        return v

class CacheConfig(BaseModel):
    """Cache configuration."""
    size: int = Field(..., gt=0, description="Cache size")
    ttl: int = Field(..., gt=0, description="Cache TTL in seconds")

class SemanticCacheConfig(BaseModel):
    """Semantic cache configuration."""
    enabled: bool = Field(True, description="Whether semantic caching is enabled")
    similarity_threshold: float = Field(..., gt=0, le=1, description="Similarity threshold")

class CachingConfig(BaseModel):
    """Caching configuration."""
    l1_cache: CacheConfig
    l2_cache: CacheConfig
    semantic_cache: SemanticCacheConfig

class AlertEndpointConfig(BaseModel):
    """Alert endpoint configuration."""
    type: str = Field(..., description="Endpoint type")
    url: Optional[str] = Field(None, description="Endpoint URL")
    address: Optional[str] = Field(None, description="Email address")

class MonitoringConfig(BaseModel):
    """Monitoring configuration."""
    prometheus: Dict[str, Any] = Field(..., description="Prometheus configuration")
    opentelemetry: Dict[str, Any] = Field(..., description="OpenTelemetry configuration")
    health_check: Dict[str, Any] = Field(..., description="Health check configuration")
    alerting: Dict[str, Any] = Field(..., description="Alerting configuration")

class OptimizationConfig(BaseModel):
    """Optimization configuration."""
    enabled: bool = Field(True, description="Whether optimization is enabled")
    interval: int = Field(..., gt=0, description="Optimization interval in seconds")
    metrics_window: int = Field(..., gt=0, description="Metrics window in seconds")
    thresholds: Dict[str, float] = Field(..., description="Optimization thresholds")

class ModelConfig(BaseModel):
    """Model configuration."""
    name: str = Field(..., description="Model name")
    cache_dir: str = Field(..., description="Model cache directory")
    device: str = Field("cpu", description="Model device (cuda or cpu)")
    batch_size: Optional[int] = Field(None, gt=0, description="Batch size")
    max_length: Optional[int] = Field(None, gt=0, description="Maximum sequence length")

class ModelsConfig(BaseModel):
    """Models configuration."""
    embeddings: ModelConfig
    query_processing: ModelConfig

class AppConfig(BaseModel):
    """Application configuration."""
    name: str = Field(..., description="Application name")
    environment: str = Field(..., description="Environment (development, staging, production)")
    log_level: str = Field("INFO", description="Logging level")

class RAGServiceConfig(BaseModel):
    """Complete RAG service configuration."""
    app: AppConfig
    server: ServerConfig
    security: SecurityConfig
    databases: DatabasesConfig
    retrieval: RetrievalConfig
    caching: CachingConfig
    monitoring: MonitoringConfig
    optimization: OptimizationConfig
    models: ModelsConfig

class ConfigurationManager:
    """Configuration manager for the RAG service."""

    def __init__(self, config_path: Union[str, Path]):
        """Initialize configuration manager.
        
        Args:
            config_path: Path to the configuration file
        """
        self.config_path = Path(config_path)
        self._load_environment()
        self.config = self._load_config()
        self._setup_logging()

    def _load_environment(self) -> None:
        """Load environment variables."""
        load_dotenv()

    def _load_config(self) -> RAGServiceConfig:
        """Load and validate configuration.
        
        Returns:
            Validated configuration object
        
        Raises:
            FileNotFoundError: If configuration file not found
            ValueError: If configuration is invalid
        """
        if not self.config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {self.config_path}")

        # Load base configuration
        config = OmegaConf.load(self.config_path)
        config_dict = OmegaConf.to_container(config, resolve=True)

        # Override with environment variables
        self._override_from_env(config_dict)

        try:
            # Validate configuration
            return RAGServiceConfig(**config_dict)
        except Exception as e:
            raise ValueError(f"Invalid configuration: {str(e)}")

    def _override_from_env(self, config: Dict[str, Any], prefix: str = "RAG_") -> None:
        """Override configuration with environment variables.
        
        Args:
            config: Configuration dictionary to update
            prefix: Environment variable prefix
        """
        for key, value in os.environ.items():
            if key.startswith(prefix):
                # Convert RAG_SECTION__SUBSECTION_KEY to section.subsection.key
                config_key = key[len(prefix):].lower().replace("__", ".")
                self._set_nested_value(config, config_key, value)

    def _set_nested_value(self, config: Dict[str, Any], key: str, value: str) -> None:
        """Set nested dictionary value using dot notation.
        
        Args:
            config: Configuration dictionary to update
            key: Dot-notation key (e.g., "section.subsection.key")
            value: Value to set
        """
        parts = key.split(".")
        current = config
        for part in parts[:-1]:
            current = current.setdefault(part, {})
        current[parts[-1]] = value

    def _setup_logging(self) -> None:
        """Set up logging configuration."""
        logging.basicConfig(
            level=getattr(logging, self.config.app.log_level.upper()),
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )

    def get_config(self) -> RAGServiceConfig:
        """Get the validated configuration.
        
        Returns:
            Validated configuration object
        """
        return self.config 