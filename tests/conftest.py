"""Shared test fixtures and configuration."""

import os
import pytest
import asyncio
from datetime import datetime
from typing import Dict, Any

from rag_service.core.database import DatabaseManager, Document
from rag_service.core.retrieval import RetrievalManager
from rag_service.core.generation import GenerationManager
from rag_service.core.optimization import OptimizationManager
from rag_service.security.security_manager import SecurityManager
from rag_service.monitoring.metrics import MetricsManager

# Test Configuration
@pytest.fixture
def test_config() -> Dict[str, Any]:
    """Create base test configuration."""
    return {
        "application": {
            "name": "rag-service-test",
            "version": "1.0.0",
            "environment": "test",
            "logging": {
                "level": "DEBUG",
                "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            }
        },
        "database": {
            "elasticsearch": {
                "hosts": ["localhost:9200"],
                "index_name": "test_documents",
                "timeout": 30,
                "max_connections": 10
            },
            "mongodb": {
                "uri": "mongodb://localhost:27017",
                "database": "test_rag_service",
                "collection": "test_documents",
                "max_connections": 10
            },
            "redis": {
                "host": "localhost",
                "port": 6379,
                "db": 1,
                "max_connections": 10
            }
        },
        "retrieval": {
            "strategies": [
                {
                    "name": "semantic",
                    "enabled": True,
                    "weight": 1.0,
                    "params": {
                        "model_name": "all-MiniLM-L6-v2",
                        "similarity_threshold": 0.7
                    }
                },
                {
                    "name": "keyword",
                    "enabled": True,
                    "weight": 0.5,
                    "params": {
                        "min_score": 0.3
                    }
                }
            ],
            "cache": {
                "enabled": True,
                "ttl": 60
            }
        },
        "generation": {
            "models": {
                "gpt-3.5-turbo": {
                    "api_url": "https://api.openai.com/v1/chat/completions",
                    "api_key": "${OPENAI_API_KEY}",
                    "max_tokens": 1000,
                    "temperature": 0.7
                }
            },
            "default_model": "gpt-3.5-turbo"
        },
        "optimization": {
            "strategies": [
                {
                    "name": "cache_optimization",
                    "enabled": True,
                    "schedule": "hourly"
                }
            ],
            "thresholds": {
                "latency": 1.0,
                "error_rate": 0.01
            }
        },
        "security": {
            "authentication": {
                "enabled": True,
                "token_header": "Authorization",
                "cache_ttl": 300
            },
            "rate_limiting": {
                "enabled": True,
                "default_limit": 100,
                "window_size": 3600
            }
        },
        "monitoring": {
            "metrics": {
                "enabled": True,
                "host": "localhost",
                "port": 9090
            }
        }
    }

# Test Data
@pytest.fixture
def test_documents() -> list[Document]:
    """Create test documents."""
    return [
        Document(
            id="1",
            content="Python is a high-level programming language.",
            metadata={
                "source": "test",
                "category": "programming"
            },
            embedding=None,
            created_at=datetime.now(),
            updated_at=datetime.now()
        ),
        Document(
            id="2",
            content="Machine learning is a subset of artificial intelligence.",
            metadata={
                "source": "test",
                "category": "ai"
            },
            embedding=None,
            created_at=datetime.now(),
            updated_at=datetime.now()
        ),
        Document(
            id="3",
            content="FastAPI is a modern web framework for Python.",
            metadata={
                "source": "test",
                "category": "programming"
            },
            embedding=None,
            created_at=datetime.now(),
            updated_at=datetime.now()
        )
    ]

# Core Components
@pytest.fixture
async def database_manager(test_config):
    """Create and initialize database manager."""
    manager = DatabaseManager(test_config["database"])
    await manager.connect()
    yield manager
    await manager.close()

@pytest.fixture
def metrics_manager(test_config):
    """Create metrics manager."""
    return MetricsManager(test_config["monitoring"])

@pytest.fixture
def security_manager(test_config, metrics_manager):
    """Create security manager."""
    return SecurityManager(metrics_manager, test_config["security"])

@pytest.fixture
def retrieval_manager(database_manager, metrics_manager, test_config):
    """Create retrieval manager."""
    return RetrievalManager(
        database_manager,
        metrics_manager,
        test_config["retrieval"]
    )

@pytest.fixture
async def generation_manager(metrics_manager, test_config):
    """Create and initialize generation manager."""
    manager = GenerationManager(
        metrics_manager,
        test_config["generation"]
    )
    await manager.initialize()
    yield manager
    await manager.close()

@pytest.fixture
def optimization_manager(
    retrieval_manager,
    generation_manager,
    metrics_manager,
    test_config
):
    """Create optimization manager."""
    return OptimizationManager(
        retrieval_manager,
        generation_manager,
        metrics_manager,
        test_config["optimization"]
    )

# Test Utilities
@pytest.fixture
def auth_headers():
    """Create authentication headers."""
    return {
        "Authorization": "Bearer test_token"
    }

@pytest.fixture
def admin_headers():
    """Create admin authentication headers."""
    return {
        "Authorization": "Bearer admin_token",
        "X-Role": "admin"
    }

# Test Environment Setup
def pytest_configure(config):
    """Configure test environment."""
    os.environ["ENVIRONMENT"] = "test"
    os.environ["LOG_LEVEL"] = "DEBUG"
    os.environ["DISABLE_EXTERNAL_CALLS"] = "true"

# Async Support
@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close() 