import re
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.api.health import health_router
from app.config import CACHE_TAG, VERSION, settings
from app.exceptions import register_exception_handlers
from app.libs.jwt_utils import decode_token


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    import asyncio
    import logging
    import os

    from app.libs.log_buffer import get_log_handler
    handler = get_log_handler()
    root = logging.getLogger()
    root.addHandler(handler)
    if root.level > logging.INFO:
        root.setLevel(logging.INFO)
    # Ensure uvicorn error logs (tracebacks) also reach the buffer
    logging.getLogger("uvicorn.error").addHandler(handler)

    logger = logging.getLogger(__name__)
    os.makedirs(settings.STORAGE_BASE_PATH, exist_ok=True)
    os.makedirs(os.path.join(settings.STORAGE_BASE_PATH, "composited"), exist_ok=True)

    logger.info("Cache tag: %s — rotates on every process start", CACHE_TAG)

    # 0a. Warn if SECRET_KEY is still the default — tokens can be forged
    _DEFAULT_KEYS = {
        "change-me-in-production-use-a-long-random-string",
        "change-me-in-production",
    }
    if settings.SECRET_KEY in _DEFAULT_KEYS and not settings.DISABLE_AUTH:
        logger.warning(
            "\n"
            "═══ INSECURE SECRET_KEY ══════════════════════════════════\n"
            "  SECRET_KEY is still the default value.\n"
            "  Anyone can forge JWT tokens and impersonate any user.\n"
            "  Set a strong random SECRET_KEY in your .env file.\n"
            "═════════════════════════════════════════════════════════\n"
        )

    # 0b. Verify database connectivity early — surface credential mismatches
    #    loudly instead of letting every request silently 500.
    try:
        from sqlalchemy import text
        from app.database import async_session_factory
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
        logger.info("Database connection verified")
    except Exception as e:
        logger.error(
            "═══ DATABASE CONNECTION FAILED ═══\n"
            "  %s\n"
            "  DATABASE_URL starts with: %s…\n"
            "  \n"
            "  Common fix: check that DB_USER / DB_PASSWORD in your .env\n"
            "  match the credentials the PostgreSQL volume was created with.\n"
            "  If you renamed the project (OpenInsight → OpenLucid), your\n"
            "  .env may still have the old credentials while the DB volume\n"
            "  uses the new defaults. Update .env or recreate the DB volume.\n"
            "═══════════════════════════════════",
            e, settings.DATABASE_URL[:40],
        )
        raise

    # 1. Run alembic migrations
    try:
        proc = await asyncio.create_subprocess_exec(
            "alembic", "upgrade", "head",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await proc.communicate()
        output = stdout.decode().strip() if stdout else ""
        if proc.returncode != 0:
            logger.error("alembic upgrade head failed:\n%s", output)
            raise RuntimeError(f"Alembic migration failed (exit code {proc.returncode})")
        if output:
            logger.info("alembic upgrade head:\n%s", output)
    except FileNotFoundError:
        logger.warning("alembic not found in PATH, skipping auto-migration")

    # 3. Background startup tasks (hash backfill + re-queue stuck parses)
    task = asyncio.create_task(_startup_recovery())
    task.add_done_callback(_log_task_exception)

    # 4. APP_URL sanity — surface-clarify rather than silently embed bogus
    # preview_urls in MCP responses. Agents receive useless URLs like
    # "http://nihao.com/..." when this is left as a placeholder.
    from app.config import settings as _settings
    _app_url = (_settings.APP_URL or "").strip().lower()
    _placeholders = ("nihao.com", "example.com", "change-me")
    if not _app_url or any(p in _app_url for p in _placeholders):
        logger.warning(
            "APP_URL appears to be a placeholder ('%s'). Preview URLs served via MCP will be suppressed "
            "until you set APP_URL to a public, agent-reachable URL in your .env or Settings → MCP.",
            _settings.APP_URL,
        )

    # 5. Start MCP Streamable HTTP session manager. When FastMCP is run
    # as a standalone app it does this via its own lifespan hook — but
    # we're mounting it behind our top-level dispatcher, which only
    # forwards lifespan to FastAPI. So we have to run the session
    # manager here to avoid a "Task group is not initialized" error on
    # the first /mcp request. (SSE transport doesn't need this — it
    # initializes per-connection inside sse_app.)
    from app.mcp_server import mcp as _mcp_server
    async with _mcp_server.session_manager.run():
        yield


def _log_task_exception(task: "asyncio.Task") -> None:
    """Log unhandled exceptions from background tasks."""
    import logging
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        logging.getLogger(__name__).error(
            "Background task %s failed: %s", task.get_name(), exc, exc_info=exc,
        )


async def _startup_recovery() -> None:
    """Backfill missing file hashes and re-queue stuck assets on startup."""
    import asyncio
    import hashlib
    import logging
    import os

    from sqlalchemy import select

    from app.adapters.storage import LocalStorageAdapter
    from app.database import async_session_factory
    from app.models.asset import Asset

    logger = logging.getLogger(__name__)
    storage = LocalStorageAdapter()

    try:
        # 1. Backfill file_hash for assets that predate the feature
        async with async_session_factory() as session:
            result = await session.execute(
                select(Asset).where(Asset.file_hash.is_(None), Asset.storage_uri.isnot(None))
            )
            assets_no_hash = list(result.scalars().all())
            backfilled = 0
            for asset in assets_no_hash:
                try:
                    path = storage.get_absolute_path(asset.storage_uri)
                    if os.path.exists(path):
                        with open(path, "rb") as f:
                            asset.file_hash = hashlib.sha256(f.read()).hexdigest()
                        backfilled += 1
                except Exception as e:
                    logger.warning("Hash backfill failed for asset %s: %s", asset.id, e)
            if backfilled:
                await session.commit()
                logger.info("Startup: backfilled file_hash for %d assets", backfilled)

        # 2. Re-queue assets stuck in pending/processing from a previous run
        async with async_session_factory() as session:
            result = await session.execute(
                select(Asset.id).where(Asset.parse_status.in_(["pending", "processing"]))
            )
            stuck_ids = [row[0] for row in result]

        if stuck_ids:
            logger.info("Startup: re-queuing %d stuck assets", len(stuck_ids))
            from app.api.assets import _parse_in_background
            for asset_id in stuck_ids:
                asyncio.create_task(_parse_in_background(asset_id))

        # 3. Reconcile the google media-provider mirror with the current
        # gemini LLM config. The mirror is normally maintained by hooks in
        # LLM CRUD, but pre-existing gemini rows (created before the hooks
        # landed) need a one-shot backfill — and we run it every boot so
        # operator edits straight into the DB don't leave the mirror stale.
        async with async_session_factory() as session:
            from app.application.setting_service import _sync_google_media_mirror
            await _sync_google_media_mirror(session)
            await session.commit()

    except Exception as e:
        logger.warning("Startup recovery encountered an error: %s", e, exc_info=True)


_fastapi_app = FastAPI(
    title="OpenLucid",
    description="Marketing world model — structure your data so AI can find it, understand it, and put it to work.",
    version=VERSION,
    lifespan=lifespan,
)

_cors_origins = ["*"] if settings.CORS_ORIGINS.strip() == "*" else [
    o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()
]
_cors_allow_credentials = settings.CORS_ORIGINS.strip() != "*"
_fastapi_app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)


MAX_BODY_SIZE = 300 * 1024 * 1024  # 300 MB


@_fastapi_app.middleware("http")
async def limit_body_size(request: Request, call_next):
    """Reject requests whose Content-Length exceeds 300 MB."""
    cl = request.headers.get("content-length")
    if cl and int(cl) > MAX_BODY_SIZE:
        return JSONResponse({"detail": "Request body too large (max 300 MB)"}, status_code=413)
    return await call_next(request)


# ── Cache-bust middleware ─────────────────────────────────────────────
#
# Problem: after a `docker compose up --build -d`, browsers still show
# stale /js/ and /css/ until the user hits F12 + Disable cache + reload.
# We flip the contract:
#   1. HTML is *always* revalidated (Cache-Control: no-cache). Unchanged
#      bytes come back as 304s, so bandwidth stays flat.
#   2. The HTML body is rewritten at serve time — every local reference
#      to /js/foo.js, /css/bar.css, /images/baz.png gets a ?v=<CACHE_TAG>
#      query-string suffix. CACHE_TAG rotates on every process start, so
#      a rebuild guarantees a fresh URL → the browser has no choice but
#      to re-download the file.
#   3. Static assets fetched WITH the ?v= param are treated as immutable
#      (max-age=1y). Browsers cache them forever until the URL changes.
#
# Net effect: zero user action needed after a rebuild. All future page
# loads pick up the new assets immediately.

_CACHE_BUST_SUFFIX = f"?v={CACHE_TAG}".encode()
# Matches local asset URLs in src/href attributes. Skips anything that
# already has a query (`?`) or fragment (`#`) so re-runs are idempotent
# and externally-versioned CDN paths don't get double-tagged.
_ASSET_HREF_RE = re.compile(rb'(src|href)="(/(?:js|css|images)/[^"?#]+)"')


def _rewrite_asset_ref(m: "re.Match[bytes]") -> bytes:
    return m.group(1) + b'="' + m.group(2) + _CACHE_BUST_SUFFIX + b'"'


@_fastapi_app.middleware("http")
async def cache_control(request: Request, call_next):
    response = await call_next(request)
    content_type = (response.headers.get("content-type") or "").lower()
    path = request.url.path

    # HTML: rewrite asset refs + force revalidation.
    if "text/html" in content_type:
        body = b""
        async for chunk in response.body_iterator:
            body += chunk if isinstance(chunk, (bytes, bytearray)) else chunk.encode()
        body = _ASSET_HREF_RE.sub(_rewrite_asset_ref, body)
        headers = dict(response.headers)
        headers["cache-control"] = "no-store, no-cache, must-revalidate, max-age=0"
        headers["pragma"] = "no-cache"
        headers["expires"] = "0"
        headers["content-length"] = str(len(body))
        # Length changed — drop any chunked-transfer markers the original
        # response may have carried so downstream honors the new length.
        headers.pop("transfer-encoding", None)
        # Strip validators: StaticFiles populates Last-Modified/ETag from
        # the file's mtime, but our rewritten body changes every process
        # start (new CACHE_TAG). If we left the validators, browsers would
        # send If-Modified-Since on next load, StaticFiles would return 304,
        # and the browser would keep its stale pre-rewrite cached HTML.
        # Drop them → browser has no basis to 304 → always gets fresh body.
        headers.pop("last-modified", None)
        headers.pop("etag", None)
        return Response(
            content=body,
            status_code=response.status_code,
            headers=headers,
            media_type="text/html",
        )

    # Static assets served with a ?v= param are content-addressed — safe
    # to cache aggressively. Without the param (someone typed /js/foo.js
    # directly), let the default short-cache behavior stand.
    if path.startswith(("/js/", "/css/", "/images/")) and request.query_params.get("v"):
        response.headers["cache-control"] = "public, max-age=31536000, immutable"

    return response


# Guest mode: only these (method, path_prefix) pairs may be invoked by a
# visitor holding a valid od_guest cookie. Every other non-GET request is
# refused with 403. GETs are unrestricted (owner can hide owner-only links
# in the UI; data reads power the content-creation apps).
GUEST_WRITE_ALLOWLIST: tuple[tuple[str, str], ...] = (
    ("POST", "/api/v1/ai/extract-text"),
    ("POST", "/api/v1/ai/infer-offer-knowledge"),
    ("POST", "/api/v1/ai/infer-offer-knowledge-stream"),
    ("POST", "/api/v1/apps/"),
    ("POST", "/api/v1/creations"),
    ("POST", "/api/v1/videos"),
    ("PATCH", "/api/v1/topic-plans/"),
    ("POST", "/api/v1/feedback"),
    ("POST", "/api/v1/auth/signout"),
)


def _guest_write_allowed(method: str, path: str) -> bool:
    if method == "GET":
        return True
    return any(method == m and path.startswith(p) for m, p in GUEST_WRITE_ALLOWLIST)


# ── Admin / configuration paths ─────────────────────────────────────────
#
# Paths that must require a real authenticated owner regardless of which
# non-owner identity reached the middleware (guest cookie, open-access
# fallback, future identity types). These leak secrets (API keys, MCP
# tokens) on read OR mutate the deployment on write — neither is
# something a shared-link visitor or an unconfigured deployment should
# get for free.
#
# ALL_PREFIXES: every method blocked (GET responses include API keys).
# WRITE_PREFIXES: only mutating methods blocked (GETs power content apps).
ADMIN_ALL_PREFIXES: tuple[str, ...] = ("/api/v1/settings/",)
ADMIN_WRITE_PREFIXES: tuple[str, ...] = ("/api/v1/brandkits/", "/api/v1/knowledge/")
ADMIN_WRITE_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _is_admin_path(method: str, path: str) -> bool:
    """True iff this (method, path) names a configuration / admin
    operation that must be restricted to real authenticated owners.

    Used by BOTH guest-cookie and open-access middleware branches —
    a single source of truth so adding a sensitive endpoint to the
    list applies uniformly to every non-owner identity.
    """
    if any(path.startswith(p) for p in ADMIN_ALL_PREFIXES):
        return True
    if method in ADMIN_WRITE_METHODS and any(path.startswith(p) for p in ADMIN_WRITE_PREFIXES):
        return True
    return False


# Guest cookie lifetime. Sliding session: every authenticated request
# refreshes the cookie's expiry, so an active guest never gets booted.
# Only 365 days of inactivity invalidates it client-side (and browsers
# cap at ~400 days regardless). Server-side the row never expires —
# owner toggle / regenerate is the only kill switch.
GUEST_COOKIE_MAX_AGE = 365 * 24 * 3600


def _set_guest_cookie(response, raw_token: str) -> None:
    response.set_cookie(
        "od_guest",
        value=raw_token,
        httponly=True,
        samesite="lax",
        path="/",
        max_age=GUEST_COOKIE_MAX_AGE,
        secure=settings.APP_URL.startswith("https"),
    )


@_fastapi_app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path

    # Only protect /api/* routes
    if not path.startswith("/api/"):
        return await call_next(request)

    # Public auth endpoints (no token required)
    PUBLIC = {
        "/api/v1/auth/setup-status",
        "/api/v1/auth/setup",
        "/api/v1/auth/signin",
        "/api/v1/auth/signout",
        "/api/v1/auth/forgot-password",
        "/api/v1/auth/reset-password",
        "/health",
    }

    # Admin-path enforcement uses the module-level ``_is_admin_path`` —
    # see definition above. Both the guest and open-access branches
    # reuse the same rules so adding a new admin prefix automatically
    # applies to every non-owner identity.
    if path in PUBLIC:
        return await call_next(request)

    # Asset files are public (single-user self-hosted; no multi-tenant risk).
    # WARNING: If deploying to public internet, consider adding auth here
    # to prevent unauthorized enumeration and download of uploaded assets.
    if "/assets/" in path and path.endswith(("/file", "/thumbnail")):
        return await call_next(request)

    # Allow bypass in test/dev mode
    if settings.DISABLE_AUTH:
        return await call_next(request)

    # 1. Try cookie-based JWT auth (owner session)
    token = request.cookies.get("od_access_token")
    if token:
        try:
            payload = decode_token(token)
            request.state.user_id = payload["user_id"]
            request.state.is_guest = False
            return await call_next(request)
        except Exception:
            return JSONResponse({"detail": "Invalid token"}, status_code=401)

    # 2. Try Bearer token auth (reuse MCP tokens table for CLI / API key access)
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        import hashlib
        from sqlalchemy import select
        from app.database import async_session_factory
        from app.models.mcp_token import McpToken

        raw_token = auth_header[7:]
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        try:
            async with async_session_factory() as session:
                match = await session.scalar(
                    select(McpToken).where(McpToken.token_hash == token_hash)
                )
            if match:
                from app.api.auth import SENTINEL_API_TOKEN
                request.state.user_id = SENTINEL_API_TOKEN
                request.state.is_guest = False
                return await call_next(request)
        except Exception:
            pass
        return JSONResponse({"detail": "Invalid API token"}, status_code=401)

    # 3. Try guest cookie (shareable WebUI link)
    guest_token = request.cookies.get("od_guest")
    if guest_token:
        from app.application import guest_access_service
        from app.database import async_session_factory

        try:
            async with async_session_factory() as session:
                ok = await guest_access_service.verify(session, guest_token)
        except Exception:
            ok = False
        if ok:
            # Same admin gate as the open-access fallback — guests must
            # never reach /settings/* or write to /brandkits / /knowledge,
            # regardless of method. Without this, GET /settings/llm
            # leaks API keys to anyone with a guest link.
            if _is_admin_path(request.method, path):
                return JSONResponse(
                    {"detail": "Guest mode cannot access admin / configuration endpoints"},
                    status_code=403,
                )
            if not _guest_write_allowed(request.method, path):
                return JSONResponse(
                    {"detail": "Guest mode is read-only on this endpoint"},
                    status_code=403,
                )
            from app.api.auth import SENTINEL_GUEST
            request.state.user_id = SENTINEL_GUEST
            request.state.is_guest = True
            response = await call_next(request)
            # Sliding session: every authenticated request pushes the
            # cookie's expiry back out to GUEST_COOKIE_MAX_AGE. An active
            # guest never gets logged out.
            _set_guest_cookie(response, guest_token)
            return response
        # Cookie present but no match — owner likely disabled guest mode.
        return JSONResponse({"detail": "Guest session expired"}, status_code=401)

    # 4. "No tokens configured" open-access fallback.
    # Mirrors the MCP endpoint's policy in ``_mcp_token_check``: when the
    # deployment has never minted an MCP token, treat unauthenticated
    # requests as allowed. Rationale:
    #   * The MCP path already behaves this way — an agent-caller on a
    #     fresh deployment can hit /mcp/* without a token. Having /api/*
    #     diverge ("MCP open, API locked") was the bug surfacing as
    #     "openlucid refuses to work even though no tokens are set".
    #   * Once the operator mints a token (Web UI → Settings → MCP), BOTH
    #     /mcp/* and /api/* require it — consistent lock/unlock semantics.
    #   * The owner's WebUI still uses cookie auth (step 1 above), so
    #     minting a token doesn't lock the owner out of the browser.
    # Skipped when ``DISABLE_AUTH`` is explicitly False (via an env, for
    # hardened deployments — unaffected here since DISABLE_AUTH was
    # already handled above).
    # IMPORTANT: scope this except to the DB lookup ONLY. Wrapping
    # ``call_next(request)`` in the same try would swallow any downstream
    # handler exception (e.g. an AttributeError in a service method) and
    # convert it into a misleading 401 "Not authenticated" — that's how
    # the v1.2.2-era OfferService.list indentation bug masqueraded as an
    # auth problem for ~30 minutes of debugging.
    open_access = False
    try:
        from sqlalchemy import func, select
        from app.database import async_session_factory
        from app.models.mcp_token import McpToken

        async with async_session_factory() as session:
            token_count = await session.scalar(select(func.count()).select_from(McpToken))
        open_access = not token_count
    except Exception:
        # DB lookup failed — better to reject than to accidentally open
        # under an error path.
        open_access = False

    if open_access:
        # Even in open-access mode, configuration / admin surfaces
        # require real authentication. A signed-out visitor on a
        # fresh deployment must NOT be able to read API keys via
        # /settings/* or rewrite the KB / brandkit. Once the
        # operator signs in (cookie JWT) or mints an MCP token,
        # those sessions reach the path above and bypass this gate.
        if _is_admin_path(request.method, path):
            return JSONResponse({"detail": "Sign in required"}, status_code=401)

        from app.api.auth import SENTINEL_NO_AUTH
        request.state.user_id = SENTINEL_NO_AUTH
        request.state.is_guest = False
        return await call_next(request)

    return JSONResponse({"detail": "Not authenticated"}, status_code=401)


register_exception_handlers(_fastapi_app)

_fastapi_app.include_router(health_router)
_fastapi_app.include_router(api_router, prefix="/api/v1")


# Serve the logo as favicon.ico so browsers' default /favicon.ico request
# doesn't 404 on every page load. Registered BEFORE the "/" static mount so
# the route wins the match. Accepts GET (browsers) and HEAD (crawlers).
@_fastapi_app.api_route("/favicon.ico", methods=["GET", "HEAD"], include_in_schema=False)
async def _favicon():
    from fastapi.responses import FileResponse
    return FileResponse("frontend/images/logo.png", media_type="image/png")


# Guest-mode portal: owner shares APP_URL/guest-access?t=<secret>; clicking
# verifies the token, drops an HttpOnly cookie, and redirects to the app.
# Returns 404 on miss so the endpoint doesn't reveal whether guest mode is on.
@_fastapi_app.get("/guest-access", include_in_schema=False)
async def _guest_access_portal(request: Request):
    from fastapi.responses import RedirectResponse
    from app.application import guest_access_service
    from app.database import async_session_factory

    raw_token = request.query_params.get("t", "")
    if not raw_token:
        return JSONResponse({"detail": "Not found"}, status_code=404)

    try:
        async with async_session_factory() as session:
            ok = await guest_access_service.verify(session, raw_token)
    except Exception:
        ok = False
    if not ok:
        return JSONResponse({"detail": "Not found"}, status_code=404)

    resp = RedirectResponse(url="/", status_code=302)
    _set_guest_cookie(resp, raw_token)
    return resp


_fastapi_app.mount("/uploads", StaticFiles(directory=settings.STORAGE_BASE_PATH), name="uploads")
_fastapi_app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")


# ── MCP: completely isolated from FastAPI middleware chain ─────────
#
# BaseHTTPMiddleware wraps ALL response bodies in a streaming pipeline
# that breaks SSE (causes AssertionError + ClosedResourceError).
# Mounting MCP inside FastAPI means it goes through that pipeline.
#
# Solution: top-level ASGI dispatcher routes /mcp/* to the MCP app
# BEFORE FastAPI's middleware stack ever sees the request.

from app.mcp_server import mcp as mcp_server

# Dual-transport MCP exposure:
#   /mcp/sse       → SSE (legacy transport — Claude Code, Cursor, OpenClaw)
#   /mcp           → Streamable HTTP (modern transport — Hermes, newer SDKs)
#
# Both transports share the same tool registry + token auth. Clients that
# implement only one of the two can pick the matching URL. This keeps the
# server future-proof: Streamable HTTP is the MCP spec's direction of
# travel (SSE is being deprecated), but we can't drop SSE yet because
# most deployed clients still speak it.
_mcp_sse_app = mcp_server.sse_app()
_mcp_streamable_app = mcp_server.streamable_http_app()


async def _asgi_json_response(send, status: int, body: dict):
    """Send a JSON error response via raw ASGI."""
    import json as _json
    payload = _json.dumps(body).encode()
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            [b"content-type", b"application/json"],
            [b"content-length", str(len(payload)).encode()],
        ],
    })
    await send({"type": "http.response.body", "body": payload})


async def _mcp_token_check(scope, receive, send) -> bool:
    """Check MCP token auth. Returns True if request is allowed.
    Single DB query: look up token directly. If no tokens exist, the table is empty
    and the query returns None — same as "open access"."""
    import hashlib
    from sqlalchemy import select, func
    from app.database import async_session_factory
    from app.models.mcp_token import McpToken

    headers = dict(scope.get("headers", []))
    auth_header = (headers.get(b"authorization", b"")).decode()

    if not auth_header.startswith("Bearer "):
        # No token provided — check if any tokens are configured
        async with async_session_factory() as session:
            count = await session.scalar(select(func.count()).select_from(McpToken))
            if not count:
                return True  # No tokens configured — open access
        await _asgi_json_response(send, 401, {"detail": "MCP token required"})
        return False

    # Token provided — validate it (single query)
    raw_token = auth_header[7:]
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    async with async_session_factory() as session:
        match = await session.scalar(
            select(McpToken).where(McpToken.token_hash == token_hash)
        )
        if not match:
            await _asgi_json_response(send, 401, {"detail": "Invalid MCP token"})
            return False
        # Record usage — feeds the "Connected Agents" list in Settings.
        from datetime import datetime, timezone
        match.last_used_at = datetime.now(timezone.utc)
        await session.commit()
    return True


class _TopLevelDispatcher:
    """Top-level ASGI app that routes MCP traffic to the right transport
    and everything else to FastAPI. MCP apps stay outside FastAPI's
    middleware stack because BaseHTTPMiddleware wraps response bodies in
    a streaming pipeline that breaks both SSE and streamable-http.

    Routing:
      /mcp            → streamable_http_app (Streamable HTTP transport)
      /mcp/sse        → sse_app (SSE transport, legacy)
      /mcp/messages/* → sse_app (SSE's back-channel POST endpoint)
      else            → FastAPI (all the REST routes + static frontend)
    """

    def __init__(self, fastapi_app, mcp_sse_app, mcp_streamable_app):
        self.fastapi_app = fastapi_app
        self.mcp_sse_app = mcp_sse_app
        self.mcp_streamable_app = mcp_streamable_app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            # Forward lifespan to FastAPI (DB migrations, startup tasks, etc.).
            # MCP transports manage per-connection lifecycles internally.
            return await self.fastapi_app(scope, receive, send)

        if scope["type"] == "http":
            path = scope.get("path", "")

            # Streamable HTTP: exact /mcp (with optional trailing slash).
            # Client libraries typically POST JSON-RPC here; the streamable
            # app's internal router also expects the literal path "/mcp",
            # so we pass scope through unchanged (no prefix strip).
            if path == "/mcp" or path == "/mcp/":
                if not await _mcp_token_check(scope, receive, send):
                    return
                # Normalize to /mcp so the internal route matches.
                if path == "/mcp/":
                    scope = dict(scope)
                    scope["path"] = "/mcp"
                return await self.mcp_streamable_app(scope, receive, send)

            # SSE transport: /mcp/sse, /mcp/messages/...
            # The SSE app expects paths without the /mcp prefix.
            if path.startswith("/mcp/"):
                scope = dict(scope)
                scope["path"] = path[4:] or "/"
                scope["root_path"] = scope.get("root_path", "") + "/mcp"
                if not await _mcp_token_check(scope, receive, send):
                    return
                return await self.mcp_sse_app(scope, receive, send)

        return await self.fastapi_app(scope, receive, send)


# This is the ASGI app that uvicorn runs.
# `/mcp`         → Streamable HTTP app (Hermes, newer SDKs)
# `/mcp/sse/*`   → SSE app (Claude Code, Cursor, OpenClaw, ...)
# `/*`           → FastAPI app (with full middleware stack)
app = _TopLevelDispatcher(_fastapi_app, _mcp_sse_app, _mcp_streamable_app)
