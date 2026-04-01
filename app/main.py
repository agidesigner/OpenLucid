from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.api.health import health_router
from app.config import VERSION, settings
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
            raise RuntimeError("Database migration failed")
        if output:
            logger.info("alembic upgrade head:\n%s", output)
    except FileNotFoundError:
        logger.warning("alembic not found in PATH, skipping auto-migration")

    # 2. Belt-and-suspenders: ensure every column that was added after the
    #    initial migration exists. Uses IF NOT EXISTS so it is idempotent and
    #    safe to run on every startup, even when alembic records are in sync.
    await _ensure_schema()

    # 3. Background startup tasks (hash backfill + re-queue stuck parses)
    asyncio.create_task(_startup_recovery())
    yield


async def _ensure_schema() -> None:
    """Add any columns that may be missing due to migration drift."""
    import logging
    from sqlalchemy import text
    from app.database import async_session_factory

    logger = logging.getLogger(__name__)

    # Each tuple: (table, column, pg_type)
    columns = [
        ("assets", "title",        "VARCHAR(512)"),
        ("assets", "content_text", "TEXT"),
        ("assets", "hook_score",   "FLOAT"),
        ("assets", "reuse_score",  "FLOAT"),
        ("assets", "file_hash",    "VARCHAR(64)"),
    ]

    added = []
    async with async_session_factory() as session:
        for table, col, col_type in columns:
            try:
                await session.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {col_type}")
                )
                added.append(f"{table}.{col}")
            except Exception as e:
                logger.warning("Schema ensure failed for %s.%s: %s", table, col, e)
        await session.commit()

    if added:
        logger.info("Schema self-heal: ensured columns %s", added)


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

    except Exception as e:
        logger.warning("Startup recovery encountered an error: %s", e, exc_info=True)


app = FastAPI(
    title="OpenLucid",
    description="Marketing world model — structure your data so AI can find it, understand it, and put it to work.",
    version=VERSION,
    lifespan=lifespan,
)

_cors_origins = ["*"] if settings.CORS_ORIGINS.strip() == "*" else [
    o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


MAX_BODY_SIZE = 300 * 1024 * 1024  # 300 MB


@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    """Reject requests whose Content-Length exceeds 300 MB."""
    cl = request.headers.get("content-length")
    if cl and int(cl) > MAX_BODY_SIZE:
        return JSONResponse({"detail": "Request body too large (max 300 MB)"}, status_code=413)
    return await call_next(request)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path

    # MCP token auth: required only when tokens exist
    if path.startswith("/mcp/"):
        import hashlib
        from sqlalchemy import select, func
        from app.database import async_session_factory
        from app.models.mcp_token import McpToken

        async with async_session_factory() as session:
            count = await session.scalar(select(func.count()).select_from(McpToken))
            if count and count > 0:
                auth_header = request.headers.get("authorization", "")
                if not auth_header.startswith("Bearer "):
                    return JSONResponse({"detail": "MCP token required"}, status_code=401)
                raw_token = auth_header[7:]
                token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
                match = await session.scalar(
                    select(McpToken).where(McpToken.token_hash == token_hash)
                )
                if not match:
                    return JSONResponse({"detail": "Invalid MCP token"}, status_code=401)
        return await call_next(request)

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
    if path in PUBLIC:
        return await call_next(request)

    # Allow bypass in test/dev mode
    if settings.DISABLE_AUTH:
        return await call_next(request)

    token = request.cookies.get("od_access_token")
    if not token:
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)
    try:
        payload = decode_token(token)
        request.state.user_id = payload["user_id"]
    except Exception:
        return JSONResponse({"detail": "Invalid token"}, status_code=401)

    return await call_next(request)


register_exception_handlers(app)

app.include_router(health_router)
app.include_router(api_router, prefix="/api/v1")

from app.mcp_server import mcp as mcp_server
app.mount("/mcp", mcp_server.sse_app())

app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
