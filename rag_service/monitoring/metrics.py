"""Monitoring and metrics module for comprehensive observability of the RAG service."""

from typing import Dict, Optional, Any
import time
from dataclasses import dataclass
from prometheus_client import Counter, Histogram, Gauge, Summary
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
import logging

@dataclass
class MetricsLabels:
    """Common metric labels."""
    method: str
    status: str
    component: str

class MetricsCollector:
    """Centralized metrics collection for the RAG service."""

    def __init__(self):
        """Initialize metrics collectors."""
        # Request metrics
        self.request_counter = Counter(
            'rag_requests_total',
            'Total number of requests',
            ['method', 'status']
        )
        self.request_latency = Histogram(
            'rag_request_duration_seconds',
            'Request duration in seconds',
            ['method', 'status'],
            buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
        )

        # Query processing metrics
        self.query_processing_latency = Histogram(
            'rag_query_processing_duration_seconds',
            'Query processing duration in seconds',
            ['stage', 'status'],
            buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0)
        )
        self.query_expansion_ratio = Histogram(
            'rag_query_expansion_ratio',
            'Ratio of expanded query length to original query length',
            buckets=(1.0, 1.5, 2.0, 3.0, 5.0)
        )

        # Retrieval metrics
        self.retrieval_latency = Histogram(
            'rag_retrieval_duration_seconds',
            'Retrieval duration in seconds',
            ['method', 'status', 'retriever_type'],
            buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
        )
        self.retrieval_results_count = Histogram(
            'rag_retrieval_results_count',
            'Number of results returned by retrieval',
            ['retriever_type'],
            buckets=(1, 5, 10, 20, 50, 100)
        )

        # Cache metrics
        self.cache_hits = Counter(
            'rag_cache_hits_total',
            'Total number of cache hits',
            ['cache_level', 'cache_type']
        )
        self.cache_misses = Counter(
            'rag_cache_misses_total',
            'Total number of cache misses',
            ['cache_level', 'cache_type']
        )
        self.cache_latency = Histogram(
            'rag_cache_operation_duration_seconds',
            'Cache operation duration in seconds',
            ['operation', 'cache_level', 'status'],
            buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1)
        )

        # Database metrics
        self.db_query_latency = Histogram(
            'rag_db_query_duration_seconds',
            'Database query duration in seconds',
            ['database', 'operation', 'status'],
            buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5)
        )
        self.db_connection_pool = Gauge(
            'rag_db_connections_active',
            'Number of active database connections',
            ['database']
        )
        self.db_errors = Counter(
            'rag_db_errors_total',
            'Total number of database errors',
            ['database', 'error_type']
        )

        # Model metrics
        self.model_inference_latency = Histogram(
            'rag_model_inference_duration_seconds',
            'Model inference duration in seconds',
            ['model_type', 'status'],
            buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5)
        )
        self.model_batch_size = Histogram(
            'rag_model_batch_size',
            'Model batch size',
            ['model_type'],
            buckets=(1, 4, 8, 16, 32, 64)
        )

        # Resource metrics
        self.memory_usage = Gauge(
            'rag_memory_usage_bytes',
            'Memory usage in bytes',
            ['component']
        )
        self.cpu_usage = Gauge(
            'rag_cpu_usage_percent',
            'CPU usage percentage',
            ['component']
        )

        # Error metrics
        self.error_counter = Counter(
            'rag_errors_total',
            'Total number of errors',
            ['component', 'error_type', 'severity']
        )

        # Business metrics
        self.query_complexity = Histogram(
            'rag_query_complexity_score',
            'Query complexity score',
            buckets=(1, 2, 3, 5, 8, 13)
        )
        self.result_relevance = Summary(
            'rag_result_relevance_score',
            'Result relevance score'
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