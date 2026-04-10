"""In-memory sliding-window rate limiter (no external dependencies)."""

from __future__ import annotations

import random
import time
import threading
from collections import defaultdict

from fastapi import HTTPException, Request

MAX_REQUESTS = 5
WINDOW_SECONDS = 60
_CLEANUP_PROBABILITY = 0.01  # 1% chance per request to clean up stale entries

_lock = threading.Lock()
_hits: dict[str, list[float]] = defaultdict(list)


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def check_rate_limit(request: Request) -> None:
    """Raise 429 if the client IP exceeds MAX_REQUESTS within WINDOW_SECONDS."""
    ip = _get_client_ip(request)
    now = time.monotonic()
    cutoff = now - WINDOW_SECONDS

    with _lock:
        timestamps = _hits[ip]
        # Prune expired entries
        _hits[ip] = timestamps = [t for t in timestamps if t > cutoff]
        if len(timestamps) >= MAX_REQUESTS:
            raise HTTPException(429, "Too many requests, please try again later")
        timestamps.append(now)

        # Probabilistic cleanup of stale IPs to prevent unbounded memory growth
        if random.random() < _CLEANUP_PROBABILITY:
            stale = [k for k, v in _hits.items() if k != ip and all(t <= cutoff for t in v)]
            for k in stale:
                del _hits[k]
