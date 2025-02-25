# OpenTelemetry Integration for RAG Service

This document explains how to use the OpenTelemetry integration in the RAG service for comprehensive monitoring and observability.

## Overview

The RAG service includes full OpenTelemetry integration for distributed tracing, metrics, and logging. This provides:

- **Distributed Tracing**: Track requests as they flow through the system
- **Metrics Collection**: Monitor performance and resource usage
- **Structured Logging**: Correlate logs with traces and metrics
- **Visualization**: View data in Grafana, Jaeger, and other tools

## Components

The OpenTelemetry integration consists of:

1. **OpenTelemetry SDK**: Integrated into the RAG service
2. **OpenTelemetry Collector**: Collects, processes, and exports telemetry data
3. **Prometheus**: Stores metrics
4. **Grafana**: Visualizes metrics and traces
5. **Jaeger**: Visualizes distributed traces

## Configuration

### Environment Variables

The following environment variables can be used to configure OpenTelemetry:

```
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
OTEL_RESOURCE_ATTRIBUTES=service.name=rag-service,deployment.environment=development
OTEL_TRACES_SAMPLER=parentbased_traceidratio
OTEL_TRACES_SAMPLER_ARG=1.0
OTEL_METRICS_EXPORTER=otlp
OTEL_LOGS_EXPORTER=otlp
```

### Configuration File

The OpenTelemetry configuration is defined in `config/telemetry.yaml`. This file includes:

- Sampling configuration
- Resource attributes
- Instrumentation settings
- Batch processor settings
- Propagation settings
- Metrics configuration

## Using OpenTelemetry in Your Code

### Manual Tracing

You can create spans manually in your code:

```python
from opentelemetry import trace

# Get a tracer
tracer = trace.get_tracer("my_component")

# Create a span
with tracer.start_as_current_span("my_operation") as span:
    # Add attributes to the span
    span.set_attribute("attribute_key", "attribute_value")
    
    # Add events to the span
    span.add_event("event_name", {"event_attribute": "value"})
    
    # Set status
    span.set_status(trace.Status(trace.StatusCode.OK))
    
    # Perform your operation
    result = perform_operation()
```

### Manual Metrics

You can record metrics manually:

```python
from opentelemetry import metrics

# Get a meter
meter = metrics.get_meter("my_component")

# Create a counter
counter = meter.create_counter("my_counter", description="Description of my counter")

# Record a value
counter.add(1, {"dimension": "value"})

# Create a histogram
histogram = meter.create_histogram("my_histogram", description="Description of my histogram")

# Record a value
histogram.record(42.0, {"dimension": "value"})
```

### Using the Metrics Manager

The RAG service includes a `MetricsManager` that simplifies recording metrics:

```python
from rag_service.monitoring.metrics import MetricsManager

# Get the metrics manager
metrics_manager = MetricsManager.get_instance()

# Record a request
metrics_manager.record_request(
    method="get_documents",
    status="success",
    component="retrieval",
    latency=0.42
)

# Record token usage
metrics_manager.record_token_usage(
    model="gpt-4",
    token_type="input",
    count=150
)

# Use the context manager to measure latency
with metrics_manager.measure_latency(
    metric_type="retrieval",
    strategy="semantic",
    component="retrieval"
):
    # Your code here
    result = perform_retrieval()

# Use the decorator to instrument a method
@metrics_manager.instrument(component="retrieval")
async def retrieve_documents(query):
    # Your code here
    return documents
```

## Viewing Telemetry Data

### Traces

Traces can be viewed in Jaeger at http://localhost:16686

### Metrics

Metrics can be viewed in:
- Prometheus: http://localhost:9091
- Grafana: http://localhost:3000

## Dashboards

Pre-configured Grafana dashboards are available for:

1. **RAG Service Overview**: General service health and performance
2. **Retrieval Performance**: Detailed metrics for document retrieval
3. **Generation Performance**: Detailed metrics for text generation
4. **Database Performance**: Metrics for database operations
5. **API Performance**: Metrics for API endpoints

## Troubleshooting

### Common Issues

1. **No traces appearing in Jaeger**:
   - Check that the OpenTelemetry Collector is running
   - Verify the OTEL_EXPORTER_OTLP_ENDPOINT environment variable
   - Check the sampling rate (OTEL_TRACES_SAMPLER_ARG)

2. **No metrics in Prometheus**:
   - Check that the OpenTelemetry Collector is running
   - Verify the Prometheus scrape configuration
   - Check the OpenTelemetry Collector configuration

3. **High cardinality metrics**:
   - Review your labels/dimensions to ensure they don't have unbounded values
   - Use the filter processor in the OpenTelemetry Collector to limit metrics

## Best Practices

1. **Use semantic conventions**: Follow the [OpenTelemetry semantic conventions](https://opentelemetry.io/docs/specs/semconv/) for naming and attributes.

2. **Add context to spans**: Include relevant attributes and events in spans to make debugging easier.

3. **Use appropriate sampling**: Adjust sampling rates based on traffic volume and importance.

4. **Monitor the monitoring**: Keep an eye on the resource usage of your telemetry components.

5. **Correlate across signals**: Use trace IDs in logs and metrics to correlate data.

## External Services Integration

The OpenTelemetry Collector is configured to export data to:

1. **Prometheus**: For metrics storage and querying
2. **Jaeger**: For trace visualization
3. **Honeycomb.io**: For advanced observability (if configured)

To enable Honeycomb.io integration, set the `HONEYCOMB_API_KEY` environment variable. 