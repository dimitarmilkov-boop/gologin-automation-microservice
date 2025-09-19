import httpx
import base64
import secrets
import hashlib
from typing import Dict, Optional, Tuple
from urllib.parse import urlencode
from loguru import logger

from app.config import settings

class TwitterOAuthClient:
    def __init__(self, client_id: str, client_secret: str, callback_url: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.callback_url = callback_url
        self.api_base = "https://api.twitter.com/2/oauth2"

    def get_oauth_apps_config(self) -> Dict[str, Dict]:
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

    @staticmethod
    def create_client_for_app(api_app: str) -> 'TwitterOAuthClient':
        apps_config = TwitterOAuthClient.get_oauth_apps_config(TwitterOAuthClient)

        if api_app not in apps_config:
            raise ValueError(f"Unknown API app: {api_app}")

        config = apps_config[api_app]
        return TwitterOAuthClient(
            client_id=config["client_id"],
            client_secret=config["client_secret"],
            callback_url=config["callback_url"]
        )

    def generate_pkce_challenge(self) -> Tuple[str, str]:
        code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('utf-8').rstrip('=')
        code_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode()).digest()
        ).decode('utf-8').rstrip('=')

        return code_verifier, code_challenge

    def build_authorization_url(self, scopes: str, state: str) -> Tuple[str, str, str]:
        code_verifier, code_challenge = self.generate_pkce_challenge()

        params = {
            'response_type': 'code',
            'client_id': self.client_id,
            'redirect_uri': self.callback_url,
            'scope': scopes,
            'state': state,
            'code_challenge': code_challenge,
            'code_challenge_method': 'S256'
        }

        auth_url = f"https://twitter.com/i/oauth2/authorize?{urlencode(params)}"
        return auth_url, code_verifier, state

    async def exchange_code_for_tokens(self, auth_code: str, code_verifier: str) -> Dict:
        async with httpx.AsyncClient() as client:

            auth_header = base64.b64encode(
                f"{self.client_id}:{self.client_secret}".encode()
            ).decode()

            headers = {
                'Authorization': f'Basic {auth_header}',
                'Content-Type': 'application/x-www-form-urlencoded'
            }

            data = {
                'grant_type': 'authorization_code',
                'code': auth_code,
                'redirect_uri': self.callback_url,
                'code_verifier': code_verifier,
                'client_id': self.client_id
            }

            try:
                response = await client.post(
                    f"{self.api_base}/token",
                    headers=headers,
                    data=data
                )

                response.raise_for_status()
                token_data = response.json()

                logger.info("Successfully exchanged authorization code for tokens")
                return {
                    'access_token': token_data.get('access_token'),
                    'refresh_token': token_data.get('refresh_token'),
                    'token_type': token_data.get('token_type', 'Bearer'),
                    'expires_in': token_data.get('expires_in'),
                    'scope': token_data.get('scope', '').split(' ')
                }

            except httpx.HTTPStatusError as e:
                logger.error(f"Token exchange failed: {e.response.status_code} - {e.response.text}")
                raise Exception(f"Token exchange failed: {e.response.text}")

    async def refresh_access_token(self, refresh_token: str) -> Dict:
        async with httpx.AsyncClient() as client:

            auth_header = base64.b64encode(
                f"{self.client_id}:{self.client_secret}".encode()
            ).decode()

            headers = {
                'Authorization': f'Basic {auth_header}',
                'Content-Type': 'application/x-www-form-urlencoded'
            }

            data = {
                'grant_type': 'refresh_token',
                'refresh_token': refresh_token,
                'client_id': self.client_id
            }

            try:
                response = await client.post(
                    f"{self.api_base}/token",
                    headers=headers,
                    data=data
                )

                response.raise_for_status()
                token_data = response.json()

                logger.info("Successfully refreshed access token")
                return {
                    'access_token': token_data.get('access_token'),
                    'refresh_token': token_data.get('refresh_token'),
                    'token_type': token_data.get('token_type', 'Bearer'),
                    'expires_in': token_data.get('expires_in'),
                    'scope': token_data.get('scope', '').split(' ')
                }

            except httpx.HTTPStatusError as e:
                logger.error(f"Token refresh failed: {e.response.status_code} - {e.response.text}")
                raise Exception(f"Token refresh failed: {e.response.text}")

    async def verify_credentials(self, access_token: str) -> Optional[Dict]:
        async with httpx.AsyncClient() as client:
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

                logger.info(f"Token verified for user: {user_data.get('data', {}).get('username')}")
                return user_data.get('data')

            except httpx.HTTPStatusError as e:
                logger.error(f"Token verification failed: {e.response.status_code}")
                return None

    async def revoke_token(self, token: str, token_type_hint: str = "access_token") -> bool:
        async with httpx.AsyncClient() as client:

            auth_header = base64.b64encode(
                f"{self.client_id}:{self.client_secret}".encode()
            ).decode()

            headers = {
                'Authorization': f'Basic {auth_header}',
                'Content-Type': 'application/x-www-form-urlencoded'
            }

            data = {
                'token': token,
                'token_type_hint': token_type_hint,
                'client_id': self.client_id
            }

            try:
                response = await client.post(
                    f"{self.api_base}/revoke",
                    headers=headers,
                    data=data
                )

                response.raise_for_status()
                logger.info(f"Successfully revoked {token_type_hint}")
                return True

            except httpx.HTTPStatusError as e:
                logger.error(f"Token revocation failed: {e.response.status_code} - {e.response.text}")
                return False