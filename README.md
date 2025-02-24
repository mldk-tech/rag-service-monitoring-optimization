# RAG Service with Monitoring and Optimization

A production-ready Retrieval Augmented Generation (RAG) service with comprehensive monitoring, optimization, and observability features.

## Features

- **Advanced RAG Pipeline**: Sophisticated retrieval system with multiple backends (Elasticsearch, Vector DB, Graph DB)
- **Performance Optimization**: Automated performance tuning with metrics-driven optimization
- **Comprehensive Monitoring**: Prometheus metrics, OpenTelemetry tracing, and health checks
- **Production-Ready**: Built with security, scalability, and reliability in mind
- **Multi-Level Caching**: Tiered caching system with adaptive optimization
- **Query Processing**: Advanced query analysis and expansion capabilities
- **Observability**: Detailed logging, metrics, and tracing for every component

## Architecture

The service is built with a modular architecture focusing on:

- Configuration Management
- Database Management (Elasticsearch, MongoDB, Redis)
- Retrieval System
- Query Processing
- Performance Optimization
- Monitoring and Metrics
- Health Monitoring
- Security Implementation

## Requirements

- Python 3.8+
- Elasticsearch 8.0+
- MongoDB 5.0+
- Redis 6.0+
- Docker (for containerization)
- Kubernetes (for orchestration)

## Installation

1. Clone the repository:
```bash
git clone https://github.com/your-org/rag-service.git
cd rag-service
```

2. Install dependencies:
```bash
pip install -e ".[dev]"
```

3. Set up environment variables:
```bash
cp .env.example .env
# Edit .env with your configuration
```

## Configuration

The service uses a hierarchical configuration system:

1. Default configuration in `config/default.yaml`
2. Environment-specific configuration in `config/{env}.yaml`
3. Environment variables (prefixed with `RAG_`)
4. Runtime configuration via API

See `config/default.yaml` for all available options.

## Usage

1. Start the service:
```bash
python -m rag_service
```

2. Use the API:
```python
import httpx

async with httpx.AsyncClient() as client:
    response = await client.post(
        "http://localhost:8000/api/v1/retrieve",
        json={
            "query": "What are the main exports of Israel?",
            "options": {"top_k": 5}
        }
    )
    results = response.json()
```

## Development

1. Set up development environment:
```bash
pip install -e ".[dev]"
```

2. Run tests:
```bash
pytest
```

3. Format code:
```bash
black .
isort .
```

4. Run linters:
```bash
ruff check .
mypy .
```

## Monitoring

The service exposes:

- Prometheus metrics at `/metrics`
- Health check at `/health`
- OpenTelemetry traces to configured endpoint

## Deployment

1. Build Docker image:
```bash
docker build -t rag-service .
```

2. Deploy to Kubernetes:
```bash
kubectl apply -f k8s/
```

See `deployment/` directory for detailed deployment configurations.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run tests and linters
5. Submit a pull request

## License

MIT License - see LICENSE file for details