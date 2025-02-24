"""Main application module for service initialization and startup."""

import os
import sys
import asyncio
import yaml
from typing import Dict, Any, Optional
import uvicorn
from prometheus_client import start_http_server
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from pydantic import BaseModel, Field, validator
import structlog
from contextlib import asynccontextmanager
import signal

from rag_service.api.endpoints import app, APIManager
from rag_service.core.database import DatabaseManager
from rag_service.core.retrieval import RetrievalManager
from rag_service.core.generation import GenerationManager
from rag_service.core.optimization import OptimizationManager
from rag_service.security.security_manager import SecurityManager
from rag_service.monitoring.metrics import MetricsManager
from rag_service.config.config_manager import ConfigManager

# Enhanced logging setup
logger = structlog.get_logger(__name__)

# Global managers
api_manager = None
config_manager = None
shutdown_event = asyncio.Event()

class ApplicationConfig(BaseModel):
    """Enhanced application configuration with validation."""
    class TracingConfig(BaseModel):
        enabled: bool = Field(default=False)
        otlp_endpoint: str = Field(default="localhost:4317")
        service_name: str = Field(default="rag-service")
        sample_rate: float = Field(default=1.0, ge=0.0, le=1.0)

    class MonitoringConfig(BaseModel):
        class MetricsConfig(BaseModel):
            enabled: bool = Field(default=True)
            port: int = Field(default=9090, ge=1024, le=65535)
            path: str = Field(default="/metrics")

        metrics: MetricsConfig = Field(default_factory=MetricsConfig)

    tracing: TracingConfig = Field(default_factory=TracingConfig)
    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)
    log_level: str = Field(default="INFO")
    graceful_shutdown_timeout: int = Field(default=30, ge=1)

def setup_signal_handlers():
    """Setup signal handlers for graceful shutdown."""
    def handle_signal(signum, frame):
        logger.info("shutdown_signal_received", signal=signum)
        shutdown_event.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

def configure_logging(log_level: str):
    """Configure structured logging."""
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer()
        ],
        wrapper_class=structlog.BoundLogger,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True
    )

def setup_tracing(config: ApplicationConfig.TracingConfig):
    """Setup OpenTelemetry tracing."""
    if not config.enabled:
        return

    provider = TracerProvider(
        sampler=trace.sampling.TraceIdRatioBased(config.sample_rate)
    )
    otlp_exporter = OTLPSpanExporter(endpoint=config.otlp_endpoint)
    provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
    trace.set_tracer_provider(provider)

    logger.info(
        "tracing_configured",
        endpoint=config.otlp_endpoint,
        service_name=config.service_name,
        sample_rate=config.sample_rate
    )

def setup_metrics(config: ApplicationConfig.MonitoringConfig.MetricsConfig):
    """Setup Prometheus metrics server."""
    if not config.enabled:
        return

    start_http_server(port=config.port)
    logger.info(
        "metrics_server_started",
        port=config.port,
        path=config.path
    )

@asynccontextmanager
async def lifespan(app):
    """Manage application lifespan with proper initialization and cleanup."""
    try:
        await startup_event()
        yield
    finally:
        await shutdown_event()

app.router.lifespan_context = lifespan

@app.on_event("startup")
async def startup_event():
    """Initialize application on startup with enhanced error handling."""
    global api_manager, config_manager

    try:
        # Load and validate configuration
        config_path = os.getenv("RAG_CONFIG_PATH", "config/config.yaml")
        config_manager = ConfigManager(config_path)
        config = config_manager.load_config()
        app_config = ApplicationConfig(**config["application"])

        # Configure logging and monitoring
        configure_logging(app_config.log_level)
        setup_signal_handlers()
        setup_tracing(app_config.tracing)
        setup_metrics(app_config.monitoring.metrics)

        # Initialize managers with proper error handling
        try:
            metrics_manager = MetricsManager.get_instance()
            
            # Initialize database manager
            db_manager = DatabaseManager(config["database"])
            await db_manager.connect()

            # Initialize security manager
            security_manager = SecurityManager(
                metrics_manager,
                config["security"]
            )

            # Initialize retrieval manager
            retrieval_manager = RetrievalManager(
                db_manager,
                metrics_manager,
                config["retrieval"]
            )

            # Initialize generation manager
            generation_manager = GenerationManager(
                metrics_manager,
                config["generation"]
            )
            await generation_manager.initialize()

            # Initialize optimization manager
            optimization_manager = OptimizationManager(
                retrieval_manager,
                generation_manager,
                metrics_manager,
                config["optimization"]
            )

            # Initialize API manager
            api_manager = APIManager(
                retrieval_manager=retrieval_manager,
                generation_manager=generation_manager,
                optimization_manager=optimization_manager,
                security_manager=security_manager,
                metrics_manager=metrics_manager,
                config=config["api"]
            )

            logger.info(
                "application_initialized",
                config_path=config_path
            )

        except Exception as e:
            logger.error(
                "manager_initialization_failed",
                error=str(e),
                error_type=type(e).__name__
            )
            raise

    except Exception as e:
        logger.error(
            "startup_failed",
            error=str(e),
            error_type=type(e).__name__
        )
        raise

@app.on_event("shutdown")
async def shutdown_event():
    """Clean up resources on shutdown with enhanced error handling."""
    global api_manager

    if not api_manager:
        return

    logger.info("shutdown_initiated")
    
    try:
        # Set shutdown timeout
        shutdown_timeout = ApplicationConfig().graceful_shutdown_timeout
        
        async def cleanup():
            try:
                # Clean up managers in reverse order
                if hasattr(api_manager, "optimization_manager"):
                    await api_manager.optimization_manager.cleanup()

                if hasattr(api_manager, "generation_manager"):
                    await api_manager.generation_manager.close()

                if hasattr(api_manager, "retrieval_manager"):
                    await api_manager.retrieval_manager.cleanup()

                if hasattr(api_manager, "security_manager"):
                    await api_manager.security_manager.cleanup()

                if hasattr(api_manager, "db_manager"):
                    await api_manager.db_manager.close()

                logger.info("cleanup_completed")

            except Exception as e:
                logger.error(
                    "cleanup_failed",
                    error=str(e),
                    error_type=type(e).__name__
                )
                raise

        try:
            # Run cleanup with timeout
            await asyncio.wait_for(cleanup(), timeout=shutdown_timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "cleanup_timeout",
                timeout=shutdown_timeout
            )

    except Exception as e:
        logger.error(
            "shutdown_failed",
            error=str(e),
            error_type=type(e).__name__
        )
        raise

    finally:
        # Clear global state
        api_manager = None
        logger.info("shutdown_completed")

def get_api_manager() -> APIManager:
    """Get the API manager instance with enhanced error handling.
    
    Returns:
        API manager instance
        
    Raises:
        RuntimeError: If API manager is not initialized
    """
    if api_manager is None:
        logger.error("api_manager_not_initialized")
        raise RuntimeError("API manager not initialized")
    return api_manager

# Update FastAPI dependencies
app.dependency_overrides[APIManager] = get_api_manager

class Application:
    """Enhanced application class with proper lifecycle management."""

    def __init__(self, config_path: str):
        """Initialize application.
        
        Args:
            config_path: Path to configuration file
        """
        self.config_path = config_path
        self.config_manager = None
        self.shutdown_event = asyncio.Event()

    async def initialize(self):
        """Initialize application components."""
        try:
            # Load configuration
            self.config_manager = ConfigManager(self.config_path)
            config = self.config_manager.load_config()
            
            # Configure application
            app_config = ApplicationConfig(**config["application"])
            configure_logging(app_config.log_level)
            setup_signal_handlers()
            setup_tracing(app_config.tracing)
            setup_metrics(app_config.monitoring.metrics)

            logger.info(
                "application_initialized",
                config_path=self.config_path
            )

        except Exception as e:
            logger.error(
                "initialization_failed",
                error=str(e),
                error_type=type(e).__name__
            )
            raise

    def run(self):
        """Run the application with uvicorn."""
        config = self.config_manager.get_config()
        uvicorn.run(
            app,
            host=config["server"]["host"],
            port=config["server"]["port"],
            reload=config["server"].get("reload", False),
            log_level=config["application"]["log_level"].lower()
        )

async def main():
    """Enhanced main entry point with proper error handling."""
    try:
        # Get configuration path
        config_path = os.environ.get(
            "RAG_CONFIG_PATH",
            "config/config.yaml"
        )

        # Create and initialize application
        app = Application(config_path)
        await app.initialize()

        # Run application
        app.run()

    except Exception as e:
        logger.error(
            "application_failed",
            error=str(e),
            error_type=type(e).__name__
        )
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main()) 