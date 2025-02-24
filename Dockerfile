FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -u 1000 rag_user

# Copy application files
COPY pyproject.toml ./
COPY README.md ./
COPY rag_service ./rag_service
COPY config /app/config

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir . && \
    pip install --no-cache-dir "uvicorn[standard]" && \
    pip install --no-cache-dir \
        opentelemetry-api \
        opentelemetry-sdk \
        opentelemetry-exporter-otlp \
        opentelemetry-instrumentation \
        opentelemetry-instrumentation-fastapi \
        opentelemetry-instrumentation-requests \
        opentelemetry-instrumentation-elasticsearch \
        opentelemetry-instrumentation-pymongo \
        opentelemetry-instrumentation-redis \
        redis>=4.2.0

# Replace aioredis with redis.asyncio in your code
RUN find /app -type f -name "*.py" -exec sed -i 's/import aioredis/from redis import asyncio as aioredis/g' {} \;
RUN find /app -type f -name "*.py" -exec sed -i 's/from aioredis/from redis.asyncio/g' {} \;

# Create necessary directories and set permissions
RUN mkdir -p logs && \
    chown -R rag_user:rag_user /app

# Switch to non-root user
USER rag_user

# Set environment variables
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    RAG_CONFIG_PATH=/app/config/config.yaml

# Expose ports
EXPOSE 8000 9090

# Health check
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run application
CMD ["python", "-m", "uvicorn", "rag_service.app:app", "--host", "0.0.0.0", "--port", "8000"]