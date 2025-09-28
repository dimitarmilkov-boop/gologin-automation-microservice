#!/usr/bin/env python3
import logging
import time
import random
import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import sqlite3

from gologin import GoLogin
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException

from fix_db_connections import DBConnection

# Import browser startup handler
try:
    from browser_startup_handler import startup_handler
except ImportError:
    startup_handler = None

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/gologin_warmup.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

class GoLoginManager:
    """Manages GoLogin browser profiles and X account warmup activities."""
    
    def __init__(self, db_path='twitter_accounts.db'):
        self.db_path = db_path
        self.logger = logging.getLogger(self.__class__.__name__)
        
        # GoLogin configuration
        self.gologin_token = os.getenv('GOLOGIN_TOKEN')
        if not self.gologin_token:
            raise ValueError("GOLOGIN_TOKEN environment variable is required")
        
        # Warmup configuration
        self.warmup_activities = {
            'browsing': {
                'min_duration': 120,  # 2 minutes
                'max_duration': 600,  # 10 minutes
                'pages_to_visit': ['home', 'explore', 'notifications', 'search']
            },
            'scrolling': {
                'min_scrolls': 5,
                'max_scrolls': 20,
                'scroll_delay_min': 1,
                'scroll_delay_max': 3
            },
            'interaction': {
                'min_actions': 2,
                'max_actions': 8,
                'actions': ['like', 'retweet', 'view_profile', 'read_tweet']
            }
        }
        
        # X URLs
        self.x_urls = {
            'login': 'https://x.com/i/flow/login',
            'home': 'https://x.com/home',
            'explore': 'https://x.com/explore',
            'notifications': 'https://x.com/notifications',
            'search': 'https://x.com/search'
        }
        
        # Initialize database tables
        self._init_database_tables()
    
    def _init_database_tables(self):
        """Initialize GoLogin-related database tables."""
        try:
            with DBConnection(self.db_path) as (conn, c):
                # Create gologin_profiles table
                c.execute('''
                    CREATE TABLE IF NOT EXISTS gologin_profiles (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        account_id INTEGER NOT NULL,
                        profile_id TEXT NOT NULL UNIQUE,
                        profile_name TEXT NOT NULL,
                        proxy_country TEXT,
                        user_agent TEXT,
                        is_active INTEGER DEFAULT 1,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        last_warmup_at TIMESTAMP,
                        warmup_count INTEGER DEFAULT 0,
                        notes TEXT,
                        FOREIGN KEY (account_id) REFERENCES twitter_accounts(id) ON DELETE CASCADE
                    )
                ''')
                
                # Create warmup_sessions table
                c.execute('''
                    CREATE TABLE IF NOT EXISTS warmup_sessions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        profile_id TEXT NOT NULL,
                        account_id INTEGER NOT NULL,
                        session_start TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        session_end TIMESTAMP,
                        duration_seconds INTEGER,
                        activities_performed TEXT,  -- JSON array of activities
                        pages_visited TEXT,         -- JSON array of pages
                        interactions_count INTEGER DEFAULT 0,
                        status TEXT DEFAULT 'active',  -- active, completed, failed
                        error_message TEXT,
                        notes TEXT,
                        FOREIGN KEY (profile_id) REFERENCES gologin_profiles(profile_id),
                        FOREIGN KEY (account_id) REFERENCES twitter_accounts(id)
                    )
                ''')
                
                # Create warmup_settings table
                c.execute('''
                    CREATE TABLE IF NOT EXISTS warmup_settings (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        account_id INTEGER NOT NULL UNIQUE,
                        is_enabled INTEGER DEFAULT 1,
                        sessions_per_day INTEGER DEFAULT 2,
                        min_session_duration INTEGER DEFAULT 300,  -- 5 minutes
                        max_session_duration INTEGER DEFAULT 1800, -- 30 minutes
                        working_hours_start INTEGER DEFAULT 9,
                        working_hours_end INTEGER DEFAULT 21,
                        activities_config TEXT,  -- JSON config for activities
                        last_session_at TIMESTAMP,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (account_id) REFERENCES twitter_accounts(id) ON DELETE CASCADE
                    )
                ''')
                
                # Create indexes
                c.execute('CREATE INDEX IF NOT EXISTS idx_gologin_profiles_account ON gologin_profiles(account_id)')
                c.execute('CREATE INDEX IF NOT EXISTS idx_warmup_sessions_profile ON warmup_sessions(profile_id)')
                c.execute('CREATE INDEX IF NOT EXISTS idx_warmup_sessions_date ON warmup_sessions(session_start)')
                
                self.logger.info("GoLogin database tables initialized successfully")
                
        except Exception as e:
            self.logger.error(f"Error initializing database tables: {e}")
            raise
    
    def create_profile(self, account_id: int, profile_name: str, os_type: str = 'win', proxy_country: str = 'us') -> Optional[str]:
        """Create a new GoLogin profile for an account."""
        try:
            gl = GoLogin({
                "token": self.gologin_token
            })
            
            # Create profile with custom parameters
            profile_data = gl.createProfileRandomFingerprint({
                "os": os_type,
                "name": profile_name
            })
            
            if not profile_data or 'id' not in profile_data:
                self.logger.error(f"Failed to create GoLogin profile for account {account_id}")
                return None
            
            profile_id = profile_data['id']
            
            # Add proxy if specified
            if proxy_country:
                try:
                    gl.addGologinProxyToProfile(profile_id, proxy_country)
                    self.logger.info(f"Added {proxy_country} proxy to profile {profile_id}")
                except Exception as e:
                    self.logger.warning(f"Failed to add proxy to profile {profile_id}: {e}")
            
            # Store profile in database
            with DBConnection(self.db_path) as (conn, c):
                c.execute('''
                    INSERT INTO gologin_profiles 
                    (account_id, profile_id, profile_name, proxy_country, user_agent)
                    VALUES (?, ?, ?, ?, ?)
                ''', (
                    account_id, 
                    profile_id, 
                    profile_name, 
                    proxy_country,
                    profile_data.get('navigator', {}).get('userAgent', '')
                ))
            
            self.logger.info(f"Created GoLogin profile {profile_id} for account {account_id}")
            return profile_id
            
        except Exception as e:
            self.logger.error(f"Error creating GoLogin profile: {e}")
            return None
    
    def get_account_profile(self, account_id: int) -> Optional[Dict[str, Any]]:
        """Get the GoLogin profile for an account."""
        try:
            with DBConnection(self.db_path) as (conn, c):
                c.execute('''
                    SELECT profile_id, profile_name, proxy_country, user_agent, 
                           last_warmup_at, warmup_count
                    FROM gologin_profiles 
                    WHERE account_id = ? AND is_active = 1
                ''', (account_id,))
                
                row = c.fetchone()
                if row:
                    return {
                        'profile_id': row[0],
                        'profile_name': row[1],
                        'proxy_country': row[2],
                        'user_agent': row[3],
                        'last_warmup_at': row[4],
                        'warmup_count': row[5]
                    }
                return None
                
        except Exception as e:
            self.logger.error(f"Error getting account profile: {e}")
            return None
    
    def start_browser_session(self, profile_id: str) -> tuple[Optional[webdriver.Chrome], Optional[GoLogin]]:
        """Start a browser session with the specified GoLogin profile."""
        try:
            # Initialize GoLogin
            gl = GoLogin({
                "token": self.gologin_token,
                "profile_id": profile_id
            })
            
            # Start browser and get websocket URL
            debugger_address = gl.start()
            
            if not debugger_address:
                self.logger.error(f"Failed to start GoLogin profile {profile_id}")
                return None, None
            
            # Get chromium version for webdriver
            chromium_version = gl.get_chromium_version()
            
            # Install webdriver
            service = Service(ChromeDriverManager(driver_version=chromium_version).install())
            
            # Configure Chrome options
            chrome_options = webdriver.ChromeOptions()
            chrome_options.add_experimental_option("debuggerAddress", debugger_address)
            
            # Start Selenium driver
            driver = webdriver.Chrome(service=service, options=chrome_options)
            
            # Schedule post-launch tasks (Cloudflare solving, etc.)
            if startup_handler:
                startup_handler.schedule_post_launch_tasks(profile_id, driver, delay_seconds=20)
                self.logger.info(f"Scheduled post-launch tasks for profile {profile_id}")
            
            self.logger.info(f"Started browser session for profile {profile_id}")
            return driver, gl
            
        except Exception as e:
            self.logger.error(f"Error starting browser session: {e}")
            return None, None
    
    def login_to_x(self, driver: webdriver.Chrome, account_credentials: Dict[str, str]) -> bool:
        """Login to X using the browser."""
        try:
            self.logger.info("Logging into X...")
            
            # Navigate to X login page
            driver.get(self.x_urls['login'])
            time.sleep(random.uniform(3, 6))
            
            # Wait for login form
            wait = WebDriverWait(driver, 20)
            
            # Find username field
            username_field = wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, 'input[autocomplete="username"]'))
            )
            
            # Type username with human-like delays
            username = account_credentials.get('username', '')
            self._type_human_like(username_field, username)
            time.sleep(random.uniform(1, 2))
            
            # Click next button
            next_button = driver.find_element(By.XPATH, '//span[text()="Next"]/ancestor::button')
            next_button.click()
            time.sleep(random.uniform(2, 4))
            
            # Handle password field
            password_field = wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, 'input[name="password"]'))
            )
            
            password = account_credentials.get('password', '')
            self._type_human_like(password_field, password)
            time.sleep(random.uniform(1, 2))
            
            # Click login button
            login_button = driver.find_element(By.XPATH, '//span[text()="Log in"]/ancestor::button')
            login_button.click()
            
            # Wait for successful login (check for home page or timeline)
            try:
                wait.until(
                    EC.any_of(
                        EC.url_contains('/home'),
                        EC.presence_of_element_located((By.CSS_SELECTOR, '[data-testid="primaryColumn"]'))
                    )
                )
                self.logger.info("Successfully logged into X")
                return True
                
            except TimeoutException:
                # Check for additional verification steps
                if "challenge" in driver.current_url or "verification" in driver.current_url:
                    self.logger.warning("Login requires additional verification - manual intervention needed")
                    return False
                raise
                
        except Exception as e:
            self.logger.error(f"Error logging into X: {e}")
            return False
    
    def _type_human_like(self, element, text: str):
        """Type text with human-like delays."""
        for char in text:
            element.send_keys(char)
            time.sleep(random.uniform(0.05, 0.15))
    
    def perform_warmup_activities(self, driver: webdriver.Chrome, duration_minutes: int = 10) -> Dict[str, Any]:
        """Perform warmup activities on X."""
        try:
            session_data = {
                'pages_visited': [],
                'activities_performed': [],
                'interactions_count': 0,
                'start_time': datetime.now(),
                'duration_minutes': duration_minutes
            }
            
            end_time = datetime.now() + timedelta(minutes=duration_minutes)
            
            self.logger.info(f"Starting warmup activities for {duration_minutes} minutes")
            
            while datetime.now() < end_time:
                activity = random.choice(['browse_timeline', 'explore_page', 'scroll_and_read', 'search_content'])
                
                try:
                    if activity == 'browse_timeline':
                        self._browse_timeline(driver, session_data)
                    elif activity == 'explore_page':
                        self._visit_explore_page(driver, session_data)
                    elif activity == 'scroll_and_read':
                        self._scroll_and_read(driver, session_data)
                    elif activity == 'search_content':
                        self._search_content(driver, session_data)
                        
                    session_data['activities_performed'].append({
                        'activity': activity,
                        'timestamp': datetime.now().isoformat(),
                        'duration': random.randint(30, 120)
                    })
                    
                    # Random break between activities
                    time.sleep(random.uniform(10, 30))
                    
                except Exception as e:
                    self.logger.warning(f"Error in activity {activity}: {e}")
                    continue
            
            session_data['end_time'] = datetime.now()
            session_data['actual_duration'] = (session_data['end_time'] - session_data['start_time']).total_seconds()
            
            self.logger.info(f"Completed warmup session - visited {len(session_data['pages_visited'])} pages, performed {len(session_data['activities_performed'])} activities")
            return session_data
            
        except Exception as e:
            self.logger.error(f"Error performing warmup activities: {e}")
            return session_data
    
    def _browse_timeline(self, driver: webdriver.Chrome, session_data: Dict[str, Any]):
        """Browse the home timeline."""
        try:
            if driver.current_url != self.x_urls['home']:
                driver.get(self.x_urls['home'])
                session_data['pages_visited'].append('home')
                time.sleep(random.uniform(3, 6))
            
            # Scroll through timeline
            scroll_count = random.randint(3, 8)
            for _ in range(scroll_count):
                driver.execute_script("window.scrollBy(0, window.innerHeight);")
                time.sleep(random.uniform(2, 4))
                
                # Occasionally interact with tweets
                if random.random() < 0.3:  # 30% chance
                    self._interact_with_tweets(driver, session_data)
            
        except Exception as e:
            self.logger.warning(f"Error browsing timeline: {e}")
    
    def _visit_explore_page(self, driver: webdriver.Chrome, session_data: Dict[str, Any]):
        """Visit the explore page."""
        try:
            driver.get(self.x_urls['explore'])
            session_data['pages_visited'].append('explore')
            time.sleep(random.uniform(3, 6))
            
            # Scroll through explore content
            scroll_count = random.randint(2, 5)
            for _ in range(scroll_count):
                driver.execute_script("window.scrollBy(0, window.innerHeight);")
                time.sleep(random.uniform(2, 4))
                
        except Exception as e:
            self.logger.warning(f"Error visiting explore page: {e}")
    
    def _scroll_and_read(self, driver: webdriver.Chrome, session_data: Dict[str, Any]):
        """Scroll and simulate reading tweets."""
        try:
            # Get all visible tweets
            tweets = driver.find_elements(By.CSS_SELECTOR, '[data-testid="tweet"]')
            
            if tweets:
                # Select a few random tweets to "read"
                tweets_to_read = random.sample(tweets, min(3, len(tweets)))
                
                for tweet in tweets_to_read:
                    try:
                        # Scroll tweet into view
                        driver.execute_script("arguments[0].scrollIntoView(true);", tweet)
                        time.sleep(random.uniform(2, 5))  # Simulate reading time
                        
                        session_data['interactions_count'] += 1
                        
                    except Exception as e:
                        self.logger.debug(f"Error reading tweet: {e}")
                        continue
                        
        except Exception as e:
            self.logger.warning(f"Error in scroll and read: {e}")
    
    def _search_content(self, driver: webdriver.Chrome, session_data: Dict[str, Any]):
        """Perform a search and browse results."""
        try:
            # List of search terms
            search_terms = [
                "technology", "news", "AI", "crypto", "sports", "music", 
                "movies", "science", "business", "trending"
            ]
            
            search_term = random.choice(search_terms)
            
            # Navigate to search
            search_url = f"https://x.com/search?q={search_term}&src=typed_query"
            driver.get(search_url)
            session_data['pages_visited'].append(f'search_{search_term}')
            time.sleep(random.uniform(3, 6))
            
            # Scroll through search results
            scroll_count = random.randint(2, 4)
            for _ in range(scroll_count):
                driver.execute_script("window.scrollBy(0, window.innerHeight);")
                time.sleep(random.uniform(2, 4))
                
        except Exception as e:
            self.logger.warning(f"Error searching content: {e}")
    
    def _interact_with_tweets(self, driver: webdriver.Chrome, session_data: Dict[str, Any]):
        """Perform subtle interactions with tweets."""
        try:
            # Find like buttons (but don't click them to avoid actual engagement)
            tweets = driver.find_elements(By.CSS_SELECTOR, '[data-testid="tweet"]')
            
            if tweets:
                # Just hover over some tweets to simulate interest
                for tweet in random.sample(tweets, min(2, len(tweets))):
                    try:
                        actions = ActionChains(driver)
                        actions.move_to_element(tweet).perform()
                        time.sleep(random.uniform(1, 2))
                        session_data['interactions_count'] += 1
                        
                    except Exception as e:
                        self.logger.debug(f"Error hovering over tweet: {e}")
                        continue
                        
        except Exception as e:
            self.logger.warning(f"Error interacting with tweets: {e}")
    
    def run_warmup_session(self, account_id: int) -> bool:
        """Run a complete warmup session for an account."""
        driver = None
        gl = None
        session_id = None
        
        try:
            self.logger.info(f"Starting warmup session for account {account_id}")
            
            # Get account profile
            profile = self.get_account_profile(account_id)
            if not profile:
                self.logger.error(f"No GoLogin profile found for account {account_id}")
                return False
            
            # Get account credentials
            account_credentials = self._get_account_credentials(account_id)
            if not account_credentials:
                self.logger.error(f"No credentials found for account {account_id}")
                return False
            
            # Start browser session
            driver, gl = self.start_browser_session(profile['profile_id'])
            if not driver or not gl:
                return False
            
            # Create session record
            session_id = self._create_session_record(profile['profile_id'], account_id)
            
            # Login to X
            if not self.login_to_x(driver, account_credentials):
                self._update_session_record(session_id, status='failed', error='Login failed')
                return False
            
            # Determine session duration
            duration = random.randint(5, 15)  # 5-15 minutes
            
            # Perform warmup activities
            session_data = self.perform_warmup_activities(driver, duration)
            
            # Update session record
            self._update_session_record(
                session_id,
                status='completed',
                duration=int(session_data['actual_duration']),
                activities=json.dumps(session_data['activities_performed']),
                pages=json.dumps(session_data['pages_visited']),
                interactions=session_data['interactions_count']
            )
            
            # Update profile warmup stats
            self._update_profile_warmup_stats(profile['profile_id'])
            
            self.logger.info(f"Warmup session completed successfully for account {account_id}")
            return True
            
        except Exception as e:
            self.logger.error(f"Error in warmup session: {e}")
            if session_id:
                self._update_session_record(session_id, status='failed', error=str(e))
            return False
            
        finally:
            # Cleanup
            if driver:
                try:
                    driver.quit()
                except:
                    pass
            
            if gl:
                try:
                    time.sleep(2)  # Wait a bit before stopping
                    gl.stop()
                except:
                    pass
    
    def _get_account_credentials(self, account_id: int) -> Optional[Dict[str, str]]:
        """Get account credentials for X login."""
        try:
            with DBConnection(self.db_path) as (conn, c):
                c.execute('''
                    SELECT account_name
                    FROM twitter_accounts
                    WHERE id = ?
                ''', (account_id,))
                
                row = c.fetchone()
                if row:
                    # Note: You'll need to implement proper credential storage
                    # This is a placeholder - implement secure credential storage
                    return {
                        'username': row[0],  # account_name as username
                        'password': 'PLACEHOLDER_PASSWORD'  # Implement secure password storage
                    }
                return None
                
        except Exception as e:
            self.logger.error(f"Error getting account credentials: {e}")
            return None
    
    def _create_session_record(self, profile_id: str, account_id: int) -> Optional[int]:
        """Create a new session record."""
        try:
            with DBConnection(self.db_path) as (conn, c):
                c.execute('''
                    INSERT INTO warmup_sessions (profile_id, account_id)
                    VALUES (?, ?)
                ''', (profile_id, account_id))
                
                return c.lastrowid
                
        except Exception as e:
            self.logger.error(f"Error creating session record: {e}")
            return None
    
    def _update_session_record(self, session_id: int, status: str = None, duration: int = None, 
                              activities: str = None, pages: str = None, interactions: int = None, 
                              error: str = None):
        """Update session record."""
        try:
            with DBConnection(self.db_path) as (conn, c):
                updates = []
                params = []
                
                if status:
                    updates.append("status = ?")
                    params.append(status)
                
                if duration:
                    updates.append("duration_seconds = ?")
                    params.append(duration)
                
                if activities:
                    updates.append("activities_performed = ?")
                    params.append(activities)
                
                if pages:
                    updates.append("pages_visited = ?")
                    params.append(pages)
                
                if interactions is not None:
                    updates.append("interactions_count = ?")
                    params.append(interactions)
                
                if error:
                    updates.append("error_message = ?")
                    params.append(error)
                
                if status in ['completed', 'failed']:
                    updates.append("session_end = CURRENT_TIMESTAMP")
                
                if updates:
                    query = f"UPDATE warmup_sessions SET {', '.join(updates)} WHERE id = ?"
                    params.append(session_id)
                    c.execute(query, params)
                
        except Exception as e:
            self.logger.error(f"Error updating session record: {e}")
    
    def _update_profile_warmup_stats(self, profile_id: str):
        """Update profile warmup statistics."""
        try:
            with DBConnection(self.db_path) as (conn, c):
                c.execute('''
                    UPDATE gologin_profiles 
                    SET last_warmup_at = CURRENT_TIMESTAMP,
                        warmup_count = warmup_count + 1
                    WHERE profile_id = ?
                ''', (profile_id,))
                
        except Exception as e:
            self.logger.error(f"Error updating profile stats: {e}")
    
    def get_warmup_eligible_accounts(self) -> List[Dict[str, Any]]:
        """Get accounts that are eligible for warmup."""
        try:
            with DBConnection(self.db_path) as (conn, c):
                # Get accounts with GoLogin profiles and warmup enabled
                c.execute('''
                    SELECT 
                        ta.id,
                        ta.account_name,
                        gp.profile_id,
                        gp.profile_name,
                        ws.is_enabled,
                        ws.sessions_per_day,
                        ws.working_hours_start,
                        ws.working_hours_end,
                        ws.last_session_at,
                        gp.last_warmup_at
                    FROM twitter_accounts ta
                    JOIN gologin_profiles gp ON ta.id = gp.account_id
                    LEFT JOIN warmup_settings ws ON ta.id = ws.account_id
                    WHERE gp.is_active = 1
                    AND (ws.is_enabled IS NULL OR ws.is_enabled = 1)
                    AND ta.oauth_type = 'oauth2'
                ''')
                
                accounts = []
                current_hour = datetime.now().hour
                
                for row in c.fetchall():
                    account_id, account_name, profile_id, profile_name, is_enabled, sessions_per_day, \
                    start_hour, end_hour, last_session_at, last_warmup_at = row
                    
                    # Check working hours
                    start_hour = start_hour or 9
                    end_hour = end_hour or 21
                    
                    if not (start_hour <= current_hour < end_hour):
                        continue
                    
                    # Check if account needs warmup today
                    sessions_per_day = sessions_per_day or 2
                    
                    # Count today's sessions
                    c.execute('''
                        SELECT COUNT(*) FROM warmup_sessions
                        WHERE account_id = ? 
                        AND date(session_start) = date('now')
                        AND status = 'completed'
                    ''', (account_id,))
                    
                    today_sessions = c.fetchone()[0]
                    
                    if today_sessions < sessions_per_day:
                        accounts.append({
                            'id': account_id,
                            'account_name': account_name,
                            'profile_id': profile_id,
                            'profile_name': profile_name,
                            'sessions_needed': sessions_per_day - today_sessions,
                            'last_session_at': last_session_at,
                            'last_warmup_at': last_warmup_at
                        })
                
                return accounts
                
        except Exception as e:
            self.logger.error(f"Error getting warmup eligible accounts: {e}")
            return []
    
    def run_scheduled_warmup(self):
        """Run scheduled warmup for eligible accounts."""
        try:
            self.logger.info("Starting scheduled warmup process")
            
            eligible_accounts = self.get_warmup_eligible_accounts()
            
            if not eligible_accounts:
                self.logger.info("No accounts eligible for warmup")
                return
            
            self.logger.info(f"Found {len(eligible_accounts)} accounts eligible for warmup")
            
            # Process accounts with random delays to avoid detection
            for account in eligible_accounts:
                try:
                    self.logger.info(f"Processing warmup for account {account['account_name']}")
                    
                    success = self.run_warmup_session(account['id'])
                    
                    if success:
                        self.logger.info(f"Warmup completed for account {account['account_name']}")
                    else:
                        self.logger.warning(f"Warmup failed for account {account['account_name']}")
                    
                    # Random delay between accounts (5-15 minutes)
                    delay = random.randint(300, 900)
                    self.logger.info(f"Waiting {delay/60:.1f} minutes before next account...")
                    time.sleep(delay)
                    
                except Exception as e:
                    self.logger.error(f"Error processing account {account['account_name']}: {e}")
                    continue
            
            self.logger.info("Scheduled warmup process completed")
            
        except Exception as e:
            self.logger.error(f"Error in scheduled warmup: {e}")

if __name__ == "__main__":
    # Test the GoLogin manager
    manager = GoLoginManager()
    manager.run_scheduled_warmup() 