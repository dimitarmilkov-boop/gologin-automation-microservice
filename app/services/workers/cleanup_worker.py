"""
Cleanup Worker - Stale Session Cleanup
Following DDD guide specifications
"""

import asyncio
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import AuthorizationSession
from app.utils.logger import get_logger
from app.utils.exceptions import DatabaseConnectionException

logger = get_logger(__name__)

class CleanupWorker:
    """
    Stale session cleanup worker
    Cleans up old authorization sessions every hour
    """

    def __init__(self):
        self.running = False
        self.cleanup_interval = 3600  # 1 hour in seconds
        self.session_timeout_hours = 2  # Mark sessions as timeout after 2 hours

    async def run(self) -> None:
        """Main worker execution loop"""
        self.running = True

        logger.info(
            "cleanup_worker.started",
            cleanup_interval_minutes=self.cleanup_interval // 60,
            session_timeout_hours=self.session_timeout_hours
        )

        while self.running:
            try:
                await self._cleanup_iteration()

                logger.debug(
                    "cleanup_worker.sleeping",
                    sleep_seconds=self.cleanup_interval
                )

                await asyncio.sleep(self.cleanup_interval)

            except Exception as e:
                logger.error(
                    "cleanup_worker.error",
                    error=str(e),
                    exc_info=True
                )

                # Sleep for 5 minutes on error before retrying
                await asyncio.sleep(300)

        logger.info("cleanup_worker.stopped")

    async def _cleanup_iteration(self) -> None:
        """Single cleanup iteration"""
        start_time = datetime.utcnow()
        cutoff_time = start_time - timedelta(hours=self.session_timeout_hours)

        db = SessionLocal()

        try:
            logger.debug(
                "cleanup_worker.iteration_started",
                cutoff_time=cutoff_time
            )

            # Find stale sessions
            stale_sessions = db.query(AuthorizationSession).filter(
                AuthorizationSession.status == "pending",
                AuthorizationSession.started_at < cutoff_time
            ).all()

            if not stale_sessions:
                logger.debug("cleanup_worker.no_stale_sessions")
                return

            # Mark sessions as timed out
            timeout_count = 0
            for session in stale_sessions:
                session.status = "timeout"
                session.error_message = f"Session timed out after {self.session_timeout_hours} hours"
                session.completed_at = start_time
                timeout_count += 1

                logger.debug(
                    "cleanup_worker.session_timeout",
                    session_id=session.id,
                    account_id=session.account_id,
                    api_app=session.api_app,
                    started_at=session.started_at
                )

            # Commit changes
            db.commit()

            duration_seconds = (datetime.utcnow() - start_time).total_seconds()

            logger.info(
                "cleanup_worker.iteration_completed",
                stale_sessions_cleaned=timeout_count,
                duration_seconds=duration_seconds
            )

        except Exception as e:
            logger.error(
                "cleanup_worker.database_error",
                error=str(e),
                exc_info=True
            )
            db.rollback()
            raise DatabaseConnectionException(str(e))

        finally:
            db.close()

    def stop(self) -> None:
        """Stop the worker gracefully"""
        logger.info("cleanup_worker.stop_requested")
        self.running = False

    def is_running(self) -> bool:
        """Check if worker is currently running"""
        return self.running

    async def force_cleanup(self) -> dict:
        """Force an immediate cleanup iteration"""
        logger.info("cleanup_worker.force_cleanup_requested")

        try:
            await self._cleanup_iteration()
            return {"status": "success", "message": "Force cleanup completed"}
        except Exception as e:
            logger.error(
                "cleanup_worker.force_cleanup_failed",
                error=str(e),
                exc_info=True
            )
            return {"status": "error", "message": str(e)}

    async def get_cleanup_stats(self) -> dict:
        """Get cleanup statistics"""
        db = SessionLocal()

        try:
            # Count sessions by status
            pending_count = db.query(AuthorizationSession).filter(
                AuthorizationSession.status == "pending"
            ).count()

            timeout_count = db.query(AuthorizationSession).filter(
                AuthorizationSession.status == "timeout"
            ).count()

            completed_count = db.query(AuthorizationSession).filter(
                AuthorizationSession.status.in_(["success", "error"])
            ).count()

            # Count recent activity (last 24 hours)
            recent_cutoff = datetime.utcnow() - timedelta(hours=24)
            recent_sessions = db.query(AuthorizationSession).filter(
                AuthorizationSession.started_at > recent_cutoff
            ).count()

            return {
                "pending_sessions": pending_count,
                "timeout_sessions": timeout_count,
                "completed_sessions": completed_count,
                "recent_sessions_24h": recent_sessions,
                "cleanup_interval_minutes": self.cleanup_interval // 60,
                "session_timeout_hours": self.session_timeout_hours
            }

        except Exception as e:
            logger.error(
                "cleanup_worker.stats_error",
                error=str(e),
                exc_info=True
            )
            return {"error": str(e)}

        finally:
            db.close()