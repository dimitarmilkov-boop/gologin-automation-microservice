"""
Automation Service - ProfileAutomator + BrowserController
Following DDD guide specifications - both classes in single file
"""

import asyncio
import time
import secrets
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, WebDriverException
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse, parse_qs
from datetime import datetime
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AuthorizationSession
from app.utils.logger import (
    get_logger,
    log_authorization_started,
    log_authorization_completed,
    log_authorization_failed,
    log_browser_action
)
from app.utils.retry import retry_browser_action, with_timeout
from app.utils.exceptions import (
    SeleniumConnectionException,
    BrowserAutomationException,
    AuthorizationTimeoutException,
    UserDeniedAuthorizationException,
    ProfileNotFoundException
)

logger = get_logger(__name__)

class BrowserController:
    """
    Low-level Selenium operations
    Technical browser automation details
    """

    def __init__(self):
        self.timeout = getattr(settings, 'browser_timeout', 30000) // 1000

    @retry_browser_action
    async def connect_to_profile(self, port: int) -> webdriver.Chrome:
        """Connect to GoLogin browser instance"""

        chrome_options = Options()
        chrome_options.add_argument(f"--remote-debugging-port={port}")
        chrome_options.add_experimental_option("debuggerAddress", f"127.0.0.1:{port}")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-web-security")
        chrome_options.add_argument("--disable-features=VizDisplayCompositor")

        try:
            driver = webdriver.Chrome(options=chrome_options)

            log_browser_action(
                logger,
                action="connect_to_profile",
                profile_id=f"port_{port}",
                success=True,
                details=f"Connected to browser on port {port}"
            )

            return driver

        except Exception as e:
            log_browser_action(
                logger,
                action="connect_to_profile",
                profile_id=f"port_{port}",
                success=False,
                details=str(e)
            )
            raise SeleniumConnectionException(port)

    async def navigate_to_oauth(self, driver: webdriver.Chrome, oauth_url: str) -> bool:
        """Navigate to OAuth authorization URL"""

        try:
            logger.debug(
                "browser.navigate",
                url=oauth_url[:80] + "..." if len(oauth_url) > 80 else oauth_url
            )

            driver.get(oauth_url)
            await asyncio.sleep(3)  # Wait for page load

            log_browser_action(
                logger,
                action="navigate_to_oauth",
                profile_id="unknown",
                success=True,
                details="Successfully navigated to OAuth URL"
            )

            return True

        except Exception as e:
            log_browser_action(
                logger,
                action="navigate_to_oauth",
                profile_id="unknown",
                success=False,
                details=str(e)
            )
            return False

    async def check_login_required(self, driver: webdriver.Chrome) -> bool:
        """Check if user needs to login (return False if login required)"""

        login_elements = [
            "//input[@name='text']",
            "//input[@autocomplete='username']",
            "//input[@data-testid='ocfEnterTextTextInput']",
            "//input[@placeholder*='email']",
            "//input[@placeholder*='username']"
        ]

        try:
            for xpath in login_elements:
                try:
                    element = WebDriverWait(driver, 3).until(
                        EC.presence_of_element_located((By.XPATH, xpath))
                    )
                    if element.is_displayed():
                        log_browser_action(
                            logger,
                            action="check_login_required",
                            profile_id="unknown",
                            success=False,
                            details="Login form detected - user not logged in"
                        )
                        return False
                except TimeoutException:
                    continue

            log_browser_action(
                logger,
                action="check_login_required",
                profile_id="unknown",
                success=True,
                details="User already logged in"
            )
            return True

        except Exception as e:
            logger.error(
                "browser.check_login_error",
                error=str(e),
                exc_info=True
            )
            return False

    @retry_browser_action
    async def click_authorize_button(self, driver: webdriver.Chrome) -> bool:
        """Find and click the Twitter/X authorize button"""

        authorize_selectors = [
            "//div[@data-testid='OAuth_Consent_Button']",
            "//div[@role='button' and contains(text(), 'Authorize')]",
            "//div[@role='button' and contains(text(), 'Allow')]",
            "//button[contains(text(), 'Authorize')]",
            "//button[contains(text(), 'Allow')]",
            "//input[@type='submit' and @value='Authorize']",
            "//input[@type='submit' and contains(@value, 'Allow')]",
            "//a[contains(@class, 'authorize') or contains(@class, 'allow')]"
        ]

        try:
            for selector in authorize_selectors:
                try:
                    button = WebDriverWait(driver, 5).until(
                        EC.element_to_be_clickable((By.XPATH, selector))
                    )

                    if button.is_displayed():
                        logger.debug(
                            "browser.authorize_button_found",
                            selector=selector
                        )

                        button.click()
                        await asyncio.sleep(2)  # Wait for click processing

                        log_browser_action(
                            logger,
                            action="click_authorize_button",
                            profile_id="unknown",
                            success=True,
                            details=f"Clicked authorize button: {selector}"
                        )

                        return True

                except TimeoutException:
                    continue

            log_browser_action(
                logger,
                action="click_authorize_button",
                profile_id="unknown",
                success=False,
                details="No authorize button found"
            )

            return False

        except Exception as e:
            log_browser_action(
                logger,
                action="click_authorize_button",
                profile_id="unknown",
                success=False,
                details=str(e)
            )
            return False

    @with_timeout(30.0)
    async def wait_for_callback(self, driver: webdriver.Chrome, callback_url: str, timeout: int = 30) -> Optional[str]:
        """Wait for OAuth callback and extract authorization code"""

        start_time = time.time()

        try:
            while time.time() - start_time < timeout:
                current_url = driver.current_url

                logger.debug(
                    "browser.url_check",
                    current_url=current_url[:80] + "..." if len(current_url) > 80 else current_url
                )

                # Check if we reached callback URL
                if callback_url in current_url:
                    logger.info(
                        "browser.callback_detected",
                        callback_url=current_url
                    )

                    # Extract authorization code
                    parsed_url = urlparse(current_url)
                    query_params = parse_qs(parsed_url.query)

                    if 'code' in query_params:
                        auth_code = query_params['code'][0]

                        log_browser_action(
                            logger,
                            action="wait_for_callback",
                            profile_id="unknown",
                            success=True,
                            details=f"Authorization code extracted: {auth_code[:10]}..."
                        )

                        return auth_code

                    if 'error' in query_params:
                        error = query_params['error'][0]
                        error_desc = query_params.get('error_description', [''])[0]

                        log_browser_action(
                            logger,
                            action="wait_for_callback",
                            profile_id="unknown",
                            success=False,
                            details=f"OAuth error: {error} - {error_desc}"
                        )

                        return None

                # Check for denial or error in URL
                if any(term in current_url.lower() for term in ["denied", "error", "cancel"]):
                    log_browser_action(
                        logger,
                        action="wait_for_callback",
                        profile_id="unknown",
                        success=False,
                        details="User denied authorization or error occurred"
                    )
                    return None

                await asyncio.sleep(1)

            # Timeout reached
            log_browser_action(
                logger,
                action="wait_for_callback",
                profile_id="unknown",
                success=False,
                details=f"Timeout after {timeout} seconds"
            )

            return None

        except Exception as e:
            log_browser_action(
                logger,
                action="wait_for_callback",
                profile_id="unknown",
                success=False,
                details=str(e)
            )
            return None

    async def take_screenshot(self, driver: webdriver.Chrome, filename: str) -> bool:
        """Take screenshot for debugging"""

        try:
            screenshot_path = f"/tmp/{filename}"
            driver.save_screenshot(screenshot_path)

            log_browser_action(
                logger,
                action="take_screenshot",
                profile_id="unknown",
                success=True,
                details=f"Screenshot saved: {screenshot_path}"
            )

            return True

        except Exception as e:
            log_browser_action(
                logger,
                action="take_screenshot",
                profile_id="unknown",
                success=False,
                details=str(e)
            )
            return False

    async def cleanup_driver(self, driver: webdriver.Chrome) -> None:
        """Clean up browser driver"""

        if driver:
            try:
                driver.quit()
                logger.debug("browser.driver_closed")
            except Exception as e:
                logger.warning(
                    "browser.driver_cleanup_error",
                    error=str(e)
                )

class ProfileAutomator:
    """
    High-level orchestration of authorization flow
    Business logic for when/why to authorize
    """

    def __init__(self, gologin_service, oauth_service):
        self.gologin_service = gologin_service
        self.oauth_service = oauth_service
        self.browser = BrowserController()

    async def authorize_account(
        self,
        profile_id: str,
        account_id: str,
        api_app,
        force_reauth: bool = False,
        session_id: Optional[int] = None
    ) -> Dict:
        """
        Main authorization flow orchestration
        Business logic for complete OAuth flow
        """

        start_time = time.time()
        api_app_str = api_app.value if hasattr(api_app, 'value') else str(api_app)

        log_authorization_started(
            logger,
            profile_id=profile_id,
            account_id=account_id,
            api_app=api_app_str
        )

        driver = None
        profile_started = False

        try:
            # Check existing authorization if not forcing reauth
            if not force_reauth:
                existing_auth = await self._check_existing_authorization(account_id, api_app_str)
                if existing_auth and existing_auth["status"] == "success":
                    duration = time.time() - start_time
                    log_authorization_completed(
                        logger,
                        profile_id=profile_id,
                        account_id=account_id,
                        duration_seconds=duration
                    )
                    return existing_auth

            # Start GoLogin profile
            profile_info = await self.gologin_service.start_profile(profile_id)
            profile_started = True
            port = profile_info["port"]

            # Connect to browser
            driver = await self.browser.connect_to_profile(port)

            # Generate OAuth URL
            state = secrets.token_urlsafe(32)
            scopes = "tweet.read users.read follows.read follows.write offline.access"
            auth_url, code_verifier = self.oauth_service.generate_auth_url(
                api_app_str, scopes, state
            )

            # Navigate to OAuth URL
            if not await self.browser.navigate_to_oauth(driver, auth_url):
                raise BrowserAutomationException("Failed to navigate to OAuth URL")

            # Check if user is logged in
            if not await self.browser.check_login_required(driver):
                raise BrowserAutomationException("User needs to login to Twitter/X first")

            # Click authorize button
            if not await self.browser.click_authorize_button(driver):
                # Take screenshot for debugging
                await self.browser.take_screenshot(driver, f"no_button_{account_id}_{int(time.time())}.png")
                raise BrowserAutomationException("Could not find or click authorize button")

            # Wait for callback and extract code
            callback_base = self.oauth_service._get_app_config(api_app_str)["callback_url"]
            auth_code = await self.browser.wait_for_callback(driver, callback_base, timeout=30)

            if not auth_code:
                # Take screenshot for debugging
                await self.browser.take_screenshot(driver, f"no_code_{account_id}_{int(time.time())}.png")
                raise UserDeniedAuthorizationException(account_id)

            # Exchange code for tokens
            token_data = await self.oauth_service.exchange_code_for_tokens(
                auth_code, api_app_str, code_verifier
            )

            # Verify credentials
            user_data = await self.oauth_service.verify_credentials(token_data['access_token'])
            if not user_data:
                raise BrowserAutomationException("Failed to verify credentials with obtained token")

            # Store tokens in session if provided
            if session_id:
                await self._store_tokens_in_session(session_id, token_data)

            duration = time.time() - start_time
            log_authorization_completed(
                logger,
                profile_id=profile_id,
                account_id=account_id,
                duration_seconds=duration
            )

            return {
                "status": "success",
                "oauth_token": token_data['access_token'],
                "oauth_token_secret": None,  # OAuth 2.0 doesn't use token secret
                "refresh_token": token_data['refresh_token'],
                "scopes": token_data['scope'],
                "user_data": user_data,
                "session_id": session_id
            }

        except Exception as e:
            log_authorization_failed(
                logger,
                profile_id=profile_id,
                account_id=account_id,
                error=str(e),
                error_code=getattr(e, 'error_code', 'AUTHORIZATION_FAILED')
            )

            # Take screenshot on error if driver available
            if driver:
                try:
                    await self.browser.take_screenshot(driver, f"error_{account_id}_{int(time.time())}.png")
                except:
                    pass

            return {
                "status": "error",
                "error_code": getattr(e, 'error_code', 'AUTHORIZATION_FAILED'),
                "message": str(e),
                "session_id": session_id
            }

        finally:
            # Cleanup browser
            if driver:
                await self.browser.cleanup_driver(driver)

            # Stop profile
            if profile_started:
                try:
                    await self.gologin_service.stop_profile(profile_id)
                except Exception as e:
                    logger.warning(
                        "automation.profile_stop_failed",
                        profile_id=profile_id,
                        error=str(e)
                    )

    async def check_authorization_status(self, account_id: str, api_app: str) -> Dict:
        """Check if account is already authorized"""

        try:
            # This would check database for existing valid tokens
            # and optionally verify them with Twitter API
            # Implementation depends on how you store authorization state

            return {
                "status": "not_implemented",
                "message": "Status check not yet implemented"
            }

        except Exception as e:
            logger.error(
                "automation.status_check_failed",
                account_id=account_id,
                api_app=api_app,
                error=str(e),
                exc_info=True
            )

            return {
                "status": "error",
                "error_code": "STATUS_CHECK_FAILED",
                "message": str(e)
            }

    async def revoke_authorization(self, account_id: str, api_app: str) -> Dict:
        """Revoke authorization for account"""

        try:
            # This would revoke tokens via Twitter API
            # and update database state
            # Implementation depends on token storage strategy

            return {
                "status": "not_implemented",
                "message": "Revocation not yet implemented"
            }

        except Exception as e:
            logger.error(
                "automation.revocation_failed",
                account_id=account_id,
                api_app=api_app,
                error=str(e),
                exc_info=True
            )

            return {
                "status": "error",
                "error_code": "REVOCATION_FAILED",
                "message": str(e)
            }

    async def _check_existing_authorization(self, account_id: str, api_app: str) -> Optional[Dict]:
        """Check for existing valid authorization"""
        # Placeholder for checking existing authorization state
        # Would implement database lookup and token refresh logic
        return None

    async def _store_tokens_in_session(self, session_id: int, token_data: Dict) -> None:
        """Store tokens in authorization session"""
        # Placeholder for token storage
        # Would implement database update logic
        pass