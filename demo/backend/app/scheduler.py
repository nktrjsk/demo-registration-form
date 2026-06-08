"""APScheduler wrapper that triggers `create_if_demo_day` every day at 00:00
in the configured local timezone."""
import logging
import os
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.auto_create import LOCAL_TZ, create_if_demo_day, now_local
from app.database import async_session


logger = logging.getLogger(__name__)


_scheduler: AsyncIOScheduler | None = None


def is_disabled() -> bool:
    return os.environ.get("BITSWAN_DISABLE_SCHEDULER") == "1"


async def _daily_tick() -> None:
    async with async_session() as session:
        result = await create_if_demo_day(now_local(), session)
    if result is not None:
        logger.info("Auto-created Demo meeting for %s", result.isoformat())


async def catch_up_today(now: datetime | None = None) -> None:
    """Ensure today's instance exists if today is a demo day. APScheduler's
    cron trigger does not backfill missed firings, so a restart past 00:00
    on a demo day would otherwise leave the day with no instance."""
    if is_disabled():
        return
    when = now if now is not None else now_local()
    async with async_session() as session:
        result = await create_if_demo_day(when, session)
    if result is not None:
        logger.info("Startup backfill: ensured Demo meeting for %s", result.isoformat())


def start() -> None:
    global _scheduler
    if is_disabled():
        logger.info("Scheduler disabled via BITSWAN_DISABLE_SCHEDULER=1")
        return
    if _scheduler is not None:
        return
    _scheduler = AsyncIOScheduler(timezone=LOCAL_TZ)
    _scheduler.add_job(
        _daily_tick,
        CronTrigger(hour=0, minute=0, timezone=LOCAL_TZ),
        id="auto_create_demo_meeting",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("Scheduler started (timezone=%s)", LOCAL_TZ)


def shutdown() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
