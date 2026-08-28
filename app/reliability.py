"""Small retry primitive for transient Fulcra/network operations."""

from random import random
import socket
import time
from typing import Any, Callable, Optional, TypeVar
from urllib.error import HTTPError, URLError

T = TypeVar("T")


def is_retryable(exc: BaseException) -> bool:
    """Classify temporary DNS, connection, timeout, 429, and 5xx failures."""
    current: Optional[BaseException] = exc
    while current is not None:
        if isinstance(current, HTTPError):
            return current.code == 429 or 500 <= current.code < 600
        if isinstance(current, (URLError, TimeoutError, ConnectionError, socket.timeout)):
            return True
        if isinstance(current, OSError):
            # Includes temporary DNS/socket failures such as errno -3.
            return True
        current = current.__cause__ or current.__context__
    return False


def retry_call(
    operation: Callable[[], T],
    *,
    operation_name: str,
    attempts: int = 5,
    base_delay: float = 0.25,
    max_delay: float = 4.0,
    on_retry: Optional[Callable[[dict[str, Any]], None]] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    random_fn: Callable[[], float] = random,
) -> T:
    """Run an operation with bounded exponential backoff and jitter."""
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception as exc:
            if attempt >= attempts or not is_retryable(exc):
                raise
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            delay += delay * 0.2 * random_fn()
            if on_retry:
                on_retry(
                    {
                        "event": "retry",
                        "stage": "fulcra",
                        "operation": operation_name,
                        "attempt": attempt + 1,
                        "max_attempts": attempts,
                        "delay_seconds": round(delay, 3),
                        "error": str(exc),
                    }
                )
            sleep_fn(delay)
    raise AssertionError("retry loop exhausted unexpectedly")
