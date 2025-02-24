# RAG Service Local Development Setup Report

## 1. System Requirements

### Base Requirements

- Python 3.11 or higher
- pip (Python package manager)
- Git (for version control)
- At least 8GB RAM (recommended due to ML models)
- 20GB free disk space

### Required External Services

1. **Elasticsearch 8.11.1**
   - For vector search and document storage
   - Requires Java Runtime Environment (JRE)
   - Minimum 4GB RAM allocated

2. **MongoDB 6.0**
   - For metadata and document storage
   - Minimum 2GB RAM recommended

3. **Redis 7.2**
   - For caching and rate limiting
   - Minimum 512MB RAM

4. **Jaeger 1.50**
   - For distributed tracing
   - Optional for local development

## 2. Installation Steps

### Step 1: Python Environment Setup

```bash
# Create virtual environment
python -m venv venv

# Activate virtual environment
# Windows:
venv\Scripts\activate
# Unix/MacOS:
source venv/bin/activate

# Install dependencies
pip install -e .
```

### Step 2: External Services Installation

#### Elasticsearch

- Download and install Elasticsearch 8.11.1
- Configure basic security settings
- Set up development credentials

#### MongoDB

- Install MongoDB Community Edition 6.0
- Create database and user accounts
- Set up authentication

#### Redis

- Install Redis 7.2
- Configure for local access
- Set up persistence if needed

#### Jaeger (Optional)

- Install Jaeger All-in-One
- Configure collectors and UI

## 3. Configuration

### Environment Variables
Create a `.env` file with:

```env
# OpenAI API Configuration
OPENAI_API_KEY=your_api_key_here

# MongoDB Configuration
MONGO_ROOT_USER=admin
MONGO_ROOT_PASSWORD=your_password

# Service Configuration
RAG_SERVICE_ENV=development
RAG_SERVICE_LOG_LEVEL=DEBUG
RAG_SERVICE_PORT=8000
RAG_SERVICE_METRICS_PORT=9090

# Database Connection URLs
ELASTICSEARCH_URL=http://localhost:9200
MONGODB_URL=mongodb://localhost:27017
REDIS_URL=redis://localhost:6379
```

### Application Configuration

Update `config/config.yaml` with local development settings:

- Service endpoints
- Model configurations
- Monitoring settings

## 4. Running the Service

### Start External Services

1. Start Elasticsearch:

```bash
elasticsearch
```

2. Start MongoDB:

```bash
mongod
```

3. Start Redis:

```bash
redis-server
```

4. Start Jaeger (optional):

```bash
jaeger-all-in-one
```

### Start the RAG Service

```bash
python -m uvicorn rag_service.app:app --host 0.0.0.0 --port 8000 --reload
```

## 5. Verification Steps

### Health Checks

1. RAG Service API: `http://localhost:8000/health`
2. Elasticsearch: `http://localhost:9200/_cluster/health`
3. MongoDB: Test connection via MongoDB Compass
4. Redis: Test using redis-cli

### Service URLs

- Main API: `http://localhost:8000`
- API Documentation: `http://localhost:8000/docs`
- Metrics: `http://localhost:9090`
- Jaeger UI: `http://localhost:16686`

## 6. Development Tools (Recommended)

- VS Code or PyCharm for Python development
- MongoDB Compass for database management
- Kibana for Elasticsearch management
- Redis Commander for Redis management
- Postman or Insomnia for API testing

## 7. Monitoring and Debugging

### Logging

- Logs directory: `./logs`
- Log level: Configurable via environment variables
- Log format: JSON for production, human-readable for development

### Metrics

- Prometheus metrics at `:9090/metrics`
- Basic system metrics
- Application-specific metrics

### Tracing

- Jaeger UI for distributed tracing
- OpenTelemetry integration
- Performance monitoring

## 8. Common Issues and Solutions

1. Port Conflicts
   - Check for services using required ports
   - Configure alternative ports if needed

2. Memory Issues
   - Adjust JVM heap size for Elasticsearch
   - Monitor Redis memory usage
   - Check MongoDB memory requirements

3. Permission Issues
   - Ensure proper file permissions
   - Check service user permissions
   - Verify database credentials
