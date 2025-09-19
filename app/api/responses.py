"""
Standardized API Response Formats
Following DDD guide specifications
"""

from typing import Any, Optional, List
from pydantic import BaseModel, Field
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
    request_id: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class AuthorizationResponseData(BaseModel):
    """Authorization result data"""
    session_id: Optional[int] = None
    status: str
    profile_id: Optional[str] = None
    oauth_token: Optional[str] = None
    oauth_token_secret: Optional[str] = None
    refresh_token: Optional[str] = None
    scopes: Optional[List[str]] = None
    user_data: Optional[dict] = None

class ProfileData(BaseModel):
    """Profile information data"""
    id: str
    account_id: str
    name: Optional[str]
    status: str
    last_sync: datetime
    proxy: Optional[dict]

class HealthData(BaseModel):
    """Health check data"""
    status: str
    environment: str
    profiles_limit: int
    active_profiles: Optional[int] = None
    pending_authorizations: Optional[int] = None
    uptime_seconds: Optional[float] = None

# Response builders
def success_response(data: Any = None, request_id: str = None) -> APIResponse:
    """Build successful response"""
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
    """Build error response"""
    return APIResponse(
        success=False,
        error=error,
        error_code=error_code,
        request_id=request_id
    )

def paginated_response(
    data: List[Any],
    total: int,
    page: int,
    page_size: int,
    request_id: str = None
) -> PaginatedResponse:
    """Build paginated response"""
    total_pages = (total + page_size - 1) // page_size

    return PaginatedResponse(
        success=True,
        data=data,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        request_id=request_id
    )

def authorization_success_response(
    session_id: int,
    status: str,
    profile_id: str,
    oauth_token: str = None,
    oauth_token_secret: str = None,
    refresh_token: str = None,
    scopes: List[str] = None,
    user_data: dict = None,
    request_id: str = None
) -> APIResponse:
    """Build authorization success response"""
    data = AuthorizationResponseData(
        session_id=session_id,
        status=status,
        profile_id=profile_id,
        oauth_token=oauth_token,
        oauth_token_secret=oauth_token_secret,
        refresh_token=refresh_token,
        scopes=scopes,
        user_data=user_data
    )

    return success_response(data=data, request_id=request_id)

def profile_response(
    profile_id: str,
    account_id: str,
    name: str,
    status: str,
    last_sync: datetime,
    proxy: dict = None,
    request_id: str = None
) -> APIResponse:
    """Build profile response"""
    data = ProfileData(
        id=profile_id,
        account_id=account_id,
        name=name,
        status=status,
        last_sync=last_sync,
        proxy=proxy
    )

    return success_response(data=data, request_id=request_id)

def health_response(
    status: str,
    environment: str,
    profiles_limit: int,
    active_profiles: int = None,
    pending_authorizations: int = None,
    uptime_seconds: float = None,
    request_id: str = None
) -> APIResponse:
    """Build health check response"""
    data = HealthData(
        status=status,
        environment=environment,
        profiles_limit=profiles_limit,
        active_profiles=active_profiles,
        pending_authorizations=pending_authorizations,
        uptime_seconds=uptime_seconds
    )

    return success_response(data=data, request_id=request_id)