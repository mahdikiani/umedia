import asyncio
import logging

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from apps.files import worker as files_worker

irst_timezone = pytz.timezone("Asia/Tehran")
logging.getLogger("apscheduler").setLevel(logging.WARNING)


async def worker() -> None:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        files_worker.remove_deleted_files,
        "cron",
        hour=3,
        minute=30,
        timezone=irst_timezone,
    )
    scheduler.add_job(
        files_worker.remove_deleted_directories,
        "cron",
        hour=3,
        minute=30,
        timezone=irst_timezone,
    )
    scheduler.add_job(
        files_worker.remove_old_access_files,
        "cron",
        hour=3,
        minute=30,
        timezone=irst_timezone,
    )

    scheduler.start()

    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        scheduler.shutdown()
