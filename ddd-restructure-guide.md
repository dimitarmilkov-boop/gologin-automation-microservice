# DDD Restructure Implementation Guide - GoLogin Automation Service

## Table of Contents

1. [Service Layer Separation](#1-service-layer-separation)
2. [Domain Boundaries & Repositories](#2-domain-boundaries--repositories)
3. [Event-Driven Concerns](#3-event-driven-concerns)
4. [Error Handling Strategy](#4-error-handling-strategy)
5. [Testing & Mocking](#5-testing--mocking)
6. [Background Task Management](#6-background-task-management)
7. [API Response Formatting](#7-api-response-formatting)
8. [Logging & Monitoring](#8-logging--monitoring)
9. [Database Migration Strategy](#9-database-migration-strategy)
10. [Dependency Injection Container](#10-dependency-injection-container)
11. [Final Directory Structure](#final-directory-structure)
12. [Migration Priority Order](#migration-priority-order)
13. [Key Decisions Summary](#key-decisions-summary)

---

## 1. Service Layer Separation

### `gologin_service.py` Structure

**Semaphore Logic:** Keep it in `gologin_service.py` as it's core to GoLogin management

```python
# services/gologin_service.py
class GoLoginService:
    def __init__(self):
        self.api_client = GoLoginAPIClient()
        self.semaphore = asyncio.Semaphore(settings.max_concurrent_profiles)  # KEEP HERE

    async def start_profile(self, profile_id: str) -> int:
        async with self.semaphore:  # Concurrency is GoLogin's concern
            return await self.api_client.start_profile(profile_id)
```

**Database Operations:** Keep them IN the service for now, no repository pattern yet

```python
# services/gologin_service.py
class GoLoginService:
    async def get_profile_for_account(self, account_id: str, db: Session) -> Profile:
        # Direct DB access is fine for your scale
        profile = db.query(Profile).filter(
            Profile.twitter_username == account_id
        ).first()
        return profile

    async def sync_profiles_with_db(self, db: Session):
        # Keep DB operations in service
        api_profiles = await self.api_client.get_all_profiles()
        for api_profile in api_profiles:
            existing = db.query(Profile).filter(
                Profile.profile_id == api_profile['id']
            ).first()
            if existing:
                existing.profile_name = api_profile['name']
                existing.updated_at = datetime.utcnow()
            else:
                new_profile = Profile(
                    profile_id=api_profile['id'],
                    profile_name=api_profile['name']
                )
                db.add(new_profile)
        db.commit()
```

### `automation.py` Structure

**BrowserController:** Keep it IN `automation.py` as a separate class

```python
# services/automation.py - SINGLE FILE, TWO CLASSES

class BrowserController:
    """Low-level Selenium operations"""

    def connect_to_profile(self, port: int) -> webdriver.Chrome:
        """Connect to GoLogin browser instance"""
        options = webdriver.ChromeOptions()
        options.add_experimental_option("debuggerAddress", f"localhost:{port}")
        return webdriver.Chrome(options=options)

    def click_authorize_button(self, driver: webdriver.Chrome) -> bool:
        """Find and click the Twitter/X authorize button"""
        selectors = [
            "//input[@id='allow']",
            "//input[@value='Authorize app']",
            "//button[contains(@class, 'submit')]",
            "//input[@type='submit']"
        ]
        for selector in selectors:
            try:
                button = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, selector))
                )
                button.click()
                return True
            except TimeoutException:
                continue
        return False

    def extract_oauth_code(self, callback_url: str) -> Optional[str]:
        """Extract OAuth code from callback URL"""
        parsed = urllib.parse.urlparse(callback_url)
        params = urllib.parse.parse_qs(parsed.query)
        return params.get('code', [None])[0]

class ProfileAutomator:
    """High-level orchestration"""

    def __init__(self, gologin_service: GoLoginService):
        self.gologin = gologin_service
        self.browser = BrowserController()  # Internal component
        self.oauth_service = OAuthService()  # Separate service

    async def authorize_account(
        self,
        profile_id: str,
        account_id: str,
        api_app: str
    ) -> AuthorizationResult:
        """Main authorization flow orchestration"""
        port = await self.gologin.start_profile(profile_id)
        try:
            driver = self.browser.connect_to_profile(port)
            # Navigate to OAuth URL
            oauth_url = self.oauth_service.generate_auth_url(api_app)
            driver.get(oauth_url)

            # Click authorize
            if not self.browser.click_authorize_button(driver):
                raise AuthorizationTimeoutException("Could not find authorize button")

            # Wait for callback
            WebDriverWait(driver, 30).until(
                lambda d: "aiott.pro/callback" in d.current_url
            )

            # Extract code and exchange for tokens
            code = self.browser.extract_oauth_code(driver.current_url)
            tokens = await self.oauth_service.exchange_code_for_tokens(code, api_app)

            return AuthorizationResult(
                success=True,
                tokens=tokens,
                profile_id=profile_id
            )
        finally:
            await self.gologin.stop_profile(profile_id)
```

**OAuth Token Exchange:** Create a separate `oauth_service.py`

```python
# services/oauth_service.py - NEW FILE
class OAuthService:
    """All OAuth token operations"""

    def __init__(self):
        self.oauth_configs = self._load_oauth_configs()

    def generate_auth_url(self, api_app: str) -> str:
        """Generate Twitter OAuth authorization URL"""
        config = self.oauth_configs[api_app]
        params = {
            'response_type': 'code',
            'client_id': config.client_id,
            'redirect_uri': config.redirect_uri,
            'scope': 'tweet.read tweet.write users.read',
            'state': secrets.token_urlsafe(32),
            'code_challenge': self._generate_pkce_challenge(),
            'code_challenge_method': 'S256'
        }
        return f"https://twitter.com/i/oauth2/authorize?{urllib.parse.urlencode(params)}"

    async def exchange_code_for_tokens(self, code: str, api_app: str) -> dict:
        """Exchange authorization code for access tokens"""
        config = self.oauth_configs[api_app]
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.twitter.com/2/oauth2/token",
                data={
                    'code': code,
                    'grant_type': 'authorization_code',
                    'client_id': config.client_id,
                    'redirect_uri': config.redirect_uri,
                    'code_verifier': self.code_verifier
                }
            )
            return response.json()

    async def refresh_access_token(self, refresh_token: str, api_app: str) -> dict:
        """Refresh an expired access token"""
        config = self.oauth_configs[api_app]
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.twitter.com/2/oauth2/token",
                data={
                    'refresh_token': refresh_token,
                    'grant_type': 'refresh_token',
                    'client_id': config.client_id
                }
            )
            return response.json()

    async def verify_credentials(self, access_token: str) -> dict:
        """Verify token validity and get user info"""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.twitter.com/2/users/me",
                headers={"Authorization": f"Bearer {access_token}"}
            )
            return response.json()
```

---

## 2. Domain Boundaries & Repositories

**Decision: NO Repository Pattern (Yet)**

Keep direct database access in services:

```python
# services/gologin_service.py
class GoLoginService:
    async def sync_profiles(self, db: Session):
        # Direct query is fine
        profiles = db.query(Profile).all()
        # NOT: profile_repository.find_all()

    async def find_profile_by_username(self, username: str, db: Session) -> Optional[Profile]:
        # Direct database access
        return db.query(Profile).filter(
            Profile.twitter_username == username
        ).first()
        # NOT: self.profile_repository.find_by_username(username)
```

**Why:**

- Your queries are simple (no complex aggregations)
- 5 tables total, ~20 queries in entire app
- Add repositories only when you have 50+ queries or complex domain logic

**Future Repository Pattern (when needed):**

```python
# When you eventually need it (not now)
class ProfileRepository:
    def __init__(self, db: Session):
        self.db = db

    def find_by_username(self, username: str) -> Optional[Profile]:
        return self.db.query(Profile).filter(
            Profile.twitter_username == username
        ).first()

    def find_available_profiles(self, limit: int = 10) -> List[Profile]:
        return self.db.query(Profile).filter(
            Profile.is_active == True,
            Profile.in_use == False
        ).limit(limit).all()
```

---

## 3. Event-Driven Concerns

**Decision: NO Domain Events (Yet)**

Keep implicit coordination:

```python
# services/workers/sync_worker.py
class ProfileSyncWorker:
    async def run(self):
        while True:
            await self.sync_profiles()  # Direct call
            await asyncio.sleep(900)  # 15 minutes
            # NOT: self.publish_event(ProfileSyncRequired())

    async def sync_profiles(self):
        async with get_db() as db:
            await self.gologin_service.sync_profiles(db)
            logger.info("Profile sync completed")
```

**Future Event System (when needed):**

```python
# When you need events (not now)
@dataclass
class ProfileSyncCompleted:
    timestamp: datetime
    profiles_synced: int
    profiles_added: int

class EventBus:
    async def publish(self, event: Any):
        # Publish to subscribers
        pass
```

**Add events only when you need:**

- Multiple consumers for same event
- Audit logging requirements
- External system notifications
- Complex workflows with multiple steps

---

## 4. Error Handling Strategy

**Decision: YES to Domain-Specific Exceptions**

Create structured exception hierarchy:

```python
# utils/exceptions.py

# Base Exceptions
class DomainException(Exception):
    """Base for business logic errors"""
    def __init__(self, message: str, error_code: str = None):
        super().__init__(message)
        self.error_code = error_code or self.__class__.__name__

class InfrastructureException(Exception):
    """Base for technical errors"""
    def __init__(self, message: str, error_code: str = None):
        super().__init__(message)
        self.error_code = error_code or self.__class__.__name__

# Domain Exceptions (Business Logic)
class ProfileNotFoundException(DomainException):
    """Profile doesn't exist in system"""
    def __init__(self, account_id: str):
        super().__init__(
            f"No GoLogin profile found for account: {account_id}",
            "PROFILE_NOT_FOUND"
        )

class AuthorizationTimeoutException(DomainException):
    """OAuth flow took too long"""
    def __init__(self, timeout_seconds: int):
        super().__init__(
            f"Authorization timeout after {timeout_seconds} seconds",
            "AUTH_TIMEOUT"
        )

class ConcurrentProfileLimitException(DomainException):
    """Max 10 profiles already running"""
    def __init__(self):
        super().__init__(
            "Maximum concurrent profiles (10) already running",
            "CONCURRENT_LIMIT"
        )

class TokenExpiredException(DomainException):
    """OAuth token has expired"""
    def __init__(self, account_id: str):
        super().__init__(
            f"Token expired for account: {account_id}",
            "TOKEN_EXPIRED"
        )

# Infrastructure Exceptions (Technical)
class GoLoginAPIException(InfrastructureException):
    """GoLogin API communication failed"""
    def __init__(self, status_code: int, message: str):
        super().__init__(
            f"GoLogin API error ({status_code}): {message}",
            "GOLOGIN_API_ERROR"
        )

class SeleniumConnectionException(InfrastructureException):
    """Cannot connect to browser"""
    def __init__(self, port: int):
        super().__init__(
            f"Cannot connect to browser on port {port}",
            "BROWSER_CONNECTION_FAILED"
        )

class TwitterAPIException(InfrastructureException):
    """Twitter API error"""
    def __init__(self, status_code: int, message: str):
        super().__init__(
            f"Twitter API error ({status_code}): {message}",
            "TWITTER_API_ERROR"
        )
```

**Usage in Services:**

```python
# services/gologin_service.py
class GoLoginService:
    async def start_profile(self, profile_id: str) -> int:
        try:
            response = await self.api_client.start_profile(profile_id)
            return response['port']
        except httpx.HTTPError as e:
            raise GoLoginAPIException(e.response.status_code, str(e))
        except asyncio.TimeoutError:
            raise GoLoginAPIException(408, "GoLogin API timeout")

# services/automation.py
class ProfileAutomator:
    async def authorize_account(self, profile_id: str, account_id: str):
        profile = await self.gologin.get_profile_for_account(account_id)
        if not profile:
            raise ProfileNotFoundException(account_id)

        try:
            port = await self.gologin.start_profile(profile_id)
        except asyncio.TimeoutError:
            raise AuthorizationTimeoutException(30)
```

---

## 5. Testing & Mocking

**Decision: Use Python Protocols for Interfaces**

```python
# services/interfaces.py
from typing import Protocol, Optional
from selenium import webdriver

class BrowserControllerProtocol(Protocol):
    """Interface for browser automation"""
    def connect_to_profile(self, port: int) -> webdriver.Chrome: ...
    def click_authorize_button(self, driver: webdriver.Chrome) -> bool: ...
    def extract_oauth_code(self, callback_url: str) -> Optional[str]: ...

class GoLoginServiceProtocol(Protocol):
    """Interface for GoLogin operations"""
    async def start_profile(self, profile_id: str) -> int: ...
    async def stop_profile(self, profile_id: str) -> bool: ...
    async def get_profile_for_account(self, account_id: str) -> Optional[Profile]: ...

class OAuthServiceProtocol(Protocol):
    """Interface for OAuth operations"""
    def generate_auth_url(self, api_app: str) -> str: ...
    async def exchange_code_for_tokens(self, code: str, api_app: str) -> dict: ...
    async def refresh_access_token(self, refresh_token: str, api_app: str) -> dict: ...
```

**Test Structure:**

```
tests/
├── unit/                      # Mock all dependencies
│   ├── services/
│   │   ├── test_gologin_service.py
│   │   ├── test_automation.py
│   │   └── test_oauth_service.py
│   └── api/
│       ├── test_routes.py
│       └── test_dependencies.py
├── integration/               # Test with real DB, mock external APIs
│   ├── test_authorization_flow.py
│   ├── test_profile_sync.py
│   └── test_database_operations.py
├── e2e/                      # Real GoLogin, real browser (run manually)
│   └── test_full_authorization.py
└── fixtures/
    ├── mock_data.py
    └── test_helpers.py
```

**Example Unit Test:**

```python
# tests/unit/services/test_automation.py
import pytest
from unittest.mock import Mock, AsyncMock, patch
from app.services.automation import ProfileAutomator

@pytest.fixture
def mock_gologin_service():
    mock = Mock(spec=GoLoginServiceProtocol)
    mock.start_profile = AsyncMock(return_value=9222)
    mock.stop_profile = AsyncMock(return_value=True)
    return mock

@pytest.fixture
def mock_browser_controller():
    mock = Mock(spec=BrowserControllerProtocol)
    mock.connect_to_profile = Mock()
    mock.click_authorize_button = Mock(return_value=True)
    return mock

@pytest.mark.asyncio
async def test_authorize_account_success(mock_gologin_service, mock_browser_controller):
    automator = ProfileAutomator(mock_gologin_service)
    automator.browser = mock_browser_controller

    result = await automator.authorize_account(
        profile_id="test_profile",
        account_id="test_user",
        api_app="AIOTT1"
    )

    assert result.success == True
    mock_gologin_service.start_profile.assert_called_once_with("test_profile")
    mock_browser_controller.click_authorize_button.assert_called_once()
```

---

## 6. Background Task Management

**Decision: Simple asyncio tasks with dedicated workers**

```python
# services/workers/sync_worker.py
class ProfileSyncWorker:
    """Profile synchronization worker"""

    def __init__(self, gologin_service: GoLoginService):
        self.gologin_service = gologin_service
        self.running = False

    async def run(self):
        """Main worker loop"""
        self.running = True
        while self.running:
            try:
                await self._sync_iteration()
                await asyncio.sleep(settings.sync_interval_minutes * 60)
            except Exception as e:
                logger.error(f"Sync worker error: {e}")
                await asyncio.sleep(60)  # Retry after 1 minute

    async def _sync_iteration(self):
        """Single sync iteration"""
        async with get_db() as db:
            await self.gologin_service.sync_profiles(db)
            logger.info("Profile sync completed")

    def stop(self):
        self.running = False

# services/workers/cleanup_worker.py
class CleanupWorker:
    """Stale session cleanup worker"""

    async def run(self):
        """Clean up stale authorization sessions"""
        self.running = True
        while self.running:
            try:
                await self._cleanup_iteration()
                await asyncio.sleep(3600)  # Every hour
            except Exception as e:
                logger.error(f"Cleanup worker error: {e}")

    async def _cleanup_iteration(self):
        async with get_db() as db:
            cutoff_time = datetime.utcnow() - timedelta(hours=2)
            stale_sessions = db.query(AuthorizationSession).filter(
                AuthorizationSession.status == "in_progress",
                AuthorizationSession.created_at < cutoff_time
            ).all()

            for session in stale_sessions:
                session.status = "timeout"
                session.error_message = "Session timed out after 2 hours"

            db.commit()
            logger.info(f"Cleaned up {len(stale_sessions)} stale sessions")

# services/workers/monitor_worker.py
class MonitorWorker:
    """Health monitoring worker"""

    async def run(self):
        """Monitor system health"""
        self.running = True
        while self.running:
            try:
                metrics = await self._collect_metrics()
                await self._check_thresholds(metrics)
                await asyncio.sleep(60)  # Every minute
            except Exception as e:
                logger.error(f"Monitor worker error: {e}")

    async def _collect_metrics(self) -> dict:
        async with get_db() as db:
            return {
                "active_profiles": db.query(Profile).filter(Profile.in_use == True).count(),
                "pending_authorizations": db.query(AuthorizationSession).filter(
                    AuthorizationSession.status == "pending"
                ).count(),
                "failed_last_hour": db.query(AuthorizationSession).filter(
                    AuthorizationSession.status == "failed",
                    AuthorizationSession.created_at > datetime.utcnow() - timedelta(hours=1)
                ).count()
            }
```

**Main Application Integration:**

```python
# main.py
from app.services.workers import ProfileSyncWorker, CleanupWorker, MonitorWorker

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    background_tasks = []

    # Initialize workers
    sync_worker = ProfileSyncWorker(gologin_service)
    cleanup_worker = CleanupWorker()
    monitor_worker = MonitorWorker()

    # Start workers
    background_tasks.append(asyncio.create_task(sync_worker.run()))
    background_tasks.append(asyncio.create_task(cleanup_worker.run()))
    background_tasks.append(asyncio.create_task(monitor_worker.run()))

    yield

    # Shutdown
    sync_worker.stop()
    cleanup_worker.stop()
    monitor_worker.stop()

    # Cancel all tasks
    for task in background_tasks:
        task.cancel()
    await asyncio.gather(*background_tasks, return_exceptions=True)
```

---

## 7. API Response Formatting

**Decision: Standardized response wrapper**

```python
# api/responses.py
from typing import Any, Optional, List
from pydantic import BaseModel
from datetime import datetime

class APIResponse(BaseModel):
    """Standardized API response format"""
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
    error_code: Optional[str] = None
    request_id: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class PaginatedResponse(BaseModel):
    """Paginated list response"""
    success: bool
    data: List[Any]
    total: int
    page: int
    page_size: int
    total_pages: int

# Response builders
def success_response(data: Any = None, request_id: str = None) -> APIResponse:
    return APIResponse(
        success=True,
        data=data,
        request_id=request_id
    )

def error_response(
    error: str,
    error_code: str = "UNKNOWN_ERROR",
    request_id: str = None
) -> APIResponse:
    return APIResponse(
        success=False,
        error=error,
        error_code=error_code,
        request_id=request_id
    )
```

**Usage in Routes:**

```python
# api/routes.py
from app.api.responses import success_response, error_response

@router.post("/authorize", response_model=APIResponse)
async def authorize_account(
    request: AuthorizationRequest,
    gologin_service: GoLoginService = Depends(get_gologin_service)
) -> APIResponse:
    request_id = str(uuid.uuid4())

    try:
        # Find profile
        profile = await gologin_service.get_profile_for_account(request.account_id)
        if not profile:
            return error_response(
                error=f"No profile found for account {request.account_id}",
                error_code="PROFILE_NOT_FOUND",
                request_id=request_id
            )

        # Start authorization
        result = await automator.authorize_account(
            profile.profile_id,
            request.account_id,
            request.api_app
        )

        return success_response(
            data={
                "session_id": result.session_id,
                "status": "in_progress",
                "profile_id": profile.profile_id
            },
            request_id=request_id
        )

    except DomainException as e:
        return error_response(
            error=str(e),
            error_code=e.error_code,
            request_id=request_id
        )
    except Exception as e:
        logger.error(f"Unexpected error in authorize_account: {e}")
        return error_response(
            error="An unexpected error occurred",
            error_code="INTERNAL_ERROR",
            request_id=request_id
        )
```

---

## 8. Logging & Monitoring

**Decision: Structured JSON logging with correlation IDs**

```python
# utils/logger.py
import structlog
from contextvars import ContextVar
from typing import Any, Dict
import sys

# Context variable for request tracking
request_id_var: ContextVar[str] = ContextVar('request_id', default='')

def setup_logging(log_level: str = "INFO", json_output: bool = True):
    """Configure structured logging"""

    processors = [
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        add_request_id,  # Custom processor
        add_app_context,  # Custom processor
    ]

    if json_output:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

def add_request_id(logger, method_name, event_dict):
    """Add request ID to all log entries"""
    request_id = request_id_var.get()
    if request_id:
        event_dict['request_id'] = request_id
    return event_dict

def add_app_context(logger, method_name, event_dict):
    """Add application context to logs"""
    event_dict['service'] = 'gologin-automation'
    event_dict['environment'] = settings.environment
    return event_dict

def get_logger(name: str = None) -> structlog.BoundLogger:
    """Get a configured logger instance"""
    return structlog.get_logger(name)

# Middleware for request ID tracking
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get('X-Request-ID', str(uuid.uuid4()))
        request_id_var.set(request_id)

        response = await call_next(request)
        response.headers['X-Request-ID'] = request_id
        return response
```

**Usage in Services:**

```python
# services/automation.py
from app.utils.logger import get_logger

logger = get_logger(__name__)

class ProfileAutomator:
    async def authorize_account(self, profile_id: str, account_id: str, api_app: str):
        logger.info(
            "authorization.started",
            profile_id=profile_id,
            account_id=account_id,
            api_app=api_app
        )

        try:
            port = await self.gologin.start_profile(profile_id)
            logger.debug("profile.started", profile_id=profile_id, port=port)

            # ... authorization logic ...

            logger.info(
                "authorization.completed",
                profile_id=profile_id,
                account_id=account_id,
                duration_seconds=time.time() - start_time
            )

        except Exception as e:
            logger.error(
                "authorization.failed",
                profile_id=profile_id,
                account_id=account_id,
                error=str(e),
                exc_info=True
            )
            raise
```

**Example Log Output (JSON):**

```json
{
  "timestamp": "2024-01-15T10:30:45.123456Z",
  "level": "info",
  "logger": "app.services.automation",
  "event": "authorization.started",
  "profile_id": "profile_123",
  "account_id": "twitter_user",
  "api_app": "AIOTT1",
  "request_id": "abc-123-def-456",
  "service": "gologin-automation",
  "environment": "production"
}
```

---

## 9. Database Migration Strategy

**Decision: Keep Alembic as-is with domain versioning**

```
alembic/
├── versions/
│   ├── 001_initial_schema.py
│   ├── 002_add_api_keys.py
│   ├── 003_add_refresh_token.py
│   └── 004_add_profile_metadata.py
├── alembic.ini
└── env.py
```

**Models with Version Documentation:**

```python
# app/models.py

"""
Domain Model Versions:
v1.0 (2024-01-01): Initial Profile, AuthorizationSession
v1.1 (2024-01-05): Added ApiKey table for authentication
v1.2 (2024-01-10): Added oauth_token_secret to AuthorizationSession
v2.0 (2024-01-15): Added refresh_token and scopes columns
v2.1 (2024-01-20): Added profile metadata and sync timestamps
"""

from sqlalchemy import Column, Integer, String, DateTime, Boolean, Text, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func

Base = declarative_base()

class Profile(Base):
    """
    GoLogin Profile entity
    v1.0: Initial creation
    v2.1: Added last_synced, metadata fields
    """
    __tablename__ = "profiles"

    id = Column(Integer, primary_key=True)
    profile_id = Column(String(50), unique=True, nullable=False, index=True)
    profile_name = Column(String(20), unique=True, nullable=False)  # 4-digit number
    twitter_username = Column(String(50), nullable=True, index=True)
    api_app = Column(String(20), nullable=True)  # AIOTT1, AIOTT2, etc.

    # v2.1 additions
    last_synced = Column(DateTime, nullable=True)
    metadata = Column(JSON, nullable=True)  # Store proxy info, etc.

    # Status fields
    is_active = Column(Boolean, default=True)
    in_use = Column(Boolean, default=False)

    # Timestamps
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now())

class AuthorizationSession(Base):
    """
    OAuth Authorization Session
    v1.0: Initial creation
    v1.2: Added oauth_token_secret
    v2.0: Added refresh_token, scopes
    """
    __tablename__ = "authorization_sessions"

    id = Column(Integer, primary_key=True)
    session_id = Column(String(50), unique=True, nullable=False, index=True)
    profile_id = Column(String(50), nullable=False)
    twitter_username = Column(String(50), nullable=False)
    api_app = Column(String(20), nullable=False)

    # OAuth tokens (v2.0)
    access_token = Column(Text, nullable=True)
    refresh_token = Column(Text, nullable=True)  # v2.0
    oauth_token_secret = Column(Text, nullable=True)  # v1.2
    scopes = Column(JSON, nullable=True)  # v2.0

    # Status tracking
    status = Column(String(20), nullable=False, default='pending')
    error_message = Column(Text, nullable=True)

    # Timestamps
    created_at = Column(DateTime, server_default=func.now())
    completed_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)
```

---

## 10. Dependency Injection Container

**Decision: Use FastAPI's built-in DI only**

```python
# api/dependencies.py
from typing import Generator
from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.services.gologin_service import GoLoginService
from app.services.oauth_service import OAuthService
from app.services.automation import ProfileAutomator
from app.config import settings

# Database dependency
def get_db() -> Generator[Session, None, None]:
    """Provide database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Service instances (singleton pattern)
_gologin_service = None
_oauth_service = None
_profile_automator = None

def get_gologin_service() -> GoLoginService:
    """Get or create GoLogin service instance"""
    global _gologin_service
    if not _gologin_service:
        _gologin_service = GoLoginService()
    return _gologin_service

def get_oauth_service() -> OAuthService:
    """Get or create OAuth service instance"""
    global _oauth_service
    if not _oauth_service:
        _oauth_service = OAuthService()
    return _oauth_service

def get_profile_automator(
    gologin: GoLoginService = Depends(get_gologin_service),
    oauth: OAuthService = Depends(get_oauth_service)
) -> ProfileAutomator:
    """Get or create Profile Automator instance"""
    global _profile_automator
    if not _profile_automator:
        _profile_automator = ProfileAutomator(gologin, oauth)
    return _profile_automator

# Authentication dependency
async def verify_api_key(x_api_key: str = Header(None)) -> str:
    """Verify API key from request header"""
    if not x_api_key:
        raise HTTPException(status_code=401, detail="X-API-Key header missing")

    if x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")

    return x_api_key

# Rate limiting dependency
from collections import defaultdict
from datetime import datetime, timedelta

_rate_limit_store = defaultdict(list)

async def check_rate_limit(
    api_key: str = Depends(verify_api_key)
) -> None:
    """Simple rate limiting - 100 requests per minute per API key"""
    now = datetime.utcnow()
    minute_ago = now - timedelta(minutes=1)

    # Clean old entries
    _rate_limit_store[api_key] = [
        timestamp for timestamp in _rate_limit_store[api_key]
        if timestamp > minute_ago
    ]

    # Check limit
    if len(_rate_limit_store[api_key]) >= 100:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    # Add current request
    _rate_limit_store[api_key].append(now)
```

**Usage in Routes:**

```python
# api/routes.py
from fastapi import APIRouter, Depends
from app.api.dependencies import (
    get_db,
    get_gologin_service,
    get_profile_automator,
    verify_api_key,
    check_rate_limit
)

router = APIRouter()

@router.post(
    "/authorize",
    dependencies=[Depends(verify_api_key), Depends(check_rate_limit)]
)
async def authorize_account(
    request: AuthorizationRequest,
    db: Session = Depends(get_db),
    gologin: GoLoginService = Depends(get_gologin_service),
    automator: ProfileAutomator = Depends(get_profile_automator)
):
    """
    Clean dependency injection without external libraries
    - Authentication and rate limiting via dependencies
    - Service instances injected automatically
    - Database session managed per request
    """
    profile = await gologin.get_profile_for_account(request.account_id, db)
    result = await automator.authorize_account(
        profile.profile_id,
        request.account_id,
        request.api_app
    )
    return result
```

---

## Final Directory Structure

```
app/
├── __init__.py
├── main.py                      # Application entry point
├── config.py                    # All configuration settings
├── database.py                  # Database connection management
├── models.py                    # SQLAlchemy + Pydantic models
│
├── api/
│   ├── __init__.py
│   ├── routes.py               # All API endpoints with routers
│   ├── dependencies.py         # DI, auth, rate limiting
│   └── responses.py            # Standardized API responses
│
├── services/
│   ├── __init__.py
│   ├── gologin_service.py     # GoLogin API + profile management
│   ├── automation.py           # ProfileAutomator + BrowserController
│   ├── oauth_service.py        # OAuth token operations
│   ├── interfaces.py           # Protocol definitions for testing
│   └── workers/
│       ├── __init__.py
│       ├── sync_worker.py      # Profile synchronization
│       ├── cleanup_worker.py   # Session cleanup
│       └── monitor_worker.py   # Health monitoring
│
└── utils/
    ├── __init__.py
    ├── logger.py               # Structured JSON logging
    ├── exceptions.py           # Domain/Infrastructure exceptions
    └── retry.py                # Retry decorators for resilience

tests/
├── unit/
│   ├── services/
│   │   ├── test_gologin_service.py
│   │   ├── test_automation.py
│   │   └── test_oauth_service.py
│   └── api/
│       ├── test_routes.py
│       └── test_dependencies.py
├── integration/
│   ├── test_authorization_flow.py
│   ├── test_profile_sync.py
│   └── test_database_operations.py
├── e2e/
│   └── test_full_authorization.py
└── fixtures/
    ├── mock_data.py
    └── test_helpers.py

alembic/
├── versions/
│   ├── 001_initial_schema.py
│   ├── 002_add_api_keys.py
│   ├── 003_add_refresh_token.py
│   └── 004_add_profile_metadata.py
├── alembic.ini
└── env.py

# Root files
.env                            # Environment variables
.env.example                    # Example environment file
requirements.txt                # Python dependencies
docker-compose.yml             # Docker configuration
Dockerfile                     # Container definition
README.md                      # Project documentation
```

---

## Migration Priority Order

### Week 1: Core Restructuring

1. Create new directory structure
2. Move existing code to new locations:
   - `api.py` → `api/routes.py`
   - `profile_manager.py` → `services/gologin_service.py`
   - `browser_automation.py` + `authorization.py` → `services/automation.py`
   - `oauth_client.py` → `services/oauth_service.py`
3. Update all imports
4. Test that everything still works

### Week 2: Add Infrastructure

1. Implement exception hierarchy in `utils/exceptions.py`
2. Set up structured logging in `utils/logger.py`
3. Create standardized responses in `api/responses.py`
4. Add retry decorators in `utils/retry.py`

### Week 3: Enhance Services

1. Separate BrowserController from ProfileAutomator
2. Create service interfaces/protocols
3. Implement background workers
4. Add comprehensive error handling

### Week 4: Testing & Documentation

1. Set up test structure
2. Write unit tests for critical paths
3. Add integration tests
4. Update documentation

---

## Key Decisions Summary

| Component                      | Decision | Rationale                           |
| ------------------------------ | -------- | ----------------------------------- |
| **Repository Pattern**         | **NO**   | Simple queries, <50 total in app    |
| **Domain Events**              | **NO**   | No multiple consumers needed yet    |
| **DI Container**               | **NO**   | FastAPI's built-in DI is sufficient |
| **Structured Exceptions**      | **YES**  | Better error handling and debugging |
| **JSON Logging**               | **YES**  | Essential for production debugging  |
| **Service Interfaces**         | **YES**  | Enables proper testing and mocking  |
| **Celery/RabbitMQ**            | **NO**   | Overkill for 3 background workers   |
| **Response Standards**         | **YES**  | Consistent API experience           |
| **Alembic Migrations**         | **YES**  | Keep existing, add versioning docs  |
| **Separate BrowserController** | **YES**  | Clean separation of concerns        |

---

## Implementation Checklist

### Immediate Actions (Day 1)

- [ ] Create new directory structure
- [ ] Move existing files to new locations
- [ ] Update imports in all files
- [ ] Verify application still runs

### Short Term (Week 1)

- [ ] Implement exception hierarchy
- [ ] Add structured logging
- [ ] Create API response standards
- [ ] Separate OAuth service

### Medium Term (Week 2-3)

- [ ] Add background workers
- [ ] Implement service protocols
- [ ] Create comprehensive tests
- [ ] Add monitoring and metrics

### Long Term (Month 1)

- [ ] Complete documentation
- [ ] Add performance monitoring
- [ ] Implement caching where needed
- [ ] Consider repository pattern if queries grow

---

## Notes on Maintaining Backward Compatibility

1. **API Endpoints**: Keep all existing endpoints working exactly as before
2. **Database Schema**: No changes to existing tables, only additions
3. **Configuration**: All existing environment variables remain the same
4. **External Integration**: GoLogin API and Twitter OAuth flows unchanged

---

## Performance Considerations

- **Semaphore in GoLoginService**: Limits concurrent profiles to 10
- **Database Connection Pool**: Set to 20 connections (2x concurrent profiles)
- **Async Operations**: All I/O operations are async
- **Background Workers**: Run in separate asyncio tasks, not threads

---

## Security Considerations

- **API Key Authentication**: Required for all endpoints
- **Rate Limiting**: 100 requests per minute per API key
- **Token Storage**: Encrypted in database (add encryption layer)
- **Request ID Tracking**: For audit and debugging

---

## Monitoring and Observability

- **Structured Logging**: JSON format with request IDs
- **Metrics Collection**: Via monitor_worker.py
- **Health Endpoint**: `/health` for liveness checks
- **Error Tracking**: All exceptions logged with full context

---

## Future Enhancements (Not Now)

1. **Repository Pattern**: When queries exceed 50+
2. **Event Bus**: When multiple services need same events
3. **CQRS**: If read/write patterns diverge significantly
4. **GraphQL**: If API consumers need flexible queries
5. **Distributed Tracing**: When microservices multiply

This structure provides a solid foundation that can scale from your current 4,000 accounts to 40,000+ without major rewrites.
