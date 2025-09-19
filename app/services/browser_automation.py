import asyncio
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.desired_capabilities import DesiredCapabilities
from selenium.common.exceptions import TimeoutException, WebDriverException
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse, parse_qs
from loguru import logger
import time

from app.config import settings

class TwitterAutomation:
    def __init__(self, profile_info: Dict):
        self.profile_info = profile_info
        self.driver = None
        self.timeout = settings.browser_timeout // 1000

    async def connect_to_profile(self):
        chrome_options = Options()
        chrome_options.add_argument(f"--remote-debugging-port={self.profile_info['port']}")
        chrome_options.add_experimental_option("debuggerAddress", f"127.0.0.1:{self.profile_info['port']}")

        capabilities = DesiredCapabilities.CHROME.copy()
        capabilities.update(chrome_options.to_capabilities())

        try:
            self.driver = webdriver.Chrome(options=chrome_options)
            logger.info(f"Connected to profile {self.profile_info['profile_id']}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to profile: {str(e)}")
            return False

    async def navigate_to_oauth(self, client_id: str, redirect_uri: str, scopes: str) -> bool:
        if not self.driver:
            return False

        oauth_url = (
            f"https://twitter.com/i/oauth2/authorize"
            f"?response_type=code"
            f"&client_id={client_id}"
            f"&redirect_uri={redirect_uri}"
            f"&scope={scopes}"
            f"&state=state123"
            f"&code_challenge=challenge"
            f"&code_challenge_method=plain"
        )

        try:
            logger.info(f"Navigating to OAuth URL: {oauth_url}")
            self.driver.get(oauth_url)
            await asyncio.sleep(3)
            return True
        except Exception as e:
            logger.error(f"Failed to navigate to OAuth URL: {str(e)}")
            return False

    async def handle_login_if_needed(self) -> bool:
        try:
            login_elements = [
                "//input[@name='text']",
                "//input[@autocomplete='username']",
                "//input[@data-testid='ocfEnterTextTextInput']"
            ]

            for xpath in login_elements:
                try:
                    element = WebDriverWait(self.driver, 3).until(
                        EC.presence_of_element_located((By.XPATH, xpath))
                    )
                    if element.is_displayed():
                        logger.info("Login required - already logged in session expected")
                        return False
                except TimeoutException:
                    continue

            return True

        except Exception as e:
            logger.error(f"Error checking login status: {str(e)}")
            return False

    async def click_authorize_button(self) -> bool:
        try:
            authorize_selectors = [
                "//div[@data-testid='OAuth_Consent_Button']",
                "//div[@role='button' and contains(text(), 'Authorize')]",
                "//div[@role='button' and contains(text(), 'Allow')]",
                "//button[contains(text(), 'Authorize')]",
                "//button[contains(text(), 'Allow')]",
                "//input[@type='submit' and @value='Authorize']"
            ]

            for selector in authorize_selectors:
                try:
                    button = WebDriverWait(self.driver, 5).until(
                        EC.element_to_be_clickable((By.XPATH, selector))
                    )

                    if button.is_displayed():
                        logger.info(f"Found authorize button with selector: {selector}")
                        button.click()
                        logger.info("Authorize button clicked")
                        await asyncio.sleep(2)
                        return True

                except TimeoutException:
                    continue

            logger.warning("No authorize button found")
            return False

        except Exception as e:
            logger.error(f"Error clicking authorize button: {str(e)}")
            return False

    async def wait_for_callback_and_extract_code(self, callback_url: str, timeout: int = 30) -> Optional[str]:
        try:
            start_time = time.time()

            while time.time() - start_time < timeout:
                current_url = self.driver.current_url
                logger.debug(f"Current URL: {current_url}")

                if callback_url in current_url:
                    logger.info(f"Callback URL detected: {current_url}")

                    parsed_url = urlparse(current_url)
                    query_params = parse_qs(parsed_url.query)

                    if 'code' in query_params:
                        auth_code = query_params['code'][0]
                        logger.info(f"Authorization code extracted: {auth_code[:10]}...")
                        return auth_code

                    if 'error' in query_params:
                        error = query_params['error'][0]
                        error_desc = query_params.get('error_description', [''])[0]
                        logger.error(f"OAuth error: {error} - {error_desc}")
                        return None

                if "denied" in current_url.lower() or "error" in current_url.lower():
                    logger.error("User denied authorization or error occurred")
                    return None

                await asyncio.sleep(1)

            logger.error("Timeout waiting for callback")
            return None

        except Exception as e:
            logger.error(f"Error waiting for callback: {str(e)}")
            return None

    async def check_existing_authorization(self, api_app: str) -> Optional[Dict]:
        try:
            apps_url = "https://twitter.com/settings/connected_apps"
            self.driver.get(apps_url)
            await asyncio.sleep(3)

            app_elements = self.driver.find_elements(By.XPATH, f"//div[contains(text(), '{api_app}')]")

            if app_elements:
                logger.info(f"Found existing authorization for {api_app}")
                return {"status": "authorized", "app": api_app}

            return None

        except Exception as e:
            logger.error(f"Error checking existing authorization: {str(e)}")
            return None

    async def revoke_authorization(self, api_app: str) -> bool:
        try:
            apps_url = "https://twitter.com/settings/connected_apps"
            self.driver.get(apps_url)
            await asyncio.sleep(3)

            app_element = self.driver.find_element(By.XPATH, f"//div[contains(text(), '{api_app}')]")

            revoke_button = app_element.find_element(By.XPATH, ".//following::button[contains(text(), 'Revoke')]")
            revoke_button.click()

            confirm_button = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//div[@role='button' and contains(text(), 'Revoke')]"))
            )
            confirm_button.click()

            logger.info(f"Revoked authorization for {api_app}")
            return True

        except Exception as e:
            logger.error(f"Error revoking authorization: {str(e)}")
            return False

    async def take_screenshot(self, filename: str) -> bool:
        try:
            if self.driver:
                self.driver.save_screenshot(f"/tmp/{filename}")
                logger.info(f"Screenshot saved: {filename}")
                return True
        except Exception as e:
            logger.error(f"Error taking screenshot: {str(e)}")
        return False

    async def cleanup(self):
        if self.driver:
            try:
                self.driver.quit()
                logger.info("Browser driver closed")
            except Exception as e:
                logger.error(f"Error closing driver: {str(e)}")

class BrowserAutomationService:
    def __init__(self, profile_manager):
        self.profile_manager = profile_manager

    async def authorize_twitter_account(
        self,
        account_id: str,
        client_id: str,
        redirect_uri: str,
        scopes: str = "tweet.read%20users.read%20follows.read%20follows.write"
    ) -> Tuple[bool, Optional[str], Optional[str]]:

        profile_info = await self.profile_manager.get_profile_by_account(account_id)
        if not profile_info:
            return False, None, f"No profile found for account {account_id}"

        started_profile = await self.profile_manager.start_profile(profile_info["id"])
        if not started_profile:
            return False, None, f"Failed to start profile for {account_id}"

        automation = TwitterAutomation(started_profile)

        try:
            if not await automation.connect_to_profile():
                return False, None, "Failed to connect to browser profile"

            if not await automation.navigate_to_oauth(client_id, redirect_uri, scopes):
                return False, None, "Failed to navigate to OAuth URL"

            if not await automation.handle_login_if_needed():
                return False, None, "User needs to login manually"

            if not await automation.click_authorize_button():
                return False, None, "Failed to click authorize button"

            auth_code = await automation.wait_for_callback_and_extract_code(redirect_uri)

            if auth_code:
                return True, auth_code, "Authorization successful"
            else:
                return False, None, "Failed to extract authorization code"

        except Exception as e:
            logger.error(f"Browser automation error: {str(e)}")
            return False, None, str(e)

        finally:
            await automation.cleanup()
            await self.profile_manager.stop_profile(profile_info["id"])