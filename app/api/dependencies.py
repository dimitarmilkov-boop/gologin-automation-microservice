"""
API Dependencies for DI, Authentication, and Rate Limiting
Following DDD guide specifications
"""

from typing import Generator
from collections import defaultdict
from datetime import datetime, timedelta
from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.config import settings
from app.models import ApiKey
from app.utils.logger import get_logger

logger = get_logger(__name__)

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

def get_gologin_service():
    """Get or create GoLogin service instance"""
    global _gologin_service
    if not _gologin_service:
        from app.services.gologin_service import GoLoginService
        _gologin_service = GoLoginService()
    return _gologin_service

def get_oauth_service():
    """Get or create OAuth service instance"""
    global _oauth_service
    if not _oauth_service:
        from app.services.oauth_service import OAuthService
        _oauth_service = OAuthService()
    return _oauth_service

def get_profile_automator(
    gologin_service = Depends(get_gologin_service),
    oauth_service = Depends(get_oauth_service)
):
    """Get or create Profile Automator instance"""
    global _profile_automator
    if not _profile_automator:
        from app.services.automation import ProfileAutomator
        _profile_automator = ProfileAutomator(gologin_service, oauth_service)
    return _profile_automator

# Authentication dependency
async def verify_api_key(
    x_api_key: str = Header(None, alias="X-API-Key"),
    db: Session = Depends(get_db)
) -> str:
    """Verify API key from request header"""
    if not x_api_key:
        logger.warning("api_auth.missing_header")
        raise HTTPException(
            status_code=401,
            detail="X-API-Key header missing"
        )

    # Check against database
    api_key = db.query(ApiKey).filter(
        ApiKey.key == x_api_key,
        ApiKey.is_active == True
    ).first()

    if not api_key:
        logger.warning(
            "api_auth.invalid_key",
            key_prefix=x_api_key[:8] + "..." if len(x_api_key) > 8 else x_api_key
        )
        raise HTTPException(
            status_code=401,
            detail="Invalid API key"
        )

    # Update last used timestamp
    api_key.last_used = datetime.utcnow()
    db.commit()

    logger.debug(
        "api_auth.success",
        api_key_name=api_key.name,
        key_id=api_key.id
    )

    return x_api_key

# Rate limiting dependency
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
    current_requests = len(_rate_limit_store[api_key])
    if current_requests >= 100:
        logger.warning(
            "rate_limit.exceeded",
            api_key_prefix=api_key[:8] + "...",
            requests_in_window=current_requests
        )
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded: 100 requests per minute"
        )

    # Add current request
    _rate_limit_store[api_key].append(now)

    logger.debug(
        "rate_limit.check",
        api_key_prefix=api_key[:8] + "...",
        requests_in_window=current_requests + 1
    )

# Profile validation dependency
async def validate_profile_access(
    account_id: str,
    gologin_service = Depends(get_gologin_service),
    db: Session = Depends(get_db)
):
    """Validate that profile exists and is accessible"""
    profile = await gologin_service.get_profile_for_account(account_id, db)
    if not profile:
        logger.warning(
            "profile.not_found",
            account_id=account_id
        )
        raise HTTPException(
            status_code=404,
            detail=f"No profile found for account: {account_id}"
        )

    return profile

# API app validation dependency
async def validate_api_app(api_app: str) -> str:
    """Ensure api_app is valid (AIOTT1, AIOTT2, etc.)"""
    valid_apps = ["AIOTT1", "AIOTT2", "AIOTT3"]

    if api_app not in valid_apps:
        logger.warning(
            "api_app.invalid",
            api_app=api_app,
            valid_apps=valid_apps
        )
        raise HTTPException(
            status_code=400,
            detail=f"Invalid API app: {api_app}. Must be one of: {', '.join(valid_apps)}"
        )

    return api_app

# Concurrent profile limit check
async def check_concurrent_limit(
    gologin_service = Depends(get_gologin_service)
) -> None:
    """Ensure max 10 concurrent profiles not exceeded"""
    active_count = gologin_service.get_active_profiles_count()

    if active_count >= settings.max_concurrent_profiles:
        logger.warning(
            "concurrent_limit.exceeded",
            active_profiles=active_count,
            max_allowed=settings.max_concurrent_profiles
        )
        raise HTTPException(
            status_code=429,
            detail=f"Maximum concurrent profiles ({settings.max_concurrent_profiles}) already running"
        )

    logger.debug(
        "concurrent_limit.check",
        active_profiles=active_count,
        max_allowed=settings.max_concurrent_profiles
    )

# Request ID generation
import uuid

def generate_request_id() -> str:
    """Generate unique request ID"""
    return str(uuid.uuid4())

# Common dependency combinations
def auth_and_rate_limit():
    """Combined auth and rate limit dependencies"""
    return [Depends(verify_api_key), Depends(check_rate_limit)]

def full_validation():
    """Full validation stack for authorization endpoints"""
    return [
        Depends(verify_api_key),
        Depends(check_rate_limit),
        Depends(check_concurrent_limit)
    ]