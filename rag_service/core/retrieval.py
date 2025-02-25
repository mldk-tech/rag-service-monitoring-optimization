"""Enhanced retrieval module for fetching and ranking relevant documents."""

from typing import List, Dict, Any, Optional, Tuple, Set, Union
import time
from datetime import datetime, timedelta
import numpy as np
from enum import Enum
from dataclasses import dataclass, field
from prometheus_client import Counter, Histogram, Summary, Gauge
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from pydantic import BaseModel, Field, validator
import structlog
from functools import wraps
import asyncio
from abc import ABC, abstractmethod
from transformers import AutoTokenizer, AutoModel
import torch

from rag_service.core.database import Document, DatabaseManager, QueryError
from rag_service.monitoring.metrics import MetricsManager

# Enhanced logging setup
logger = structlog.get_logger(__name__)

class RetrievalStrategy(str, Enum):
    """Available retrieval strategies."""
    SEMANTIC = "semantic"
    KEYWORD = "keyword"
    HYBRID = "hybrid"
    DENSE = "dense"
    SPARSE = "sparse"
    ENSEMBLE = "ensemble"

class RetrievalMode(str, Enum):
    """Retrieval operation modes."""
    PRECISION = "precision"
    RECALL = "recall"
    BALANCED = "balanced"
    FAST = "fast"
    THOROUGH = "thorough"

class RetrievalError(Exception):
    """Base class for retrieval errors."""
    def __init__(
        self,
        message: str,
        error_code: str,
        details: Optional[Dict[str, Any]] = None
    ):
        super().__init__(message)
        self.error_code = error_code
        self.details = details or {}
        self.timestamp = datetime.utcnow()

class RetrievalTimeoutError(RetrievalError):
    """Raised when retrieval operation times out."""
    def __init__(
        self,
        message: str,
        timeout: float,
        details: Optional[Dict[str, Any]] = None
    ):
        super().__init__(
            message,
            "RETRIEVAL_TIMEOUT",
            {
                "timeout": timeout,
                **(details or {})
            }
        )

class RetrievalResult(BaseModel):
    """Enhanced model for retrieval results."""
    document: Document
    score: float = Field(ge=0.0, le=1.0)
    retrieval_time: float
    source: str
    strategy: RetrievalStrategy
    confidence: float = Field(ge=0.0, le=1.0)
    relevance_score: Optional[float] = Field(None, ge=0.0, le=1.0)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    retrieved_at: datetime = Field(default_factory=datetime.utcnow)

    @validator("score", "confidence", "relevance_score")
    def validate_scores(cls, v):
        """Validate score values."""
        if v is not None and not (0 <= v <= 1):
            raise ValueError("Score must be between 0 and 1")
        return v

class RetrievalConfig(BaseModel):
    """Enhanced retrieval configuration."""
    class StrategyConfig(BaseModel):
        """Configuration for a retrieval strategy."""
        name: RetrievalStrategy
        weight: float = Field(default=1.0, gt=0.0)
        enabled: bool = True
        timeout: float = Field(default=5.0, gt=0.0)
        params: Dict[str, Any] = Field(default_factory=dict)
        fallback_strategy: Optional[RetrievalStrategy] = None

    strategies: List[StrategyConfig]
    default_mode: RetrievalMode = Field(default=RetrievalMode.BALANCED)
    cache_ttl: int = Field(default=3600, gt=0)
    max_results: int = Field(default=100, gt=0)
    min_score: float = Field(default=0.0, ge=0.0, le=1.0)
    timeout: float = Field(default=10.0, gt=0.0)
    batch_size: int = Field(default=10, gt=0)
    rerank_top_k: int = Field(default=100, gt=0)
    use_cache: bool = True
    deduplicate: bool = True

class RetrievalMetrics:
    """Enhanced retrieval metrics collection."""

    def __init__(self):
        """Initialize retrieval metrics."""
        # Core metrics
        self.retrieval_requests = Counter(
            'rag_retrieval_requests_total',
            'Total number of retrieval requests',
            ['strategy', 'mode', 'status']
        )
        self.retrieval_latency = Histogram(
            'rag_retrieval_duration_seconds',
            'Duration of retrieval operations',
            ['strategy', 'mode', 'status'],
            buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
        )
        self.retrieval_scores = Summary(
            'rag_retrieval_scores',
            'Distribution of retrieval scores',
            ['strategy', 'mode']
        )

        # Cache metrics
        self.cache_hits = Counter(
            'rag_retrieval_cache_hits_total',
            'Total number of cache hits',
            ['strategy']
        )
        self.cache_misses = Counter(
            'rag_retrieval_cache_misses_total',
            'Total number of cache misses',
            ['strategy']
        )
        self.cache_errors = Counter(
            'rag_retrieval_cache_errors_total',
            'Total number of cache errors',
            ['operation']
        )

        # Result metrics
        self.results_returned = Histogram(
            'rag_retrieval_results_count',
            'Number of results returned',
            ['strategy', 'mode'],
            buckets=(0, 1, 2, 5, 10, 20, 50, 100)
        )
        self.result_scores = Histogram(
            'rag_retrieval_result_scores',
            'Distribution of result scores',
            ['strategy'],
            buckets=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
        )

        # Error metrics
        self.retrieval_errors = Counter(
            'rag_retrieval_errors_total',
            'Total number of retrieval errors',
            ['strategy', 'error_type']
        )
        self.retrieval_timeouts = Counter(
            'rag_retrieval_timeouts_total',
            'Total number of retrieval timeouts',
            ['strategy']
        )

        # Performance metrics
        self.query_complexity = Histogram(
            'rag_retrieval_query_complexity',
            'Query complexity score',
            buckets=(1, 2, 3, 5, 8, 13, 21)
        )
        self.active_retrievals = Gauge(
            'rag_active_retrievals',
            'Number of currently active retrievals',
            ['strategy']
        )

class RetrievalManager:
    """Enhanced manager for document retrieval operations."""

    def __init__(
        self,
        db_manager: DatabaseManager,
        metrics_manager: MetricsManager,
        config: Dict[str, Any]
    ):
        """Initialize enhanced retrieval manager.
        
        Args:
            db_manager: Database manager instance
            metrics_manager: Metrics manager instance
            config: Retrieval configuration
        """
        self.db_manager = db_manager
        self.metrics_manager = metrics_manager
        self.config = RetrievalConfig(**config)
        self.tracer = trace.get_tracer(__name__)
        self.metrics = RetrievalMetrics()
        self.model_name = config.get("embedding_model", "sentence-transformers/all-MiniLM-L6-v2")
        
        # Initialize model and tokenizer
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_name)
        self.model = AutoModel.from_pretrained(self.model_name)
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.model.to(self.device)
        
        # Initialize metrics
        self.embedding_time = Histogram(
            "rag_embedding_generation_seconds",
            "Time spent generating embeddings",
            ["model", "status"]
        )
        self.embedding_errors = Counter(
            "rag_embedding_errors_total",
            "Total number of embedding generation errors",
            ["model", "error_type"]
        )

        # Initialize strategy registry
        self.strategies = {
            strategy.name: strategy
            for strategy in self.config.strategies
            if strategy.enabled
        }

        # Initialize caches
        self._query_cache = {}
        self._embedding_cache = {}
        
        logger.info(
            "retrieval_manager_initialized",
            strategies=list(self.strategies.keys())
        )

    async def retrieve(
        self,
        query: str,
        filters: Optional[Dict[str, Any]] = None,
        top_k: Optional[int] = None,
        mode: Optional[RetrievalMode] = None,
        timeout: Optional[float] = None,
        strategy_names: Optional[List[RetrievalStrategy]] = None
    ) -> List[RetrievalResult]:
        """Enhanced document retrieval with comprehensive features.
        
        Args:
            query: Search query
            filters: Optional filters
            top_k: Number of top results to return
            mode: Retrieval mode
            timeout: Operation timeout
            strategy_names: Specific strategies to use
            
        Returns:
            List of retrieval results
            
        Raises:
            RetrievalError: If retrieval fails
            RetrievalTimeoutError: If operation times out
        """
        start_time = time.time()
        mode = mode or self.config.default_mode
        top_k = min(top_k or self.config.max_results, self.config.max_results)
        timeout = timeout or self.config.timeout
        
        with self.tracer.start_span("retrieve_documents") as span:
            try:
                span.set_attribute("query", query)
                span.set_attribute("mode", mode)
                span.set_attribute("top_k", top_k)
                
                # Track active retrievals
                for strategy in self.strategies.values():
                    self.metrics.active_retrievals.labels(
                        strategy=strategy.name
                    ).inc()

                try:
                    # Check cache if enabled
                    if self.config.use_cache:
                        cache_key = self._get_cache_key(
                            query, filters, top_k, mode
                        )
                        cached_results = await self._get_cached_results(cache_key)
                        if cached_results:
                            span.set_attribute("cache_hit", True)
                            return cached_results

                    # Select strategies to use
                    strategies = self._select_strategies(
                        strategy_names,
                        mode
                    )

                    # Execute retrieval with timeout
                    try:
                        async with asyncio.timeout(timeout):
                            all_results = await self._execute_strategies(
                                strategies,
                                query,
                                filters,
                                top_k,
                                mode
                            )
                    except asyncio.TimeoutError:
                        raise RetrievalTimeoutError(
                            f"Retrieval timed out after {timeout} seconds",
                            timeout,
                            {
                                "query": query,
                                "mode": mode,
                                "strategies": [s.name for s in strategies]
                            }
                        )

                    # Process results
                    final_results = await self._process_results(
                        all_results,
                        top_k,
                        mode
                    )

                    # Cache results if enabled
                    if self.config.use_cache:
                        await self._cache_results(cache_key, final_results)

                    # Record success metrics
                    duration = time.time() - start_time
                    self.metrics.retrieval_requests.labels(
                        strategy="combined",
                        mode=mode,
                        status="success"
                    ).inc()
                    self.metrics.retrieval_latency.labels(
                        strategy="combined",
                        mode=mode,
                        status="success"
                    ).observe(duration)
                    self.metrics.results_returned.labels(
                        strategy="combined",
                        mode=mode
                    ).observe(len(final_results))

                    span.set_attribute("results_count", len(final_results))
                    span.set_status(Status(StatusCode.OK))

                    return final_results

                finally:
                    # Cleanup active retrievals
                    for strategy in self.strategies.values():
                        self.metrics.active_retrievals.labels(
                            strategy=strategy.name
                        ).dec()

            except RetrievalError:
                raise
            except Exception as e:
                duration = time.time() - start_time
                self.metrics.retrieval_requests.labels(
                    strategy="combined",
                    mode=mode,
                    status="error"
                ).inc()
                self.metrics.retrieval_latency.labels(
                    strategy="combined",
                    mode=mode,
                    status="error"
                ).observe(duration)
                self.metrics.retrieval_errors.labels(
                    strategy="combined",
                    error_type=type(e).__name__
                ).inc()

                span.set_status(Status(StatusCode.ERROR))
                span.set_attribute("error", str(e))
                
                logger.error(
                    "retrieval_failed",
                    error=str(e),
                    error_type=type(e).__name__,
                    query=query,
                    mode=mode
                )
                
                raise RetrievalError(
                    f"Retrieval failed: {str(e)}",
                    "RETRIEVAL_ERROR",
                    {
                        "query": query,
                        "mode": mode,
                        "error_type": type(e).__name__
                    }
                )

    def _select_strategies(
        self,
        strategy_names: Optional[List[RetrievalStrategy]],
        mode: RetrievalMode
    ) -> List[RetrievalConfig.StrategyConfig]:
        """Select appropriate retrieval strategies based on mode and configuration."""
        if strategy_names:
            return [
                strategy for name, strategy in self.strategies.items()
                if name in strategy_names and strategy.enabled
            ]
            
        if mode == RetrievalMode.FAST:
            # Use fastest strategies
            return [
                strategy for strategy in self.strategies.values()
                if strategy.enabled and strategy.timeout <= 1.0
            ]
        elif mode == RetrievalMode.THOROUGH:
            # Use all enabled strategies
            return list(self.strategies.values())
        elif mode == RetrievalMode.PRECISION:
            # Prioritize precise strategies
            return [
                strategy for strategy in self.strategies.values()
                if strategy.enabled and strategy.name in {
                    RetrievalStrategy.SEMANTIC,
                    RetrievalStrategy.DENSE
                }
            ]
        elif mode == RetrievalMode.RECALL:
            # Prioritize high-recall strategies
            return [
                strategy for strategy in self.strategies.values()
                if strategy.enabled and strategy.name in {
                    RetrievalStrategy.HYBRID,
                    RetrievalStrategy.ENSEMBLE
                }
            ]
        else:  # BALANCED
            # Use a balanced mix of strategies
            return [
                strategy for strategy in self.strategies.values()
                if strategy.enabled and strategy.name in {
                    RetrievalStrategy.SEMANTIC,
                    RetrievalStrategy.KEYWORD,
                    RetrievalStrategy.HYBRID
                }
            ]

    async def _execute_strategies(
        self,
        strategies: List[RetrievalConfig.StrategyConfig],
        query: str,
        filters: Optional[Dict[str, Any]],
        top_k: int,
        mode: RetrievalMode
    ) -> List[RetrievalResult]:
        """Execute multiple retrieval strategies in parallel."""
        tasks = []
        for strategy in strategies:
            task = asyncio.create_task(
                self._execute_strategy(
                    strategy,
                    query,
                    filters,
                    top_k,
                    mode
                )
            )
            tasks.append(task)

        # Gather results with error handling
        all_results = []
        for task in asyncio.as_completed(tasks):
            try:
                results = await task
                all_results.extend(results)
            except Exception as e:
                logger.error(
                    "strategy_execution_failed",
                    error=str(e),
                    error_type=type(e).__name__
                )
                # Continue with other strategies

        return all_results

    async def _execute_strategy(
        self,
        strategy: RetrievalConfig.StrategyConfig,
        query: str,
        filters: Optional[Dict[str, Any]],
        top_k: int,
        mode: RetrievalMode
    ) -> List[RetrievalResult]:
        """Execute a single retrieval strategy with error handling and metrics."""
        start_time = time.time()
        
        with self.tracer.start_span(f"strategy_{strategy.name}") as span:
            try:
                # Execute strategy with timeout
                try:
                    async with asyncio.timeout(strategy.timeout):
                        if strategy.name == RetrievalStrategy.SEMANTIC:
                            results = await self._semantic_search(
                                query, filters, top_k, strategy.params
                            )
                        elif strategy.name == RetrievalStrategy.KEYWORD:
                            results = await self._keyword_search(
                                query, filters, top_k, strategy.params
                            )
                        elif strategy.name == RetrievalStrategy.HYBRID:
                            results = await self._hybrid_search(
                                query, filters, top_k, strategy.params
                            )
                        elif strategy.name == RetrievalStrategy.DENSE:
                            results = await self._dense_search(
                                query, filters, top_k, strategy.params
                            )
                        elif strategy.name == RetrievalStrategy.SPARSE:
                            results = await self._sparse_search(
                                query, filters, top_k, strategy.params
                            )
                        elif strategy.name == RetrievalStrategy.ENSEMBLE:
                            results = await self._ensemble_search(
                                query, filters, top_k, strategy.params
                            )
                        else:
                            logger.warning(
                                "unknown_strategy",
                                strategy=strategy.name
                            )
                            return []

                except asyncio.TimeoutError:
                    self.metrics.retrieval_timeouts.labels(
                        strategy=strategy.name
                    ).inc()
                    
                    # Try fallback if configured
                    if strategy.fallback_strategy:
                        logger.info(
                            "using_fallback_strategy",
                            original=strategy.name,
                            fallback=strategy.fallback_strategy
                        )
                        fallback = self.strategies[strategy.fallback_strategy]
                        return await self._execute_strategy(
                            fallback,
                            query,
                            filters,
                            top_k,
                            mode
                        )
                    raise

                # Apply strategy weight
                for result in results:
                    result.score *= strategy.weight

                # Record metrics
                duration = time.time() - start_time
                self.metrics.retrieval_requests.labels(
                    strategy=strategy.name,
                    mode=mode,
                    status="success"
                ).inc()
                self.metrics.retrieval_latency.labels(
                    strategy=strategy.name,
                    mode=mode,
                    status="success"
                ).observe(duration)

                for result in results:
                    self.metrics.retrieval_scores.labels(
                        strategy=strategy.name,
                        mode=mode
                    ).observe(result.score)
                    self.metrics.result_scores.labels(
                        strategy=strategy.name
                    ).observe(result.score)

                span.set_attribute("results_count", len(results))
                span.set_status(Status(StatusCode.OK))

                return results

            except Exception as e:
                duration = time.time() - start_time
                self.metrics.retrieval_requests.labels(
                    strategy=strategy.name,
                    mode=mode,
                    status="error"
                ).inc()
                self.metrics.retrieval_latency.labels(
                    strategy=strategy.name,
                    mode=mode,
                    status="error"
                ).observe(duration)
                self.metrics.retrieval_errors.labels(
                    strategy=strategy.name,
                    error_type=type(e).__name__
                ).inc()

                span.set_status(Status(StatusCode.ERROR))
                span.set_attribute("error", str(e))
                
                logger.error(
                    "strategy_failed",
                    strategy=strategy.name,
                    error=str(e),
                    error_type=type(e).__name__
                )
                
                raise

    async def _process_results(
        self,
        results: List[RetrievalResult],
        top_k: int,
        mode: RetrievalMode
    ) -> List[RetrievalResult]:
        """Process and refine retrieval results."""
        # Remove duplicates if enabled
        if self.config.deduplicate:
            results = self._deduplicate_results(results)

        # Filter by minimum score
        results = [
            r for r in results
            if r.score >= self.config.min_score
        ]

        # Rerank if needed
        if len(results) > self.config.rerank_top_k:
            results = await self._rerank_results(
                results[:self.config.rerank_top_k],
                mode
            )

        # Sort by score and limit to top_k
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:top_k]

    def _deduplicate_results(
        self,
        results: List[RetrievalResult]
    ) -> List[RetrievalResult]:
        """Remove duplicate results while keeping highest scoring versions."""
        seen_ids = set()
        unique_results = []
        
        for result in sorted(results, key=lambda x: x.score, reverse=True):
            if result.document.id not in seen_ids:
                seen_ids.add(result.document.id)
                unique_results.append(result)
                
        return unique_results

    async def _rerank_results(
        self,
        results: List[RetrievalResult],
        mode: RetrievalMode
    ) -> List[RetrievalResult]:
        """Rerank results using more sophisticated scoring."""
        # Implement reranking logic based on mode
        if mode == RetrievalMode.PRECISION:
            # Prioritize high confidence results
            for result in results:
                result.score *= result.confidence
        elif mode == RetrievalMode.RECALL:
            # Use more lenient scoring
            for result in results:
                result.score = max(result.score, result.confidence)
        
        return results

    def _get_cache_key(
        self,
        query: str,
        filters: Optional[Dict[str, Any]],
        top_k: int,
        mode: RetrievalMode
    ) -> str:
        """Generate cache key for retrieval results."""
        key_parts = [
            "retrieval",
            query,
            str(sorted(filters.items())) if filters else "no_filters",
            str(top_k),
            mode
        ]
        return ":".join(key_parts)

    async def _get_cached_results(
        self,
        cache_key: str
    ) -> Optional[List[RetrievalResult]]:
        """Get cached retrieval results with error handling."""
        try:
            cached = await self.db_manager.get_cache(cache_key)
            if cached:
                results = [RetrievalResult(**r) for r in cached]
                self.metrics.cache_hits.labels(
                    strategy="combined"
                ).inc()
                return results
            
            self.metrics.cache_misses.labels(
                strategy="combined"
            ).inc()
            
        except Exception as e:
            self.metrics.cache_errors.labels(
                operation="get"
            ).inc()
            logger.warning(
                "cache_retrieval_failed",
                error=str(e),
                error_type=type(e).__name__
            )
            
        return None

    async def _cache_results(
        self,
        cache_key: str,
        results: List[RetrievalResult]
    ):
        """Cache retrieval results with error handling."""
        try:
            await self.db_manager.set_cache(
                cache_key,
                [r.dict() for r in results],
                ttl=self.config.cache_ttl
            )
        except Exception as e:
            self.metrics.cache_errors.labels(
                operation="set"
            ).inc()
            logger.warning(
                "cache_storage_failed",
                error=str(e),
                error_type=type(e).__name__
            )

    async def _semantic_search(
        self,
        query: str,
        filters: Optional[Dict[str, Any]] = None,
        top_k: int = 10
    ) -> List[RetrievalResult]:
        """Perform semantic search using vector embeddings."""
        with self.tracer.start_as_current_span("semantic_search") as span:
            span.set_attribute("query", query)
            span.set_attribute("top_k", top_k)
            
            start_time = time.time()
            
            try:
                # Generate embedding for the query
                query_embedding = await self._generate_embedding(query)
                
                # Search documents by vector similarity
                documents = await self.db_manager.search_by_vector(
                    index=self.config["elasticsearch"]["index"],
                    vector=query_embedding,
                    filters=filters,
                    top_k=top_k
                )
                
                # Convert to retrieval results
                results = []
                for doc in documents:
                    score = doc.metadata.get("score", 0.0)
                    results.append(
                        RetrievalResult(
                            document=doc,
                            score=score,
                            retrieval_time=time.time() - start_time,
                            source="elasticsearch",
                            strategy=RetrievalStrategy.SEMANTIC,
                            confidence=score,
                            metadata={"vector_search": True}
                        )
                    )
                
                span.set_attribute("result_count", len(results))
                return results
            
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR))
                span.record_exception(e)
                logger.error(
                    "semantic_search_error",
                    error=str(e),
                    query=query,
                    exc_info=True
                )
                raise RetrievalError(
                    f"Semantic search failed: {str(e)}",
                    "SEMANTIC_SEARCH_ERROR"
                )

    async def _keyword_search(
        self,
        query: str,
        filters: Optional[Dict[str, Any]] = None,
        top_k: int = 10
    ) -> List[RetrievalResult]:
        """Perform keyword-based search using text analysis."""
        with self.tracer.start_as_current_span("keyword_search") as span:
            span.set_attribute("query", query)
            span.set_attribute("top_k", top_k)
            
            start_time = time.time()
            
            try:
                # Prepare the search query with BM25 scoring
                search_query = {
                    "query": {
                        "bool": {
                            "must": [
                                {
                                    "match": {
                                        "content": {
                                            "query": query,
                                            "operator": "or",
                                            "fuzziness": "AUTO"
                                        }
                                    }
                                }
                            ]
                        }
                    }
                }
                
                # Add filters if provided
                if filters:
                    for key, value in filters.items():
                        search_query["query"]["bool"].setdefault("filter", [])
                        search_query["query"]["bool"]["filter"].append(
                            {"term": {f"metadata.{key}": value}}
                        )
                
                # Execute the search
                documents = await self.db_manager.search_documents(
                    index=self.config["elasticsearch"]["index"],
                    query=search_query,
                    size=top_k
                )
                
                # Convert to retrieval results
                results = []
                for doc in documents:
                    score = doc.metadata.get("score", 0.0)
                    results.append(
                        RetrievalResult(
                            document=doc,
                            score=score,
                            retrieval_time=time.time() - start_time,
                            source="elasticsearch",
                            strategy=RetrievalStrategy.KEYWORD,
                            confidence=score,
                            metadata={"keyword_search": True}
                        )
                    )
                
                span.set_attribute("result_count", len(results))
                return results
                
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR))
                span.record_exception(e)
                logger.error(
                    "keyword_search_error",
                    error=str(e),
                    query=query,
                    exc_info=True
                )
                raise RetrievalError(
                    f"Keyword search failed: {str(e)}",
                    "KEYWORD_SEARCH_ERROR"
                )

    async def _hybrid_search(
        self,
        query: str,
        filters: Optional[Dict[str, Any]] = None,
        top_k: int = 10
    ) -> List[RetrievalResult]:
        """Perform hybrid search combining semantic and keyword approaches."""
        with self.tracer.start_as_current_span("hybrid_search") as span:
            span.set_attribute("query", query)
            span.set_attribute("top_k", top_k)
            
            start_time = time.time()
            
            try:
                # Run both search types in parallel
                semantic_task = asyncio.create_task(
                    self._semantic_search(query, filters, top_k)
                )
                keyword_task = asyncio.create_task(
                    self._keyword_search(query, filters, top_k)
                )
                
                # Wait for both to complete
                semantic_results, keyword_results = await asyncio.gather(
                    semantic_task, keyword_task
                )
                
                # Combine and deduplicate results
                combined_results = {}
                
                # Process semantic results first
                for result in semantic_results:
                    doc_id = result.document.id
                    combined_results[doc_id] = result
                
                # Process keyword results, combining scores for duplicates
                for result in keyword_results:
                    doc_id = result.document.id
                    if doc_id in combined_results:
                        # Document exists in both result sets, combine scores
                        existing = combined_results[doc_id]
                        semantic_weight = self.config.get("hybrid_weights", {}).get("semantic", 0.7)
                        keyword_weight = self.config.get("hybrid_weights", {}).get("keyword", 0.3)
                        
                        # Calculate weighted score
                        combined_score = (
                            existing.score * semantic_weight + 
                            result.score * keyword_weight
                        )
                        
                        # Update the existing result
                        combined_results[doc_id] = RetrievalResult(
                            document=existing.document,
                            score=combined_score,
                            retrieval_time=time.time() - start_time,
                            source="hybrid",
                            strategy=RetrievalStrategy.HYBRID,
                            confidence=combined_score,
                            metadata={
                                "semantic_score": existing.score,
                                "keyword_score": result.score,
                                "hybrid_search": True
                            }
                        )
                    else:
                        # New document, add to results
                        combined_results[doc_id] = result
                
                # Convert to list and sort by score
                results = list(combined_results.values())
                results.sort(key=lambda x: x.score, reverse=True)
                
                # Limit to top_k
                results = results[:top_k]
                
                span.set_attribute("result_count", len(results))
                return results
                
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR))
                span.record_exception(e)
                logger.error(
                    "hybrid_search_error",
                    error=str(e),
                    query=query,
                    exc_info=True
                )
                raise RetrievalError(
                    f"Hybrid search failed: {str(e)}",
                    "HYBRID_SEARCH_ERROR"
                )

    async def _dense_search(
        self,
        query: str,
        filters: Optional[Dict[str, Any]] = None,
        top_k: int = 10
    ) -> List[RetrievalResult]:
        """Perform dense vector search using a specialized embedding model."""
        with self.tracer.start_as_current_span("dense_search") as span:
            span.set_attribute("query", query)
            span.set_attribute("top_k", top_k)
            
            start_time = time.time()
            
            try:
                # Generate dense embedding for the query
                # This uses a different model than the standard embedding model
                # For now, we'll use the same method but in a real implementation
                # this would use a different model or approach
                query_embedding = await self._generate_dense_embedding(query)
                
                # Search documents by vector similarity
                documents = await self.db_manager.search_by_vector(
                    index=self.config["elasticsearch"]["dense_index"],
                    vector=query_embedding,
                    vector_field="dense_embedding",
                    filters=filters,
                    top_k=top_k
                )
                
                # Convert to retrieval results
                results = []
                for doc in documents:
                    score = doc.metadata.get("score", 0.0)
                    results.append(
                        RetrievalResult(
                            document=doc,
                            score=score,
                            retrieval_time=time.time() - start_time,
                            source="elasticsearch",
                            strategy=RetrievalStrategy.DENSE,
                            confidence=score,
                            metadata={"dense_search": True}
                        )
                    )
                
                span.set_attribute("result_count", len(results))
                return results
                
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR))
                span.record_exception(e)
                logger.error(
                    "dense_search_error",
                    error=str(e),
                    query=query,
                    exc_info=True
                )
                # Fall back to semantic search if dense search fails
                logger.info("Falling back to semantic search after dense search failure")
                return await self._semantic_search(query, filters, top_k)

    async def _sparse_search(
        self,
        query: str,
        filters: Optional[Dict[str, Any]] = None,
        top_k: int = 10
    ) -> List[RetrievalResult]:
        """Perform sparse vector search using techniques like BM25 or SPLADE."""
        with self.tracer.start_as_current_span("sparse_search") as span:
            span.set_attribute("query", query)
            span.set_attribute("top_k", top_k)
            
            start_time = time.time()
            
            try:
                # For now, this is similar to keyword search but could be enhanced
                # with SPLADE or other sparse embedding techniques in the future
                search_query = {
                    "query": {
                        "bool": {
                            "must": [
                                {
                                    "match": {
                                        "content": {
                                            "query": query,
                                            "operator": "or",
                                            "boost": 2.0
                                        }
                                    }
                                }
                            ],
                            "should": [
                                {
                                    "match_phrase": {
                                        "content": {
                                            "query": query,
                                            "boost": 3.0,
                                            "slop": 2
                                        }
                                    }
                                }
                            ]
                        }
                    }
                }
                
                # Add filters if provided
                if filters:
                    for key, value in filters.items():
                        search_query["query"]["bool"].setdefault("filter", [])
                        search_query["query"]["bool"]["filter"].append(
                            {"term": {f"metadata.{key}": value}}
                        )
                
                # Execute the search
                documents = await self.db_manager.search_documents(
                    index=self.config["elasticsearch"]["index"],
                    query=search_query,
                    size=top_k
                )
                
                # Convert to retrieval results
                results = []
                for doc in documents:
                    score = doc.metadata.get("score", 0.0)
                    results.append(
                        RetrievalResult(
                            document=doc,
                            score=score,
                            retrieval_time=time.time() - start_time,
                            source="elasticsearch",
                            strategy=RetrievalStrategy.SPARSE,
                            confidence=score,
                            metadata={"sparse_search": True}
                        )
                    )
                
                span.set_attribute("result_count", len(results))
                return results
                
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR))
                span.record_exception(e)
                logger.error(
                    "sparse_search_error",
                    error=str(e),
                    query=query,
                    exc_info=True
                )
                raise RetrievalError(
                    f"Sparse search failed: {str(e)}",
                    "SPARSE_SEARCH_ERROR"
                )

    async def _ensemble_search(
        self,
        query: str,
        filters: Optional[Dict[str, Any]] = None,
        top_k: int = 10
    ) -> List[RetrievalResult]:
        """Perform ensemble search combining multiple retrieval strategies."""
        with self.tracer.start_as_current_span("ensemble_search") as span:
            span.set_attribute("query", query)
            span.set_attribute("top_k", top_k)
            
            start_time = time.time()
            
            try:
                # Define which strategies to use in the ensemble
                strategies = [
                    (self._semantic_search, self.config.get("ensemble_weights", {}).get("semantic", 0.4)),
                    (self._keyword_search, self.config.get("ensemble_weights", {}).get("keyword", 0.2)),
                    (self._dense_search, self.config.get("ensemble_weights", {}).get("dense", 0.3)),
                    (self._sparse_search, self.config.get("ensemble_weights", {}).get("sparse", 0.1))
                ]
                
                # Create tasks for all strategies
                tasks = []
                for strategy_func, _ in strategies:
                    task = asyncio.create_task(strategy_func(query, filters, top_k))
                    tasks.append(task)
                
                # Wait for all tasks to complete
                all_results = await asyncio.gather(*tasks, return_exceptions=True)
                
                # Process results, handling any exceptions
                combined_results = {}
                for i, results in enumerate(all_results):
                    if isinstance(results, Exception):
                        logger.warning(
                            "ensemble_strategy_failed",
                            strategy=strategies[i][0].__name__,
                            error=str(results)
                        )
                        continue
                        
                    strategy_weight = strategies[i][1]
                    
                    for result in results:
                        doc_id = result.document.id
                        weighted_score = result.score * strategy_weight
                        
                        if doc_id in combined_results:
                            # Update existing entry
                            existing = combined_results[doc_id]
                            existing["score"] += weighted_score
                            existing["strategies"].append(result.strategy.value)
                            existing["raw_scores"][result.strategy.value] = result.score
                        else:
                            # Create new entry
                            combined_results[doc_id] = {
                                "document": result.document,
                                "score": weighted_score,
                                "strategies": [result.strategy.value],
                                "raw_scores": {result.strategy.value: result.score}
                            }
                
                # Convert to retrieval results
                results = []
                for doc_id, data in combined_results.items():
                    results.append(
                        RetrievalResult(
                            document=data["document"],
                            score=min(data["score"], 1.0),  # Ensure score is <= 1.0
                            retrieval_time=time.time() - start_time,
                            source="ensemble",
                            strategy=RetrievalStrategy.ENSEMBLE,
                            confidence=min(data["score"], 1.0),
                            metadata={
                                "ensemble_search": True,
                                "contributing_strategies": data["strategies"],
                                "raw_scores": data["raw_scores"]
                            }
                        )
                    )
                
                # Sort by score and limit to top_k
                results.sort(key=lambda x: x.score, reverse=True)
                results = results[:top_k]
                
                span.set_attribute("result_count", len(results))
                return results
                
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR))
                span.record_exception(e)
                logger.error(
                    "ensemble_search_error",
                    error=str(e),
                    query=query,
                    exc_info=True
                )
                # Fall back to hybrid search if ensemble fails
                logger.info("Falling back to hybrid search after ensemble search failure")
                return await self._hybrid_search(query, filters, top_k)

    async def _generate_dense_embedding(self, text: str) -> List[float]:
        """Generate dense embedding using a specialized model."""
        # This would use a different model than the standard embedding model
        # For now, we'll use the same method but in a real implementation
        # this would use a different model or approach
        return await self._generate_embedding(text)

    async def _generate_embedding(self, text: str) -> List[float]:
        try:
            with self.embedding_time.labels(model=self.model_name, status="success").time():
                # Tokenize and prepare input
                inputs = self.tokenizer(
                    text,
                    padding=True,
                    truncation=True,
                    max_length=512,
                    return_tensors="pt"
                ).to(self.device)

                # Generate embeddings
                with torch.no_grad():
                    outputs = self.model(**inputs)
                    # Use mean pooling to get sentence embedding
                    attention_mask = inputs["attention_mask"]
                    token_embeddings = outputs.last_hidden_state
                    input_mask_expanded = attention_mask.unsqueeze(-1).expand(token_embeddings.size()).float()
                    embeddings = torch.sum(token_embeddings * input_mask_expanded, 1) / torch.clamp(input_mask_expanded.sum(1), min=1e-9)
                    
                return embeddings[0].cpu().numpy().tolist()
        except Exception as e:
            logger.error("embedding_generation_failed", error=str(e), query=text)
            self.embedding_errors.labels(model=self.model_name, error_type=type(e).__name__).inc()
            raise 