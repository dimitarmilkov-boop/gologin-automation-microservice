import secrets
from typing import Dict, Optional
from datetime import datetime
from loguru import logger
from sqlalchemy.orm import Session

from app.services.browser_automation import BrowserAutomationService
from app.services.oauth_client import TwitterOAuthClient
from app.services.profile_manager import ProfileManager
from app.models import AuthorizationSession
from app.schemas import ApiApp

class AuthorizationService:
    def __init__(self, profile_manager: ProfileManager, db: Session):
        self.profile_manager = profile_manager
        self.browser_service = BrowserAutomationService(profile_manager)
        self.db = db

    async def authorize_account(
        self,
        account_id: str,
        api_app: ApiApp,
        force_reauth: bool = False,
        session_id: Optional[int] = None
    ) -> Dict:

        logger.info(f"Starting authorization for {account_id} with {api_app}")

        try:
            oauth_client = TwitterOAuthClient.create_client_for_app(api_app.value)

            if not force_reauth:
                existing_auth = await self._check_existing_authorization(account_id, api_app.value)
                if existing_auth:
                    logger.info(f"Found valid existing authorization for {account_id}")
                    return existing_auth

            state = secrets.token_urlsafe(32)
            scopes = "tweet.read users.read follows.read follows.write offline.access"

            auth_url, code_verifier, state = oauth_client.build_authorization_url(scopes, state)

            success, auth_code, error_msg = await self.browser_service.authorize_twitter_account(
                account_id=account_id,
                client_id=oauth_client.client_id,
                redirect_uri=oauth_client.callback_url,
                scopes=scopes.replace(" ", "%20")
            )

            if not success:
                logger.error(f"Browser automation failed: {error_msg}")
                return {
                    "status": "error",
                    "error_code": "BROWSER_AUTOMATION_FAILED",
                    "message": error_msg
                }

            try:
                token_data = await oauth_client.exchange_code_for_tokens(auth_code, code_verifier)

                user_data = await oauth_client.verify_credentials(token_data['access_token'])
                if not user_data:
                    return {
                        "status": "error",
                        "error_code": "TOKEN_VERIFICATION_FAILED",
                        "message": "Failed to verify credentials with obtained token"
                    }

                await self._store_tokens(account_id, api_app.value, token_data, session_id)

                logger.info(f"Authorization successful for {account_id}")
                return {
                    "status": "success",
                    "oauth_token": token_data['access_token'],
                    "oauth_token_secret": None,
                    "refresh_token": token_data['refresh_token'],
                    "scopes": token_data['scope'],
                    "user_data": user_data
                }

            except Exception as e:
                logger.error(f"Token exchange failed: {str(e)}")
                return {
                    "status": "error",
                    "error_code": "TOKEN_EXCHANGE_FAILED",
                    "message": str(e)
                }

        except Exception as e:
            logger.error(f"Authorization service error: {str(e)}")
            return {
                "status": "error",
                "error_code": "AUTHORIZATION_SERVICE_ERROR",
                "message": str(e)
            }

    async def _check_existing_authorization(self, account_id: str, api_app: str) -> Optional[Dict]:
        try:
            recent_session = self.db.query(AuthorizationSession).filter(
                AuthorizationSession.account_id == account_id.lower(),
                AuthorizationSession.api_app == api_app,
                AuthorizationSession.status == "success",
                AuthorizationSession.refresh_token.isnot(None)
            ).order_by(AuthorizationSession.completed_at.desc()).first()

            if recent_session and recent_session.refresh_token:
                oauth_client = TwitterOAuthClient.create_client_for_app(api_app)

                try:
                    token_data = await oauth_client.refresh_access_token(recent_session.refresh_token)

                    user_data = await oauth_client.verify_credentials(token_data['access_token'])
                    if user_data:
                        await self._store_tokens(account_id, api_app, token_data, recent_session.id)

                        return {
                            "status": "success",
                            "oauth_token": token_data['access_token'],
                            "oauth_token_secret": None,
                            "refresh_token": token_data['refresh_token'],
                            "scopes": token_data['scope'],
                            "user_data": user_data
                        }

                except Exception as e:
                    logger.warning(f"Token refresh failed for {account_id}: {str(e)}")

            return None

        except Exception as e:
            logger.error(f"Error checking existing authorization: {str(e)}")
            return None

    async def _store_tokens(self, account_id: str, api_app: str, token_data: Dict, session_id: Optional[int]):
        if session_id:
            session = self.db.query(AuthorizationSession).filter(
                AuthorizationSession.id == session_id
            ).first()

            if session:
                session.oauth_token = token_data['access_token']
                session.refresh_token = token_data['refresh_token']
                session.scopes = token_data['scope']
                session.status = "success"
                session.completed_at = datetime.utcnow()
                self.db.commit()

    async def check_authorization_status(self, account_id: str, api_app: str) -> Dict:
        try:
            recent_session = self.db.query(AuthorizationSession).filter(
                AuthorizationSession.account_id == account_id.lower(),
                AuthorizationSession.api_app == api_app,
                AuthorizationSession.status == "success"
            ).order_by(AuthorizationSession.completed_at.desc()).first()

            if recent_session and recent_session.oauth_token:
                oauth_client = TwitterOAuthClient.create_client_for_app(api_app)
                user_data = await oauth_client.verify_credentials(recent_session.oauth_token)

                if user_data:
                    return {
                        "status": "authorized",
                        "account_id": account_id,
                        "api_app": api_app,
                        "user_data": user_data,
                        "last_authorized": recent_session.completed_at
                    }

            return {
                "status": "not_authorized",
                "account_id": account_id,
                "api_app": api_app
            }

        except Exception as e:
            logger.error(f"Error checking authorization status: {str(e)}")
            return {
                "status": "error",
                "error_code": "STATUS_CHECK_FAILED",
                "message": str(e)
            }

    async def revoke_authorization(self, account_id: str, api_app: str) -> Dict:
        try:
            recent_session = self.db.query(AuthorizationSession).filter(
                AuthorizationSession.account_id == account_id.lower(),
                AuthorizationSession.api_app == api_app,
                AuthorizationSession.status == "success"
            ).order_by(AuthorizationSession.completed_at.desc()).first()

            if recent_session and recent_session.oauth_token:
                oauth_client = TwitterOAuthClient.create_client_for_app(api_app)

                revoked = await oauth_client.revoke_token(recent_session.oauth_token)

                if revoked:
                    recent_session.status = "revoked"
                    recent_session.oauth_token = None
                    recent_session.refresh_token = None
                    self.db.commit()

                    return {
                        "status": "success",
                        "message": f"Authorization revoked for {account_id}"
                    }

            return {
                "status": "error",
                "error_code": "NO_ACTIVE_AUTHORIZATION",
                "message": f"No active authorization found for {account_id}"
            }

        except Exception as e:
            logger.error(f"Error revoking authorization: {str(e)}")
            return {
                "status": "error",
                "error_code": "REVOCATION_FAILED",
                "message": str(e)
            }