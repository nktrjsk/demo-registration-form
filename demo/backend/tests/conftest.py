import asyncio
import os

import pytest
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession


# --- Selenium fixtures (for UI tests) ---

INTERNAL_FRONTEND_HOST = os.environ.get(
    "INTERNAL_FRONTEND_HOST",
    "demo-meeting-form-internal-frontend-0f13-live-dev",
)
INTERNAL_FRONTEND_URL = f"http://{INTERNAL_FRONTEND_HOST}:8080/admin/"

CHROMEDRIVER_PATH = os.environ.get("CHROMEDRIVER_PATH", "/usr/bin/chromedriver")


@pytest.fixture(scope="session")
def driver():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1280,800")
    service = Service(executable_path=CHROMEDRIVER_PATH)
    d = webdriver.Chrome(service=service, options=options)
    try:
        yield d
    finally:
        d.quit()


@pytest.fixture
def internal_frontend_url():
    return INTERNAL_FRONTEND_URL


# --- DB helpers ---
#
# Each call creates a fresh async engine so cleanup/inspection runs in its own
# event loop, independent of the app's engine (which the FastAPI TestClient
# binds to its own loop on first use).


def _db_url() -> str:
    user = os.environ.get("POSTGRES_USER", "admin")
    password = os.environ.get("POSTGRES_PASSWORD", "")
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db = os.environ.get("POSTGRES_DB", "postgres")
    return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{db}"


async def _with_session(coro_fn):
    engine = create_async_engine(_db_url())
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with Session() as session:
            return await coro_fn(session)
    finally:
        await engine.dispose()


def db_run(coro_fn):
    """Run a coroutine that takes a session, on a fresh engine + loop."""
    return asyncio.run(_with_session(coro_fn))


def clear_table(model_cls):
    async def _do(session):
        await session.execute(delete(model_cls))
        await session.commit()

    db_run(_do)
