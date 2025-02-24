"""Circuit breaker decorators for database operations."""

from functools import wraps
import structlog
from typing import Any, Callable
from prometheus_client import Counter

logger = structlog.get_logger(__name__)

def circuit_breaker(service: str):
    """Circuit breaker decorator factory."""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(self, *args, **kwargs):
            if not hasattr(self, "_circuit_breakers"):
                raise RuntimeError("Circuit breakers not initialized")
                
            breaker = self._circuit_breakers[service]
            return await breaker.call(lambda: func(self, *args, **kwargs))
        return wrapper
    return decorator

# Specific service decorators
def es_breaker(func: Callable) -> Callable:
    """Circuit breaker for Elasticsearch operations."""
    return circuit_breaker("elasticsearch")(func)

def mongo_breaker(func: Callable) -> Callable:
    """Circuit breaker for MongoDB operations."""
    return circuit_breaker("mongodb")(func)

def redis_breaker(func: Callable) -> Callable:
    """Circuit breaker for Redis operations."""
    return circuit_breaker("redis")(func)

# Retry decorator for transient failures
def with_retry(max_retries: int = 3, backoff_factor: float = 1.0):
    """Retry decorator with exponential backoff."""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(self, *args, **kwargs):
            from tenacity import (
                retry,
                stop_after_attempt,
                wait_exponential,
                retry_if_exception_type
            )
            
            @retry(
                stop=stop_after_attempt(max_retries),
                wait=wait_exponential(multiplier=backoff_factor),
                retry=retry_if_exception_type(Exception),
                before_sleep=lambda retry_state: logger.warning(
                    "operation_retry",
                    attempt=retry_state.attempt_number,
                    func=func.__name__
                )
            )
            async def retry_wrapper():
                return await func(self, *args, **kwargs)
                
            return await retry_wrapper()
        return wrapper
    return decorator 