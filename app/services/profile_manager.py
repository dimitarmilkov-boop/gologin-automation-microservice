import asyncio
import httpx
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings
from app.database import SessionLocal
from app.models import Profile

class ProfileManager:
    def __init__(self):
        self.gologin_token = settings.gologin_token
        self.api_url = settings.gologin_api_url
        self.max_concurrent = settings.max_concurrent_profiles
        self.active_profiles: Dict[str, Dict] = {}
        self.profile_semaphore = asyncio.Semaphore(self.max_concurrent)
        self.client = None

    async def initialize(self):
        self.client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {self.gologin_token}"},
            timeout=30.0
        )
        logger.info(f"ProfileManager initialized with max {self.max_concurrent} concurrent profiles")

    async def cleanup(self):
        for profile_id in list(self.active_profiles.keys()):
            await self.stop_profile(profile_id)
        if self.client:
            await self.client.aclose()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def get_profiles(self) -> List[Dict]:
        response = await self.client.get(f"{self.api_url}/profiles")
        response.raise_for_status()
        return response.json()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def get_profile(self, profile_id: str) -> Optional[Dict]:
        try:
            response = await self.client.get(f"{self.api_url}/profiles/{profile_id}")
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def start_profile(self, profile_id: str) -> Dict:
        async with self.profile_semaphore:
            if profile_id in self.active_profiles:
                logger.info(f"Profile {profile_id} already active")
                return self.active_profiles[profile_id]

            logger.info(f"Starting GoLogin profile: {profile_id}")

            response = await self.client.post(
                f"{self.api_url}/profiles/{profile_id}/start",
                json={"headless": settings.browser_headless}
            )
            response.raise_for_status()
            result = response.json()

            self.active_profiles[profile_id] = {
                "profile_id": profile_id,
                "ws_endpoint": result.get("wsEndpoint"),
                "port": result.get("port"),
                "started_at": datetime.utcnow()
            }

            logger.info(f"Profile {profile_id} started on port {result.get('port')}")
            return self.active_profiles[profile_id]

    async def stop_profile(self, profile_id: str) -> bool:
        if profile_id not in self.active_profiles:
            return True

        try:
            logger.info(f"Stopping GoLogin profile: {profile_id}")
            response = await self.client.post(f"{self.api_url}/profiles/{profile_id}/stop")
            response.raise_for_status()

            del self.active_profiles[profile_id]
            logger.info(f"Profile {profile_id} stopped")
            return True

        except Exception as e:
            logger.error(f"Error stopping profile {profile_id}: {str(e)}")
            return False

    async def sync_profiles(self, force: bool = False) -> Dict:
        logger.info("Syncing GoLogin profiles")
        db = SessionLocal()

        try:
            gologin_profiles = await self.get_profiles()

            new_count = 0
            updated_count = 0

            for gl_profile in gologin_profiles:
                profile_id = gl_profile.get("id")
                account_name = gl_profile.get("name", "").lower()

                if not account_name:
                    continue

                existing = db.query(Profile).filter(Profile.id == profile_id).first()

                if existing:
                    existing.account_id = account_name
                    existing.name = gl_profile.get("name")
                    existing.proxy = gl_profile.get("proxy")
                    existing.browser_type = gl_profile.get("browserType", "chrome")
                    existing.last_sync = datetime.utcnow()
                    updated_count += 1
                else:
                    new_profile = Profile(
                        id=profile_id,
                        account_id=account_name,
                        name=gl_profile.get("name"),
                        proxy=gl_profile.get("proxy"),
                        browser_type=gl_profile.get("browserType", "chrome"),
                        status="active",
                        last_sync=datetime.utcnow()
                    )
                    db.add(new_profile)
                    new_count += 1

            db.commit()

            logger.info(f"Profile sync complete: {new_count} new, {updated_count} updated")
            return {
                "total": len(gologin_profiles),
                "new": new_count,
                "updated": updated_count
            }

        except Exception as e:
            logger.error(f"Profile sync failed: {str(e)}")
            db.rollback()
            raise
        finally:
            db.close()

    async def get_profile_by_account(self, account_id: str) -> Optional[Dict]:
        db = SessionLocal()
        try:
            profile = db.query(Profile).filter(
                Profile.account_id == account_id.lower()
            ).first()

            if profile:
                return {
                    "id": profile.id,
                    "account_id": profile.account_id,
                    "name": profile.name,
                    "proxy": profile.proxy,
                    "browser_type": profile.browser_type
                }
            return None
        finally:
            db.close()

    def get_active_profiles_count(self) -> int:
        return len(self.active_profiles)

    async def cleanup_stale_profiles(self):
        stale_threshold = datetime.utcnow() - timedelta(minutes=30)
        stale_profiles = [
            pid for pid, info in self.active_profiles.items()
            if info["started_at"] < stale_threshold
        ]

        for profile_id in stale_profiles:
            logger.warning(f"Cleaning up stale profile: {profile_id}")
            await self.stop_profile(profile_id)