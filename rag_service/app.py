"""Main application module for service initialization and startup."""

import os
import sys
import logging
import asyncio
import yaml
from typing import Dict, Any, Optional
import uvicorn
from prometheus_client import start_http_server
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

from rag_service.api.endpoints import app, APIManager, api_manager
from rag_service.core.database import DatabaseManager
from rag_service.core.retrieval import RetrievalManager
from rag_service.core.generation import GenerationManager
from rag_service.core.optimization import OptimizationManager
from rag_service.security.security_manager import SecurityManager
from rag_service.monitoring.metrics import MetricsManager

class Application:
    """Main application class for RAG service."""

    def __init__(self, config_path: str):
        """Initialize application.
        
        Args:
            config_path: Path to configuration file
        """
        self.config_path = config_path
        self.config = {}
        self.managers = {}
        self.is_initialized = False

    async def initialize(self):
        """Initialize application components."""
        try:
            # Load configuration
            self.config = self._load_config()

            # Configure logging
            self._setup_logging()

            # Configure tracing
            self._setup_tracing()

            # Initialize managers
            await self._initialize_managers()

            # Initialize API
            self._initialize_api()

            # Start metrics server
            self._start_metrics_server()

            self.is_initialized = True
            logging.info("Application initialized successfully")

        except Exception as e:
            logging.error("Failed to initialize application: %s", str(e))
            raise

    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from file."""
        try:
            with open(self.config_path, 'r') as f:
                config = yaml.safe_load(f)

            # Validate configuration
            self._validate_config(config)

            return config

        except Exception as e:
            logging.error("Failed to load configuration: %s", str(e))
            raise

    def _validate_config(self, config: Dict[str, Any]):
        """Validate configuration structure and values."""
        required_sections = [
            "application",
            "database",
            "retrieval",
            "generation",
            "optimization",
            "security",
            "monitoring",
            "api"
        ]

        for section in required_sections:
            if section not in config:
                raise ValueError(f"Missing required config section: {section}")

        # Validate specific configuration values
        app_config = config["application"]
        if not app_config.get("name"):
            raise ValueError("Application name not configured")
        if not app_config.get("version"):
            raise ValueError("Application version not configured")

    def _setup_logging(self):
        """Configure logging system."""
        log_config = self.config["application"].get("logging", {})
        
        logging.basicConfig(
            level=log_config.get("level", "INFO"),
            format=log_config.get(
                "format",
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            ),
            handlers=[
                logging.StreamHandler(),
                logging.FileHandler(
                    log_config.get("file", "rag_service.log")
                )
            ]
        )

    def _setup_tracing(self):
        """Configure distributed tracing."""
        trace_config = self.config["application"].get("tracing", {})

        if trace_config.get("enabled", False):
            # Set tracer provider
            provider = TracerProvider()
            
            # Configure OTLP exporter
            otlp_exporter = OTLPSpanExporter(
                endpoint=trace_config.get(
                    "otlp_endpoint",
                    "localhost:4317"
                )
            )
            
            # Add span processor
            provider.add_span_processor(
                BatchSpanProcessor(otlp_exporter)
            )
            
            # Set global tracer provider
            trace.set_tracer_provider(provider)

    async def _initialize_managers(self):
        """Initialize service managers."""
        try:
            # Initialize metrics manager first
            self.managers["metrics"] = MetricsManager(
                self.config["monitoring"]
            )

            # Initialize database manager
            self.managers["database"] = DatabaseManager(
                self.config["database"]
            )
            await self.managers["database"].connect()

            # Initialize security manager
            self.managers["security"] = SecurityManager(
                self.managers["metrics"],
                self.config["security"]
            )

            # Initialize retrieval manager
            self.managers["retrieval"] = RetrievalManager(
                self.managers["database"],
                self.managers["metrics"],
                self.config["retrieval"]
            )

            # Initialize generation manager
            self.managers["generation"] = GenerationManager(
                self.managers["metrics"],
                self.config["generation"]
            )
            await self.managers["generation"].initialize()

            # Initialize optimization manager
            self.managers["optimization"] = OptimizationManager(
                self.managers["retrieval"],
                self.managers["generation"],
                self.managers["metrics"],
                self.config["optimization"]
            )

        except Exception as e:
            logging.error("Failed to initialize managers: %s", str(e))
            await self.shutdown()
            raise

    def _initialize_api(self):
        """Initialize API manager and endpoints."""
        try:
            # Create API manager instance
            global api_manager
            api_manager = APIManager(
                retrieval_manager=self.managers["retrieval"],
                generation_manager=self.managers["generation"],
                optimization_manager=self.managers["optimization"],
                security_manager=self.managers["security"],
                metrics_manager=self.managers["metrics"],
                config=self.config["api"]
            )

        except Exception as e:
            logging.error("Failed to initialize API: %s", str(e))
            raise

    def _start_metrics_server(self):
        """Start Prometheus metrics server."""
        metrics_config = self.config["monitoring"].get("metrics", {})
        
        if metrics_config.get("enabled", False):
            try:
                start_http_server(
                    port=metrics_config.get("port", 9090),
                    addr=metrics_config.get("host", "0.0.0.0")
                )
                logging.info(
                    "Metrics server started on port %d",
                    metrics_config.get("port", 9090)
                )
            except Exception as e:
                logging.error("Failed to start metrics server: %s", str(e))
                raise

    async def shutdown(self):
        """Shutdown application components."""
        logging.info("Shutting down application...")

        # Shutdown managers in reverse order
        if "optimization" in self.managers:
            logging.info("Shutting down optimization manager...")
            # Optimization manager has no shutdown method

        if "generation" in self.managers:
            logging.info("Shutting down generation manager...")
            await self.managers["generation"].close()

        if "retrieval" in self.managers:
            logging.info("Shutting down retrieval manager...")
            # Retrieval manager has no shutdown method

        if "security" in self.managers:
            logging.info("Shutting down security manager...")
            # Security manager has no shutdown method

        if "database" in self.managers:
            logging.info("Shutting down database manager...")
            await self.managers["database"].close()

        if "metrics" in self.managers:
            logging.info("Shutting down metrics manager...")
            # Metrics manager has no shutdown method

        logging.info("Application shutdown complete")

    def run(self):
        """Run the application."""
        if not self.is_initialized:
            raise RuntimeError("Application not initialized")

        api_config = self.config["api"]
        
        uvicorn.run(
            app,
            host=api_config.get("host", "0.0.0.0"),
            port=api_config.get("port", 8000),
            workers=api_config.get("workers", 1),
            log_level=api_config.get("log_level", "info"),
            reload=api_config.get("reload", False)
        )

async def main():
    """Main entry point."""
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
        logging.error("Application failed: %s", str(e))
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main()) 