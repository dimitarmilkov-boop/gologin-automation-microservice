from fastapi import APIRouter, Depends, HTTPException, Header, BackgroundTasks
from sqlalchemy.orm import Session
from typing import Optional, List
from datetime import datetime
from loguru import logger

from app.database import get_db
from app.schemas import (
    AuthorizationRequest, AuthorizationResponse,
    ProfileStatus, ProfileSyncRequest, ProfileSyncResponse
)
from app.models import AuthorizationSession, Profile, ApiKey
from app.services.authorization import AuthorizationService
from app.services.profile_manager import ProfileManager
from app.auth import verify_api_key

router = APIRouter()

async def get_profile_manager():
    from app.main import app
    return app.state.profile_manager

@router.post("/authorize", response_model=AuthorizationResponse)
async def authorize(
    request: AuthorizationRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    api_key: str = Header(..., alias="X-API-Key"),
    profile_manager: ProfileManager = Depends(get_profile_manager)
):
    if not verify_api_key(api_key, db):
        raise HTTPException(status_code=401, detail="Invalid API key")

    logger.info(f"Authorization request for {request.account_id} with {request.api_app}")

    session = AuthorizationSession(
        account_id=request.account_id,
        api_app=request.api_app.value,
        status="pending"
    )
    db.add(session)
    db.commit()

    try:
        auth_service = AuthorizationService(profile_manager, db)
        result = await auth_service.authorize_account(
            account_id=request.account_id,
            api_app=request.api_app,
            force_reauth=request.force_reauth,
            session_id=session.id
        )

        session.status = result["status"]
        if result["status"] == "success":
            session.oauth_token = result.get("oauth_token")
            session.oauth_token_secret = result.get("oauth_token_secret")
            session.refresh_token = result.get("refresh_token")
            session.scopes = result.get("scopes")
        else:
            session.error_code = result.get("error_code")
            session.error_message = result.get("message")

        session.completed_at = datetime.utcnow()
        db.commit()

        return AuthorizationResponse(
            status=result["status"],
            account_id=request.account_id,
            api_app=request.api_app,
            oauth_token=result.get("oauth_token"),
            oauth_token_secret=result.get("oauth_token_secret"),
            refresh_token=result.get("refresh_token"),
            scopes=result.get("scopes"),
            error_code=result.get("error_code"),
            message=result.get("message"),
            session_id=session.id
        )

    except Exception as e:
        logger.error(f"Authorization failed: {str(e)}")
        session.status = "error"
        session.error_message = str(e)
        session.completed_at = datetime.utcnow()
        db.commit()

        raise HTTPException(status_code=500, detail=str(e))

@router.get("/profiles", response_model=List[ProfileStatus])
async def list_profiles(
    db: Session = Depends(get_db),
    api_key: str = Header(..., alias="X-API-Key")
):
    if not verify_api_key(api_key, db):
        raise HTTPException(status_code=401, detail="Invalid API key")

    profiles = db.query(Profile).filter(Profile.status == "active").all()
    return [
        ProfileStatus(
            id=p.id,
            account_id=p.account_id,
            name=p.name,
            status=p.status,
            last_sync=p.last_sync,
            proxy=p.proxy
        ) for p in profiles
    ]

@router.get("/profiles/{account_id}", response_model=ProfileStatus)
async def get_profile(
    account_id: str,
    db: Session = Depends(get_db),
    api_key: str = Header(..., alias="X-API-Key")
):
    if not verify_api_key(api_key, db):
        raise HTTPException(status_code=401, detail="Invalid API key")

    profile = db.query(Profile).filter(Profile.account_id == account_id.lower()).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    return ProfileStatus(
        id=profile.id,
        account_id=profile.account_id,
        name=profile.name,
        status=profile.status,
        last_sync=profile.last_sync,
        proxy=profile.proxy
    )

@router.post("/profiles/sync", response_model=ProfileSyncResponse)
async def sync_profiles(
    request: ProfileSyncRequest,
    db: Session = Depends(get_db),
    api_key: str = Header(..., alias="X-API-Key"),
    profile_manager: ProfileManager = Depends(get_profile_manager)
):
    if not verify_api_key(api_key, db):
        raise HTTPException(status_code=401, detail="Invalid API key")

    logger.info(f"Manual profile sync requested (force={request.force})")

    try:
        result = await profile_manager.sync_profiles(force=request.force)

        return ProfileSyncResponse(
            status="success",
            profiles_synced=result["total"],
            new_profiles=result["new"],
            updated_profiles=result["updated"],
            timestamp=datetime.utcnow()
        )
    except Exception as e:
        logger.error(f"Profile sync failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/sessions/{session_id}")
async def get_session(
    session_id: int,
    db: Session = Depends(get_db),
    api_key: str = Header(..., alias="X-API-Key")
):
    if not verify_api_key(api_key, db):
        raise HTTPException(status_code=401, detail="Invalid API key")

    session = db.query(AuthorizationSession).filter(AuthorizationSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    return {
        "id": session.id,
        "account_id": session.account_id,
        "api_app": session.api_app,
        "status": session.status,
        "started_at": session.started_at,
        "completed_at": session.completed_at,
        "error_code": session.error_code,
        "error_message": session.error_message
    }