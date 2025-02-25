"""Integration tests for the retrieval component."""

import pytest
import asyncio
from datetime import datetime
import os
import json
from typing import Dict, Any, List

from rag_service.core.retrieval import (
    RetrievalManager,
    RetrievalStrategy,
    RetrievalMode,
    RetrievalError
)
from rag_service.core.database import DatabaseManager, Document
from rag_service.monitoring.metrics import MetricsManager

# Test data
TEST_DOCUMENTS = [
    Document(
        id="doc1",
        content="This is a test document about artificial intelligence and machine learning.",
        metadata={"source": "test", "category": "ai"},
        embedding=[0.1] * 768,
        created_at=datetime.now(),
        updated_at=datetime.now()
    ),
    Document(
        id="doc2",
        content="Natural language processing is a subfield of artificial intelligence.",
        metadata={"source": "test", "category": "nlp"},
        embedding=[0.2] * 768,
        created_at=datetime.now(),
        updated_at=datetime.now()
    ),
    Document(
        id="doc3",
        content="Retrieval augmented generation combines search with text generation.",
        metadata={"source": "test", "category": "rag"},
        embedding=[0.3] * 768,
        created_at=datetime.now(),
        updated_at=datetime.now()
    ),
    Document(
        id="doc4",
        content="Vector databases are optimized for similarity search operations.",
        metadata={"source": "test", "category": "databases"},
        embedding=[0.4] * 768,
        created_at=datetime.now(),
        updated_at=datetime.now()
    ),
    Document(
        id="doc5",
        content="Embeddings represent text as dense vectors in a high-dimensional space.",
        metadata={"source": "test", "category": "embeddings"},
        embedding=[0.5] * 768,
        created_at=datetime.now(),
        updated_at=datetime.now()
    )
]

@pytest.fixture
async def db_manager():
    """Create and initialize a database manager for testing."""
    # Load test configuration
    config_path = os.environ.get("TEST_CONFIG_PATH", "config/test_config.yaml")
    with open(config_path, "r") as f:
        config = json.load(f)
    
    # Initialize database manager
    db_manager = DatabaseManager(config["database"])
    await db_manager.connect()
    
    # Create test index and insert test documents
    index_name = "test_retrieval_index"
    for doc in TEST_DOCUMENTS:
        await db_manager.index_document(
            index=index_name,
            document=doc
        )
    
    # Wait for indexing to complete
    await asyncio.sleep(1)
    
    yield db_manager
    
    # Cleanup
    try:
        await db_manager.es_client.indices.delete(index=index_name)
    except Exception:
        pass
    await db_manager.close()

@pytest.fixture
def metrics_manager():
    """Create a metrics manager for testing."""
    return MetricsManager.get_instance()

@pytest.fixture
async def retrieval_manager(db_manager, metrics_manager):
    """Create and initialize a retrieval manager for testing."""
    # Load test configuration
    config_path = os.environ.get("TEST_CONFIG_PATH", "config/test_config.yaml")
    with open(config_path, "r") as f:
        config = json.load(f)
    
    # Initialize retrieval manager
    retrieval_manager = RetrievalManager(
        db_manager,
        metrics_manager,
        config["retrieval"]
    )
    
    yield retrieval_manager

@pytest.mark.asyncio
async def test_semantic_search(retrieval_manager):
    """Test semantic search functionality."""
    # Perform semantic search
    results = await retrieval_manager.search(
        query="What is artificial intelligence?",
        strategy=RetrievalStrategy.SEMANTIC,
        top_k=3
    )
    
    # Verify results
    assert len(results) > 0
    assert all(isinstance(result.document, Document) for result in results)
    assert all(0 <= result.score <= 1.0 for result in results)
    assert all(result.strategy == RetrievalStrategy.SEMANTIC for result in results)

@pytest.mark.asyncio
async def test_keyword_search(retrieval_manager):
    """Test keyword search functionality."""
    # Perform keyword search
    results = await retrieval_manager.search(
        query="artificial intelligence",
        strategy=RetrievalStrategy.KEYWORD,
        top_k=3
    )
    
    # Verify results
    assert len(results) > 0
    assert all(isinstance(result.document, Document) for result in results)
    assert all(0 <= result.score <= 1.0 for result in results)
    assert all(result.strategy == RetrievalStrategy.KEYWORD for result in results)
    
    # Check that documents containing the keywords are returned
    assert any("artificial intelligence" in result.document.content.lower() for result in results)

@pytest.mark.asyncio
async def test_hybrid_search(retrieval_manager):
    """Test hybrid search functionality."""
    # Perform hybrid search
    results = await retrieval_manager.search(
        query="vector similarity search",
        strategy=RetrievalStrategy.HYBRID,
        top_k=3
    )
    
    # Verify results
    assert len(results) > 0
    assert all(isinstance(result.document, Document) for result in results)
    assert all(0 <= result.score <= 1.0 for result in results)
    assert all(result.strategy == RetrievalStrategy.HYBRID for result in results)

@pytest.mark.asyncio
async def test_search_with_filters(retrieval_manager):
    """Test search with metadata filters."""
    # Perform search with filters
    results = await retrieval_manager.search(
        query="embeddings",
        strategy=RetrievalStrategy.SEMANTIC,
        filters={"category": "embeddings"},
        top_k=3
    )
    
    # Verify results
    assert len(results) > 0
    assert all(result.document.metadata.get("category") == "embeddings" for result in results)

@pytest.mark.asyncio
async def test_search_with_mode(retrieval_manager):
    """Test search with different retrieval modes."""
    # Test precision mode
    precision_results = await retrieval_manager.search(
        query="artificial intelligence",
        strategy=RetrievalStrategy.SEMANTIC,
        mode=RetrievalMode.PRECISION,
        top_k=3
    )
    
    # Test recall mode
    recall_results = await retrieval_manager.search(
        query="artificial intelligence",
        strategy=RetrievalStrategy.SEMANTIC,
        mode=RetrievalMode.RECALL,
        top_k=3
    )
    
    # Verify results
    assert len(precision_results) > 0
    assert len(recall_results) > 0
    
    # In precision mode, scores should generally be higher
    if precision_results and recall_results:
        avg_precision_score = sum(r.score for r in precision_results) / len(precision_results)
        avg_recall_score = sum(r.score for r in recall_results) / len(recall_results)
        
        # This is a heuristic check - may not always hold in real scenarios
        # but should be true for our test data
        assert avg_precision_score >= avg_recall_score

@pytest.mark.asyncio
async def test_error_handling(retrieval_manager):
    """Test error handling in retrieval operations."""
    # Test with invalid strategy
    with pytest.raises(ValueError):
        await retrieval_manager.search(
            query="test",
            strategy="invalid_strategy",
            top_k=3
        )
    
    # Test with empty query
    with pytest.raises(ValueError):
        await retrieval_manager.search(
            query="",
            strategy=RetrievalStrategy.SEMANTIC,
            top_k=3
        )
    
    # Test with invalid top_k
    with pytest.raises(ValueError):
        await retrieval_manager.search(
            query="test",
            strategy=RetrievalStrategy.SEMANTIC,
            top_k=-1
        ) 