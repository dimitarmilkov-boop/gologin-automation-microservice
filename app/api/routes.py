"""
API Routes with Domain-Grouped Organization
Following DDD guide specifications
"""

import uuid
import time
from fastapi import APIRouter, Depends, BackgroundTasks
from sqlalchemy.orm import Session
from typing import List
from datetime import datetime

from app.database import get_db
from app.config import settings
from app.schemas import (
    AuthorizationRequest, AuthorizationResponse,
    ProfileStatus, ProfileSyncRequest, ProfileSyncResponse
)
from app.models import AuthorizationSession, Profile
from app.api.dependencies import (
    get_gologin_service,
    get_profile_automator,
    verify_api_key,
    check_rate_limit,
    check_concurrent_limit,
    validate_api_app,
    generate_request_id,
    auth_and_rate_limit,
    full_validation
)
from app.api.responses import (
    APIResponse,
    success_response,
    error_response,
    authorization_success_response,
    profile_response,
    health_response,
    paginated_response
)
from app.utils.logger import get_logger, log_authorization_started, log_authorization_completed, log_authorization_failed
from app.utils.exceptions import (
    DomainException,
    ProfileNotFoundException,
    AuthorizationTimeoutException,
    InvalidAPIAppException
)

logger = get_logger(__name__)

# Main router
router = APIRouter()

# Authorization endpoints group
authorization_router = APIRouter(prefix="/authorization", tags=["authorization"])
profiles_router = APIRouter(prefix="/profiles", tags=["profiles"])
system_router = APIRouter(prefix="/system", tags=["system"])

@authorization_router.post("/authorize", response_model=APIResponse)
async def authorize_account(
    request: AuthorizationRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    gologin_service = Depends(get_gologin_service),
    automator = Depends(get_profile_automator),
    dependencies = Depends(full_validation)
) -> APIResponse:
    """
    Start OAuth authorization for a Twitter account using GoLogin profile
    """
    request_id = generate_request_id()
    start_time = time.time()

    # Validate API app
    try:
        validated_api_app = await validate_api_app(request.api_app.value)
    except Exception as e:
        return error_response(
            error=str(e),
            error_code="INVALID_API_APP",
            request_id=request_id
        )

    log_authorization_started(
        logger,
        profile_id="unknown",
        account_id=request.account_id,
        api_app=request.api_app.value
    )

    # Create authorization session
    session = AuthorizationSession(
        account_id=request.account_id,
        api_app=request.api_app.value,
        status="pending"
    )
    db.add(session)
    db.commit()

    try:
        # Find profile for account
        profile = await gologin_service.get_profile_for_account(request.account_id, db)
        if not profile:
            raise ProfileNotFoundException(request.account_id)

        # Start authorization process
        result = await automator.authorize_account(
            profile_id=profile.id,
            account_id=request.account_id,
            api_app=request.api_app,
            force_reauth=request.force_reauth,
            session_id=session.id
        )

        # Update session with results
        session.status = result["status"]
        if result["status"] == "success":
            session.oauth_token = result.get("oauth_token")
            session.oauth_token_secret = result.get("oauth_token_secret")
            session.refresh_token = result.get("refresh_token")
            session.scopes = result.get("scopes")

            log_authorization_completed(
                logger,
                profile_id=profile.id,
                account_id=request.account_id,
                duration_seconds=time.time() - start_time
            )

            return authorization_success_response(
                session_id=session.id,
                status="success",
                profile_id=profile.id,
                oauth_token=result.get("oauth_token"),
                oauth_token_secret=result.get("oauth_token_secret"),
                refresh_token=result.get("refresh_token"),
                scopes=result.get("scopes"),
                user_data=result.get("user_data"),
                request_id=request_id
            )

        else:
            session.error_code = result.get("error_code")
            session.error_message = result.get("message")

            log_authorization_failed(
                logger,
                profile_id=profile.id,
                account_id=request.account_id,
                error=result.get("message", "Unknown error"),
                error_code=result.get("error_code")
            )

            return error_response(
                error=result.get("message", "Authorization failed"),
                error_code=result.get("error_code", "AUTHORIZATION_FAILED"),
                request_id=request_id
            )

    except DomainException as e:
        log_authorization_failed(
            logger,
            profile_id="unknown",
            account_id=request.account_id,
            error=str(e),
            error_code=e.error_code
        )

        session.status = "error"
        session.error_code = e.error_code
        session.error_message = str(e)

        return error_response(
            error=str(e),
            error_code=e.error_code,
            request_id=request_id
        )

    except Exception as e:
        logger.error(
            "authorization.unexpected_error",
            account_id=request.account_id,
            error=str(e),
            exc_info=True
        )

        session.status = "error"
        session.error_message = str(e)

        return error_response(
            error="An unexpected error occurred",
            error_code="INTERNAL_ERROR",
            request_id=request_id
        )

    finally:
        session.completed_at = datetime.utcnow()
        db.commit()

@authorization_router.get("/sessions/{session_id}", response_model=APIResponse)
async def get_authorization_session(
    session_id: int,
    db: Session = Depends(get_db),
    dependencies = Depends(auth_and_rate_limit)
) -> APIResponse:
    """
    Get authorization session details by ID
    """
    request_id = generate_request_id()

    session = db.query(AuthorizationSession).filter(
        AuthorizationSession.id == session_id
    ).first()

    if not session:
        return error_response(
            error=f"Authorization session {session_id} not found",
            error_code="SESSION_NOT_FOUND",
            request_id=request_id
        )

    data = {
        "id": session.id,
        "account_id": session.account_id,
        "api_app": session.api_app,
        "status": session.status,
        "started_at": session.started_at,
        "completed_at": session.completed_at,
        "error_code": session.error_code,
        "error_message": session.error_message
    }

    return success_response(data=data, request_id=request_id)

@profiles_router.get("", response_model=APIResponse)
async def list_profiles(
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
    dependencies = Depends(auth_and_rate_limit)
) -> APIResponse:
    """
    List all active GoLogin profiles with pagination
    """
    request_id = generate_request_id()

    # Calculate offset
    offset = (page - 1) * page_size

    # Get total count
    total = db.query(Profile).filter(Profile.status == "active").count()

    # Get paginated profiles
    profiles = db.query(Profile).filter(
        Profile.status == "active"
    ).offset(offset).limit(page_size).all()

    # Convert to response format
    profile_data = [
        {
            "id": p.id,
            "account_id": p.account_id,
            "name": p.name,
            "status": p.status,
            "last_sync": p.last_sync,
            "proxy": p.proxy
        }
        for p in profiles
    ]

    return paginated_response(
        data=profile_data,
        total=total,
        page=page,
        page_size=page_size,
        request_id=request_id
    )

@profiles_router.get("/{account_id}", response_model=APIResponse)
async def get_profile_by_account(
    account_id: str,
    db: Session = Depends(get_db),
    dependencies = Depends(auth_and_rate_limit)
) -> APIResponse:
    """
    Get specific profile by account ID
    """
    request_id = generate_request_id()

    profile = db.query(Profile).filter(
        Profile.account_id == account_id.lower()
    ).first()

    if not profile:
        return error_response(
            error=f"Profile not found for account: {account_id}",
            error_code="PROFILE_NOT_FOUND",
            request_id=request_id
        )

    return profile_response(
        profile_id=profile.id,
        account_id=profile.account_id,
        name=profile.name,
        status=profile.status,
        last_sync=profile.last_sync,
        proxy=profile.proxy,
        request_id=request_id
    )

@profiles_router.post("/sync", response_model=APIResponse)
async def sync_profiles_manual(
    request: ProfileSyncRequest,
    db: Session = Depends(get_db),
    gologin_service = Depends(get_gologin_service),
    dependencies = Depends(auth_and_rate_limit)
) -> APIResponse:
    """
    Manually trigger profile synchronization with GoLogin API
    """
    request_id = generate_request_id()

    logger.info(
        "profile_sync.manual_trigger",
        force=request.force,
        request_id=request_id
    )

    try:
        result = await gologin_service.sync_profiles(force=request.force)

        data = {
            "status": "success",
            "profiles_synced": result["total"],
            "new_profiles": result["new"],
            "updated_profiles": result["updated"],
            "timestamp": datetime.utcnow()
        }

        logger.info(
            "profile_sync.completed",
            **result,
            request_id=request_id
        )

        return success_response(data=data, request_id=request_id)

    except Exception as e:
        logger.error(
            "profile_sync.failed",
            error=str(e),
            request_id=request_id,
            exc_info=True
        )

        return error_response(
            error=f"Profile sync failed: {str(e)}",
            error_code="SYNC_FAILED",
            request_id=request_id
        )

@system_router.get("/health", response_model=APIResponse)
async def health_check(
    db: Session = Depends(get_db),
    gologin_service = Depends(get_gologin_service)
) -> APIResponse:
    """
    System health check endpoint
    """
    request_id = generate_request_id()

    try:
        # Get metrics
        active_profiles = gologin_service.get_active_profiles_count()
        pending_authorizations = db.query(AuthorizationSession).filter(
            AuthorizationSession.status == "pending"
        ).count()

        return health_response(
            status="healthy",
            environment=settings.environment,
            profiles_limit=settings.max_concurrent_profiles,
            active_profiles=active_profiles,
            pending_authorizations=pending_authorizations,
            request_id=request_id
        )

    except Exception as e:
        logger.error(
            "health_check.failed",
            error=str(e),
            exc_info=True
        )

        return health_response(
            status="unhealthy",
            environment=settings.environment,
            profiles_limit=settings.max_concurrent_profiles,
            request_id=request_id
        )

# Include all routers in main router
router.include_router(authorization_router)
router.include_router(profiles_router)
router.include_router(system_router)

# Legacy endpoint for backward compatibility
@router.get("/health")
async def legacy_health_check():
    """Legacy health endpoint for backward compatibility"""
    return {
        "status": "healthy",
        "environment": settings.environment,
        "profiles_limit": settings.max_concurrent_profiles
    }