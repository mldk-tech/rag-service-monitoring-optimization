"""Unit tests for retrieval functionality."""

import pytest
from unittest.mock import Mock, patch
from datetime import datetime

from rag_service.core.retrieval import (
    RetrievalManager,
    RetrievalResult,
    RetrievalStrategy,
    Document
)

@pytest.fixture
def mock_db_manager():
    """Create a mock database manager."""
    manager = Mock()
    manager.search_documents.return_value = [
        Document(
            id="1",
            content="Test content 1",
            metadata={"source": "test"},
            embedding=[0.1] * 768,
            created_at=datetime.now(),
            updated_at=datetime.now()
        ),
        Document(
            id="2",
            content="Test content 2",
            metadata={"source": "test"},
            embedding=[0.2] * 768,
            created_at=datetime.now(),
            updated_at=datetime.now()
        )
    ]
    return manager

@pytest.fixture
def mock_metrics_manager():
    """Create a mock metrics manager."""
    return Mock()

@pytest.fixture
def retrieval_config():
    """Create a test retrieval configuration."""
    return {
        "strategies": [
            {
                "name": "semantic",
                "enabled": True,
                "weight": 1.0,
                "params": {
                    "model_name": "test-model",
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
            "ttl": 3600
        },
        "max_results": 10
    }

@pytest.fixture
def retrieval_manager(mock_db_manager, mock_metrics_manager, retrieval_config):
    """Create a retrieval manager instance."""
    return RetrievalManager(
        mock_db_manager,
        mock_metrics_manager,
        retrieval_config
    )

class TestRetrievalManager:
    """Test suite for RetrievalManager."""

    @pytest.mark.unit
    async def test_initialization(self, retrieval_manager, retrieval_config):
        """Test retrieval manager initialization."""
        assert len(retrieval_manager.strategies) == 2
        assert retrieval_manager.strategies[0].name == "semantic"
        assert retrieval_manager.strategies[1].name == "keyword"

    @pytest.mark.unit
    async def test_retrieve_documents(self, retrieval_manager, mock_db_manager):
        """Test document retrieval."""
        query = "test query"
        results = await retrieval_manager.retrieve(query)
        
        assert len(results) > 0
        assert isinstance(results[0], RetrievalResult)
        assert results[0].document.content == "Test content 1"
        mock_db_manager.search_documents.assert_called_once()

    @pytest.mark.unit
    async def test_retrieve_with_filters(self, retrieval_manager, mock_db_manager):
        """Test document retrieval with filters."""
        query = "test query"
        filters = {"source": "test"}
        results = await retrieval_manager.retrieve(query, filters=filters)
        
        assert len(results) > 0
        mock_db_manager.search_documents.assert_called_once()
        call_args = mock_db_manager.search_documents.call_args[1]
        assert "filters" in call_args
        assert call_args["filters"]["source"] == "test"

    @pytest.mark.unit
    async def test_retrieve_with_cache(self, retrieval_manager, mock_db_manager):
        """Test document retrieval with caching."""
        query = "test query"
        
        # First call should hit the database
        results1 = await retrieval_manager.retrieve(query)
        assert len(results1) > 0
        mock_db_manager.search_documents.assert_called_once()
        
        # Second call should use cache
        mock_db_manager.get_cache.return_value = [r.dict() for r in results1]
        results2 = await retrieval_manager.retrieve(query)
        assert len(results2) > 0
        assert mock_db_manager.search_documents.call_count == 1

    @pytest.mark.unit
    async def test_strategy_weights(self, retrieval_manager, mock_db_manager):
        """Test strategy weight application."""
        query = "test query"
        results = await retrieval_manager.retrieve(query)
        
        # Check if weights are applied
        semantic_results = [r for r in results if r.source == "semantic"]
        keyword_results = [r for r in results if r.source == "keyword"]
        
        if semantic_results and keyword_results:
            assert semantic_results[0].score > keyword_results[0].score

    @pytest.mark.unit
    @pytest.mark.parametrize("strategy_name", ["semantic", "keyword"])
    async def test_individual_strategies(
        self,
        retrieval_manager,
        mock_db_manager,
        strategy_name
    ):
        """Test individual retrieval strategies."""
        query = "test query"
        strategy = next(
            s for s in retrieval_manager.strategies if s.name == strategy_name
        )
        
        results = await retrieval_manager._execute_strategy(
            strategy,
            query,
            None,
            10
        )
        
        assert len(results) > 0
        assert all(r.source == strategy_name for r in results)

    @pytest.mark.unit
    async def test_error_handling(self, retrieval_manager, mock_db_manager):
        """Test error handling during retrieval."""
        query = "test query"
        mock_db_manager.search_documents.side_effect = Exception("Test error")
        
        with pytest.raises(Exception):
            await retrieval_manager.retrieve(query)

    @pytest.mark.unit
    async def test_result_deduplication(self, retrieval_manager, mock_db_manager):
        """Test deduplication of results from different strategies."""
        query = "test query"
        # Make both strategies return the same document
        doc = Document(
            id="1",
            content="Test content",
            metadata={"source": "test"},
            embedding=[0.1] * 768,
            created_at=datetime.now(),
            updated_at=datetime.now()
        )
        mock_db_manager.search_documents.return_value = [doc]
        
        results = await retrieval_manager.retrieve(query)
        # Should only have one result despite both strategies returning same doc
        assert len(results) == 1
        assert results[0].document.id == "1" 