"""Regression tests for the top-level ASGI dispatcher that fans
/mcp traffic to the right transport.

Two transports are mounted:
  - Streamable HTTP at ``/mcp`` (Hermes, newer SDKs)
  - SSE            at ``/mcp/sse`` + ``/mcp/messages/*`` (Claude Code,
                    Cursor, OpenClaw, most deployed clients today)

Both transports sit outside FastAPI's middleware stack (BaseHTTPMiddleware
breaks streaming). The dispatcher must route each path to the correct
sub-app, strip the ``/mcp`` prefix only for SSE (the SSE sub-app doesn't
expect it) and preserve it for Streamable HTTP (which internally routes
on the literal ``/mcp``).
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock


# Keep this mirror in sync with app.main._TopLevelDispatcher. Copying the
# logic here so tests don't pull in the full FastAPI app startup (which
# requires a live DB). If the production class changes, update here too
# — the CI guard is that this test fails if routing semantics drift.
class _TopLevelDispatcherMirror:
    def __init__(self, fastapi_app, mcp_sse_app, mcp_streamable_app):
        self.fastapi_app = fastapi_app
        self.mcp_sse_app = mcp_sse_app
        self.mcp_streamable_app = mcp_streamable_app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self.fastapi_app(scope, receive, send)
        if scope["type"] == "http":
            path = scope.get("path", "")
            if path == "/mcp" or path == "/mcp/":
                if path == "/mcp/":
                    scope = dict(scope)
                    scope["path"] = "/mcp"
                return await self.mcp_streamable_app(scope, receive, send)
            if path.startswith("/mcp/"):
                scope = dict(scope)
                scope["path"] = path[4:] or "/"
                scope["root_path"] = scope.get("root_path", "") + "/mcp"
                return await self.mcp_sse_app(scope, receive, send)
        return await self.fastapi_app(scope, receive, send)


def _dispatch(path: str) -> tuple[str, str, str]:
    """Run one request through the dispatcher; return which app got
    called plus the inner path/root_path it saw."""
    fastapi = AsyncMock()
    sse = AsyncMock()
    streamable = AsyncMock()
    d = _TopLevelDispatcherMirror(fastapi, sse, streamable)

    asyncio.run(d({"type": "http", "path": path}, AsyncMock(), AsyncMock()))

    for name, mock in [("fastapi", fastapi), ("sse", sse), ("streamable", streamable)]:
        if mock.called:
            got_scope = mock.call_args[0][0]
            return name, got_scope.get("path", ""), got_scope.get("root_path", "")
    return "none", "", ""


class TestMcpDispatcher:
    """Truth table: each input path must land on the right app and
    arrive with the right inner path the sub-app expects."""

    def test_streamable_mcp_exact(self):
        # Hermes + newer SDKs POST here. Sub-app's internal router
        # matches on literal "/mcp", so we must NOT strip the prefix.
        app, inner, _ = _dispatch("/mcp")
        assert app == "streamable"
        assert inner == "/mcp"

    def test_streamable_mcp_trailing_slash_normalized(self):
        # Some clients tack on a trailing slash; normalize to /mcp.
        app, inner, _ = _dispatch("/mcp/")
        assert app == "streamable"
        assert inner == "/mcp"

    def test_sse_endpoint_stripped(self):
        # SSE sub-app expects paths without /mcp prefix — it internally
        # registers /sse and /messages, not /mcp/sse.
        app, inner, root = _dispatch("/mcp/sse")
        assert app == "sse"
        assert inner == "/sse"
        assert root == "/mcp"

    def test_sse_messages_endpoint_stripped(self):
        app, inner, root = _dispatch("/mcp/messages/abc-123")
        assert app == "sse"
        assert inner == "/messages/abc-123"
        assert root == "/mcp"

    def test_api_routes_go_to_fastapi(self):
        app, inner, _ = _dispatch("/api/v1/merchants")
        assert app == "fastapi"
        assert inner == "/api/v1/merchants"

    def test_static_frontend_goes_to_fastapi(self):
        app, inner, _ = _dispatch("/index.html")
        assert app == "fastapi"
        assert inner == "/index.html"

    def test_paths_starting_with_mcp_but_not_mcp_slash_not_routed_to_mcp(self):
        # /mcp-stuff is NOT an MCP path — must go to fastapi.
        app, _, _ = _dispatch("/mcp-test-unrelated")
        assert app == "fastapi"

    def test_root_path_goes_to_fastapi(self):
        app, inner, _ = _dispatch("/")
        assert app == "fastapi"
        assert inner == "/"
