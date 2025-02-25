"""Enhanced monitoring and metrics module for comprehensive observability."""

from typing import Dict, Optional, Any, List, Union, Callable
import time
from dataclasses import dataclass
from prometheus_client import Counter, Histogram, Gauge, Summary
from opentelemetry import trace, metrics
from opentelemetry.trace import Status, StatusCode
from opentelemetry.metrics import Meter, Counter as OTelCounter, Histogram as OTelHistogram
import logging
import structlog
import functools
import asyncio
from contextlib import contextmanager

logger = structlog.get_logger(__name__)

@dataclass
class MetricsLabels:
    """Common metric labels."""
    method: str
    status: str
    component: str

class MetricsManager:
    """Enhanced metrics manager with OpenTelemetry integration."""
    
    _instance = None
    
    @classmethod
    def get_instance(cls):
        """Get singleton instance of metrics manager."""
        if cls._instance is None:
            cls._instance = MetricsManager()
        return cls._instance

    def __init__(self):
        """Initialize metrics manager with Prometheus and OpenTelemetry metrics."""
        # Prometheus metrics
        self.request_counter = Counter(
            'rag_request_total', 
            'Total number of requests', 
            ['method', 'status', 'component']
        )
        
        self.request_latency = Histogram(
            'rag_request_latency_seconds', 
            'Request latency in seconds', 
            ['method', 'status', 'component'],
            buckets=[0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1, 2.5, 5, 7.5, 10]
        )
        
        self.active_requests = Gauge(
            'rag_active_requests', 
            'Number of active requests', 
            ['component']
        )
        
        self.cache_hits = Counter(
            'rag_cache_hits_total',
            'Total number of cache hits',
            ['cache_type', 'component']
        )
        
        self.cache_misses = Counter(
            'rag_cache_misses_total',
            'Total number of cache misses',
            ['cache_type', 'component']
        )
        
        self.token_usage = Counter(
            'rag_token_usage_total', 
            'Total number of tokens used', 
            ['model', 'type']
        )
        
        self.error_counter = Counter(
            'rag_errors_total', 
            'Total number of errors', 
            ['error_type', 'component']
        )
        
        self.retrieval_latency = Histogram(
            'rag_retrieval_latency_seconds', 
            'Retrieval latency in seconds', 
            ['strategy', 'status'],
            buckets=[0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1, 2.5, 5, 7.5, 10]
        )
        
        self.generation_latency = Histogram(
            'rag_generation_latency_seconds', 
            'Generation latency in seconds', 
            ['model', 'status'],
            buckets=[0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1, 2.5, 5, 7.5, 10]
        )
        
        self.db_operation_latency = Histogram(
            'rag_db_operation_latency_seconds', 
            'Database operation latency in seconds', 
            ['operation', 'database', 'status'],
            buckets=[0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1, 2.5, 5, 7.5, 10]
        )
        
        self.rate_limit_hits = Counter(
            'rag_rate_limit_hits_total', 
            'Total number of rate limit hits', 
            ['endpoint']
        )
        
        self.document_count = Gauge(
            'rag_document_count', 
            'Number of documents in the system', 
            ['index']
        )

        # OpenTelemetry metrics
        try:
            self.meter = metrics.get_meter_provider().get_meter("rag_service")
            
            # Create OpenTelemetry metrics
            self.otel_request_counter = self.meter.create_counter(
                "rag.request.count",
                description="Total number of requests",
                unit="1"
            )
            
            self.otel_request_latency = self.meter.create_histogram(
                "rag.request.duration",
                description="Request duration",
                unit="s"
            )
            
            self.otel_retrieval_latency = self.meter.create_histogram(
                "rag.retrieval.duration",
                description="Retrieval duration",
                unit="s"
            )
            
            self.otel_generation_latency = self.meter.create_histogram(
                "rag.generation.duration",
                description="Generation duration",
                unit="s"
            )
            
            self.otel_db_operation_latency = self.meter.create_histogram(
                "rag.db.operation.duration",
                description="Database operation duration",
                unit="s"
            )
            
            self.otel_token_counter = self.meter.create_counter(
                "rag.token.count",
                description="Number of tokens used",
                unit="1"
            )
            
            self.otel_error_counter = self.meter.create_counter(
                "rag.error.count",
                description="Number of errors",
                unit="1"
            )
            
            self.otel_cache_hit_counter = self.meter.create_counter(
                "rag.cache.hit.count",
                description="Number of cache hits",
                unit="1"
            )
            
            self.otel_cache_miss_counter = self.meter.create_counter(
                "rag.cache.miss.count",
                description="Number of cache misses",
                unit="1"
            )
            
            logger.info("OpenTelemetry metrics initialized successfully")
        except Exception as e:
            logger.warning("Failed to initialize OpenTelemetry metrics", error=str(e))
            self.meter = None
    
    def record_request(self, method: str, status: str, component: str, latency: float) -> None:
        """Record a request with its latency."""
        labels = {"method": method, "status": status, "component": component}
        self.request_counter.labels(method=method, status=status, component=component).inc()
        self.request_latency.labels(method=method, status=status, component=component).observe(latency)
        
        # Record in OpenTelemetry if available
        if self.meter:
            self.otel_request_counter.add(1, labels)
            self.otel_request_latency.record(latency, labels)
    
    def record_retrieval(self, strategy: str, status: str, latency: float) -> None:
        """Record a retrieval operation with its latency."""
        labels = {"strategy": strategy, "status": status}
        self.retrieval_latency.labels(strategy=strategy, status=status).observe(latency)
        
        # Record in OpenTelemetry if available
        if self.meter:
            self.otel_retrieval_latency.record(latency, labels)
    
    def record_generation(self, model: str, status: str, latency: float) -> None:
        """Record a generation operation with its latency."""
        labels = {"model": model, "status": status}
        self.generation_latency.labels(model=model, status=status).observe(latency)
        
        # Record in OpenTelemetry if available
        if self.meter:
            self.otel_generation_latency.record(latency, labels)
    
    def record_db_operation(self, operation: str, database: str, status: str, latency: float) -> None:
        """Record a database operation with its latency."""
        labels = {"operation": operation, "database": database, "status": status}
        self.db_operation_latency.labels(operation=operation, database=database, status=status).observe(latency)
        
        # Record in OpenTelemetry if available
        if self.meter:
            self.otel_db_operation_latency.record(latency, labels)
    
    def record_token_usage(self, model: str, token_type: str, count: int) -> None:
        """Record token usage."""
        labels = {"model": model, "type": token_type}
        self.token_usage.labels(model=model, type=token_type).inc(count)
        
        # Record in OpenTelemetry if available
        if self.meter:
            self.otel_token_counter.add(count, {"model": model, "token_type": token_type})
    
    def record_error(self, error_type: str, component: str) -> None:
        """Record an error."""
        labels = {"error_type": error_type, "component": component}
        self.error_counter.labels(error_type=error_type, component=component).inc()
        
        # Record in OpenTelemetry if available
        if self.meter:
            self.otel_error_counter.add(1, labels)
    
    def record_cache_hit(self, cache_type: str, component: str) -> None:
        """Record a cache hit."""
        labels = {"cache_type": cache_type, "component": component}
        self.cache_hits.labels(cache_type=cache_type, component=component).inc()
        
        # Record in OpenTelemetry if available
        if self.meter:
            self.otel_cache_hit_counter.add(1, labels)
    
    def record_cache_miss(self, cache_type: str, component: str) -> None:
        """Record a cache miss."""
        labels = {"cache_type": cache_type, "component": component}
        self.cache_misses.labels(cache_type=cache_type, component=component).inc()
        
        # Record in OpenTelemetry if available
        if self.meter:
            self.otel_cache_miss_counter.add(1, labels)
    
    def record_rate_limit_hit(self, endpoint: str) -> None:
        """Record a rate limit hit."""
        self.rate_limit_hits.labels(endpoint=endpoint).inc()
    
    def set_document_count(self, index: str, count: int) -> None:
        """Set the document count for an index."""
        self.document_count.labels(index=index).set(count)
    
    def increment_active_requests(self, component: str) -> None:
        """Increment the active requests counter."""
        self.active_requests.labels(component=component).inc()
    
    def decrement_active_requests(self, component: str) -> None:
        """Decrement the active requests counter."""
        self.active_requests.labels(component=component).dec()
    
    @contextmanager
    def measure_latency(self, metric_type: str, **labels) -> None:
        """Context manager to measure latency of an operation."""
        start_time = time.time()
        try:
            yield
            status = "success"
        except Exception as e:
            status = "error"
            self.record_error(error_type=type(e).__name__, component=labels.get("component", "unknown"))
            raise
        finally:
            latency = time.time() - start_time
            if metric_type == "request":
                self.record_request(
                    method=labels.get("method", "unknown"),
                    status=status,
                    component=labels.get("component", "unknown"),
                    latency=latency
                )
            elif metric_type == "retrieval":
                self.record_retrieval(
                    strategy=labels.get("strategy", "unknown"),
                    status=status,
                    latency=latency
                )
            elif metric_type == "generation":
                self.record_generation(
                    model=labels.get("model", "unknown"),
                    status=status,
                    latency=latency
                )
            elif metric_type == "db_operation":
                self.record_db_operation(
                    operation=labels.get("operation", "unknown"),
                    database=labels.get("database", "unknown"),
                    status=status,
                    latency=latency
                )
    
    def instrument(self, component: str, method_name: str = None):
        """Decorator to instrument a method with metrics."""
        def decorator(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                self.increment_active_requests(component)
                start_time = time.time()
                status = "success"
                try:
                    result = await func(*args, **kwargs)
                    return result
                except Exception as e:
                    status = "error"
                    self.record_error(error_type=type(e).__name__, component=component)
                    raise
                finally:
                    latency = time.time() - start_time
                    self.record_request(
                        method=method_name or func.__name__,
                        status=status,
                        component=component,
                        latency=latency
                    )
                    self.decrement_active_requests(component)
            
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                self.increment_active_requests(component)
                start_time = time.time()
                status = "success"
                try:
                    result = func(*args, **kwargs)
                    return result
                except Exception as e:
                    status = "error"
                    self.record_error(error_type=type(e).__name__, component=component)
                    raise
                finally:
                    latency = time.time() - start_time
                    self.record_request(
                        method=method_name or func.__name__,
                        status=status,
                        component=component,
                        latency=latency
                    )
                    self.decrement_active_requests(component)
            
            if asyncio.iscoroutinefunction(func):
                return async_wrapper
            else:
                return sync_wrapper
        
        return decorator 