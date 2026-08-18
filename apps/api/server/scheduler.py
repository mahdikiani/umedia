"""In-process scheduled maintenance."""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

logger = logging.getLogger(__name__)


async def run_maintenance() -> None:
    """Run lightweight periodic maintenance."""
    logger.info("Scheduled maintenance tick")


def build_scheduler() -> AsyncIOScheduler:
    """Build a scheduler safe for the single-process deployment."""
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        run_maintenance,
        "interval",
        id="maintenance",
        minutes=5,
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    return scheduler

