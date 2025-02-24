"""Enhanced monitoring and metrics module for comprehensive observability."""

from typing import Dict, Optional, Any
import time
from dataclasses import dataclass
from prometheus_client import Counter, Histogram, Gauge, Summary
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
import logging
import structlog

logger = structlog.get_logger(__name__)

@dataclass
class MetricsLabels:
    """Common metric labels."""
    method: str
    status: str
    component: str

class MetricsCollector:
    """Centralized metrics collection for the RAG service."""

    def __init__(self):
        """Initialize all metrics collectors."""
        # Database connection metrics
        self.db_connection_pool_size = Gauge(
            'rag_db_connection_pool_size',
            'Current size of database connection pools',
            ['database']
        )
        self.db_active_connections = Gauge(
            'rag_db_active_connections',
            'Number of active database connections',
            ['database']
        )
        self.db_connection_errors = Counter(
            'rag_db_connection_errors_total',
            'Total number of database connection errors',
            ['database', 'error_type']
        )
        self.db_connection_timeouts = Counter(
            'rag_db_connection_timeouts_total',
            'Total number of database connection timeouts',
            ['database']
        )

        # Database query metrics
        self.db_queries_total = Counter(
            'rag_db_queries_total',
            'Total number of database queries',
            ['database', 'operation', 'status']
        )
        self.db_query_duration_seconds = Histogram(
            'rag_db_query_duration_seconds',
            'Database query duration in seconds',
            ['database', 'operation', 'status'],
            buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5)
        )
        self.db_queries_active = Gauge(
            'rag_db_queries_active',
            'Number of currently executing queries',
            ['database', 'operation']
        )
        self.db_query_timeouts = Counter(
            'rag_db_query_timeouts_total',
            'Total number of query timeouts',
            ['database', 'operation']
        )
        self.db_query_errors = Counter(
            'rag_db_query_errors_total',
            'Total number of query errors',
            ['database', 'operation', 'error_type']
        )

        # Circuit breaker metrics
        self.circuit_breaker_state = Gauge(
            'rag_circuit_breaker_state',
            'Circuit breaker state (0=open, 1=closed)',
            ['name']
        )
        self.circuit_breaker_failures = Counter(
            'rag_circuit_breaker_failures_total',
            'Total number of circuit breaker failures',
            ['name', 'type']
        )
        self.circuit_breaker_state_changes = Counter(
            'rag_circuit_breaker_state_changes_total',
            'Total number of circuit breaker state changes',
            ['name', 'from_state', 'to_state']
        )

        # Cache metrics
        self.cache_hits = Counter(
            'rag_cache_hits_total',
            'Total number of cache hits',
            ['operation']
        )
        self.cache_misses = Counter(
            'rag_cache_misses_total',
            'Total number of cache misses',
            ['operation']
        )
        self.cache_sets = Counter(
            'rag_cache_sets_total',
            'Total number of cache sets',
            ['operation', 'status']
        )
        self.cache_evictions = Counter(
            'rag_cache_evictions_total',
            'Total number of cache evictions',
            ['reason']
        )

        # Search metrics
        self.search_latency = Histogram(
            'rag_search_latency_seconds',
            'Search operation latency in seconds',
            ['index', 'operation'],
            buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)
        )
        self.search_results = Histogram(
            'rag_search_results_count',
            'Number of search results returned',
            ['index'],
            buckets=(0, 1, 5, 10, 20, 50, 100)
        )
        self.search_cache_hit = Counter(
            'rag_search_cache_hits_total',
            'Total number of search cache hits',
            ['index']
        )
        self.search_cache_miss = Counter(
            'rag_search_cache_misses_total',
            'Total number of search cache misses',
            ['index']
        )

        # Document metrics
        self.document_inserts = Counter(
            'rag_document_inserts_total',
            'Total number of document insertions',
            ['collection']
        )
        self.document_updates = Counter(
            'rag_document_updates_total',
            'Total number of document updates',
            ['collection']
        )
        self.document_deletes = Counter(
            'rag_document_deletes_total',
            'Total number of document deletions',
            ['collection']
        )
        self.document_size_bytes = Histogram(
            'rag_document_size_bytes',
            'Document size in bytes',
            ['collection'],
            buckets=(100, 1000, 10000, 100000, 1000000)
        )

        # Health metrics
        self.db_health_status = Gauge(
            'rag_db_health_status',
            'Database health status (0=unhealthy, 1=healthy)',
            ['database']
        )
        self.health_check_duration = Histogram(
            'rag_health_check_duration_seconds',
            'Health check duration in seconds',
            ['database'],
            buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0)
        )
        self.health_check_errors = Counter(
            'rag_health_check_errors_total',
            'Total number of health check errors',
            ['database', 'error_type']
        )

        self.tracer = trace.get_tracer(__name__)

    def record_request(
        self,
        method: str,
        duration: float,
        status: str = "success"
    ) -> None:
        """Record request metrics.
        
        Args:
            method: Request method
            duration: Request duration in seconds
            status: Request status (success/error)
        """
        self.request_counter.labels(method=method, status=status).inc()
        self.request_latency.labels(method=method, status=status).observe(duration)

    def record_query_processing(
        self,
        stage: str,
        duration: float,
        status: str = "success"
    ) -> None:
        """Record query processing metrics.
        
        Args:
            stage: Processing stage
            duration: Processing duration in seconds
            status: Processing status
        """
        self.query_processing_latency.labels(
            stage=stage,
            status=status
        ).observe(duration)

    def record_query_expansion(
        self,
        original_length: int,
        expanded_length: int
    ) -> None:
        """Record query expansion metrics.
        
        Args:
            original_length: Original query length
            expanded_length: Expanded query length
        """
        if original_length > 0:
            ratio = expanded_length / original_length
            self.query_expansion_ratio.observe(ratio)

    def record_retrieval(
        self,
        method: str,
        duration: float,
        success: bool = True,
        retriever_type: str = "default",
        error_type: Optional[str] = None,
        status_code: str = "200"
    ) -> None:
        """Record retrieval metrics.
        
        Args:
            method: Retrieval method
            duration: Retrieval duration in seconds
            success: Whether retrieval was successful
            retriever_type: Type of retriever used
            error_type: Type of error if any
            status_code: HTTP status code or equivalent
        """
        status = "success" if success else "error"
        labels = {
            "method": method,
            "status": status,
            "retriever_type": retriever_type
        }

        self.retrieval_latency.labels(**labels).observe(duration)

        if not success and error_type:
            self.error_counter.labels(
                component="retrieval",
                error_type=error_type,
                severity="error"
            ).inc()

        with self.tracer.start_span("retrieval_metrics") as span:
            span.set_attribute("method", method)
            span.set_attribute("duration", duration)
            span.set_attribute("success", success)
            span.set_attribute("retriever_type", retriever_type)
            span.set_attribute("status_code", status_code)
            if error_type:
                span.set_attribute("error_type", error_type)
            span.set_status(
                Status(StatusCode.OK if success else StatusCode.ERROR)
            )

    def record_cache_operation(
        self,
        operation: str,
        cache_level: str,
        duration: float,
        success: bool = True
    ) -> None:
        """Record cache operation metrics.
        
        Args:
            operation: Cache operation (get/set)
            cache_level: Cache level (L1/L2)
            duration: Operation duration in seconds
            success: Whether operation was successful
        """
        status = "success" if success else "error"
        self.cache_latency.labels(
            operation=operation,
            cache_level=cache_level,
            status=status
        ).observe(duration)

    def record_cache_hit(
        self,
        cache_level: str,
        cache_type: str = "default"
    ) -> None:
        """Record cache hit.
        
        Args:
            cache_level: Cache level (L1/L2)
            cache_type: Type of cache
        """
        self.cache_hits.labels(
            cache_level=cache_level,
            cache_type=cache_type
        ).inc()

    def record_cache_miss(
        self,
        cache_level: str,
        cache_type: str = "default"
    ) -> None:
        """Record cache miss.
        
        Args:
            cache_level: Cache level (L1/L2)
            cache_type: Type of cache
        """
        self.cache_misses.labels(
            cache_level=cache_level,
            cache_type=cache_type
        ).inc()

    def record_db_query(
        self,
        database: str,
        operation: str,
        duration: float,
        success: bool = True,
        error_type: Optional[str] = None
    ) -> None:
        """Record database query metrics.
        
        Args:
            database: Database name
            operation: Query operation
            duration: Query duration in seconds
            success: Whether query was successful
            error_type: Type of error if any
        """
        status = "success" if success else "error"
        self.db_query_latency.labels(
            database=database,
            operation=operation,
            status=status
        ).observe(duration)

        if not success and error_type:
            self.db_errors.labels(
                database=database,
                error_type=error_type
            ).inc()

    def update_db_connections(
        self,
        database: str,
        connections: int
    ) -> None:
        """Update database connection count.
        
        Args:
            database: Database name
            connections: Number of active connections
        """
        self.db_connection_pool.labels(database=database).set(connections)

    def record_model_inference(
        self,
        model_type: str,
        duration: float,
        batch_size: int,
        success: bool = True
    ) -> None:
        """Record model inference metrics.
        
        Args:
            model_type: Type of model
            duration: Inference duration in seconds
            batch_size: Batch size used
            success: Whether inference was successful
        """
        status = "success" if success else "error"
        self.model_inference_latency.labels(
            model_type=model_type,
            status=status
        ).observe(duration)
        self.model_batch_size.labels(model_type=model_type).observe(batch_size)

    def update_resource_usage(
        self,
        component: str,
        memory_bytes: float,
        cpu_percent: float
    ) -> None:
        """Update resource usage metrics.
        
        Args:
            component: Component name
            memory_bytes: Memory usage in bytes
            cpu_percent: CPU usage percentage
        """
        self.memory_usage.labels(component=component).set(memory_bytes)
        self.cpu_usage.labels(component=component).set(cpu_percent)

    def record_error(
        self,
        component: str,
        error_type: str,
        severity: str = "error"
    ) -> None:
        """Record error metrics.
        
        Args:
            component: Component where error occurred
            error_type: Type of error
            severity: Error severity
        """
        self.error_counter.labels(
            component=component,
            error_type=error_type,
            severity=severity
        ).inc()

        if severity in ["error", "critical"]:
            logging.error(
                "Error in component %s: %s (severity: %s)",
                component,
                error_type,
                severity
            )

    def record_query_complexity(self, score: float) -> None:
        """Record query complexity score.
        
        Args:
            score: Complexity score
        """
        self.query_complexity.observe(score)

    def record_result_relevance(self, score: float) -> None:
        """Record result relevance score.
        
        Args:
            score: Relevance score
        """
        self.result_relevance.observe(score)

    def collect_all_metrics(self) -> Dict[str, Any]:
        """Collect all current metrics.
        
        Returns:
            Dictionary containing all current metric values
        """
        # This is a placeholder implementation
        # In a real system, you would collect actual values from Prometheus
        return {
            "requests": {
                "total": self.request_counter._value.sum(),
                "latency": self.request_latency._sum.sum()
            },
            "cache": {
                "hits": self.cache_hits._value.sum(),
                "misses": self.cache_misses._value.sum()
            },
            "retrieval": {
                "latency": self.retrieval_latency._sum.sum()
            },
            "errors": self.error_counter._value.sum()
        }

class MetricsManager:
    """Singleton metrics manager for the RAG service."""
    
    _instance = None
    
    @classmethod
    def get_instance(cls) -> 'MetricsManager':
        """Get or create the singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def __init__(self):
        """Initialize the metrics manager."""
        if MetricsManager._instance is not None:
            raise RuntimeError("Use get_instance() to access MetricsManager")
        self.collector = MetricsCollector()
        logger.info("metrics_manager_initialized")

    def __getattr__(self, name):
        """Delegate unknown attributes to collector."""
        return getattr(self.collector, name)

    def reset_metrics(self) -> None:
        """Reset all metrics to initial state."""
        self.collector = MetricsCollector()

    def get_collector(self) -> MetricsCollector:
        """Get the underlying metrics collector."""
        return self.collector

    def with_metrics(self, component: str, method: str):
        """Decorator to automatically record metrics for a function.
        
        Args:
            component: Component name
            method: Method name
        """
        def decorator(func):
            import inspect
            if inspect.iscoroutinefunction(func):
                async def wrapper(*args, **kwargs):
                    start_time = time.time()
                    error = None
                    try:
                        result = await func(*args, **kwargs)
                        return result
                    except Exception as e:
                        error = e
                        self.collector.record_error(
                            component=component,
                            error_type=type(e).__name__,
                            severity="error"
                        )
                        raise
                    finally:
                        duration = time.time() - start_time
                        self.collector.record_request(
                            method=method,
                            duration=duration,
                            status="error" if error else "success"
                        )
                return wrapper
            else:
                def wrapper(*args, **kwargs):
                    start_time = time.time()
                    error = None
                    try:
                        result = func(*args, **kwargs)
                        return result
                    except Exception as e:
                        error = e
                        self.collector.record_error(
                            component=component,
                            error_type=type(e).__name__,
                            severity="error"
                        )
                        raise
                    finally:
                        duration = time.time() - start_time
                        self.collector.record_request(
                            method=method,
                            duration=duration,
                            status="error" if error else "success"
                        )
                return wrapper
            return decorator
        return decorator
        return decorator 