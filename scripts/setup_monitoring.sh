#!/bin/bash

# Setup script for RAG Service monitoring environment
set -e

echo "Setting up RAG Service monitoring environment..."

# Create necessary directories
mkdir -p monitoring/prometheus
mkdir -p monitoring/grafana/dashboards
mkdir -p monitoring/grafana/provisioning/datasources
mkdir -p monitoring/grafana/provisioning/dashboards

# Check if prometheus.yml exists
if [ ! -f "monitoring/prometheus/prometheus.yml" ]; then
    echo "Creating Prometheus configuration..."
    cp config/monitoring/prometheus.yml monitoring/prometheus/prometheus.yml
fi

# Check if Grafana datasource configuration exists
if [ ! -f "monitoring/grafana/provisioning/datasources/datasource.yml" ]; then
    echo "Creating Grafana datasource configuration..."
    cat > monitoring/grafana/provisioning/datasources/datasource.yml << EOF
apiVersion: 1

datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
    editable: false
EOF
fi

# Check if Grafana dashboard provisioning configuration exists
if [ ! -f "monitoring/grafana/provisioning/dashboards/dashboards.yml" ]; then
    echo "Creating Grafana dashboard provisioning configuration..."
    cat > monitoring/grafana/provisioning/dashboards/dashboards.yml << EOF
apiVersion: 1

providers:
  - name: 'Default'
    orgId: 1
    folder: ''
    type: file
    disableDeletion: false
    updateIntervalSeconds: 10
    allowUiUpdates: true
    options:
      path: /var/lib/grafana/dashboards
      foldersFromFilesStructure: true
EOF
fi

# Check if OpenTelemetry Collector configuration exists
if [ ! -f "monitoring/otel-collector-config.yaml" ]; then
    echo "Creating OpenTelemetry Collector configuration..."
    cp config/monitoring/otel-collector-config.yaml monitoring/otel-collector-config.yaml
fi

# Check if docker-compose.yml exists
if [ ! -f "docker-compose.yml" ]; then
    echo "Error: docker-compose.yml not found. Please make sure it exists."
    exit 1
fi

echo "Checking Docker installation..."
if ! command -v docker &> /dev/null; then
    echo "Docker is not installed. Please install Docker and Docker Compose."
    exit 1
fi

if ! command -v docker-compose &> /dev/null; then
    echo "Docker Compose is not installed. Please install Docker Compose."
    exit 1
fi

echo "Setting up environment variables..."
if [ ! -f ".env" ]; then
    echo "Creating .env file with default values..."
    cat > .env << EOF
# OpenTelemetry Configuration
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
OTEL_RESOURCE_ATTRIBUTES=service.name=rag-service,deployment.environment=development
OTEL_TRACES_SAMPLER=parentbased_traceidratio
OTEL_TRACES_SAMPLER_ARG=1.0
OTEL_METRICS_EXPORTER=otlp
OTEL_LOGS_EXPORTER=otlp

# Elasticsearch Configuration
ELASTICSEARCH_HOSTS=http://elasticsearch:9200
ELASTICSEARCH_USERNAME=elastic
ELASTICSEARCH_PASSWORD=changeme

# MongoDB Configuration
MONGODB_URI=mongodb://mongodb:27017/rag_service

# Redis Configuration
REDIS_HOST=redis
REDIS_PORT=6379

# API Configuration
API_KEY=dev_api_key
EOF
fi

echo "All configurations are set up!"
echo "To start the monitoring environment, run: docker-compose up -d"
echo "To view the Grafana dashboard, open: http://localhost:3000"
echo "To view the Jaeger UI, open: http://localhost:16686"
echo "To view the Prometheus UI, open: http://localhost:9091"

echo "Setup complete!" 