"""
GoLogin Service - GoLogin API + Profile Management
Following DDD guide specifications
"""

import asyncio
import httpx
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import Profile
from app.utils.logger import get_logger, log_gologin_api_call
from app.utils.retry import retry_gologin_api, with_timeout
from app.utils.exceptions import (
    GoLoginAPIException,
    ProfileNotFoundException,
    ConcurrentProfileLimitException,
    DatabaseConnectionException
)

logger = get_logger(__name__)

class GoLoginService:
    """
    GoLogin API integration and profile management
    Keeps semaphore logic and direct DB access as per DDD guide
    """

    def __init__(self):
        self.gologin_token = settings.gologin_token
        self.api_url = settings.gologin_api_url
        self.max_concurrent = settings.max_concurrent_profiles
        self.active_profiles: Dict[str, Dict] = {}
        self.profile_semaphore = asyncio.Semaphore(self.max_concurrent)
        self.client = None

    async def initialize(self) -> None:
        """Initialize the GoLogin service"""
        self.client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {self.gologin_token}",
                "Content-Type": "application/json"
            },
            timeout=30.0
        )

        logger.info(
            "gologin_service.initialized",
            max_concurrent=self.max_concurrent,
            api_url=self.api_url
        )

    async def cleanup(self) -> None:
        """Clean up service resources"""
        # Stop all active profiles
        for profile_id in list(self.active_profiles.keys()):
            await self.stop_profile(profile_id)

        # Close HTTP client
        if self.client:
            await self.client.aclose()

        logger.info("gologin_service.cleanup_completed")

    @retry_gologin_api
    @with_timeout(30.0)
    async def get_profiles(self) -> List[Dict]:
        """Get all profiles from GoLogin API"""
        start_time = datetime.utcnow()

        try:
            response = await self.client.get(f"{self.api_url}/profiles")
            response.raise_for_status()
            profiles = response.json()

            duration_ms = (datetime.utcnow() - start_time).total_seconds() * 1000
            log_gologin_api_call(
                logger,
                endpoint="get_profiles",
                status_code=response.status_code,
                duration_ms=duration_ms
            )

            return profiles

        except httpx.HTTPStatusError as e:
            raise GoLoginAPIException(e.response.status_code, e.response.text)
        except httpx.RequestError as e:
            raise GoLoginAPIException(500, f"Connection error: {str(e)}")

    @retry_gologin_api
    @with_timeout(30.0)
    async def get_profile(self, profile_id: str) -> Optional[Dict]:
        """Get specific profile from GoLogin API"""
        start_time = datetime.utcnow()

        try:
            response = await self.client.get(f"{self.api_url}/profiles/{profile_id}")
            response.raise_for_status()
            profile = response.json()

            duration_ms = (datetime.utcnow() - start_time).total_seconds() * 1000
            log_gologin_api_call(
                logger,
                endpoint="get_profile",
                profile_id=profile_id,
                status_code=response.status_code,
                duration_ms=duration_ms
            )

            return profile

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise GoLoginAPIException(e.response.status_code, e.response.text)
        except httpx.RequestError as e:
            raise GoLoginAPIException(500, f"Connection error: {str(e)}")

    @retry_gologin_api
    @with_timeout(60.0)
    async def start_profile(self, profile_id: str) -> Dict:
        """Start a GoLogin profile and return connection info"""
        async with self.profile_semaphore:
            # Check if already active
            if profile_id in self.active_profiles:
                logger.info(
                    "gologin_profile.already_active",
                    profile_id=profile_id
                )
                return self.active_profiles[profile_id]

            # Check concurrent limit
            if len(self.active_profiles) >= self.max_concurrent:
                raise ConcurrentProfileLimitException()

            logger.info(
                "gologin_profile.starting",
                profile_id=profile_id
            )

            start_time = datetime.utcnow()

            try:
                response = await self.client.post(
                    f"{self.api_url}/profiles/{profile_id}/start",
                    json={"headless": getattr(settings, 'browser_headless', False)}
                )
                response.raise_for_status()
                result = response.json()

                profile_info = {
                    "profile_id": profile_id,
                    "ws_endpoint": result.get("wsEndpoint"),
                    "port": result.get("port"),
                    "started_at": datetime.utcnow()
                }

                self.active_profiles[profile_id] = profile_info

                duration_ms = (datetime.utcnow() - start_time).total_seconds() * 1000
                log_gologin_api_call(
                    logger,
                    endpoint="start_profile",
                    profile_id=profile_id,
                    status_code=response.status_code,
                    duration_ms=duration_ms
                )

                logger.info(
                    "gologin_profile.started",
                    profile_id=profile_id,
                    port=result.get("port"),
                    duration_ms=duration_ms
                )

                return profile_info

            except httpx.HTTPStatusError as e:
                raise GoLoginAPIException(e.response.status_code, e.response.text)
            except httpx.RequestError as e:
                raise GoLoginAPIException(500, f"Connection error: {str(e)}")

    @retry_gologin_api
    @with_timeout(30.0)
    async def stop_profile(self, profile_id: str) -> bool:
        """Stop a GoLogin profile"""
        if profile_id not in self.active_profiles:
            return True

        logger.info(
            "gologin_profile.stopping",
            profile_id=profile_id
        )

        start_time = datetime.utcnow()

        try:
            response = await self.client.post(f"{self.api_url}/profiles/{profile_id}/stop")
            response.raise_for_status()

            # Remove from active profiles
            del self.active_profiles[profile_id]

            duration_ms = (datetime.utcnow() - start_time).total_seconds() * 1000
            log_gologin_api_call(
                logger,
                endpoint="stop_profile",
                profile_id=profile_id,
                status_code=response.status_code,
                duration_ms=duration_ms
            )

            logger.info(
                "gologin_profile.stopped",
                profile_id=profile_id,
                duration_ms=duration_ms
            )

            return True

        except Exception as e:
            logger.error(
                "gologin_profile.stop_failed",
                profile_id=profile_id,
                error=str(e),
                exc_info=True
            )
            return False

    async def get_profile_by_name(self, profile_name: str, db: Session) -> Optional[Profile]:
        """Find profile by GoLogin profile name"""
        try:
            profile = db.query(Profile).filter(
                Profile.profile_name == profile_name.lower()
            ).first()

            if profile:
                logger.debug(
                    "profile.found_by_name",
                    profile_name=profile_name,
                    profile_id=profile.id
                )
            else:
                logger.warning(
                    "profile.not_found_by_name",
                    profile_name=profile_name
                )

            return profile

        except Exception as e:
            logger.error(
                "profile.database_error",
                profile_name=profile_name,
                error=str(e),
                exc_info=True
            )
            raise DatabaseConnectionException(str(e))

    async def sync_profiles(self, force: bool = False) -> Dict:
        """Sync profiles from GoLogin API to database - Direct DB operations as per DDD guide"""
        logger.info(
            "profile_sync.started",
            force=force
        )

        db = SessionLocal()

        try:
            # Get profiles from GoLogin API
            gologin_profiles = await self.get_profiles()

            new_count = 0
            updated_count = 0

            for gl_profile in gologin_profiles:
                profile_id = gl_profile.get("id")
                profile_name = gl_profile.get("name", "").lower().strip()

                if not profile_name or not profile_id:
                    logger.warning(
                        "profile_sync.invalid_profile",
                        profile_id=profile_id,
                        profile_name=profile_name
                    )
                    continue

                # Check if profile exists
                existing = db.query(Profile).filter(Profile.id == profile_id).first()

                if existing:
                    # Update existing profile
                    existing.profile_name = profile_name
                    existing.display_name = gl_profile.get("name")
                    existing.proxy = gl_profile.get("proxy")
                    existing.browser_type = gl_profile.get("browserType", "chrome")
                    existing.last_sync = datetime.utcnow()
                    updated_count += 1

                    logger.debug(
                        "profile_sync.updated",
                        profile_id=profile_id,
                        profile_name=profile_name
                    )

                else:
                    # Create new profile
                    new_profile = Profile(
                        id=profile_id,
                        profile_name=profile_name,
                        display_name=gl_profile.get("name"),
                        proxy=gl_profile.get("proxy"),
                        browser_type=gl_profile.get("browserType", "chrome"),
                        status="active",
                        last_sync=datetime.utcnow()
                    )
                    db.add(new_profile)
                    new_count += 1

                    logger.debug(
                        "profile_sync.created",
                        profile_id=profile_id,
                        profile_name=profile_name
                    )

            # Commit changes
            db.commit()

            result = {
                "total": len(gologin_profiles),
                "new": new_count,
                "updated": updated_count
            }

            logger.info(
                "profile_sync.completed",
                **result
            )

            return result

        except Exception as e:
            logger.error(
                "profile_sync.failed",
                error=str(e),
                exc_info=True
            )
            db.rollback()
            raise

        finally:
            db.close()

    def get_active_profiles_count(self) -> int:
        """Get count of currently active profiles"""
        return len(self.active_profiles)

    async def cleanup_stale_profiles(self) -> None:
        """Clean up stale profile connections"""
        stale_threshold = datetime.utcnow() - timedelta(minutes=30)
        stale_profiles = [
            pid for pid, info in self.active_profiles.items()
            if info["started_at"] < stale_threshold
        ]

        if stale_profiles:
            logger.warning(
                "profile_cleanup.stale_found",
                stale_count=len(stale_profiles),
                stale_profiles=stale_profiles
            )

            for profile_id in stale_profiles:
                try:
                    await self.stop_profile(profile_id)
                    logger.info(
                        "profile_cleanup.stale_removed",
                        profile_id=profile_id
                    )
                except Exception as e:
                    logger.error(
                        "profile_cleanup.failed",
                        profile_id=profile_id,
                        error=str(e)
                    )

    def get_profile_info(self, profile_id: str) -> Optional[Dict]:
        """Get information about active profile"""
        return self.active_profiles.get(profile_id)