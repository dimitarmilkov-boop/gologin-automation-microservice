from pydantic import BaseModel, Field, validator
from typing import Optional, List, Dict
from datetime import datetime
from enum import Enum

class ApiApp(str, Enum):
    AIOTT1 = "AIOTT1"
    AIOTT2 = "AIOTT2"
    AIOTT3 = "AIOTT3"

class AuthorizationAction(str, Enum):
    AUTHORIZE = "authorize"
    CHECK_STATUS = "check_status"
    REVOKE = "revoke"

class AuthorizationRequest(BaseModel):
    account_id: str = Field(..., description="Twitter username")
    action: AuthorizationAction = Field(default=AuthorizationAction.AUTHORIZE)
    api_app: ApiApp = Field(..., description="Which AIOTT API app to use")
    force_reauth: bool = Field(default=False, description="Force reauthorization even if already authorized")

    @validator('account_id')
    def validate_account_id(cls, v):
        if not v or len(v) < 1:
            raise ValueError('account_id cannot be empty')
        return v.lower().strip()

class AuthorizationResponse(BaseModel):
    status: str
    account_id: str
    api_app: str
    oauth_token: Optional[str] = None
    oauth_token_secret: Optional[str] = None
    refresh_token: Optional[str] = None
    scopes: Optional[List[str]] = None
    error_code: Optional[str] = None
    message: Optional[str] = None
    session_id: Optional[int] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class ProfileStatus(BaseModel):
    id: str
    account_id: str
    name: Optional[str]
    status: str
    last_sync: datetime
    proxy: Optional[Dict]

class HealthCheck(BaseModel):
    status: str
    environment: str
    profiles_limit: int
    active_profiles: Optional[int] = None
    pending_authorizations: Optional[int] = None

class ProfileSyncRequest(BaseModel):
    force: bool = False

class ProfileSyncResponse(BaseModel):
    status: str
    profiles_synced: int
    new_profiles: int
    updated_profiles: int
    timestamp: datetime