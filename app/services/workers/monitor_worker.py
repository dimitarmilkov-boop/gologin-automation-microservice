"""
Monitor Worker - Health Monitoring
Following DDD guide specifications
"""

import asyncio
import psutil
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import AuthorizationSession, Profile
from app.config import settings
from app.utils.logger import get_logger
from app.utils.exceptions import DatabaseConnectionException

logger = get_logger(__name__)

class MonitorWorker:
    """
    Health monitoring worker
    Collects metrics and checks system health every minute
    """

    def __init__(self):
        self.running = False
        self.monitor_interval = 60  # 1 minute in seconds
        self.alert_thresholds = {
            "failed_auth_rate": 0.5,  # 50% failure rate
            "memory_usage_percent": 85,  # 85% memory usage
            "pending_sessions_max": 100,  # Max 100 pending sessions
            "response_time_max_seconds": 30  # Max 30 seconds response time
        }

    async def run(self) -> None:
        """Monitor system health"""
        self.running = True

        logger.info(
            "monitor_worker.started",
            monitor_interval_seconds=self.monitor_interval,
            alert_thresholds=self.alert_thresholds
        )

        while self.running:
            try:
                metrics = await self._collect_metrics()
                await self._check_thresholds(metrics)

                logger.debug(
                    "monitor_worker.metrics_collected",
                    **metrics
                )

                await asyncio.sleep(self.monitor_interval)

            except Exception as e:
                logger.error(
                    "monitor_worker.error",
                    error=str(e),
                    exc_info=True
                )

                # Sleep for 30 seconds on error before retrying
                await asyncio.sleep(30)

        logger.info("monitor_worker.stopped")

    async def _collect_metrics(self) -> dict:
        """Collect system metrics"""
        db = SessionLocal()

        try:
            now = datetime.utcnow()
            hour_ago = now - timedelta(hours=1)

            # Database metrics
            total_profiles = db.query(Profile).filter(Profile.status == "active").count()

            # Authorization session metrics
            pending_sessions = db.query(AuthorizationSession).filter(
                AuthorizationSession.status == "pending"
            ).count()

            # Recent session metrics (last hour)
            recent_sessions = db.query(AuthorizationSession).filter(
                AuthorizationSession.started_at > hour_ago
            ).all()

            successful_recent = sum(1 for s in recent_sessions if s.status == "success")
            failed_recent = sum(1 for s in recent_sessions if s.status in ["error", "timeout"])
            total_recent = len(recent_sessions)

            # Calculate rates
            success_rate = (successful_recent / total_recent) if total_recent > 0 else 1.0
            failure_rate = (failed_recent / total_recent) if total_recent > 0 else 0.0

            # System metrics
            memory_info = psutil.virtual_memory()
            cpu_percent = psutil.cpu_percent(interval=1)

            # Application metrics
            from app.main import app
            active_profiles_count = 0
            if hasattr(app.state, 'profile_manager'):
                active_profiles_count = app.state.profile_manager.get_active_profiles_count()

            metrics = {
                # Database metrics
                "total_profiles": total_profiles,
                "pending_sessions": pending_sessions,
                "active_profiles": active_profiles_count,

                # Performance metrics
                "auth_success_rate_1h": success_rate,
                "auth_failure_rate_1h": failure_rate,
                "total_sessions_1h": total_recent,
                "successful_sessions_1h": successful_recent,
                "failed_sessions_1h": failed_recent,

                # System metrics
                "memory_usage_percent": memory_info.percent,
                "memory_available_gb": memory_info.available / (1024**3),
                "cpu_usage_percent": cpu_percent,

                # Configuration
                "max_concurrent_profiles": settings.max_concurrent_profiles,
                "profile_utilization_percent": (active_profiles_count / settings.max_concurrent_profiles) * 100,

                # Timestamp
                "collected_at": now
            }

            return metrics

        except Exception as e:
            logger.error(
                "monitor_worker.metrics_collection_failed",
                error=str(e),
                exc_info=True
            )
            raise DatabaseConnectionException(str(e))

        finally:
            db.close()

    async def _check_thresholds(self, metrics: dict) -> None:
        """Check if metrics exceed alert thresholds"""
        alerts = []

        # Check failure rate
        if metrics["auth_failure_rate_1h"] > self.alert_thresholds["failed_auth_rate"]:
            alerts.append({
                "type": "high_failure_rate",
                "value": metrics["auth_failure_rate_1h"],
                "threshold": self.alert_thresholds["failed_auth_rate"],
                "message": f"Authorization failure rate is {metrics['auth_failure_rate_1h']:.2%}"
            })

        # Check memory usage
        if metrics["memory_usage_percent"] > self.alert_thresholds["memory_usage_percent"]:
            alerts.append({
                "type": "high_memory_usage",
                "value": metrics["memory_usage_percent"],
                "threshold": self.alert_thresholds["memory_usage_percent"],
                "message": f"Memory usage is {metrics['memory_usage_percent']:.1f}%"
            })

        # Check pending sessions
        if metrics["pending_sessions"] > self.alert_thresholds["pending_sessions_max"]:
            alerts.append({
                "type": "high_pending_sessions",
                "value": metrics["pending_sessions"],
                "threshold": self.alert_thresholds["pending_sessions_max"],
                "message": f"Too many pending sessions: {metrics['pending_sessions']}"
            })

        # Check profile utilization
        if metrics["profile_utilization_percent"] > 90:
            alerts.append({
                "type": "high_profile_utilization",
                "value": metrics["profile_utilization_percent"],
                "threshold": 90,
                "message": f"Profile utilization is {metrics['profile_utilization_percent']:.1f}%"
            })

        # Log alerts
        if alerts:
            for alert in alerts:
                logger.warning(
                    "monitor_worker.alert",
                    alert_type=alert["type"],
                    value=alert["value"],
                    threshold=alert["threshold"],
                    message=alert["message"]
                )
        else:
            logger.debug("monitor_worker.all_thresholds_ok")

    def stop(self) -> None:
        """Stop the worker gracefully"""
        logger.info("monitor_worker.stop_requested")
        self.running = False

    def is_running(self) -> bool:
        """Check if worker is currently running"""
        return self.running

    async def get_current_metrics(self) -> dict:
        """Get current metrics without waiting for next iteration"""
        logger.info("monitor_worker.current_metrics_requested")

        try:
            metrics = await self._collect_metrics()
            return {"status": "success", "metrics": metrics}
        except Exception as e:
            logger.error(
                "monitor_worker.current_metrics_failed",
                error=str(e),
                exc_info=True
            )
            return {"status": "error", "message": str(e)}

    def update_thresholds(self, new_thresholds: dict) -> dict:
        """Update alert thresholds"""
        logger.info(
            "monitor_worker.thresholds_update_requested",
            new_thresholds=new_thresholds
        )

        try:
            # Validate threshold values
            for key, value in new_thresholds.items():
                if key in self.alert_thresholds:
                    if isinstance(value, (int, float)) and value > 0:
                        self.alert_thresholds[key] = value
                    else:
                        raise ValueError(f"Invalid threshold value for {key}: {value}")

            logger.info(
                "monitor_worker.thresholds_updated",
                updated_thresholds=self.alert_thresholds
            )

            return {
                "status": "success",
                "thresholds": self.alert_thresholds
            }

        except Exception as e:
            logger.error(
                "monitor_worker.threshold_update_failed",
                error=str(e)
            )
            return {"status": "error", "message": str(e)}