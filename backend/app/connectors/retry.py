"""Retry decorator for external API calls in connectors."""
import asyncio
import logging
import functools
from typing import TypeVar, Callable, Any

logger = logging.getLogger(__name__)

T = TypeVar("T")


def with_retry(
    max_retries: int = 2,
    base_delay: float = 0.5,
    max_delay: float = 10.0,
    backoff_factor: float = 2.0,
    retryable_exceptions: tuple = (Exception,),
):
    """Decorator that retries async functions with exponential backoff."""
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except retryable_exceptions as e:
                    last_exception = e
                    if attempt < max_retries:
                        delay = min(base_delay * (backoff_factor ** attempt), max_delay)
                        logger.warning(
                            "Retry %d/%d for %s: %s (waiting %.1fs)",
                            attempt + 1, max_retries, func.__name__, str(e), delay
                        )
                        await asyncio.sleep(delay)
                    else:
                        logger.error(
                            "All %d retries failed for %s: %s",
                            max_retries, func.__name__, str(e)
                        )
            raise last_exception
        return wrapper
    return decorator
