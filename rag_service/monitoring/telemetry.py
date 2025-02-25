"""OpenTelemetry configuration and setup for the RAG service."""

import os
from typing import Optional, Dict, Any, List

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
from opentelemetry.instrumentation.aiohttp_client import AioHttpClientInstrumentor
from opentelemetry.sdk.trace.sampling import TraceIdRatioBased, ParentBased
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.propagators import set_global_textmap
from opentelemetry.metrics import get_meter_provider, set_meter_provider
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
import structlog

logger = structlog.get_logger(__name__)

class TelemetryManager:
    """Manages OpenTelemetry setup and configuration."""

    def __init__(self, config: Dict[str, Any]):
        """Initialize telemetry manager with configuration."""
        self.config = config
        self.tracer_provider: Optional[TracerProvider] = None
        self.meter_provider: Optional[MeterProvider] = None
        self._setup_telemetry()

    def _setup_telemetry(self) -> None:
        """Set up OpenTelemetry with configured exporters."""
        try:
            # Set up propagators for distributed tracing context
            set_global_textmap(TraceContextTextMapPropagator())
            
            # Create resource with service information
            resource = Resource.create({
                "service.name": "rag-service",
                "service.version": os.getenv("SERVICE_VERSION", "0.1.0"),
                "deployment.environment": os.getenv("DEPLOYMENT_ENV", "development"),
                "host.name": os.getenv("HOSTNAME", "unknown"),
                "telemetry.sdk.name": "opentelemetry",
                "telemetry.sdk.language": "python",
            })

            # Initialize tracer provider with appropriate sampler
            sampler = self._create_sampler()
            self.tracer_provider = TracerProvider(resource=resource, sampler=sampler)
            trace.set_tracer_provider(self.tracer_provider)

            # Configure metrics
            self._setup_metrics(resource)

            # Configure exporters based on configuration
            self._setup_exporters()

            # Initialize automatic instrumentation
            self._setup_instrumentations()

            logger.info("OpenTelemetry setup completed successfully")

        except Exception as e:
            logger.error("Failed to setup OpenTelemetry", error=str(e))
            raise

    def _create_sampler(self):
        """Create a sampler based on configuration."""
        telemetry_config = self.config.get("telemetry", {})
        sampler_config = telemetry_config.get("sampler", {})
        
        sampler_type = sampler_config.get("type", "parentbased_traceidratio")
        sampling_rate = float(sampler_config.get("rate", 1.0))
        
        if sampler_type == "parentbased_traceidratio":
            return ParentBased(TraceIdRatioBased(sampling_rate))
        else:
            return TraceIdRatioBased(sampling_rate)

    def _setup_metrics(self, resource: Resource) -> None:
        """Set up metrics with OpenTelemetry."""
        try:
            telemetry_config = self.config.get("telemetry", {})
            
            # Create OTLP metric exporter
            if telemetry_config.get("otlp_enabled", True):
                otlp_endpoint = telemetry_config.get("otlp_endpoint", "http://otel-collector:4317")
                metric_exporter = OTLPMetricExporter(endpoint=otlp_endpoint)
                reader = PeriodicExportingMetricReader(
                    exporter=metric_exporter,
                    export_interval_millis=telemetry_config.get("metric_export_interval_millis", 30000)
                )
                
                # Create meter provider
                self.meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
                set_meter_provider(self.meter_provider)
                
                logger.info("Metrics setup completed", endpoint=otlp_endpoint)
        except Exception as e:
            logger.error("Failed to setup metrics", error=str(e))
            # Continue even if metrics setup fails

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
            telemetry_config = self.config.get("telemetry", {})
            instrumentation_config = telemetry_config.get("instrumentation", {})
            
            # Initialize common instrumentations
            RequestsInstrumentor().instrument(
                tracer_provider=self.tracer_provider,
                meter_provider=self.meter_provider
            )
            
            ElasticsearchInstrumentor().instrument(
                tracer_provider=self.tracer_provider,
                meter_provider=self.meter_provider,
                capture_elasticsearch_query=instrumentation_config.get("elasticsearch", {}).get("db_statement", True)
            )
            
            RedisInstrumentor().instrument(
                tracer_provider=self.tracer_provider,
                meter_provider=self.meter_provider,
                capture_statement=instrumentation_config.get("redis", {}).get("db_statement", True)
            )
            
            PyMongoInstrumentor().instrument(
                tracer_provider=self.tracer_provider,
                meter_provider=self.meter_provider,
                capture_statement=instrumentation_config.get("pymongo", {}).get("db_statement", True)
            )
            
            AioHttpClientInstrumentor().instrument(
                tracer_provider=self.tracer_provider,
                meter_provider=self.meter_provider
            )

            logger.info("Automatic instrumentations configured successfully")

        except Exception as e:
            logger.error("Failed to setup instrumentations", error=str(e))
            raise

    def instrument_fastapi(self, app) -> None:
        """Instrument FastAPI application."""
        try:
            telemetry_config = self.config.get("telemetry", {})
            instrumentation_config = telemetry_config.get("instrumentation", {})
            fastapi_config = instrumentation_config.get("fastapi", {})
            
            excluded_urls = fastapi_config.get("excluded_urls", "health,metrics")
            if isinstance(excluded_urls, list):
                excluded_urls = ",".join(excluded_urls)
            
            FastAPIInstrumentor.instrument_app(
                app,
                tracer_provider=self.tracer_provider,
                meter_provider=self.meter_provider,
                excluded_urls=excluded_urls,
            )
            logger.info("FastAPI instrumentation completed", excluded_urls=excluded_urls)
        except Exception as e:
            logger.error("Failed to instrument FastAPI", error=str(e))
            raise

    def get_tracer(self, name: str = "rag_service"):
        """Get a tracer instance for manual instrumentation."""
        return trace.get_tracer(name)
    
    def get_meter(self, name: str = "rag_service"):
        """Get a meter instance for manual instrumentation."""
        return get_meter_provider().get_meter(name)

    async def shutdown(self) -> None:
        """Shutdown telemetry provider and cleanup resources."""
        try:
            if self.tracer_provider:
                await self.tracer_provider.shutdown()
                logger.info("Tracer provider shut down successfully")
                
            if self.meter_provider:
                await self.meter_provider.shutdown()
                logger.info("Meter provider shut down successfully")
        except Exception as e:
            logger.error("Error shutting down telemetry providers", error=str(e)) 