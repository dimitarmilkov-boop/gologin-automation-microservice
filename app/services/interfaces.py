"""
Service Interfaces using Python Protocols
Following DDD guide specifications for testing and mocking
"""

from typing import Protocol, Optional, Dict, List
from selenium import webdriver
from sqlalchemy.orm import Session
from datetime import datetime

from app.models import Profile

class BrowserControllerProtocol(Protocol):
    """Interface for browser automation operations"""

    async def connect_to_profile(self, port: int) -> webdriver.Chrome:
        """Connect to GoLogin browser instance"""
        ...

    async def navigate_to_oauth(self, driver: webdriver.Chrome, oauth_url: str) -> bool:
        """Navigate to OAuth authorization URL"""
        ...

    async def click_authorize_button(self, driver: webdriver.Chrome) -> bool:
        """Find and click the Twitter/X authorize button"""
        ...

    async def wait_for_callback(self, driver: webdriver.Chrome, callback_url: str, timeout: int) -> Optional[str]:
        """Wait for OAuth callback and extract authorization code"""
        ...

    async def take_screenshot(self, driver: webdriver.Chrome, filename: str) -> bool:
        """Take screenshot for debugging"""
        ...

    async def cleanup_driver(self, driver: webdriver.Chrome) -> None:
        """Clean up browser driver"""
        ...

class GoLoginServiceProtocol(Protocol):
    """Interface for GoLogin API operations"""

    async def initialize(self) -> None:
        """Initialize the service"""
        ...

    async def get_profiles(self) -> List[Dict]:
        """Get all profiles from GoLogin API"""
        ...

    async def start_profile(self, profile_id: str) -> Dict:
        """Start a GoLogin profile and return connection info"""
        ...

    async def stop_profile(self, profile_id: str) -> bool:
        """Stop a GoLogin profile"""
        ...

    async def get_profile_for_account(self, account_id: str, db: Session) -> Optional[Profile]:
        """Find profile by account ID in database"""
        ...

    async def sync_profiles(self, force: bool = False) -> Dict:
        """Sync profiles from GoLogin API to database"""
        ...

    def get_active_profiles_count(self) -> int:
        """Get count of currently active profiles"""
        ...

    async def cleanup_stale_profiles(self) -> None:
        """Clean up stale profile connections"""
        ...

class OAuthServiceProtocol(Protocol):
    """Interface for OAuth operations"""

    def generate_auth_url(self, api_app: str, scopes: str, state: str) -> tuple[str, str]:
        """Generate OAuth authorization URL with PKCE"""
        ...

    async def exchange_code_for_tokens(self, code: str, api_app: str, code_verifier: str) -> Dict:
        """Exchange authorization code for access tokens"""
        ...

    async def refresh_access_token(self, refresh_token: str, api_app: str) -> Dict:
        """Refresh an expired access token"""
        ...

    async def verify_credentials(self, access_token: str) -> Optional[Dict]:
        """Verify token validity and get user info"""
        ...

    async def revoke_token(self, token: str, api_app: str, token_type_hint: str = "access_token") -> bool:
        """Revoke an access token"""
        ...

class ProfileAutomatorProtocol(Protocol):
    """Interface for high-level automation orchestration"""

    async def authorize_account(
        self,
        profile_id: str,
        account_id: str,
        api_app: str,
        force_reauth: bool = False,
        session_id: Optional[int] = None
    ) -> Dict:
        """Main authorization flow orchestration"""
        ...

    async def check_authorization_status(self, account_id: str, api_app: str) -> Dict:
        """Check if account is already authorized"""
        ...

    async def revoke_authorization(self, account_id: str, api_app: str) -> Dict:
        """Revoke authorization for account"""
        ...

class WorkerProtocol(Protocol):
    """Interface for background workers"""

    async def run(self) -> None:
        """Main worker execution loop"""
        ...

    def stop(self) -> None:
        """Stop the worker gracefully"""
        ...

class ProfileSyncWorkerProtocol(WorkerProtocol):
    """Interface for profile sync worker"""

    async def sync_iteration(self) -> None:
        """Single sync iteration"""
        ...

class CleanupWorkerProtocol(WorkerProtocol):
    """Interface for cleanup worker"""

    async def cleanup_iteration(self) -> None:
        """Single cleanup iteration"""
        ...

class MonitorWorkerProtocol(WorkerProtocol):
    """Interface for monitoring worker"""

    async def collect_metrics(self) -> Dict:
        """Collect system metrics"""
        ...

    async def check_thresholds(self, metrics: Dict) -> None:
        """Check if metrics exceed thresholds"""
        ...