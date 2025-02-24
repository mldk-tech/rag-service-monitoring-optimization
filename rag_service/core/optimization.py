"""Optimization module for performance optimization and resource management."""

from typing import List, Dict, Any, Optional, Union, Tuple
import logging
import time
import asyncio
from datetime import datetime, timedelta
import numpy as np
from prometheus_client import Counter, Histogram, Gauge
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from pydantic import BaseModel

from rag_service.monitoring.metrics import MetricsManager
from rag_service.core.retrieval import RetrievalManager
from rag_service.core.generation import GenerationManager

class OptimizationMetrics(BaseModel):
    """Model for optimization metrics."""
    latency_p50: float
    latency_p90: float
    latency_p99: float
    success_rate: float
    error_rate: float
    cache_hit_rate: float
    resource_utilization: Dict[str, float]
    throughput: float

class OptimizationResult(BaseModel):
    """Model for optimization results."""
    strategy: str
    metrics_before: OptimizationMetrics
    metrics_after: OptimizationMetrics
    improvements: Dict[str, float]
    recommendations: List[str]

class OptimizationManager:
    """Manager for performance optimization and resource management."""

    def __init__(
        self,
        retrieval_manager: RetrievalManager,
        generation_manager: GenerationManager,
        metrics_manager: MetricsManager,
        config: Dict[str, Any]
    ):
        """Initialize optimization manager.
        
        Args:
            retrieval_manager: Retrieval manager instance
            generation_manager: Generation manager instance
            metrics_manager: Metrics manager instance
            config: Optimization configuration
        """
        self.retrieval_manager = retrieval_manager
        self.generation_manager = generation_manager
        self.metrics_manager = metrics_manager
        self.config = config
        self.tracer = trace.get_tracer(__name__)

        # Initialize metrics
        self._init_metrics()

        # Load optimization strategies
        self.strategies = self._load_strategies()

        # Initialize optimization state
        self.current_state = {}
        self.optimization_history = []

    def _init_metrics(self):
        """Initialize optimization metrics."""
        self.optimization_counter = Counter(
            'rag_optimization_runs_total',
            'Total number of optimization runs',
            ['strategy', 'status']
        )
        self.optimization_latency = Histogram(
            'rag_optimization_duration_seconds',
            'Duration of optimization operations',
            ['strategy'],
            buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)
        )
        self.optimization_improvements = Gauge(
            'rag_optimization_improvements',
            'Improvements from optimization',
            ['metric']
        )
        self.resource_utilization = Gauge(
            'rag_resource_utilization',
            'Resource utilization metrics',
            ['resource']
        )

    def _load_strategies(self) -> Dict[str, Any]:
        """Load optimization strategies from configuration."""
        strategies = {}
        for strategy in self.config.get("strategies", []):
            try:
                strategy_name = strategy["name"]
                strategies[strategy_name] = {
                    "enabled": strategy.get("enabled", True),
                    "schedule": strategy.get("schedule", "hourly"),
                    "params": strategy.get("params", {}),
                    "thresholds": strategy.get("thresholds", {}),
                    "last_run": None,
                    "consecutive_failures": 0
                }
            except Exception as e:
                logging.error(
                    "Failed to load optimization strategy: %s",
                    str(e)
                )
        return strategies

    async def optimize(
        self,
        strategy_name: Optional[str] = None
    ) -> OptimizationResult:
        """Run optimization process.
        
        Args:
            strategy_name: Optional specific strategy to run
            
        Returns:
            Optimization result
        """
        start_time = time.time()
        
        with self.tracer.start_span("optimize") as span:
            try:
                # Collect current metrics
                metrics_before = await self._collect_metrics()

                # Select and run optimization strategy
                if strategy_name:
                    if strategy_name not in self.strategies:
                        raise ValueError(f"Unknown strategy: {strategy_name}")
                    strategy = self.strategies[strategy_name]
                    if not strategy["enabled"]:
                        raise ValueError(f"Strategy {strategy_name} is disabled")
                    improvements = await self._run_strategy(
                        strategy_name,
                        strategy
                    )
                else:
                    improvements = await self._run_all_strategies()

                # Collect metrics after optimization
                metrics_after = await self._collect_metrics()

                # Calculate improvements
                improvement_metrics = self._calculate_improvements(
                    metrics_before,
                    metrics_after
                )

                # Generate recommendations
                recommendations = self._generate_recommendations(
                    metrics_after,
                    improvement_metrics
                )

                # Create result
                result = OptimizationResult(
                    strategy=strategy_name or "combined",
                    metrics_before=metrics_before,
                    metrics_after=metrics_after,
                    improvements=improvement_metrics,
                    recommendations=recommendations
                )

                # Record metrics
                duration = time.time() - start_time
                self.optimization_counter.labels(
                    strategy=result.strategy,
                    status="success"
                ).inc()
                self.optimization_latency.labels(
                    strategy=result.strategy
                ).observe(duration)

                for metric, value in improvement_metrics.items():
                    self.optimization_improvements.labels(
                        metric=metric
                    ).set(value)

                # Update optimization history
                self.optimization_history.append({
                    "timestamp": datetime.now(),
                    "result": result
                })

                span.set_attribute("strategy", result.strategy)
                span.set_attribute(
                    "improvement_count",
                    len(improvement_metrics)
                )
                span.set_status(Status(StatusCode.OK))

                return result

            except Exception as e:
                duration = time.time() - start_time
                self.optimization_counter.labels(
                    strategy=strategy_name or "unknown",
                    status="error"
                ).inc()
                self.optimization_latency.labels(
                    strategy=strategy_name or "unknown"
                ).observe(duration)

                span.set_status(Status(StatusCode.ERROR))
                logging.error("Optimization failed: %s", str(e))
                raise

    async def _collect_metrics(self) -> OptimizationMetrics:
        """Collect current system metrics."""
        # Get latency metrics
        latency_metrics = await self.metrics_manager.get_latency_metrics()
        
        # Get error rates
        error_metrics = await self.metrics_manager.get_error_metrics()
        
        # Get cache metrics
        cache_metrics = await self.metrics_manager.get_cache_metrics()
        
        # Get resource utilization
        resource_metrics = await self.metrics_manager.get_resource_metrics()
        
        # Get throughput
        throughput = await self.metrics_manager.get_throughput()

        return OptimizationMetrics(
            latency_p50=latency_metrics["p50"],
            latency_p90=latency_metrics["p90"],
            latency_p99=latency_metrics["p99"],
            success_rate=1.0 - error_metrics["error_rate"],
            error_rate=error_metrics["error_rate"],
            cache_hit_rate=cache_metrics["hit_rate"],
            resource_utilization=resource_metrics,
            throughput=throughput
        )

    async def _run_strategy(
        self,
        strategy_name: str,
        strategy: Dict[str, Any]
    ) -> Dict[str, float]:
        """Run a specific optimization strategy."""
        improvements = {}
        
        if strategy_name == "cache_optimization":
            improvements = await self._optimize_cache(strategy["params"])
        elif strategy_name == "retrieval_optimization":
            improvements = await self._optimize_retrieval(strategy["params"])
        elif strategy_name == "resource_optimization":
            improvements = await self._optimize_resources(strategy["params"])
        elif strategy_name == "model_optimization":
            improvements = await self._optimize_model(strategy["params"])
        else:
            logging.warning(f"Unknown strategy: {strategy_name}")
            return {}

        # Update strategy state
        strategy["last_run"] = datetime.now()
        if improvements:
            strategy["consecutive_failures"] = 0
        else:
            strategy["consecutive_failures"] += 1

        return improvements

    async def _run_all_strategies(self) -> Dict[str, float]:
        """Run all enabled optimization strategies."""
        all_improvements = {}
        
        for strategy_name, strategy in self.strategies.items():
            if not strategy["enabled"]:
                continue
                
            try:
                improvements = await self._run_strategy(
                    strategy_name,
                    strategy
                )
                all_improvements.update(improvements)
            except Exception as e:
                logging.error(
                    "Strategy %s failed: %s",
                    strategy_name,
                    str(e)
                )

        return all_improvements

    async def _optimize_cache(
        self,
        params: Dict[str, Any]
    ) -> Dict[str, float]:
        """Optimize cache configuration and policies."""
        improvements = {}
        
        # Analyze cache hit patterns
        hit_patterns = await self._analyze_cache_patterns()
        
        # Adjust cache TTL based on hit patterns
        if hit_patterns["stale_ratio"] > params.get("stale_threshold", 0.2):
            new_ttl = self._calculate_optimal_ttl(hit_patterns)
            await self._update_cache_ttl(new_ttl)
            improvements["cache_ttl"] = new_ttl
        
        # Adjust cache size based on utilization
        if hit_patterns["eviction_rate"] > params.get("eviction_threshold", 0.1):
            new_size = self._calculate_optimal_cache_size(hit_patterns)
            await self._update_cache_size(new_size)
            improvements["cache_size"] = new_size
        
        return improvements

    async def _optimize_retrieval(
        self,
        params: Dict[str, Any]
    ) -> Dict[str, float]:
        """Optimize retrieval strategies and parameters."""
        improvements = {}
        
        # Analyze retrieval performance
        retrieval_metrics = await self._analyze_retrieval_performance()
        
        # Adjust strategy weights
        if retrieval_metrics["score_variance"] > params.get("variance_threshold", 0.2):
            new_weights = self._calculate_optimal_weights(retrieval_metrics)
            await self._update_strategy_weights(new_weights)
            improvements["strategy_weights"] = new_weights
        
        # Optimize chunk size
        if retrieval_metrics["chunk_efficiency"] < params.get("efficiency_threshold", 0.7):
            new_size = self._calculate_optimal_chunk_size(retrieval_metrics)
            await self._update_chunk_size(new_size)
            improvements["chunk_size"] = new_size
        
        return improvements

    async def _optimize_resources(
        self,
        params: Dict[str, Any]
    ) -> Dict[str, float]:
        """Optimize resource allocation and utilization."""
        improvements = {}
        
        # Analyze resource utilization
        resource_metrics = await self._analyze_resource_utilization()
        
        # Adjust connection pools
        if resource_metrics["connection_wait_time"] > params.get("wait_threshold", 0.1):
            new_pools = self._calculate_optimal_pools(resource_metrics)
            await self._update_connection_pools(new_pools)
            improvements["connection_pools"] = new_pools
        
        # Adjust concurrency limits
        if resource_metrics["concurrency_ratio"] > params.get("concurrency_threshold", 0.8):
            new_limits = self._calculate_optimal_concurrency(resource_metrics)
            await self._update_concurrency_limits(new_limits)
            improvements["concurrency_limits"] = new_limits
        
        return improvements

    async def _optimize_model(
        self,
        params: Dict[str, Any]
    ) -> Dict[str, float]:
        """Optimize language model configuration and usage."""
        improvements = {}
        
        # Analyze model performance
        model_metrics = await self._analyze_model_performance()
        
        # Adjust model parameters
        if model_metrics["quality_score"] < params.get("quality_threshold", 0.8):
            new_params = self._calculate_optimal_model_params(model_metrics)
            await self._update_model_params(new_params)
            improvements["model_params"] = new_params
        
        # Optimize token usage
        if model_metrics["token_efficiency"] < params.get("efficiency_threshold", 0.7):
            new_limits = self._calculate_optimal_token_limits(model_metrics)
            await self._update_token_limits(new_limits)
            improvements["token_limits"] = new_limits
        
        return improvements

    def _calculate_improvements(
        self,
        before: OptimizationMetrics,
        after: OptimizationMetrics
    ) -> Dict[str, float]:
        """Calculate improvement metrics."""
        return {
            "latency_improvement": (
                (before.latency_p90 - after.latency_p90) / before.latency_p90
            ),
            "error_rate_improvement": (
                (before.error_rate - after.error_rate) / before.error_rate
                if before.error_rate > 0 else 0
            ),
            "cache_hit_improvement": (
                (after.cache_hit_rate - before.cache_hit_rate)
            ),
            "throughput_improvement": (
                (after.throughput - before.throughput) / before.throughput
            )
        }

    def _generate_recommendations(
        self,
        metrics: OptimizationMetrics,
        improvements: Dict[str, float]
    ) -> List[str]:
        """Generate optimization recommendations."""
        recommendations = []
        
        # Analyze metrics and generate recommendations
        if metrics.latency_p90 > self.config["thresholds"]["latency"]:
            recommendations.append(
                "Consider increasing cache size and optimizing retrieval strategy weights"
            )
        
        if metrics.error_rate > self.config["thresholds"]["error_rate"]:
            recommendations.append(
                "Review error patterns and adjust retry policies"
            )
        
        if metrics.cache_hit_rate < self.config["thresholds"]["cache_hit_rate"]:
            recommendations.append(
                "Analyze cache patterns and adjust TTL settings"
            )
        
        if metrics.throughput < self.config["thresholds"]["throughput"]:
            recommendations.append(
                "Consider scaling resources or optimizing concurrency settings"
            )
        
        return recommendations

    async def health_check(self) -> Dict[str, Any]:
        """Check health of optimization subsystem."""
        results = {
            "status": "healthy",
            "strategies": {}
        }
        
        for strategy_name, strategy in self.strategies.items():
            status = "healthy"
            message = "OK"
            
            # Check strategy health
            if strategy["consecutive_failures"] >= self.config["max_failures"]:
                status = "warning"
                message = f"Strategy failed {strategy['consecutive_failures']} times"
            
            if strategy["last_run"]:
                time_since_last = datetime.now() - strategy["last_run"]
                if time_since_last > timedelta(hours=24):
                    status = "warning"
                    message = f"Strategy not run for {time_since_last}"
            
            results["strategies"][strategy_name] = {
                "status": status,
                "message": message,
                "last_run": strategy["last_run"],
                "enabled": strategy["enabled"]
            }
        
        return results 