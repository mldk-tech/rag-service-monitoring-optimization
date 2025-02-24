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

from rag_service.api.endpoints import app, APIManager
from rag_service.core.database import DatabaseManager
from rag_service.core.retrieval import RetrievalManager
from rag_service.core.generation import GenerationManager
from rag_service.core.optimization import OptimizationManager
from rag_service.security.security_manager import SecurityManager
from rag_service.monitoring.metrics import MetricsManager

# Global API manager instance
api_manager = None

@app.on_event("startup")
async def startup_event():
    """Initialize application on startup."""
    global api_manager
    try:
        # Load configuration
        config_path = os.getenv("RAG_CONFIG_PATH", "config/config.yaml")
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)

        # Initialize managers
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

        # Configure tracing if enabled
        if config["application"].get("tracing", {}).get("enabled", False):
            provider = TracerProvider()
            otlp_exporter = OTLPSpanExporter(
                endpoint=config["application"]["tracing"].get(
                    "otlp_endpoint",
                    "localhost:4317"
                )
            )
            provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
            trace.set_tracer_provider(provider)

        # Start metrics server if enabled
        if config["monitoring"].get("metrics", {}).get("enabled", False):
            metrics_port = config["monitoring"]["metrics"].get("port", 9090)
            start_http_server(port=metrics_port)
            logging.info(f"Metrics server started on port {metrics_port}")

        logging.info("Application initialized successfully")

    except Exception as e:
        logging.error(f"Failed to initialize application: {str(e)}")
        raise

@app.on_event("shutdown")
async def shutdown_event():
    """Clean up resources on shutdown."""
    global api_manager
    try:
        if api_manager:
            # Clean up managers in reverse order
            if hasattr(api_manager, "optimization_manager"):
                # Optimization manager cleanup
                pass

            if hasattr(api_manager, "generation_manager"):
                await api_manager.generation_manager.close()

            if hasattr(api_manager, "retrieval_manager"):
                # Retrieval manager cleanup
                pass

            if hasattr(api_manager, "security_manager"):
                # Security manager cleanup
                pass

            if hasattr(api_manager, "metrics_manager"):
                # Metrics manager cleanup
                pass

        logging.info("Application shutdown completed")

    except Exception as e:
        logging.error(f"Error during shutdown: {str(e)}")
        raise

def get_api_manager() -> APIManager:
    """Get the API manager instance.
    
    Returns:
        API manager instance
        
    Raises:
        RuntimeError: If API manager is not initialized
    """
    if api_manager is None:
        raise RuntimeError("API manager not initialized")
    return api_manager

# Update FastAPI dependencies
app.dependency_overrides[APIManager] = get_api_manager

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