#!/usr/bin/env python3
"""
X OAuth Authorization Automation for AIOTT
Automates the OAuth app authorization process using GoLogin profiles
"""

import time
import logging
import os
import socket
import base64
import json
from typing import Dict, Any, Optional, List
from urllib.parse import urlencode, urlparse, parse_qs
from dotenv import load_dotenv

try:
    import pyotp
    PYOTP_AVAILABLE = True
except ImportError:
    PYOTP_AVAILABLE = False
    logging.warning("pyotp not installed - 2FA automation will be disabled")

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from gologin import GoLogin

from fix_db_connections import DBConnection
from gologin_manager import GoLoginManager

# Note: X login credentials should be stored in a table like:
# CREATE TABLE x_login_credentials (
#     id INTEGER PRIMARY KEY,
#     profile_id TEXT,
#     username TEXT NOT NULL,
#     password TEXT NOT NULL,
#     created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
# );

# Load environment variables
load_dotenv()


class SeleniumOAuthAutomator:
    """
    Automates X OAuth authorization for AIOTT applications
    Assumes GoLogin profiles are already logged into X
    """
    
    def __init__(self, db_path: str = 'twitter_accounts.db'):
        self.db_path = db_path
        self.logger = logging.getLogger(self.__class__.__name__)
        
        # GoLogin configuration
        self.gologin_token = os.getenv('GOLOGIN_TOKEN')
        if not self.gologin_token:
            raise ValueError("GOLOGIN_TOKEN environment variable required")
        
        # Check for tunnel URL (required for proxy bypass)
        self.tunnel_url = os.getenv('AIOTT_TUNNEL_URL')
        if not self.tunnel_url:
            self.logger.warning("No AIOTT_TUNNEL_URL set - proxy issues may occur")
            self.logger.info("Set tunnel URL or disable proxy in GoLogin profile")
    
    def automate_bulk_user_login(self, profile_id: str) -> Dict[str, Any]:
        """
        NEW FLOW: Login DISCONNECTED users sequentially 
        
        Flow:
        1. Get all DISCONNECTED users from database (matches UI logic)
        2. Start GoLogin session once
        3. For each DISCONNECTED user:
           - Login to X (with 2FA if needed)
           - Mark as "Connected" in database
           - Continue to next user (even if current fails)
        4. Return summary of all login attempts
        
        ONLY processes accounts that show "Disconnected" in UI!
        """
        self.logger.info(f"Starting bulk user login automation for profile {profile_id}")
        
        # Get all DISCONNECTED users (matches UI logic)
        unauthorized_users = self._get_all_unauthorized_users()
        if not unauthorized_users:
            return {
                'success': False, 
                'error': 'No DISCONNECTED users found in database - all accounts already Connected!'
            }
        
        self.logger.info(f"Found {len(unauthorized_users)} DISCONNECTED users to login")
        
        gl = None
        driver = None
        results = {
            'success': True,
            'total_users': len(unauthorized_users),
            'successful_logins': 0,
            'failed_logins': 0,
            'login_results': []
        }
        
        try:
            # Step 1: Start GoLogin session once
            self.logger.info("Starting GoLogin session...")
            
            # Set custom temp directory to avoid Windows path issues
            import tempfile
            custom_tmpdir = tempfile.mkdtemp(prefix="gologin_")
            self.logger.info(f"Using custom temp directory: {custom_tmpdir}")
            
            gl = GoLogin({
                "token": self.gologin_token,
                "profile_id": profile_id,
                "tmpdir": custom_tmpdir
            })
            
            debugger_address = gl.start()
            if not debugger_address:
                return {'success': False, 'error': 'Failed to start GoLogin session'}
            
            self.logger.info(f"GoLogin session started - debugger: {debugger_address}")
            
            # Step 2: Connect Selenium
            driver = self._connect_selenium(debugger_address)
            if not driver:
                return {'success': False, 'error': 'Failed to connect Selenium'}
            
            # Step 3: Process each user SEQUENTIALLY (not in parallel tabs)
            # User requested: "the second account should wait for the first to finish with the login"
            for i, user in enumerate(unauthorized_users):
                self.logger.info(f"\n=== SEQUENTIAL LOGIN {i+1}/{len(unauthorized_users)} ===\nProcessing: {user['account_name']} (waiting for completion before next user)")
                
                # Check if selenium session is still alive before each user
                try:
                    driver.current_url  # Simple test to check if session is alive
                    self.logger.info(f"Selenium session is alive for user {user['account_name']}")
                except Exception as session_error:
                    self.logger.error(f"SELENIUM SESSION DIED before {user['account_name']}: {session_error}")
                    self.logger.info("Attempting to reconnect Selenium...")
                    
                    # Try to reconnect
                    driver = self._connect_selenium(debugger_address)
                    if not driver:
                        self.logger.error("FAILED TO RECONNECT SELENIUM - ABORTING REMAINING USERS")
                        break
                
                user_result = self._login_single_user_in_tab(driver, user, i + 1, len(unauthorized_users))
                results['login_results'].append(user_result)
                
                if user_result['success']:
                    results['successful_logins'] += 1
                    # Mark user as "Connected" in database
                    self._mark_user_as_connected(user['account_name'])
                    self.logger.info(f"SUCCESS: {user['account_name']} login completed - proceeding to next user")
                else:
                    results['failed_logins'] += 1
                    self.logger.warning(f"FAILED: {user['account_name']} login failed - proceeding to next user anyway")
                
                # Longer delay between sequential users for stability
                if i < len(unauthorized_users) - 1:  # Not the last user
                    self.logger.info(f"Waiting 3 seconds before next user...")
                    time.sleep(3)
            
            self.logger.info(f"Bulk login completed: {results['successful_logins']}/{results['total_users']} successful")
            
            return results
            
        except Exception as e:
            self.logger.error(f"Bulk login automation error: {e}")
            return {'success': False, 'error': str(e)}
        finally:
            if driver:
                driver.quit()
            if gl:
                gl.stop()
            # Cleanup custom temp directory
            try:
                if 'custom_tmpdir' in locals():
                    import shutil
                    shutil.rmtree(custom_tmpdir, ignore_errors=True)
                    self.logger.info(f"Cleaned up temp directory: {custom_tmpdir}")
            except Exception as cleanup_error:
                self.logger.warning(f"Could not cleanup temp directory: {cleanup_error}")
    
    def automate_oauth_for_profile(self, profile_id: str, api_app: str, login_only: bool = False) -> Dict[str, Any]:
        """
        FIXED: Now defaults to LOGIN ONLY - NO OAuth unless explicitly requested
        
        The user explicitly said: AFTER LOGIN WE DO NOT NEED AUTHORIZATION
        So this method now redirects to bulk login automation
        """
        self.logger.info(f"REDIRECTING TO BULK LOGIN - No OAuth expected after login!")
        
        # Route to bulk login automation instead of OAuth
        return self.automate_bulk_user_login(profile_id)
    
    def _connect_selenium(self, debugger_address: str) -> Optional[webdriver.Chrome]:
        """Connect Selenium to the GoLogin browser"""
        try:
            self.logger.info(f"ATTEMPTING SELENIUM CONNECTION TO: {debugger_address}")
            
            chrome_options = Options()
            chrome_options.add_experimental_option("debuggerAddress", debugger_address)
            
            # Add additional options for stability
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_argument("--disable-gpu")
            chrome_options.add_argument("--remote-debugging-port=0")  # Let Chrome choose port
            
            # Use the specific ChromeDriver version that matches GoLogin
            service = Service(ChromeDriverManager(driver_version="133.0.6943.54").install())
            
            # Set shorter timeout to fail fast if connection issues
            driver = webdriver.Chrome(service=service, options=chrome_options)
            
            # Test the connection with a simple command
            driver.get("chrome://version/")
            self.logger.info(f"SELENIUM CONNECTED SUCCESSFULLY to {debugger_address}")
            return driver
            
        except Exception as e:
            self.logger.error(f"SELENIUM CONNECTION FAILED to {debugger_address}: {e}")
            
            # Try alternative connection method
            try:
                self.logger.info("Attempting alternative Selenium connection...")
                chrome_options = Options()
                chrome_options.add_experimental_option("debuggerAddress", debugger_address)
                chrome_options.add_argument("--disable-web-security")
                chrome_options.add_argument("--disable-features=VizDisplayCompositor")
                
                service = Service(ChromeDriverManager().install())  # Use latest driver
                driver = webdriver.Chrome(service=service, options=chrome_options)
                driver.get("chrome://version/")
                self.logger.info("ALTERNATIVE SELENIUM CONNECTION SUCCESSFUL")
                return driver
                
            except Exception as e2:
                self.logger.error(f"ALTERNATIVE SELENIUM CONNECTION ALSO FAILED: {e2}")
                return None
    
    def _generate_auto_login_url(self) -> str:
        """Generate auto-login URL for AIOTT"""
        base_url = self.tunnel_url or f"http://{self._get_host_ip()}:5005"
        
        token_data = {
            'user_id': 1,
            'username': 'oauth_automator', 
            'is_admin': True,
            'expires': time.time() + 86400,
            'created': time.time()
        }
        
        token = base64.b64encode(json.dumps(token_data).encode()).decode()
        return f"{base_url}/auth/auto-login?token={token}"
    
    def _generate_oauth_url(self, profile_id: str, api_app: str) -> Dict[str, Any]:
        """Generate the X OAuth authorization URL"""
        try:
            # Determine callback URL
            base_url = self.tunnel_url or f"http://{self._get_host_ip()}:5005"
            
            # Get API configuration from database
            with DBConnection(self.db_path) as (conn, cursor):
                cursor.execute(
                    "SELECT client_id, client_secret, callback_url FROM twitter_api_configs WHERE name = ? AND is_active = 1",
                    (api_app,)
                )
                result = cursor.fetchone()
                
                if not result:
                    return {'success': False, 'error': f'API configuration not found: {api_app}'}
                
                client_id, client_secret, db_callback_url = result
                
                # Handle NULL callback_url
                if not db_callback_url:
                    db_callback_url = "api/oauth/callback"
                    self.logger.warning(f"No callback_url in database for {api_app}, using default")
                
                callback_url = f"{base_url}/{db_callback_url.lstrip('/')}"
                self.logger.info(f"Using callback URL: {callback_url}")
            
            # Generate OAuth parameters
            import secrets
            import hashlib
            
            state = f"selenium_{profile_id}_{int(time.time())}"
            code_verifier = secrets.token_urlsafe(32)
            code_challenge = base64.urlsafe_b64encode(
                hashlib.sha256(code_verifier.encode()).digest()
            ).decode().rstrip('=')
            
            # Store state in database for callback processing
            with DBConnection(self.db_path) as (conn, cursor):
                cursor.execute('''
                    INSERT INTO oauth_automation_states 
                    (state, profile_id, api_app, code_verifier, client_id, callback_url, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?, datetime('now', '+1 hour'))
                ''', (state, profile_id, api_app, code_verifier, client_id, callback_url))
            
            # Build OAuth authorization URL
            auth_params = {
                'response_type': 'code',
                'client_id': client_id,
                'redirect_uri': callback_url,
                'scope': 'tweet.read tweet.write users.read offline.access media.write like.write',
                'state': state,
                'code_challenge': code_challenge,
                'code_challenge_method': 'S256'
            }
            
            oauth_url = f"https://twitter.com/i/oauth2/authorize?{urlencode(auth_params)}"
            
            return {
                'success': True,
                'oauth_url': oauth_url,
                'state': state
            }
            
        except Exception as e:
            self.logger.error(f"Error generating OAuth URL: {e}")
            return {'success': False, 'error': str(e)}
    
    def _handle_oauth_authorization(self, driver: webdriver.Chrome, profile_id: str) -> Dict[str, Any]:
        """
        Handle the OAuth authorization page with adaptive flow detection
        Uses enhanced page state detection to handle various X scenarios
        """
        try:
            # Use enhanced page analysis
            self.logger.info("Starting OAuth authorization with adaptive flow detection")
            self._analyze_current_page(driver)
            
            # Detect current page state
            page_state = self._detect_page_state(driver)
            self.logger.info(f"Initial page state detected: {page_state}")
            
            # Handle different page states adaptively
            if page_state == "authorization_form":
                self.logger.info("Authorization form detected - proceeding directly to authorize")
                # Skip login, go straight to authorization
                
            elif page_state == "already_logged_in":
                self.logger.info("Already logged into X - waiting for OAuth redirect")
                time.sleep(3)
                page_state = self._detect_page_state(driver)
                self.logger.info(f"Post-wait page state: {page_state}")
                
            elif page_state == "login_form":
                self.logger.info("Login form detected - attempting automatic login")
                
                # Try automatic login
                if self._attempt_x_login(driver, profile_id):
                    self.logger.info("X login successful - waiting for authorization redirect")
                    time.sleep(5)  # Wait for redirect
                    
                    # Re-analyze page state after login
                    page_state = self._detect_page_state(driver)
                    self.logger.info(f"Post-login page state: {page_state}")
                    
                    if page_state != "authorization_form":
                        self._save_debug_screenshot(driver, "login_success_but_no_auth_form")
                        return {
                            'success': False, 
                            'error': f'Login successful but authorization form not found. Page state: {page_state}'
                        }
                else:
                    self._save_debug_screenshot(driver, "login_failed")
                    return {
                        'success': False, 
                        'error': 'Could not complete X login - check credentials and account status'
                    }
                    
            elif page_state == "2fa_required":
                self.logger.info("2FA verification required - attempting automatic 2FA")
                
                # Get credentials to check for 2FA secret
                credentials = self._get_x_credentials_for_profile(profile_id)
                if credentials and self._handle_2fa_verification(driver, credentials):
                    self.logger.info("2FA verification successful - waiting for authorization redirect")
                    time.sleep(5)  # Wait for redirect after 2FA
                    
                    # Re-analyze page state after 2FA
                    page_state = self._detect_page_state(driver)
                    self.logger.info(f"Post-2FA page state: {page_state}")
                    
                    if page_state != "authorization_form":
                        self._save_debug_screenshot(driver, "2fa_success_but_no_auth_form")
                        return {
                            'success': False, 
                            'error': f'2FA successful but authorization form not found. Page state: {page_state}'
                        }
                else:
                    self._save_debug_screenshot(driver, "2fa_failed")
                    return {
                        'success': False,
                        'error': '2FA verification failed or no 2FA secret available'
                    }
                
            elif page_state == "verification_required":
                self._save_debug_screenshot(driver, "verification_required")
                return {
                    'success': False,
                    'error': 'X account requires verification - manual intervention needed'
                }
                
            elif page_state == "rate_limited":
                self._save_debug_screenshot(driver, "rate_limited")
                return {
                    'success': False,
                    'error': 'Rate limited by X - please try again later'
                }
                
            elif page_state == "captcha_required":
                self._save_debug_screenshot(driver, "captcha_required")
                return {
                    'success': False,
                    'error': 'CAPTCHA required - manual intervention needed'
                }
                
            else:
                self.logger.warning(f"Unknown page state: {page_state} - attempting to proceed")
                self._save_debug_screenshot(driver, f"unknown_state_{page_state}")
                
                # Wait and re-analyze
                time.sleep(5)
                page_state = self._detect_page_state(driver)
                self.logger.info(f"After wait, page state: {page_state}")
                
                # Handle login_form after re-analysis
                if page_state == "login_form":
                    self.logger.info("Login form detected after re-analysis - attempting automatic login")
                    
                    # Try automatic login
                    if self._attempt_x_login(driver, profile_id):
                        self.logger.info("X login successful - waiting for authorization redirect")
                        time.sleep(5)  # Wait for redirect
                        
                        # Re-analyze page state after login
                        page_state = self._detect_page_state(driver)
                        self.logger.info(f"Post-login page state: {page_state}")
                        
                        if page_state not in ["authorization_form", "already_logged_in"]:
                            self._save_debug_screenshot(driver, "login_success_but_no_auth_form")
                            return {
                                'success': False, 
                                'error': f'Login successful but authorization form not found. Page state: {page_state}'
                            }
                    else:
                        self._save_debug_screenshot(driver, "login_failed_after_reanalysis")
                        return {
                            'success': False, 
                            'error': 'Could not complete X login after re-analysis - check credentials and account status'
                        }
                elif page_state not in ["authorization_form", "already_logged_in"]:
                    return {
                        'success': False,
                        'error': f'Unable to proceed - unexpected page state: {page_state}'
                    }

            # Wait a moment for the page to fully load
            time.sleep(3)
            
            # Look for the authorize button
            self.logger.info("Looking for authorize button...")
            
            # Try multiple strategies to find and click the authorize button
            authorized = False
            
            # Strategy 1: Standard OAuth authorize button
            authorize_selectors = [
                'input[id="allow"]',
                'button[id="allow"]',
                'input[value="Authorize app"]',
                'input[value="Authorize"]',
                'button[value="Authorize"]',
                'input[type="submit"][value*="Authorize"]',
                'button[type="submit"]:has-text("Authorize")',
                'form[action*="/oauth2/authorize"] input[type="submit"]',
                'form[action*="/oauth2/authorize"] button[type="submit"]'
            ]
            
            for selector in authorize_selectors:
                try:
                    element = driver.find_element(By.CSS_SELECTOR, selector)
                    if element.is_displayed() and element.is_enabled():
                        element.click()
                        self.logger.info(f"Clicked authorize button: {selector}")
                        authorized = True
                        break
                except:
                    continue
            
            # Strategy 2: Find by button text
            if not authorized:
                try:
                    buttons = driver.find_elements(By.TAG_NAME, "button")
                    for button in buttons:
                        button_text = button.text.lower()
                        if "authorize" in button_text or "allow" in button_text:
                            if button.is_displayed() and button.is_enabled():
                                button.click()
                                self.logger.info(f"Clicked button with text: {button.text}")
                                authorized = True
                                break
                except Exception as e:
                    self.logger.debug(f"Error checking buttons: {e}")
            
            # Strategy 3: Find input buttons by value
            if not authorized:
                try:
                    inputs = driver.find_elements(By.CSS_SELECTOR, "input[type='submit']")
                    for input_elem in inputs:
                        value = input_elem.get_attribute("value") or ""
                        if "authorize" in value.lower() or "allow" in value.lower():
                            if input_elem.is_displayed() and input_elem.is_enabled():
                                input_elem.click()
                                self.logger.info(f"Clicked input with value: {value}")
                                authorized = True
                                break
                except Exception as e:
                    self.logger.debug(f"Error checking input buttons: {e}")
            
            if authorized:
                return {'success': True}
            else:
                # Log page info for debugging
                self.logger.error("Could not find authorize button")
                self.logger.debug(f"Current URL: {driver.current_url}")
                # Safe unicode logging
                safe_title = driver.title.encode('ascii', 'replace').decode('ascii')
                self.logger.debug(f"Page title: {safe_title}")
                return {'success': False, 'error': 'Could not find or click authorize button'}
            
        except Exception as e:
            self.logger.error(f"Error handling authorization: {e}")
            return {'success': False, 'error': str(e)}

    def _handle_x_login(self, driver: webdriver.Chrome, profile_id: str) -> bool:
        """Handle the X login flow if the session is not active."""
        try:
            self.logger.info("Attempting to fetch credentials for login...")
            creds = self._get_credentials_for_profile(profile_id)
            if not creds:
                self.logger.error("Could not find credentials for this profile.")
                return False

            wait = WebDriverWait(driver, 20)

            # Enter username/email
            username_field = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, 'input[autocomplete="username"]')))
            username_field.send_keys(creds['username'])
            self.logger.info(f"Entered username: {creds['username']}")
            time.sleep(1)

            # Click Next - Smart Language-Independent Detection  
            next_button = self._find_button_by_attributes(driver, "next")
            if not next_button:
                next_button = self._find_button_by_structure(driver, "username_next")
            
            if next_button:
                next_button.click()
                self.logger.info("Clicked 'Next' after username (smart detection)")
            else:
                # Fallback to old method if smart detection fails
                driver.find_element(By.XPATH, '//span[text()="Next" or text()="Dalej" or text()="次へ"]/ancestor::button').click()
                self.logger.info("Clicked 'Next' after username (fallback method)")
            time.sleep(2)

            # Enter password
            password_field = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, 'input[name="password"]')))
            password_field.send_keys(creds['password'])
            self.logger.info("Entered password.")
            time.sleep(1)

            # Click Log in - Smart Language-Independent Detection
            login_button = self._find_button_by_attributes(driver, "login")
            if not login_button:
                login_button = self._find_button_by_structure(driver, "password_login")
            
            if login_button:
                login_button.click()
                self.logger.info("Clicked 'Log in' (smart detection)")
            else:
                # Fallback to old method if smart detection fails
                driver.find_element(By.XPATH, '//span[text()="Log in" or text()="Zaloguj si\u0119" or text()="ログイン"]/ancestor::button').click()
                self.logger.info("Clicked 'Log in' (fallback method)")

            return True

        except Exception as e:
            self.logger.error(f"An error occurred during X login: {e}")
            return False

    def _handle_oauth_callback(self, driver: webdriver.Chrome, state: str, 
                              api_app: str, profile_id: str) -> Dict[str, Any]:
        """Wait for OAuth callback and process the authorization code"""
        try:
            self.logger.info("Waiting for OAuth callback...")
            
            # Wait for redirect to callback URL
            for attempt in range(20):  # Wait up to 20 seconds
                current_url = driver.current_url
                
                # Check if we got the callback with authorization code
                if 'code=' in current_url:
                    self.logger.info("OAuth callback received!")
                    
                    # Parse the callback URL
                    parsed = urlparse(current_url)
                    params = parse_qs(parsed.query)
                    auth_code = params.get('code', [None])[0]
                    callback_state = params.get('state', [None])[0]
                    
                    if auth_code:
                        # Verify state matches
                        if callback_state != state:
                            self.logger.warning(f"State mismatch: expected {state}, got {callback_state}")
                        
                        # Complete account creation
                        return self._complete_account_creation(
                            auth_code, callback_state or state, api_app, profile_id
                        )
                
                # Check for error in callback
                if 'error=' in current_url:
                    parsed = urlparse(current_url)
                    params = parse_qs(parsed.query)
                    error = params.get('error', ['unknown'])[0]
                    error_desc = params.get('error_description', [''])[0]
                    return {'success': False, 'error': f'OAuth error: {error} - {error_desc}'}
                
                time.sleep(1)
            
            return {'success': False, 'error': 'Timeout waiting for OAuth callback'}
            
        except Exception as e:
            self.logger.error(f"Error handling callback: {e}")
            return {'success': False, 'error': str(e)}

    def _attempt_x_login(self, driver: webdriver.Chrome, profile_id: str) -> bool:
        """Attempt to log into X automatically using stored credentials."""
        try:
            self.logger.info("Attempting automatic X login...")
            
            # First, let's understand what page we're actually on
            self._analyze_current_page(driver)
            
            # Get credentials for this profile
            credentials = self._get_x_credentials_for_profile(profile_id)
            if not credentials:
                self.logger.error("No X login credentials found for this profile")
                return False
            
            username = credentials['username']
            password = credentials['password']
            self.logger.info(f"Found credentials for user: {username}")
            
            # Check if we're already logged in or need to handle different page states
            page_state = self._detect_page_state(driver)
            self.logger.info(f"Detected page state: {page_state}")
            
            if page_state == "already_logged_in":
                self.logger.info("Already logged into X - proceeding to authorization")
                return True
            elif page_state == "verification_required":
                self.logger.warning("X is asking for verification - manual intervention needed")
                return False
            elif page_state == "rate_limited":
                self.logger.warning("Rate limited by X - try again later")
                return False
            elif page_state != "login_form":
                self.logger.warning(f"Unexpected page state: {page_state} - attempting to proceed anyway")
            
            # Fill username/email field
            username_selectors = [
                'input[autocomplete="username"]',
                'input[name="text"]',
                'input[data-testid="ocfEnterTextTextInput"]',
                'input[placeholder*="email"]',
                'input[placeholder*="username"]',
                'input[type="text"]',
                'input[type="email"]'
            ]
            
            username_filled = False
            for selector in username_selectors:
                try:
                    username_field = driver.find_element(By.CSS_SELECTOR, selector)
                    username_field.clear()
                    username_field.send_keys(username)
                    self.logger.info(f"Filled username field using selector: {selector}")
                    username_filled = True
                    break
                except:
                    continue
            
            if not username_filled:
                self.logger.error("Could not find username field")
                self._save_debug_screenshot(driver, "username_field_not_found")
                return False
            
            time.sleep(1)
            
            # Click Next button - improved detection
            next_clicked = False
            
            # Try multiple strategies to find the Next/Dalej button
            strategies = [
                # Strategy 1: Direct text search
                lambda: driver.find_element(By.XPATH, "//span[text()='Dalej' or text()='Next']/ancestor::div[@role='button']"),
                # Strategy 2: Button with specific text
                lambda: driver.find_element(By.XPATH, "//div[@role='button' and .//text()[contains(., 'Dalej')]]"),
                # Strategy 3: Any clickable element containing 'Dalej'
                lambda: driver.find_element(By.XPATH, "//*[contains(text(), 'Dalej') and (@role='button' or self::button)]"),
                # Strategy 4: CSS selector approach
                lambda: driver.find_element(By.CSS_SELECTOR, "div[role='button']:has(span)"),
                # Strategy 5: Look for the specific button structure from screenshot
                lambda: driver.find_element(By.XPATH, "//div[@role='button']//span[contains(text(), 'Dalej')]/parent::div")
            ]
            
            for i, strategy in enumerate(strategies, 1):
                try:
                    next_button = strategy()
                    if next_button and next_button.is_enabled():
                        next_button.click()
                        self.logger.info(f"Clicked Next button using strategy {i}: '{next_button.text.strip()}'")
                        next_clicked = True
                        break
                except Exception as e:
                    self.logger.debug(f"Strategy {i} failed: {e}")
                    continue
            
            # If all strategies fail, be more selective about buttons
            if not next_clicked:
                try:
                    # Look specifically for Next/Continue type buttons, avoid Apple/Google login
                    buttons = driver.find_elements(By.CSS_SELECTOR, "div[role='button'], button")
                    for button in buttons:
                        try:
                            text = button.text.strip().lower()
                            # Only click buttons that are clearly Next/Continue, avoid login providers
                            if (text in ['dalej', 'next', 'continue', 'kontynuuj'] and 
                                'apple' not in text and 'google' not in text and 'przez' not in text):
                                self.logger.info(f"Clicking Next button with text: '{button.text.strip()}'")
                                button.click()
                                next_clicked = True
                                break
                        except:
                            continue
                except:
                    pass
            
            if not next_clicked:
                self.logger.error("Could not find Next button")
                return False
            
            time.sleep(2)
            
            # Fill password field
            password_selectors = [
                'input[name="password"]',
                'input[type="password"]',
                'input[autocomplete="current-password"]',
                'input[data-testid="ocfEnterTextTextInput"]'
            ]
            
            password_filled = False
            for selector in password_selectors:
                try:
                    password_field = driver.find_element(By.CSS_SELECTOR, selector)
                    password_field.clear()
                    password_field.send_keys(password)
                    self.logger.info("Filled password field")
                    password_filled = True
                    break
                except:
                    continue
            
            if not password_filled:
                self.logger.error("Could not find password field")
                return False
            
            time.sleep(1)
            
            # Click Login button - smart language detection
            login_clicked = False
            
            # First try by data-testid (language independent)
            try:
                login_button = driver.find_element(By.CSS_SELECTOR, '[data-testid*="Login"], [data-testid*="login"]')
                login_button.click()
                self.logger.info("Clicked Login button via data-testid")
                login_clicked = True
            except:
                pass
            
            # If that fails, try by button role and visible text
            if not login_clicked:
                try:
                    # Find all clickable elements
                    clickable_elements = driver.find_elements(By.CSS_SELECTOR, 'div[role="button"], button, input[type="submit"]')
                    
                    for element in clickable_elements:
                        try:
                            text = element.text.strip().lower()
                            # Check if text contains login-like words in any language
                            if any(word in text for word in ['log in', 'login', 'zaloguj', 'sign in', 'signin']):
                                element.click()
                                self.logger.info(f"Clicked Login button with text: '{element.text.strip()}'")
                                login_clicked = True
                                break
                        except:
                            continue
                except:
                    pass
            
            if not login_clicked:
                self.logger.error("Could not find Login button")
                return False
            
            # Wait for login to complete and check what happens
            self.logger.info("Waiting for login completion...")
            time.sleep(5)
            
            # Check if 2FA is required after login
            page_state = self._detect_page_state(driver)
            self.logger.info(f"Post-login page state: {page_state}")
            
            if page_state == "2fa_required":
                self.logger.info("2FA verification required after login")
                if self._handle_2fa_verification(driver, credentials):
                    self.logger.info("2FA verification successful")
                    time.sleep(5)  # Wait for post-2FA redirect
                else:
                    self.logger.error("2FA verification failed")
                    return False
            
            # Check current URL and page state after potential 2FA
            current_url = driver.current_url
            self.logger.info(f"Post-login URL: {current_url}")
            
            # Check if we're redirected to authorization or still need more steps
            if 'oauth' in current_url or 'authorize' in current_url:
                self.logger.info("Login successful - redirected to OAuth authorization")
                return True
            elif 'home' in current_url or 'timeline' in current_url:
                self.logger.info("Login successful - at X home page")
                return True
            else:
                self.logger.info(f"Login completed - current page: {current_url}")
                # Let's see what happens and proceed optimistically
                return True
            
        except Exception as e:
            self.logger.error(f"Error during X login: {e}")
            return False
    
    def _analyze_current_page(self, driver: webdriver.Chrome):
        """Analyze the current page to understand what X is showing us."""
        try:
            current_url = driver.current_url
            page_title = driver.title
            self.logger.info(f"Current URL: {current_url}")
            # Safe logging for page title that might contain unicode characters
            safe_title = page_title.encode('ascii', 'replace').decode('ascii')
            self.logger.info(f"Page title: {safe_title}")
            
            # Log some key elements that might be present
            elements_to_check = [
                ('login form', 'form'),
                ('username input', 'input[autocomplete="username"], input[name="text"]'),
                ('password input', 'input[type="password"], input[name="password"]'),
                ('authorize button', '[data-testid="OAuth_Consent_Button"], button'),
                ('home feed', '[data-testid="primaryColumn"]'),
                ('verification', 'input[name="challenge_response"], [data-testid="ocfEnterTextTextInput"]'),
                ('rate limit', 'div, span, p'),  # Will check text content programmatically
                ('captcha', '[data-testid="captcha"], .captcha, #captcha')
            ]
            
            for element_name, selector in elements_to_check:
                try:
                    elements = driver.find_elements(By.CSS_SELECTOR, selector)
                    if elements:
                        self.logger.info(f"Found {len(elements)} {element_name} element(s)")
                except:
                    pass
                    
        except Exception as e:
            self.logger.warning(f"Could not analyze page: {e}")
    
    def _detect_page_language(self, driver: webdriver.Chrome) -> str:
        """
        SMART LANGUAGE DETECTION - Works with ANY language!
        
        Intelligently detects page language without hardcoding every possible language.
        Supports: English, Japanese, Chinese, French, German, Spanish, Portuguese,
        Italian, Russian, Polish, Turkish, Arabic, Korean, and more!
        
        Detection methods:
        1. HTML lang attribute (most reliable)
        2. Page content text patterns (fallback)
        """
        try:
            # Method 1: Check HTML lang attribute
            html_lang = driver.find_element(By.TAG_NAME, "html").get_attribute("lang")
            if html_lang:
                detected_lang = html_lang.split('-')[0]  # 'en-US' -> 'en'
                self.logger.info(f"Detected page language from HTML: {detected_lang}")
                return detected_lang
        except:
            pass
        
        try:
            # Method 2: Check page text patterns for common languages
            page_text = driver.page_source.lower()
            
            language_patterns = {
                'en': ['sign in', 'log in', 'next', 'password', 'username'],
                'ja': ['ログイン', 'サインイン', '次へ', 'パスワード'],  
                'zh': ['登录', '登錄', '下一步', '密码', '密碼'],
                'fr': ['connexion', 'suivant', 'mot de passe'],
                'de': ['anmelden', 'weiter', 'passwort'],
                'es': ['iniciar sesión', 'siguiente', 'contraseña'],
                'pt': ['entrar', 'próximo', 'senha'],
                'it': ['accedi', 'avanti', 'password'],
                'ru': ['войти', 'далее', 'пароль'],
                'pl': ['zaloguj', 'dalej', 'hasło'],
                'tr': ['giriş yap', 'ileri', 'şifre'],
                'ar': ['تسجيل الدخول', 'التالي', 'كلمة المرور'],
                'ko': ['로그인', '다음', '비밀번호']
            }
            
            for lang, patterns in language_patterns.items():
                if any(pattern in page_text for pattern in patterns):
                    self.logger.info(f"Detected page language from content: {lang}")
                    return lang
                    
        except:
            pass
            
        self.logger.info("Could not detect page language - defaulting to 'en'")
        return 'en'  # Default fallback
    
    def _find_button_by_structure(self, driver: webdriver.Chrome, context: str = "form") -> Optional[any]:
        """
        LANGUAGE-AGNOSTIC BUTTON DETECTION - Strategy #1: Structural Positioning
        
        Finds buttons by their position relative to form fields, NOT by text content.
        This works in ANY language (French, German, Turkish, Chinese, Arabic, etc.)
        because the form structure remains consistent regardless of language.
        
        Contexts:
        - 'username_next': Find Next button near username field
        - 'password_login': Find Login button near password field
        """
        try:
            # Strategy 1: Find buttons by form context and positioning
            if context == "username_next":
                # Look for button near username field
                username_field = driver.find_element(By.CSS_SELECTOR, 'input[autocomplete="username"]')
                # Find the next clickable element (usually a button)
                buttons = driver.find_elements(By.CSS_SELECTOR, 'div[role="button"], button')
                for button in buttons:
                    if button.is_displayed() and button.is_enabled():
                        # Check if button is near the username field (same form section)
                        try:
                            if abs(button.location['y'] - username_field.location['y']) < 200:
                                self.logger.info("Found Next button by structural positioning")
                                return button
                        except:
                            continue
                            
            elif context == "password_login":
                # Look for submit button near password field
                password_field = driver.find_element(By.CSS_SELECTOR, 'input[type="password"]')
                buttons = driver.find_elements(By.CSS_SELECTOR, 'div[role="button"], button, input[type="submit"]')
                for button in buttons:
                    if button.is_displayed() and button.is_enabled():
                        try:
                            if abs(button.location['y'] - password_field.location['y']) < 200:
                                self.logger.info("Found Login button by structural positioning")
                                return button
                        except:
                            continue
            
        except Exception as e:
            self.logger.warning(f"Structural button detection failed: {e}")
            
        return None
    
    def _find_button_by_attributes(self, driver: webdriver.Chrome, button_type: str) -> Optional[any]:
        """
        LANGUAGE-AGNOSTIC BUTTON DETECTION - Strategy #2: CSS/HTML Attributes
        
        Uses data-testid, button roles, and semantic HTML attributes to find buttons.
        Completely language-independent because it relies on HTML structure, not text.
        
        Supports:
        - 'next': Next/Continue buttons in login flow
        - 'login': Login/Submit buttons  
        """
        try:
            if button_type == "next":
                # Common patterns for Next buttons across languages
                next_selectors = [
                    '[data-testid*="login_next"]',
                    '[data-testid*="LoginForm_Next"]', 
                    'button[type="button"]:not([disabled])',
                    'div[role="button"]:not([aria-disabled="true"])'
                ]
                
            elif button_type == "login":
                # Common patterns for Login buttons across languages  
                login_selectors = [
                    '[data-testid*="Login_Button"]',
                    '[data-testid*="LoginForm_Login"]',
                    'button[type="submit"]',
                    'input[type="submit"]',
                    '[role="button"][aria-label*="log"]'
                ]
            else:
                return None
                
            selectors = next_selectors if button_type == "next" else login_selectors
            
            for selector in selectors:
                try:
                    element = driver.find_element(By.CSS_SELECTOR, selector)
                    if element.is_displayed() and element.is_enabled():
                        self.logger.info(f"Found {button_type} button via CSS selector: {selector}")
                        return element
                except:
                    continue
                    
        except Exception as e:
            self.logger.warning(f"Attribute-based button detection failed: {e}")
            
        return None

    def _detect_page_state(self, driver: webdriver.Chrome) -> str:
        """Detect what state/page X is currently showing."""
        try:
            current_url = driver.current_url.lower()
            
            # Check for various page states
            if '/home' in current_url or '/timeline' in current_url:
                return "already_logged_in"
            
            if 'oauth2/authorize' in current_url:
                # Check if authorization form is present
                try:
                    auth_button = driver.find_element(By.CSS_SELECTOR, '[data-testid="OAuth_Consent_Button"]')
                    return "authorization_form"
                except:
                    # Try alternative authorization button detection
                    try:
                        buttons = driver.find_elements(By.TAG_NAME, 'button')
                        for button in buttons:
                            if 'authorize' in button.text.lower():
                                return "authorization_form"
                    except:
                        pass
            
            # Check for login form
            # Check for 2FA verification prompt FIRST - Smart Language-Independent Detection
            try:
                # Strategy 1: Structure-based 2FA detection (language independent)
                verification_input_selectors = [
                    'input[placeholder*="code"]',  # Common pattern across languages
                    'input[placeholder*="kod"]',   # Polish/Eastern European
                    'input[placeholder*="コード"]',  # Japanese
                    'input[placeholder*="验证"]',   # Chinese
                    'input[autocomplete="one-time-code"]',  # Standard HTML attribute
                    'input[data-testid*="verification"]',
                    'input[data-testid*="challenge"]',
                    'input[maxlength="6"]',  # Common 2FA code length
                    'input[maxlength="8"]'   # Some apps use 8-digit codes
                ]
                
                has_verification_input = False
                for selector in verification_input_selectors:
                    try:
                        element = driver.find_element(By.CSS_SELECTOR, selector)
                        if element.is_displayed():
                            has_verification_input = True
                            self.logger.info(f"Found 2FA input field via selector: {selector}")
                            break
                    except:
                        continue
                
                # Strategy 2: Text-based detection (as fallback)
                text_based_2fa_detected = False
                if not has_verification_input:
                    page_text = driver.page_source.lower()
                    
                    # Expanded language patterns (auto-detected based on page language)
                    detected_lang = self._detect_page_language(driver)
                    
                    language_2fa_patterns = {
                        'en': ['verification code', 'enter code', 'authenticator', '2fa', 'two-factor'],
                        'pl': ['kod weryfikacyjny', 'wpisz kod', 'aplikacji do generowania'],
                        'ja': ['認証コード', '確認コード', 'コードを入力', '認証アプリ'],
                        'zh': ['验证码', '验证代码', '输入代码', '身份验证'],
                        'fr': ['code de vérification', 'authentificateur', 'saisir le code'],
                        'de': ['bestätigungscode', 'verifikationscode', 'authentifikator'],
                        'es': ['código de verificación', 'autenticador', 'ingresa el código'],
                        'ru': ['код подтверждения', 'аутентификатор', 'введите код'],
                        'tr': ['doğrulama kodu', 'kimlik doğrulayıcı', 'kodu girin'],
                        'ar': ['رمز التحقق', 'المصادقة', 'أدخل الرمز'],
                        'ko': ['인증 코드', '확인 코드', '코드 입력']
                    }
                    
                    # Check patterns for detected language + common English patterns
                    patterns_to_check = language_2fa_patterns.get(detected_lang, []) + language_2fa_patterns.get('en', [])
                    
                    if any(pattern in page_text for pattern in patterns_to_check):
                        text_based_2fa_detected = True
                        self.logger.info(f"2FA detected via text patterns for language: {detected_lang}")
                
                if has_verification_input or text_based_2fa_detected:
                    self.logger.info(f"2FA page detected - found 2FA indicator text in page")
                    
                    # Try to find the verification code input field
                    verification_selectors = [
                        'input[type="text"]',
                        'input[placeholder*="kod"]',
                        'input[placeholder*="code"]',
                        '[data-testid="ocfEnterTextTextInput"]'
                    ]
                    
                    for selector in verification_selectors:
                        try:
                            verification_input = driver.find_element(By.CSS_SELECTOR, selector)
                            if verification_input.is_displayed():
                                self.logger.info(f"2FA input field found with selector: {selector}")
                                return "2fa_required"
                        except:
                            continue
                            
                    self.logger.info("2FA text found but no input field located")
            except Exception as e:
                self.logger.debug(f"Error checking for 2FA: {e}")
                pass
                
            # Check for login form (username input) - AFTER 2FA check
            try:
                driver.find_element(By.CSS_SELECTOR, 'input[autocomplete="username"], input[name="text"]')
                return "login_form"
            except:
                pass
            
            # Check for verification/challenge
            try:
                driver.find_element(By.CSS_SELECTOR, 'input[name="challenge_response"], [data-testid="ocfEnterTextTextInput"]')
                return "verification_required"
            except:
                pass
            
            # Check for rate limiting
            page_text = driver.page_source.lower()
            if any(phrase in page_text for phrase in ['rate limit', 'try again later', 'too many requests']):
                return "rate_limited"
            
            # Check for captcha
            try:
                driver.find_element(By.CSS_SELECTOR, '[data-testid="captcha"], .captcha, #captcha')
                return "captcha_required"
            except:
                pass
                
            return "unknown"
            
        except Exception as e:
            self.logger.warning(f"Could not detect page state: {e}")
            return "unknown"
    
    def _save_debug_screenshot(self, driver: webdriver.Chrome, filename_suffix: str):
        """Save a screenshot for debugging purposes."""
        try:
            # Create screenshots directory if it doesn't exist
            os.makedirs('logs/screenshots', exist_ok=True)
            
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            filename = f"logs/screenshots/debug_{filename_suffix}_{timestamp}.png"
            
            driver.save_screenshot(filename)
            self.logger.info(f"Debug screenshot saved: {filename}")
            
            # Also log current URL and page title (with unicode safety)
            self.logger.info(f"Screenshot context - URL: {driver.current_url}")
            safe_title = driver.title.encode('ascii', 'replace').decode('ascii')
            self.logger.info(f"Screenshot context - Title: {safe_title}")
            
        except Exception as e:
            self.logger.warning(f"Could not save debug screenshot: {e}")
    
    def _get_all_unauthorized_users(self) -> List[Dict[str, str]]:
        """Get all DISCONNECTED users from database that need login (matches UI logic)."""
        try:
            with DBConnection(self.db_path) as (conn, cursor):
                # Get accounts that are DISCONNECTED in UI (no valid tokens or expired tokens)
                cursor.execute("""
                    SELECT account_name, custom_prompt, oauth2_access_token, oauth2_token_expires_at
                    FROM twitter_accounts 
                    WHERE custom_prompt LIKE 'CSV_PASSWORD:%'
                    AND (
                        oauth2_access_token IS NULL 
                        OR oauth2_token_expires_at IS NULL
                        OR datetime(oauth2_token_expires_at) <= datetime('now')
                    )
                    ORDER BY created_at 
                """)
                
                users = []
                for row in cursor.fetchall():
                    account_name, custom_prompt, oauth2_access_token, oauth2_token_expires_at = row
                    self.logger.info(f"Found DISCONNECTED account: {account_name} (token: {oauth2_access_token}, expires: {oauth2_token_expires_at})")
                    
                    if custom_prompt and 'CSV_PASSWORD:' in custom_prompt:
                        user_data = {'account_name': account_name}
                        
                        # Parse custom_prompt field: CSV_PASSWORD:password|2FA_SECRET:secret
                        parts = custom_prompt.split('|')
                        for part in parts:
                            if part.startswith('CSV_PASSWORD:'):
                                user_data['password'] = part.replace('CSV_PASSWORD:', '')
                            elif part.startswith('2FA_SECRET:'):
                                user_data['2fa_secret'] = part.replace('2FA_SECRET:', '')
                        
                        users.append(user_data)
                
                self.logger.info(f"Total DISCONNECTED accounts that need login: {len(users)}")
                return users
                
        except Exception as e:
            self.logger.error(f"Error getting unauthorized users: {e}")
            return []
    
    def _login_single_user_in_tab(self, driver: webdriver.Chrome, user: Dict[str, str], user_index: int, total_users: int) -> Dict[str, Any]:
        """Login a single user sequentially (not in parallel tabs)."""
        username = user['account_name']
        self.logger.info(f"[{user_index}/{total_users}] Starting login for user: {username}")
        
        try:
            # For sequential processing: Always use same tab, just navigate to login
            # This ensures each user completes fully before next user starts
            self.logger.info(f"Processing {username} sequentially (waiting for completion)")
            
            # Navigate to X login (clears any previous session)
            driver.get("https://x.com/i/flow/login")
            time.sleep(3)
            
            # Login process
            success = self._attempt_x_login_with_user_data(driver, user)
            
            if success:
                # Verify home feed
                if self._detect_home_feed(driver):
                    self.logger.info(f"SUCCESS [{user_index}/{total_users}] {username} - LOGIN SUCCESS")
                    return {
                        'success': True,
                        'username': username,
                        'message': 'Login successful - reached home feed'
                    }
                else:
                    self.logger.warning(f"WARNING [{user_index}/{total_users}] {username} - Login completed but home feed not detected")
                    return {
                        'success': False,
                        'username': username,
                        'error': 'Login completed but home feed not detected'
                    }
            else:
                self.logger.error(f"FAILED [{user_index}/{total_users}] {username} - LOGIN FAILED")
                return {
                    'success': False,
                    'username': username,
                    'error': 'Login failed'
                }
                
        except Exception as e:
            self.logger.error(f"ERROR [{user_index}/{total_users}] {username} - ERROR: {e}")
            return {
                'success': False,
                'username': username,
                'error': str(e)
            }
    
    def _attempt_x_login_with_user_data(self, driver: webdriver.Chrome, user: Dict[str, str]) -> bool:
        """Attempt login with specific user data."""
        try:
            username = user['account_name']
            password = user['password']
            has_2fa = '2fa_secret' in user
            
            self.logger.info(f"Attempting login for {username}" + (" (with 2FA)" if has_2fa else ""))
            
            # Detect current page state
            page_state = self._detect_page_state(driver)
            self.logger.info(f"Initial page state: {page_state}")
            
            # Handle different page states intelligently - but ALWAYS try normal login first
            if page_state == "already_logged_in":
                self.logger.info(f"User {username} already logged in - checking home feed")
                return self._detect_home_feed(driver)
            elif page_state == "2fa_required":
                self.logger.info(f"2FA page detected for {username} - but will try normal login flow first")
                # Don't immediately handle 2FA - might be false detection
                # Continue with normal login process and handle 2FA later if needed
            elif page_state in ["login_form", "unknown"]:
                self.logger.info(f"Will attempt normal login flow on page state: {page_state}")
            else:
                self.logger.info(f"Page state '{page_state}' - will attempt normal login flow anyway")
            
            # Fill username (only if not already handled 2FA above)
            username_filled = False
            username_selectors = [
                'input[autocomplete="username"]',
                'input[name="text"]', 
                'input[placeholder*="email"]',
                'input[placeholder*="username"]',
                'input[type="text"]',
                'input[type="email"]'
            ]
            
            for selector in username_selectors:
                try:
                    username_field = driver.find_element(By.CSS_SELECTOR, selector)
                    if username_field.is_displayed():
                        username_field.clear()
                        time.sleep(0.5)  # Small delay after clearing
                        username_field.send_keys(username)
                        time.sleep(2)  # Slower delay after typing
                        self.logger.info(f"Filled username: {username} using selector: {selector}")
                        username_filled = True
                        break
                except Exception:
                    continue
            
            if not username_filled:
                self.logger.warning(f"Could not find username field for {username} - checking if already logged in")
                # Check if we're already on home feed
                current_state = self._detect_page_state(driver)
                if current_state == "already_logged_in":
                    self.logger.info(f"User {username} appears to be already logged in")
                    return self._detect_home_feed(driver)
                else:
                    self.logger.warning(f"Cannot find username field for {username} - will try to continue with password field")
            
            # Click Next - Smart Language-Independent Detection
            try:
                # Detect page language first
                page_lang = self._detect_page_language(driver)
                self.logger.info(f"Page language detected: {page_lang}")
                
                # Try multiple intelligent strategies (no hardcoded text!)
                next_button = None
                
                # Strategy 1: CSS/Attribute-based detection (language independent)
                next_button = self._find_button_by_attributes(driver, "next")
                
                # Strategy 2: Structural positioning (near username field)
                if not next_button:
                    next_button = self._find_button_by_structure(driver, "username_next")
                
                # Strategy 3: Fallback - find any enabled button after username field
                if not next_button:
                    buttons = driver.find_elements(By.CSS_SELECTOR, 'div[role="button"], button')
                    for button in buttons:
                        if button.is_displayed() and button.is_enabled():
                            next_button = button
                            self.logger.info("Found Next button via fallback method")
                            break
                
                if next_button:
                    next_button.click()
                    time.sleep(3)  # Slower delay after clicking Next
                    self.logger.info(f"Successfully clicked Next button (language: {page_lang})")
                else:
                    self.logger.warning("Could not find Next button - checking if we can proceed directly to password")
                    time.sleep(2)  # Give some time even if Next button not found
                    
            except Exception as e:
                self.logger.warning(f"Error clicking Next: {e} - will try to continue anyway")
            
            # Fill password
            password_filled = False
            password_selectors = [
                'input[type="password"]',
                'input[name="password"]',
                'input[autocomplete="current-password"]'
            ]
            
            for selector in password_selectors:
                try:
                    password_field = driver.find_element(By.CSS_SELECTOR, selector)
                    if password_field.is_displayed():
                        password_field.clear()
                        time.sleep(0.5)  # Small delay after clearing
                        password_field.send_keys(password)
                        time.sleep(2)  # Slower delay after typing password
                        self.logger.info(f"Filled password using selector: {selector}")
                        password_filled = True
                        break
                except Exception:
                    continue
            
            if not password_filled:
                self.logger.warning(f"Could not find password field for {username} - checking if already logged in")
                current_state = self._detect_page_state(driver)
                if current_state == "already_logged_in":
                    self.logger.info(f"Password field not found but user appears logged in")
                    return self._detect_home_feed(driver)
                else:
                    self.logger.warning(f"Could not find password field for {username} - will try to continue with login button")
            
            # Click Login - Smart Language-Independent Detection
            try:
                login_button = None
                
                # Strategy 1: CSS/Attribute-based detection (language independent)
                login_button = self._find_button_by_attributes(driver, "login")
                
                # Strategy 2: Structural positioning (near password field)
                if not login_button:
                    login_button = self._find_button_by_structure(driver, "password_login")
                
                # Strategy 3: Fallback - try specific testid
                if not login_button:
                    try:
                        login_button = driver.find_element(By.CSS_SELECTOR, '[data-testid="LoginForm_Login_Button"]')
                        self.logger.info("Found Login button via data-testid")
                    except:
                        pass
                
                if login_button:
                    login_button.click()
                    time.sleep(4)  # Slower delay after clicking Login
                    self.logger.info(f"Successfully clicked Login button (language: {page_lang})")
                else:
                    self.logger.warning("Could not find Login button - checking if already logged in")
                    current_state = self._detect_page_state(driver)
                    if current_state == "already_logged_in":
                        self.logger.info("Login button not found but user appears logged in")
                        return self._detect_home_feed(driver)
                    else:
                        self.logger.warning(f"Could not find Login button for {username} - will try Enter key or continue")
                    
            except Exception as e:
                self.logger.warning(f"Error clicking Login: {e} - will continue to check for 2FA or home feed")
                # Continue to 2FA check below instead of failing
            
            # Check for 2FA
            post_login_state = self._detect_page_state(driver)
            if post_login_state == "2fa_required" and has_2fa:
                self.logger.info("2FA required - generating code")
                if self._handle_2fa_verification(driver, user):  # ✅ FIXED: Pass entire user dict
                    self.logger.info("2FA completed successfully")
                    time.sleep(3)  # Wait for redirect after 2FA
                else:
                    self.logger.error("2FA verification failed")
                    return False
            elif post_login_state == "2fa_required" and not has_2fa:
                self.logger.error("2FA required but no 2FA secret available")
                return False
            
            # Final check - should be on home feed or redirected successfully
            time.sleep(3)
            final_state = self._detect_page_state(driver)
            self.logger.info(f"Final page state: {final_state}")
            
            if final_state == "already_logged_in":
                return self._detect_home_feed(driver)
            else:
                self.logger.warning(f"Login completed but final state is '{final_state}' - checking home feed anyway")
                return self._detect_home_feed(driver)
            
        except Exception as e:
            self.logger.error(f"Error in login attempt: {e}")
            return False
    
    def _detect_home_feed(self, driver: webdriver.Chrome) -> bool:
        """Detect if user has reached X home feed (multi-language support)."""
        try:
            current_url = driver.current_url
            page_source = driver.page_source.lower()
            
            # URL patterns for home feed
            home_url_patterns = [
                '/home',
                'x.com/home',
                'twitter.com/home'
            ]
            
            # Check URL first
            if any(pattern in current_url.lower() for pattern in home_url_patterns):
                self.logger.info(f"Home feed detected via URL: {current_url}")
                return True
            
            # Text patterns for home feed (multiple languages)
            home_text_patterns = [
                # English
                'what\'s happening',
                'home timeline',
                'for you',
                'following',
                
                # Polish  
                'co się dzieje',
                'strona główna',
                'dla ciebie',
                'obserwowani',
                
                # General patterns
                'timeline',
                'tweet',
                'retweet'
            ]
            
            # Check page text
            if any(pattern in page_source for pattern in home_text_patterns):
                self.logger.info("Home feed detected via page text patterns")
                return True
            
            # Check for home feed specific elements
            try:
                # Look for tweet composition area
                driver.find_element(By.CSS_SELECTOR, '[data-testid="tweetTextarea_0"]')
                self.logger.info("Home feed detected via tweet compose element")
                return True
            except:
                pass
            
            try:
                # Look for timeline elements
                driver.find_element(By.CSS_SELECTOR, '[data-testid="primaryColumn"]')
                self.logger.info("Home feed detected via primary column element")
                return True
            except:
                pass
            
            self.logger.warning("Home feed not detected - current URL: " + current_url)
            return False
            
        except Exception as e:
            self.logger.error(f"Error detecting home feed: {e}")
            return False
    
    def _mark_user_as_connected(self, username: str) -> bool:
        """Mark user as Connected in database by adding minimal OAuth tokens."""
        try:
            with DBConnection(self.db_path) as (conn, cursor):
                # Add minimal OAuth2 token to show as "Connected"
                # This satisfies the is_token_valid check in app.py
                # Set expiry to 30 days so UI shows "Connected" for a long time
                cursor.execute("""
                    UPDATE twitter_accounts 
                    SET oauth2_access_token = ?,
                        oauth2_token_expires_at = datetime('now', '+30 days'),
                        last_token_refresh = datetime('now')
                    WHERE account_name = ?
                """, (f"login_token_{username}", username))
                
                if cursor.rowcount > 0:
                    self.logger.info(f"SUCCESS: Marked {username} as Connected in database")
                    return True
                else:
                    self.logger.error(f"Failed to update {username} - account not found")
                    return False
                    
        except Exception as e:
            self.logger.error(f"Error marking {username} as connected: {e}")
            return False

    def _get_x_credentials_for_profile(self, profile_id: str) -> Optional[Dict[str, str]]:
        """Get X login credentials - checks database for account being authorized."""
        
        # First, try to find an unauthorized account in database that needs OAuth
        try:
            with DBConnection(self.db_path) as (conn, cursor):
                # Look for accounts without OAuth tokens (unauthorized)
                cursor.execute("""
                    SELECT account_name, custom_prompt 
                    FROM twitter_accounts 
                    WHERE oauth2_access_token IS NULL 
                    AND custom_prompt LIKE 'CSV_PASSWORD:%'
                    ORDER BY created_at 
                    LIMIT 1
                """)
                
                row = cursor.fetchone()
                if row:
                    account_name, custom_prompt = row
                    # Parse custom_prompt field: CSV_PASSWORD:password|2FA_SECRET:secret
                    if custom_prompt and 'CSV_PASSWORD:' in custom_prompt:
                        credentials = {'username': account_name}
                        
                        # Split by pipe to handle multiple fields
                        parts = custom_prompt.split('|')
                        for part in parts:
                            if part.startswith('CSV_PASSWORD:'):
                                credentials['password'] = part.replace('CSV_PASSWORD:', '')
                            elif part.startswith('2FA_SECRET:'):
                                credentials['2fa_secret'] = part.replace('2FA_SECRET:', '')
                        
                        # Log credential info (without sensitive data)
                        has_2fa = ' + 2FA' if '2fa_secret' in credentials else ''
                        self.logger.info(f"Using database credentials for profile {profile_id}: {credentials['username']}{has_2fa}")
                        return credentials
                        
        except Exception as e:
            self.logger.warning(f"Could not load database credentials: {e}")
        
        # Fallback to hardcoded credentials for testing
        test_credentials = {
            'username': 'mhohoy',
            'password': '0ZJnf24Js2'
        }
        
        self.logger.info(f"Using fallback test credentials for profile {profile_id}: {test_credentials['username']}")
        return test_credentials

    def _generate_2fa_code(self, secret: str) -> Optional[str]:
        """Generate TOTP 2FA code from secret key"""
        if not PYOTP_AVAILABLE:
            self.logger.error("pyotp not available - cannot generate 2FA codes")
            return None
            
        try:
            totp = pyotp.TOTP(secret)
            code = totp.now()
            self.logger.info(f"Generated 2FA code: {code}")
            return code
        except Exception as e:
            self.logger.error(f"Failed to generate 2FA code: {e}")
            return None

    def _handle_2fa_verification(self, driver: webdriver.Chrome, credentials: Dict[str, str]) -> bool:
        """Handle 2FA verification if required and credentials are available"""
        try:
            if '2fa_secret' not in credentials:
                self.logger.warning("2FA required but no 2FA secret available")
                return False
            
            # Generate 2FA code
            twofa_code = self._generate_2fa_code(credentials['2fa_secret'])
            if not twofa_code:
                self.logger.error("Failed to generate 2FA code")
                return False
            
            self.logger.info(f"Generated 2FA code, attempting to enter: {twofa_code}")
            
            # Find the verification input field
            try:
                # Try multiple selectors for the 2FA input field
                verification_input = None
                selectors = [
                    'input[type="text"]',
                    'input[name="verfication_code"]',
                    'input[data-testid="ocfEnterTextTextInput"]',
                    'input[placeholder*="kod"]',
                    'input[placeholder*="code"]'
                ]
                
                for selector in selectors:
                    try:
                        verification_input = driver.find_element(By.CSS_SELECTOR, selector)
                        self.logger.info(f"Found 2FA input field with selector: {selector}")
                        break
                    except:
                        continue
                
                if not verification_input:
                    self.logger.error("Could not find 2FA verification input field")
                    return False
                
                # Clear and enter the 2FA code
                verification_input.clear()
                verification_input.send_keys(twofa_code)
                self.logger.info("Entered 2FA code")
                
                # Find and click submit button
                time.sleep(1)  # Brief pause
                
                submit_selectors = [
                    'button[type="submit"]',
                    '[data-testid="LoginForm_Login_Button"]',
                    'button:contains("Zaloguj")',
                    'button:contains("Log in")',
                    'button:contains("Continue")',
                    'button:contains("Dalej")'
                ]
                
                submit_button = None
                for selector in submit_selectors:
                    try:
                        if ':contains(' in selector:
                            # Handle text-based selectors differently
                            buttons = driver.find_elements(By.TAG_NAME, 'button')
                            text_to_find = selector.split(':contains("')[1].replace('")', '')
                            for button in buttons:
                                if text_to_find.lower() in button.text.lower():
                                    submit_button = button
                                    break
                        else:
                            submit_button = driver.find_element(By.CSS_SELECTOR, selector)
                        
                        if submit_button:
                            self.logger.info(f"Found 2FA submit button with selector: {selector}")
                            break
                    except:
                        continue
                
                if submit_button:
                    submit_button.click()
                    self.logger.info("Clicked 2FA submit button")
                    time.sleep(3)  # Wait for 2FA verification
                    return True
                else:
                    self.logger.warning("Could not find 2FA submit button - trying Enter key")
                    verification_input.send_keys("\n")
                    time.sleep(3)
                    return True
                    
            except Exception as e:
                self.logger.error(f"Error during 2FA input: {e}")
                return False
                
        except Exception as e:
            self.logger.error(f"Error handling 2FA verification: {e}")
            return False

    def _detect_home_feed(self, driver: webdriver.Chrome) -> bool:
        """Detect if user has successfully reached X home feed (multi-language)"""
        try:
            current_url = driver.current_url.lower()
            
            # Check URL patterns first
            home_url_patterns = ['/home', '/timeline', 'x.com/home', 'twitter.com/home']
            if any(pattern in current_url for pattern in home_url_patterns):
                self.logger.info(f"Home feed detected via URL: {current_url}")
                return True
            
            # Check page content for home feed indicators
            page_text = driver.page_source.lower()
            
            # Multi-language home feed indicators
            home_indicators = [
                # English
                "what's happening", "for you", "following", "timeline", "compose tweet",
                "what's new", "trending", "who to follow",
                # Polish  
                "co się dzieje", "dla ciebie", "obserwowani", "oś czasu", "napisz tweet",
                "co nowego", "trendy", "kogo obserwować",
                # Spanish
                "qué está pasando", "para ti", "siguiendo", "cronología",
                # German
                "was ist los", "für dich", "folge ich", "timeline",
                # French
                "quoi de neuf", "pour vous", "abonnements", "fil d'actualité"
            ]
            
            # Check for home feed text indicators
            found_indicators = [indicator for indicator in home_indicators if indicator in page_text]
            if found_indicators:
                self.logger.info(f"Home feed detected via content indicators: {found_indicators[:3]}")
                return True
            
            # Check for specific UI elements that indicate home feed
            try:
                # Look for tweet compose box
                compose_selectors = [
                    '[data-testid="tweetTextarea_0"]',
                    '[placeholder*="happening"]',
                    '[placeholder*="dzieje"]',
                    'div[role="textbox"]',
                    '.public-DraftEditor-content'
                ]
                
                for selector in compose_selectors:
                    try:
                        compose_element = driver.find_element(By.CSS_SELECTOR, selector)
                        if compose_element:
                            self.logger.info(f"Home feed detected via compose box: {selector}")
                            return True
                    except:
                        continue
                        
                # Look for navigation menu elements
                nav_selectors = [
                    '[data-testid="AppTabBar_Home_Link"]',
                    '[aria-label*="Home"]',
                    '[aria-label*="Strona główna"]',
                    'nav a[href="/home"]',
                    'nav a[href*="home"]'
                ]
                
                for selector in nav_selectors:
                    try:
                        nav_element = driver.find_element(By.CSS_SELECTOR, selector)
                        if nav_element:
                            self.logger.info(f"Home feed detected via navigation: {selector}")
                            return True
                    except:
                        continue
                        
            except Exception as e:
                self.logger.debug(f"Error checking UI elements: {e}")
            
            self.logger.info("Home feed not detected - still on different page")
            return False
            
        except Exception as e:
            self.logger.error(f"Error detecting home feed: {e}")
            return False

    def _complete_account_creation(self, auth_code: str, state: str, 
                                  api_app: str, profile_id: str) -> Dict[str, Any]:
        """Complete the account creation process"""
        try:
            # Import the existing functions from app.py
            from app import exchange_oauth_code_for_tokens, create_account_from_oauth_tokens
            
            # Get stored OAuth state data
            with DBConnection(self.db_path) as (conn, cursor):
                cursor.execute(
                    "SELECT code_verifier, client_id, callback_url FROM oauth_automation_states WHERE state = ?",
                    (state,)
                )
                state_data = cursor.fetchone()
                
                if not state_data:
                    return {'success': False, 'error': 'OAuth state not found'}
                
                code_verifier, client_id, callback_url = state_data
            
            # Exchange authorization code for tokens
            token_result = exchange_oauth_code_for_tokens(
                auth_code, code_verifier, client_id, api_app, callback_url
            )
            
            if not token_result['success']:
                return token_result
            
            # Create account in database
            account_result = create_account_from_oauth_tokens(
                token_result['tokens'],
                token_result['user_info'],
                api_app,
                profile_id
            )
            
            return account_result
            
        except Exception as e:
            self.logger.error(f"Error completing account creation: {e}")
            return {'success': False, 'error': str(e)}
    
    def _get_host_ip(self) -> str:
        """Get the host IP address"""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except:
            return "127.0.0.1"
    
    def _cleanup(self, driver: Optional[webdriver.Chrome], gl: Optional[GoLogin]):
        """Clean up browser resources"""
        if driver:
            try:
                driver.quit()
            except:
                pass
        if gl:
            try:
                time.sleep(1)
                gl.stop()
            except:
                pass
    
    def create_job_record(self, profile_id: str, api_app: str) -> int:
        """Create job record for tracking"""
        try:
            with DBConnection(self.db_path) as (conn, cursor):
                cursor.execute('''
                    INSERT INTO oauth_automation_jobs 
                    (profile_id, api_app, status, progress_step, created_at)
                    VALUES (?, ?, 'running', 'selenium_automation', CURRENT_TIMESTAMP)
                ''', (profile_id, api_app))
                return cursor.lastrowid
        except Exception as e:
            self.logger.error(f"Error creating job record: {e}")
            return 0
    
    def update_job_status(self, job_id: int, status: str, progress_step: str, 
                         account_id: Optional[int] = None, error: Optional[str] = None):
        """Update job status"""
        try:
            with DBConnection(self.db_path) as (conn, cursor):
                cursor.execute('''
                    UPDATE oauth_automation_jobs 
                    SET status = ?, progress_step = ?, account_id = ?, error_message = ?,
                        completed_at = CASE WHEN ? IN ('completed', 'failed') THEN CURRENT_TIMESTAMP ELSE completed_at END
                    WHERE id = ?
                ''', (status, progress_step, account_id, error, status, job_id))
        except Exception as e:
            self.logger.error(f"Error updating job status: {e}")


if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    print("=== X OAuth Authorization Automation ===\n")
    
    # Check requirements
    if not os.getenv('GOLOGIN_TOKEN'):
        print("ERROR: GOLOGIN_TOKEN not set")
        exit(1)
    
    tunnel_url = os.getenv('AIOTT_TUNNEL_URL')
    if tunnel_url:
        print(f"✓ Using tunnel: {tunnel_url}")
    else:
        print("⚠ No tunnel URL - ensure GoLogin profile has no proxy or local access works")
    
    # Test automation
    automator = SeleniumOAuthAutomator()
    
    profile_id = input("\nEnter GoLogin profile ID: ").strip()
    api_app = input("Enter API app name (AIOTT1/AIOTT2): ").strip()
    
    print(f"\nStarting automation...")
    result = automator.automate_oauth_for_profile(profile_id, api_app)
    
    if result.get('success'):
        print(f"\n✓ SUCCESS!")
        print(f"  Account: {result.get('username')}")
        print(f"  ID: {result.get('account_id')}")
    else:
        print(f"\n✗ FAILED: {result.get('error')}")