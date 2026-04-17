"""Retry decorator with circuit breaker for external API calls in connectors."""
import asyncio
import logging
import functools
import time
from typing import TypeVar, Callable, Any

logger = logging.getLogger(__name__)

T = TypeVar("T")

# ---------------------------------------------------------------------------
# Circuit breaker — tracks per-function failure state
# ---------------------------------------------------------------------------
_circuit_state: dict[str, dict] = {}

# Configurable thresholds
CIRCUIT_FAILURE_THRESHOLD = 5   # failures before opening the circuit
CIRCUIT_RECOVERY_TIMEOUT = 60   # seconds before trying half-open


def _get_circuit(func_name: str) -> dict:
    """Return (or initialise) circuit breaker state for *func_name*."""
    if func_name not in _circuit_state:
        _circuit_state[func_name] = {
            "failures": 0,
            "last_failure": 0.0,
            "state": "closed",  # closed | open | half-open
        }
    return _circuit_state[func_name]


def reset_circuit(func_name: str | None = None) -> None:
    """Reset circuit breaker state.  Pass *None* to reset all circuits."""
    if func_name is None:
        _circuit_state.clear()
    else:
        _circuit_state.pop(func_name, None)


# ---------------------------------------------------------------------------
# Retry decorator
# ---------------------------------------------------------------------------
# H12: restrict the default retry set to transient I/O so that user errors
# (bad ADQL syntax, invalid coordinates → ValueError / HTTPStatusError) are
# NOT retried and do NOT count toward the circuit breaker.  Callers that want
# to widen the set pass their own tuple.
_DEFAULT_RETRYABLE: tuple = (
    ConnectionError,
    TimeoutError,
    OSError,
)


def with_retry(
    max_retries: int = 2,
    base_delay: float = 0.5,
    max_delay: float = 10.0,
    backoff_factor: float = 2.0,
    retryable_exceptions: tuple = _DEFAULT_RETRYABLE,
):
    """Decorator that retries async functions with exponential backoff.

    Includes a circuit breaker: after ``CIRCUIT_FAILURE_THRESHOLD``
    consecutive failures the circuit opens and calls are rejected
    immediately for ``CIRCUIT_RECOVERY_TIMEOUT`` seconds, after which a
    single probe call is allowed (half-open).
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            circuit = _get_circuit(func.__qualname__)

            # --- Circuit breaker gate ---
            if circuit["state"] == "open":
                if time.monotonic() - circuit["last_failure"] > CIRCUIT_RECOVERY_TIMEOUT:
                    circuit["state"] = "half-open"
                    logger.info("Circuit half-open for %s — allowing probe call", func.__name__)
                else:
                    raise ConnectionError(
                        f"Circuit breaker open for {func.__name__} "
                        f"— service temporarily unavailable"
                    )

            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    result = await func(*args, **kwargs)
                    # Success — reset circuit
                    circuit["failures"] = 0
                    circuit["state"] = "closed"
                    return result
                except retryable_exceptions as e:
                    last_exception = e
                    # R10: structured connector_error metric so operators see
                    # transient-failure rate per connector in /metrics.
                    try:
                        from app.observability.metrics import record_counter
                        record_counter(
                            "connector_error_total", 1.0,
                            connector=func.__qualname__.split(".")[0],
                            kind=type(e).__name__,
                            retryable="true",
                        )
                    except Exception:
                        pass
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

            # All retries exhausted — update circuit breaker
            circuit["failures"] += 1
            circuit["last_failure"] = time.monotonic()
            if circuit["failures"] >= CIRCUIT_FAILURE_THRESHOLD:
                circuit["state"] = "open"
                logger.warning(
                    "Circuit breaker OPEN for %s after %d consecutive failures",
                    func.__name__, circuit["failures"],
                )
                try:
                    from app.observability.metrics import record_counter
                    record_counter(
                        "circuit_breaker_open_total", 1.0,
                        connector=func.__qualname__.split(".")[0],
                    )
                except Exception:
                    pass

            raise last_exception  # type: ignore[misc]
        return wrapper
    return decorator


def get_all_circuit_states() -> dict[str, dict]:
    """R10: expose circuit state so /metrics can emit a gauge per connector.

    Returns a shallow copy keyed by fully-qualified function name; values
    contain ``state`` (closed|half-open|open), ``failures`` count, and
    ``last_failure`` monotonic timestamp.
    """
    return {name: dict(state) for name, state in _circuit_state.items()}
