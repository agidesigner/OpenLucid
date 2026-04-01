from collections.abc import AsyncGenerator

import asyncio
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.database import get_db
from app.main import app
from app.models import Base

# Derive a separate test database URL to avoid clobbering dev data
_test_db_url = settings.DATABASE_URL.rsplit("/", 1)[0] + "/openlucid_test"

# Use NullPool to avoid connection reuse issues in tests
test_engine = create_async_engine(_test_db_url, poolclass=NullPool)
test_session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def _ensure_test_database() -> None:
    """Create the test database if it doesn't exist."""
    admin_url = settings.DATABASE_URL.rsplit("/", 1)[0] + "/postgres"
    admin_engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    async with admin_engine.connect() as conn:
        exists = (await conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = 'openlucid_test'")
        )).scalar()
        if not exists:
            await conn.execute(text("CREATE DATABASE openlucid_test"))
    await admin_engine.dispose()


# Create the test database at import time (before any async fixtures run)
asyncio.run(_ensure_test_database())


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    """Ensure tables exist, truncate data after each test."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        # Truncate all tables with CASCADE to handle FK constraints
        table_names = ", ".join(f'"{t.name}"' for t in reversed(Base.metadata.sorted_tables))
        if table_names:
            await conn.execute(text(f"TRUNCATE TABLE {table_names} CASCADE"))


async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
    async with test_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


app.dependency_overrides[get_db] = override_get_db


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
