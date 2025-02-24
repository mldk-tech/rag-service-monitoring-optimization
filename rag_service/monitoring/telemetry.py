"""OpenTelemetry configuration and setup for the RAG service."""

import os
from typing import Optional, Dict, Any

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.jaeger.thrift import JaegerExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.elasticsearch import ElasticsearchInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.pymongo import PyMongoInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
import structlog

logger = structlog.get_logger(__name__)

class TelemetryManager:
    """Manages OpenTelemetry setup and configuration."""

    def __init__(self, config: Dict[str, Any]):
        """Initialize telemetry manager with configuration."""
        self.config = config
        self.tracer_provider: Optional[TracerProvider] = None
        self._setup_telemetry()

    def _setup_telemetry(self) -> None:
        """Set up OpenTelemetry with configured exporters."""
        try:
            # Create resource with service information
            resource = Resource.create({
                "service.name": "rag-service",
                "service.version": os.getenv("SERVICE_VERSION", "0.1.0"),
                "deployment.environment": os.getenv("DEPLOYMENT_ENV", "development")
            })

            # Initialize tracer provider
            self.tracer_provider = TracerProvider(resource=resource)
            trace.set_tracer_provider(self.tracer_provider)

            # Configure exporters based on configuration
            self._setup_exporters()

            # Initialize automatic instrumentation
            self._setup_instrumentations()

            logger.info("OpenTelemetry setup completed successfully")

        except Exception as e:
            logger.error("Failed to setup OpenTelemetry", error=str(e))
            raise

    def _setup_exporters(self) -> None:
        """Set up configured trace exporters."""
        if not self.tracer_provider:
            raise RuntimeError("TracerProvider not initialized")

        telemetry_config = self.config.get("telemetry", {})
        
        # OTLP exporter (for systems like OpenTelemetry Collector)
        if telemetry_config.get("otlp_enabled", True):
            otlp_endpoint = telemetry_config.get("otlp_endpoint", "http://otel-collector:4317")
            otlp_exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
            self.tracer_provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
            logger.info("OTLP exporter configured", endpoint=otlp_endpoint)

        # Jaeger exporter
        if telemetry_config.get("jaeger_enabled", False):
            jaeger_host = telemetry_config.get("jaeger_host", "jaeger")
            jaeger_port = telemetry_config.get("jaeger_port", 6831)
            jaeger_exporter = JaegerExporter(
                agent_host_name=jaeger_host,
                agent_port=jaeger_port,
            )
            self.tracer_provider.add_span_processor(BatchSpanProcessor(jaeger_exporter))
            logger.info("Jaeger exporter configured", host=jaeger_host, port=jaeger_port)

    def _setup_instrumentations(self) -> None:
        """Set up automatic instrumentations for various components."""
        try:
            # Initialize common instrumentations
            RequestsInstrumentor().instrument()
            ElasticsearchInstrumentor().instrument()
            RedisInstrumentor().instrument()
            PyMongoInstrumentor().instrument()

            logger.info("Automatic instrumentations configured successfully")

        except Exception as e:
            logger.error("Failed to setup instrumentations", error=str(e))
            raise

    def instrument_fastapi(self, app) -> None:
        """Instrument FastAPI application."""
        try:
            FastAPIInstrumentor.instrument_app(
                app,
                tracer_provider=self.tracer_provider,
                excluded_urls="health,metrics",
            )
            logger.info("FastAPI instrumentation completed")
        except Exception as e:
            logger.error("Failed to instrument FastAPI", error=str(e))
            raise

    def get_tracer(self, name: str = "rag_service"):
        """Get a tracer instance for manual instrumentation."""
        return trace.get_tracer(name)

    async def shutdown(self) -> None:
        """Shutdown telemetry provider and cleanup resources."""
        if self.tracer_provider:
            await self.tracer_provider.shutdown()
            logger.info("Telemetry provider shut down successfully") 