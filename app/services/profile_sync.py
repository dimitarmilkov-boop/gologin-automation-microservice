import asyncio
from datetime import datetime, timedelta
from loguru import logger

from app.config import settings

async def start_profile_sync():
    logger.info(f"Starting profile sync task (interval: {settings.profile_sync_interval}s)")

    async def sync_task():
        while True:
            try:
                from app.main import app
                if hasattr(app.state, 'profile_manager'):
                    profile_manager = app.state.profile_manager

                    logger.debug("Running scheduled profile sync")
                    result = await profile_manager.sync_profiles()

                    logger.info(f"Profile sync completed: {result['total']} total, "
                              f"{result['new']} new, {result['updated']} updated")

                    await profile_manager.cleanup_stale_profiles()

                await asyncio.sleep(settings.profile_sync_interval)

            except Exception as e:
                logger.error(f"Profile sync task error: {str(e)}")
                await asyncio.sleep(60)

    return asyncio.create_task(sync_task())