"""
Profile Synchronization Worker
Following DDD guide specifications - focused on profile sync only
"""

import asyncio
from datetime import datetime, timedelta

from app.config import settings
from app.services.gologin_service import GoLoginService
from app.utils.logger import get_logger, log_profile_sync_completed
from app.utils.exceptions import GoLoginAPIException, DatabaseConnectionException

logger = get_logger(__name__)

class ProfileSyncWorker:
    """
    Profile synchronization worker
    Syncs profiles from GoLogin API to database every 15-30 minutes
    """

    def __init__(self, gologin_service: GoLoginService):
        self.gologin_service = gologin_service
        self.running = False
        self.sync_interval = settings.profile_sync_interval  # seconds

    async def run(self) -> None:
        """Main worker loop"""
        self.running = True

        logger.info(
            "sync_worker.started",
            sync_interval_minutes=self.sync_interval // 60
        )

        while self.running:
            try:
                await self._sync_iteration()

                logger.debug(
                    "sync_worker.sleeping",
                    sleep_seconds=self.sync_interval
                )

                await asyncio.sleep(self.sync_interval)

            except Exception as e:
                logger.error(
                    "sync_worker.error",
                    error=str(e),
                    exc_info=True
                )

                # Sleep for 1 minute on error before retrying
                await asyncio.sleep(60)

        logger.info("sync_worker.stopped")

    async def _sync_iteration(self) -> None:
        """Single sync iteration"""
        start_time = datetime.utcnow()

        try:
            logger.debug("sync_worker.iteration_started")

            # Sync profiles from GoLogin API
            result = await self.gologin_service.sync_profiles(force=False)

            # Clean up stale profiles
            await self.gologin_service.cleanup_stale_profiles()

            duration_seconds = (datetime.utcnow() - start_time).total_seconds()

            log_profile_sync_completed(
                logger,
                profiles_synced=result["total"],
                new_profiles=result["new"],
                updated_profiles=result["updated"]
            )

            logger.info(
                "sync_worker.iteration_completed",
                duration_seconds=duration_seconds,
                **result
            )

        except GoLoginAPIException as e:
            logger.error(
                "sync_worker.gologin_api_error",
                error_code=e.error_code,
                error=str(e)
            )
            raise

        except DatabaseConnectionException as e:
            logger.error(
                "sync_worker.database_error",
                error_code=e.error_code,
                error=str(e)
            )
            raise

        except Exception as e:
            logger.error(
                "sync_worker.unexpected_error",
                error=str(e),
                exc_info=True
            )
            raise

    def stop(self) -> None:
        """Stop the worker gracefully"""
        logger.info("sync_worker.stop_requested")
        self.running = False

    def is_running(self) -> bool:
        """Check if worker is currently running"""
        return self.running

    async def force_sync(self) -> dict:
        """Force an immediate sync iteration"""
        logger.info("sync_worker.force_sync_requested")

        try:
            await self._sync_iteration()
            return {"status": "success", "message": "Force sync completed"}
        except Exception as e:
            logger.error(
                "sync_worker.force_sync_failed",
                error=str(e),
                exc_info=True
            )
            return {"status": "error", "message": str(e)}