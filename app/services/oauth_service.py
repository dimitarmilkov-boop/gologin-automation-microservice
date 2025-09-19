"""
OAuth Service - Twitter OAuth 2.0 Operations
Following DDD guide specifications
"""

import httpx
import base64
import secrets
import hashlib
from typing import Dict, Optional, Tuple, List
from urllib.parse import urlencode
from datetime import datetime

from app.config import settings
from app.utils.logger import get_logger
from app.utils.retry import retry_twitter_api, with_timeout
from app.utils.exceptions import TwitterAPIException, InvalidAPIAppException

logger = get_logger(__name__)

class OAuthService:
    """
    Twitter OAuth 2.0 service with PKCE support
    Handles all OAuth token operations
    """

    def __init__(self):
        self.api_base = "https://api.twitter.com/2/oauth2"
        self.oauth_configs = self._load_oauth_configs()

    def _load_oauth_configs(self) -> Dict[str, Dict]:
        """Load OAuth app configurations"""
        return {
            "AIOTT1": {
                "client_id": settings.aiott1_client_id,
                "client_secret": settings.aiott1_client_secret,
                "callback_url": settings.aiott1_callback_url
            },
            "AIOTT2": {
                "client_id": settings.aiott2_client_id,
                "client_secret": settings.aiott2_client_secret,
                "callback_url": settings.aiott2_callback_url
            },
            "AIOTT3": {
                "client_id": settings.aiott3_client_id,
                "client_secret": settings.aiott3_client_secret,
                "callback_url": settings.aiott3_callback_url
            }
        }

    def _get_app_config(self, api_app: str) -> Dict:
        """Get configuration for specific API app"""
        if api_app not in self.oauth_configs:
            raise InvalidAPIAppException(api_app)

        return self.oauth_configs[api_app]

    def _generate_pkce_challenge(self) -> Tuple[str, str]:
        """Generate PKCE code verifier and challenge"""
        code_verifier = base64.urlsafe_b64encode(
            secrets.token_bytes(32)
        ).decode('utf-8').rstrip('=')

        code_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode()).digest()
        ).decode('utf-8').rstrip('=')

        return code_verifier, code_challenge

    def generate_auth_url(self, api_app: str, scopes: str, state: str) -> Tuple[str, str]:
        """Generate Twitter OAuth authorization URL with PKCE"""
        config = self._get_app_config(api_app)
        code_verifier, code_challenge = self._generate_pkce_challenge()

        params = {
            'response_type': 'code',
            'client_id': config["client_id"],
            'redirect_uri': config["callback_url"],
            'scope': scopes,
            'state': state,
            'code_challenge': code_challenge,
            'code_challenge_method': 'S256'
        }

        auth_url = f"https://twitter.com/i/oauth2/authorize?{urlencode(params)}"

        logger.debug(
            "oauth.auth_url_generated",
            api_app=api_app,
            scopes=scopes,
            client_id=config["client_id"][:8] + "...",
            callback_url=config["callback_url"]
        )

        return auth_url, code_verifier

    @retry_twitter_api
    @with_timeout(30.0)
    async def exchange_code_for_tokens(self, code: str, api_app: str, code_verifier: str) -> Dict:
        """Exchange authorization code for access tokens"""
        config = self._get_app_config(api_app)
        start_time = datetime.utcnow()

        async with httpx.AsyncClient(timeout=30.0) as client:
            # Prepare authentication header
            auth_header = base64.b64encode(
                f"{config['client_id']}:{config['client_secret']}".encode()
            ).decode()

            headers = {
                'Authorization': f'Basic {auth_header}',
                'Content-Type': 'application/x-www-form-urlencoded'
            }

            data = {
                'grant_type': 'authorization_code',
                'code': code,
                'redirect_uri': config["callback_url"],
                'code_verifier': code_verifier,
                'client_id': config["client_id"]
            }

            try:
                response = await client.post(
                    f"{self.api_base}/token",
                    headers=headers,
                    data=data
                )

                response.raise_for_status()
                token_data = response.json()

                duration_ms = (datetime.utcnow() - start_time).total_seconds() * 1000

                logger.info(
                    "oauth.token_exchange_success",
                    api_app=api_app,
                    duration_ms=duration_ms,
                    token_type=token_data.get('token_type'),
                    expires_in=token_data.get('expires_in'),
                    scopes=token_data.get('scope', '').split(' ')
                )

                return {
                    'access_token': token_data.get('access_token'),
                    'refresh_token': token_data.get('refresh_token'),
                    'token_type': token_data.get('token_type', 'Bearer'),
                    'expires_in': token_data.get('expires_in'),
                    'scope': token_data.get('scope', '').split(' ')
                }

            except httpx.HTTPStatusError as e:
                error_details = e.response.text
                logger.error(
                    "oauth.token_exchange_failed",
                    api_app=api_app,
                    status_code=e.response.status_code,
                    error_details=error_details
                )
                raise TwitterAPIException(e.response.status_code, error_details)

            except httpx.RequestError as e:
                logger.error(
                    "oauth.token_exchange_connection_error",
                    api_app=api_app,
                    error=str(e)
                )
                raise TwitterAPIException(500, f"Connection error: {str(e)}")

    @retry_twitter_api
    @with_timeout(30.0)
    async def refresh_access_token(self, refresh_token: str, api_app: str) -> Dict:
        """Refresh an expired access token"""
        config = self._get_app_config(api_app)
        start_time = datetime.utcnow()

        async with httpx.AsyncClient(timeout=30.0) as client:
            # Prepare authentication header
            auth_header = base64.b64encode(
                f"{config['client_id']}:{config['client_secret']}".encode()
            ).decode()

            headers = {
                'Authorization': f'Basic {auth_header}',
                'Content-Type': 'application/x-www-form-urlencoded'
            }

            data = {
                'grant_type': 'refresh_token',
                'refresh_token': refresh_token,
                'client_id': config["client_id"]
            }

            try:
                response = await client.post(
                    f"{self.api_base}/token",
                    headers=headers,
                    data=data
                )

                response.raise_for_status()
                token_data = response.json()

                duration_ms = (datetime.utcnow() - start_time).total_seconds() * 1000

                logger.info(
                    "oauth.token_refresh_success",
                    api_app=api_app,
                    duration_ms=duration_ms,
                    token_type=token_data.get('token_type'),
                    expires_in=token_data.get('expires_in')
                )

                return {
                    'access_token': token_data.get('access_token'),
                    'refresh_token': token_data.get('refresh_token'),
                    'token_type': token_data.get('token_type', 'Bearer'),
                    'expires_in': token_data.get('expires_in'),
                    'scope': token_data.get('scope', '').split(' ')
                }

            except httpx.HTTPStatusError as e:
                error_details = e.response.text
                logger.error(
                    "oauth.token_refresh_failed",
                    api_app=api_app,
                    status_code=e.response.status_code,
                    error_details=error_details
                )
                raise TwitterAPIException(e.response.status_code, error_details)

            except httpx.RequestError as e:
                logger.error(
                    "oauth.token_refresh_connection_error",
                    api_app=api_app,
                    error=str(e)
                )
                raise TwitterAPIException(500, f"Connection error: {str(e)}")

    @retry_twitter_api
    @with_timeout(15.0)
    async def verify_credentials(self, access_token: str) -> Optional[Dict]:
        """Verify token validity and get user info"""
        start_time = datetime.utcnow()

        async with httpx.AsyncClient(timeout=15.0) as client:
            headers = {
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json'
            }

            try:
                response = await client.get(
                    "https://api.twitter.com/2/users/me",
                    headers=headers
                )

                response.raise_for_status()
                user_data = response.json()

                duration_ms = (datetime.utcnow() - start_time).total_seconds() * 1000

                username = user_data.get('data', {}).get('username')
                user_id = user_data.get('data', {}).get('id')

                logger.info(
                    "oauth.credentials_verified",
                    username=username,
                    user_id=user_id,
                    duration_ms=duration_ms
                )

                return user_data.get('data')

            except httpx.HTTPStatusError as e:
                logger.warning(
                    "oauth.credentials_verification_failed",
                    status_code=e.response.status_code,
                    error=e.response.text
                )
                return None

            except httpx.RequestError as e:
                logger.error(
                    "oauth.credentials_verification_connection_error",
                    error=str(e)
                )
                return None

    @retry_twitter_api
    @with_timeout(15.0)
    async def revoke_token(self, token: str, api_app: str, token_type_hint: str = "access_token") -> bool:
        """Revoke an access token"""
        config = self._get_app_config(api_app)
        start_time = datetime.utcnow()

        async with httpx.AsyncClient(timeout=15.0) as client:
            # Prepare authentication header
            auth_header = base64.b64encode(
                f"{config['client_id']}:{config['client_secret']}".encode()
            ).decode()

            headers = {
                'Authorization': f'Basic {auth_header}',
                'Content-Type': 'application/x-www-form-urlencoded'
            }

            data = {
                'token': token,
                'token_type_hint': token_type_hint,
                'client_id': config["client_id"]
            }

            try:
                response = await client.post(
                    f"{self.api_base}/revoke",
                    headers=headers,
                    data=data
                )

                response.raise_for_status()

                duration_ms = (datetime.utcnow() - start_time).total_seconds() * 1000

                logger.info(
                    "oauth.token_revoked",
                    api_app=api_app,
                    token_type_hint=token_type_hint,
                    duration_ms=duration_ms
                )

                return True

            except httpx.HTTPStatusError as e:
                logger.error(
                    "oauth.token_revocation_failed",
                    api_app=api_app,
                    status_code=e.response.status_code,
                    error=e.response.text
                )
                return False

            except httpx.RequestError as e:
                logger.error(
                    "oauth.token_revocation_connection_error",
                    api_app=api_app,
                    error=str(e)
                )
                return False

    def get_supported_scopes(self) -> List[str]:
        """Get list of supported OAuth scopes"""
        return [
            "tweet.read",
            "tweet.write",
            "users.read",
            "follows.read",
            "follows.write",
            "offline.access"
        ]

    def build_scope_string(self, scopes: List[str]) -> str:
        """Build scope string for OAuth URL"""
        supported = self.get_supported_scopes()
        filtered_scopes = [scope for scope in scopes if scope in supported]

        if not filtered_scopes:
            filtered_scopes = ["tweet.read", "users.read"]  # Default scopes

        return " ".join(filtered_scopes)