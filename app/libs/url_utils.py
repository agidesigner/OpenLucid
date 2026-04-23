"""Public-URL helper.

Anywhere we build a link that will be *handed to someone else* (guest share
URL, password-reset email, MCP preview URL) we face the same question: what
base URL do we prepend?

Priority:
  1. ``settings.APP_URL`` if the owner has configured a real value.
  2. Otherwise fall back to ``request.base_url`` — the scheme/host the
     current browser is talking to. Good enough for LAN / dev / first-run
     scenarios where nobody has set APP_URL yet.

Placeholder hosts from earlier test runs (``nihao.com``, ``example.com``,
``change-me``) are treated as "not configured" so a leftover value doesn't
poison every generated link.
"""
from __future__ import annotations

from fastapi import Request

from app.config import settings


_PLACEHOLDER_HOSTS: tuple[str, ...] = (
    "nihao.com",
    "example.com",
    "change-me",
)


def _looks_configured(url: str) -> bool:
    u = (url or "").strip().lower()
    if not u:
        return False
    return not any(bad in u for bad in _PLACEHOLDER_HOSTS)


def get_public_base_url(request: Request | None = None) -> str:
    """Return the best base URL for outbound links (no trailing slash).

    If ``settings.APP_URL`` is set to a non-placeholder value, use it.
    Else fall back to ``request.base_url`` — the host the browser is on
    right now. If no request is available (background task, CLI), use
    APP_URL as-is even if unset, so callers get a stable deterministic
    value (empty string if truly unconfigured).
    """
    if _looks_configured(settings.APP_URL):
        return settings.APP_URL.rstrip("/")
    if request is not None:
        return str(request.base_url).rstrip("/")
    return (settings.APP_URL or "").rstrip("/")
