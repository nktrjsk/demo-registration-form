import logging
import os
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.database import init_db, shutdown_db, async_session
from app.models import GalleryImage, Person
from app import minio_client, scheduler as auto_scheduler
from app.routers.internal import router as internal_router
from app.routers.public import router as public_router

# uvicorn configures only its own loggers; without this, `app.*` log calls
# (scheduler/auth/etc.) propagate to a handler-less root and are dropped.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

logger = logging.getLogger(__name__)
app = FastAPI()


# --- Lifecycle ---


# Dev-only roster fixtures. Real employees auto-grow the roster via OIDC
# login (REQ-005), but the dev stage has no fake OIDC users — so without
# seeding, the admin attendance UI has nobody to act on. Gated strictly
# on stage to prevent leaking dummy rows into staging/production.
DEV_DUMMY_PEOPLE = [
    ("Anna Nováková", "anna.novakova@dummy.dev"),
    ("Petr Svoboda", "petr.svoboda@dummy.dev"),
    ("Lucie Procházková", "lucie.prochazkova@dummy.dev"),
    ("Tomáš Dvořák", "tomas.dvorak@dummy.dev"),
    ("Eva Novotná", "eva.novotna@dummy.dev"),
]


def _is_dev_stage() -> bool:
    return os.environ.get("BITSWAN_AUTOMATION_STAGE", "") in {"live-dev", "dev"}


async def seed_dev_dummies_into(session) -> int:
    """Idempotently insert dummy resolved Persons into the given session.

    Skips any row whose email already exists, so this is safe across
    restarts and won't disturb persons promoted from placeholders.
    Returns the number of rows inserted.
    """
    now = datetime.now(timezone.utc)
    existing_emails = set(
        (
            await session.execute(
                select(Person.email).where(
                    Person.email.in_([e for _, e in DEV_DUMMY_PEOPLE])
                )
            )
        ).scalars().all()
    )
    added = 0
    for display_name, email in DEV_DUMMY_PEOPLE:
        if email in existing_emails:
            continue
        session.add(Person(display_name=display_name, email=email, first_seen_at=now))
        added += 1
    if added:
        await session.commit()
    return added


async def _seed_dev_dummies():
    """Startup wrapper for seed_dev_dummies_into: gates on stage flags
    and runs against the app's shared async session.

    Disabled via BITSWAN_DISABLE_DEV_SEED — tests set this in conftest
    so that the lifespan-startup seed doesn't pollute fresh Person tables.
    """
    if os.environ.get("BITSWAN_DISABLE_DEV_SEED"):
        return
    if not _is_dev_stage():
        return
    async with async_session() as db:
        added = await seed_dev_dummies_into(db)
    if added:
        logger.info("Seeded %d dummy persons (dev stage)", added)


async def _preseed_gallery():
    """Preseed the bitswan logo into MinIO and the gallery_images table."""
    key = "bitswan-logo.svg"
    async with async_session() as db:
        exists = await db.execute(
            select(GalleryImage).where(GalleryImage.key == key)
        )
        if exists.scalar_one_or_none():
            return
    await minio_client.preseed_logo()
    async with async_session() as db:
        db.add(GalleryImage(
            key=key,
            title="BitSwan Logo",
            content_type="image/svg+xml",
            size=0,
            uploaded_by="system",
        ))
        await db.commit()


@app.on_event("startup")
async def startup():
    await init_db()
    await minio_client.ensure_bucket()
    await _preseed_gallery()
    await _seed_dev_dummies()
    auto_scheduler.start()
    await auto_scheduler.catch_up_today()


@app.on_event("shutdown")
async def shutdown():
    auto_scheduler.shutdown()
    await shutdown_db()


# --- CORS ---

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Health ---


@app.get("/health")
def health():
    return {"status": "ok"}


# --- Routers ---

app.include_router(internal_router, prefix="/internal")
app.include_router(public_router, prefix="/public")
