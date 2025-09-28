#!/usr/bin/env python3
"""
Enhanced GoLogin Manager - Base Class

This module provides the enhanced base functionality for GoLogin profile management
including cloud sessions, API integration, and database operations.

ARCHITECTURE NOTE:
- This class serves as the base for GoLoginHybridManager
- For new implementations, use GoLoginHybridManager or GlobalGoLoginSessionManager
- This file provides core GoLogin API integration and cloud session management
- Database operations, profile synchronization, and basic session management

RECOMMENDED USAGE:
- Use GoLoginHybridManager for high-level operations
- Use GlobalGoLoginSessionManager for persistent sessions and automation
- Direct use of this class is for specific cloud operations only

See: GOLOGIN_ENHANCED_README.md for current implementation guide
"""

import logging
import time
import random
import json
import os
import threading
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import sqlite3
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

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

# Port manager for local sessions
class PortManager:
    def __init__(self, start_port: int = 3500, end_port: int = 3600):
        self.start_port = start_port
        self.end_port = end_port
        self.used_ports = set()
        self.lock = threading.Lock()
        
    def get_available_port(self) -> Optional[int]:
        """Get an available port from the range."""
        with self.lock:
            for port in range(self.start_port, self.end_port + 1):
                if port not in self.used_ports:
                    import socket
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    try:
                        sock.bind(('localhost', port))
                        sock.close()
                        self.used_ports.add(port)
                        return port
                    except OSError:
                        continue
            return None
    
    def release_port(self, port: int):
        """Release a port back to the available pool."""
        with self.lock:
            self.used_ports.discard(port)

class EnhancedGoLoginManager:
    """Enhanced GoLogin manager with cloud support, port management, and profile synchronization."""
    
    def __init__(self, db_path='twitter_accounts.db'):
        self.db_path = db_path
        self.logger = logging.getLogger(self.__class__.__name__)
        
        # GoLogin configuration
        self.gologin_token = os.getenv('GOLOGIN_TOKEN')
        if not self.gologin_token:
            self.logger.warning("GOLOGIN_TOKEN not set - API operations will be disabled")
            self.api_enabled = False
        else:
            self.api_enabled = True
        
        # Enhanced configuration options
        self.execution_mode = os.getenv('GOLOGIN_EXECUTION_MODE', 'cloud')  # cloud or local
        self.use_cloud = os.getenv('GOLOGIN_USE_CLOUD', 'true').lower() == 'true'
        self.is_headless = os.getenv('GOLOGIN_HEADLESS', 'true').lower() == 'true'
        self.cloud_timeout = int(os.getenv('GOLOGIN_CLOUD_TIMEOUT', '300'))
        self.max_concurrent = int(os.getenv('GOLOGIN_MAX_CONCURRENT_PROFILES', '5'))
        
        # Port management
        port_start = int(os.getenv('GOLOGIN_PORT_RANGE_START', '3500'))
        port_end = int(os.getenv('GOLOGIN_PORT_RANGE_END', '3600'))
        self.port_manager = PortManager(port_start, port_end)
        
        # Profile synchronization
        self.sync_profiles = os.getenv('GOLOGIN_SYNC_PROFILES', 'true').lower() == 'true'
        self.auto_update_profiles = os.getenv('GOLOGIN_AUTO_UPDATE_PROFILES', 'true').lower() == 'true'
        
        # Active sessions tracking
        self.active_sessions = {}
        self.sessions_lock = threading.Lock()
        
        # GoLogin API base URL (from official SDK)
        self.api_base = 'https://api.gologin.com'
        
        # Enhanced HTTP session with connection pooling and retry logic
        self.session = self._create_http_session()
        
        # Initialize database tables
        self._init_database_tables()
        
        # Sync profiles on startup if enabled and API is available
        if self.sync_profiles and self.api_enabled:
            self._sync_profiles_from_cloud()
    
    def _init_database_tables(self):
        """Initialize enhanced GoLogin-related database tables."""
        try:
            with DBConnection(self.db_path) as (conn, c):
                # Enhanced gologin_profiles table
                c.execute('''
                    CREATE TABLE IF NOT EXISTS gologin_profiles (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        account_id INTEGER,
                        profile_id TEXT NOT NULL UNIQUE,
                        profile_name TEXT NOT NULL,
                        os_type TEXT DEFAULT 'win',
                        proxy_country TEXT,
                        proxy_type TEXT,
                        user_agent TEXT,
                        screen_resolution TEXT,
                        timezone TEXT,
                        language TEXT,
                        execution_mode TEXT DEFAULT 'cloud',
                        assigned_port INTEGER,
                        is_active INTEGER DEFAULT 1,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        last_warmup_at TIMESTAMP,
                        last_sync_at TIMESTAMP,
                        warmup_count INTEGER DEFAULT 0,
                        cloud_profile_data TEXT,  -- JSON data from GoLogin API
                        notes TEXT,
                        FOREIGN KEY (account_id) REFERENCES twitter_accounts(id) ON DELETE SET NULL
                    )
                ''')
                
                # Profile sync log table
                c.execute('''
                    CREATE TABLE IF NOT EXISTS profile_sync_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        profile_id TEXT NOT NULL,
                        sync_type TEXT NOT NULL,  -- 'import', 'update', 'delete'
                        sync_status TEXT NOT NULL,  -- 'success', 'failed'
                        sync_data TEXT,  -- JSON data
                        error_message TEXT,
                        sync_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (profile_id) REFERENCES gologin_profiles(profile_id)
                    )
                ''')
                
                # Active cloud sessions table for persistence across requests
                c.execute('''
                    CREATE TABLE IF NOT EXISTS active_cloud_sessions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        profile_id TEXT NOT NULL UNIQUE,
                        start_time REAL NOT NULL,
                        status TEXT DEFAULT 'running',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # Keep existing tables but add new indexes
                c.execute('CREATE INDEX IF NOT EXISTS idx_gologin_profiles_execution_mode ON gologin_profiles(execution_mode)')
                c.execute('CREATE INDEX IF NOT EXISTS idx_gologin_profiles_sync ON gologin_profiles(last_sync_at)')
                c.execute('CREATE INDEX IF NOT EXISTS idx_profile_sync_log_profile ON profile_sync_log(profile_id)')
                c.execute('CREATE INDEX IF NOT EXISTS idx_active_cloud_sessions_profile ON active_cloud_sessions(profile_id)')
                
                self.logger.debug("Enhanced GoLogin database tables initialized successfully")
                
        except Exception as e:
            self.logger.error(f"Error initializing database tables: {e}")
            raise
    
    def _create_http_session(self) -> requests.Session:
        """Create HTTP session with optimized connection pooling and retry logic."""
        session = requests.Session()
        
        # Configure retry strategy
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "POST", "PUT", "DELETE", "OPTIONS", "TRACE"]
        )
        
        # Configure HTTP adapter with larger connection pool
        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=10,  # Increase connection pool size
            pool_maxsize=20,      # Increase max connections per pool
            pool_block=False      # Don't block on pool exhaustion
        )
        
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        # Set default headers
        session.headers.update({
            'Authorization': f'Bearer {self.gologin_token}',
            'Content-Type': 'application/json',
            'User-Agent': 'GoLogin-Enhanced-Manager/1.0'
        })
        
        return session

    def get_gologin_profiles_from_api(self) -> List[Dict[str, Any]]:
        """Fetch ALL profiles from GoLogin API using proper pagination (30 profiles per page)."""
        if not self.api_enabled:
            self.logger.warning("API operations disabled - returning empty list")
            return []
            
        try:
            all_profiles = []
            page = 1
            
            while True:
                # Use official pagination parameter
                params = {
                    'page': page,
                    'sorterField': 'createdAt',
                    'sorterOrder': 'descend'
                }
                
                # Use enhanced session with connection pooling
                response = self.session.get(f'{self.api_base}/browser/v2', 
                                          params=params, timeout=30)
                response.raise_for_status()
                
                data = response.json()
                
                # Extract profiles from the current page
                profiles_batch = data.get('profiles', [])
                total_count = data.get('allProfilesCount', 0)
                
                if not profiles_batch:
                    break
                
                all_profiles.extend(profiles_batch)
                
                # Only log every 5th page to reduce verbosity
                if page % 5 == 0 or page == 1 or len(profiles_batch) < 30:
                    self.logger.debug(f"Fetched page {page}: {len(profiles_batch)} profiles (total so far: {len(all_profiles)}/{total_count})")
                
                # If we got fewer than 30 profiles, we've reached the last page
                if len(profiles_batch) < 30:
                    break
                
                page += 1
            
            self.logger.debug(f"Successfully fetched {len(all_profiles)} profiles from GoLogin API (total available: {total_count})")
            return all_profiles
            
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Error fetching profiles from GoLogin API: {e}")
            return []
        except Exception as e:
            self.logger.error(f"Unexpected error fetching profiles: {e}")
            return []
    
    def get_profile_details_from_api(self, profile_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed information about a specific profile from GoLogin API."""
        try:
            # Use enhanced session with connection pooling
            response = self.session.get(f'{self.api_base}/browser/features/{profile_id}/info-for-run', timeout=30)
            response.raise_for_status()
            
            profile_details = response.json()
            self.logger.info(f"Fetched details for profile {profile_id}")
            return profile_details
            
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Error fetching profile {profile_id} details: {e}")
            return None
        except Exception as e:
            self.logger.error(f"Unexpected error fetching profile details: {e}")
            return None
    
    def _sync_profiles_from_cloud(self):
        """Synchronize profiles from GoLogin cloud to local database."""
        try:
            self.logger.debug("Starting profile synchronization from GoLogin cloud...")
            
            cloud_profiles = self.get_gologin_profiles_from_api()
            if not cloud_profiles:
                self.logger.warning("No profiles found in GoLogin cloud or API error")
                return
            
            with DBConnection(self.db_path) as (conn, c):
                # Get existing profile IDs
                c.execute('SELECT profile_id FROM gologin_profiles')
                existing_profiles = {row[0] for row in c.fetchall()}
                
                synced_count = 0
                for profile in cloud_profiles:
                    try:
                        profile_id = profile.get('id')
                        if not profile_id:
                            continue
                        
                        # Extract profile information from the API response
                        profile_name = profile.get('name', f'Profile_{profile_id[:8]}')
                        os_type = profile.get('os', 'win')
                        
                        # Basic proxy information
                        proxy_info = profile.get('proxy', {})
                        proxy_country = None
                        proxy_type = None
                        
                        if proxy_info.get('mode') == 'gologin':
                            proxy_country = 'us'  # Default, can be updated later
                            proxy_type = 'gologin'
                        elif proxy_info.get('mode') in ['http', 'https', 'socks5']:
                            proxy_type = proxy_info.get('mode')
                        
                        # Extract additional information
                        user_agent = profile.get('navigator', {}).get('userAgent', '')
                        screen_resolution = profile.get('navigator', {}).get('resolution', '1920x1080')
                        timezone_info = profile.get('timezone', {})
                        timezone = timezone_info.get('timezone', 'America/New_York') if timezone_info.get('fillBasedOnIp') else timezone_info.get('id', 'America/New_York')
                        language = profile.get('navigator', {}).get('language', 'en-US')
                        
                        if profile_id in existing_profiles:
                            # Update existing profile
                            c.execute('''
                                UPDATE gologin_profiles 
                                SET profile_name = ?, os_type = ?, user_agent = ?,
                                    screen_resolution = ?, timezone = ?, language = ?,
                                    proxy_country = ?, proxy_type = ?,
                                    cloud_profile_data = ?, last_sync_at = CURRENT_TIMESTAMP
                                WHERE profile_id = ?
                            ''', (
                                profile_name, os_type, user_agent, screen_resolution,
                                timezone, language, proxy_country, proxy_type,
                                json.dumps(profile), profile_id
                            ))
                            
                            # Log sync activity
                            c.execute('''
                                INSERT INTO profile_sync_log 
                                (profile_id, sync_type, sync_status, sync_data)
                                VALUES (?, 'update', 'success', ?)
                            ''', (profile_id, json.dumps({'action': 'updated_from_cloud'})))
                            
                        else:
                            # Insert new profile (account_id is NULL for cloud-imported profiles)
                            c.execute('''
                                INSERT INTO gologin_profiles 
                                (account_id, profile_id, profile_name, os_type, user_agent, screen_resolution,
                                 timezone, language, proxy_country, proxy_type, execution_mode,
                                 cloud_profile_data, last_sync_at)
                                VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'cloud', ?, CURRENT_TIMESTAMP)
                            ''', (
                                profile_id, profile_name, os_type, user_agent, screen_resolution,
                                timezone, language, proxy_country, proxy_type,
                                json.dumps(profile)
                            ))
                            
                            # Log sync activity
                            c.execute('''
                                INSERT INTO profile_sync_log 
                                (profile_id, sync_type, sync_status, sync_data)
                                VALUES (?, 'import', 'success', ?)
                            ''', (profile_id, json.dumps({'action': 'imported_from_cloud'})))
                        
                        synced_count += 1
                        
                    except Exception as e:
                        self.logger.error(f"Error syncing profile {profile.get('id', 'unknown')}: {e}")
                        # Log failed sync
                        if profile.get('id'):
                            c.execute('''
                                INSERT INTO profile_sync_log 
                                (profile_id, sync_type, sync_status, error_message)
                                VALUES (?, 'import', 'failed', ?)
                            ''', (profile.get('id'), str(e)))
                        continue
            
            self.logger.debug(f"Profile synchronization completed. Synced {synced_count} profiles.")
            
        except Exception as e:
            self.logger.error(f"Error during profile synchronization: {e}")
    
    def create_profile_enhanced(self, account_id: Optional[int], profile_name: str, 
                              os_type: str = 'win', proxy_country: str = 'us',
                              execution_mode: str = None) -> Optional[str]:
        """Create a new GoLogin profile with enhanced options."""
        try:
            execution_mode = execution_mode or self.execution_mode
            
            # Prepare GoLogin configuration
            gl_config = {"token": self.gologin_token}
            
            gl = GoLogin(gl_config)
            
            # Create profile with custom parameters
            profile_data = gl.createProfileRandomFingerprint({
                "os": os_type,
                "name": profile_name
            })
            
            if not profile_data or 'id' not in profile_data:
                self.logger.error(f"Failed to create GoLogin profile")
                return None
            
            profile_id = profile_data['id']
            
            # Add proxy if specified
            if proxy_country:
                try:
                    gl.addGologinProxyToProfile(profile_id, proxy_country)
                    self.logger.info(f"Added {proxy_country} proxy to profile {profile_id}")
                except Exception as e:
                    self.logger.warning(f"Failed to add proxy to profile {profile_id}: {e}")
            
            # Get assigned port for local execution
            assigned_port = None
            if execution_mode == 'local':
                assigned_port = self.port_manager.get_available_port()
                if not assigned_port:
                    self.logger.warning("No available ports for local execution")
            
            # Store profile in database
            with DBConnection(self.db_path) as (conn, c):
                c.execute('''
                    INSERT INTO gologin_profiles 
                    (account_id, profile_id, profile_name, os_type, proxy_country, proxy_type,
                     execution_mode, assigned_port, cloud_profile_data)
                    VALUES (?, ?, ?, ?, ?, 'gologin', ?, ?, ?)
                ''', (
                    account_id, profile_id, profile_name, os_type, proxy_country,
                    execution_mode, assigned_port, json.dumps(profile_data)
                ))
            
            self.logger.info(f"Created GoLogin profile {profile_id} for account {account_id} (mode: {execution_mode})")
            return profile_id
            
        except Exception as e:
            self.logger.error(f"Error creating GoLogin profile: {e}")
            return None
    
    def get_all_profiles_for_display(self) -> List[Dict[str, Any]]:
        """Get all profiles with enhanced information for display in web interface."""
        try:
            with DBConnection(self.db_path) as (conn, c):
                c.execute('''
                    SELECT 
                        gp.id, gp.account_id, gp.profile_id, gp.profile_name,
                        gp.os_type, gp.proxy_country, gp.proxy_type,
                        gp.user_agent, gp.screen_resolution, gp.timezone, gp.language,
                        gp.execution_mode, gp.assigned_port, gp.is_active,
                        gp.created_at, gp.last_warmup_at, gp.last_sync_at, gp.warmup_count,
                        ta.account_name,
                        gp.captcha_detected, gp.captcha_resolved, gp.captcha_detected_at, 
                        gp.captcha_resolved_at, gp.captcha_detection_reason
                    FROM gologin_profiles gp
                    LEFT JOIN twitter_accounts ta ON gp.account_id = ta.id
                    ORDER BY gp.created_at DESC
                ''')
                
                profiles = []
                for row in c.fetchall():
                    profile_id = row[2]
                    
                    # Check if session is active (check database for persistence)
                    is_session_active = profile_id in self.active_sessions
                    if not is_session_active:
                        # Check database for active sessions
                        c.execute('SELECT COUNT(*) FROM active_cloud_sessions WHERE profile_id = ?', (profile_id,))
                        is_session_active = c.fetchone()[0] > 0
                    
                    profiles.append({
                        'id': row[0],
                        'account_id': row[1],
                        'profile_id': profile_id,
                        'profile_name': row[3],
                        'os_type': row[4],
                        'proxy_country': row[5],
                        'proxy_type': row[6],
                        'user_agent': row[7][:100] + '...' if row[7] and len(row[7]) > 100 else row[7],
                        'screen_resolution': row[8],
                        'timezone': row[9],
                        'language': row[10],
                        'execution_mode': row[11],
                        'assigned_port': row[12],
                        'is_active': row[13],
                        'created_at': row[14],
                        'last_warmup_at': row[15],
                        'last_sync_at': row[16],
                        'warmup_count': row[17],
                        'account_name': row[18],
                        'is_session_active': is_session_active,
                        'captcha_detected': bool(row[19]) if row[19] is not None else False,
                        'captcha_resolved': bool(row[20]) if row[20] is not None else False,
                        'captcha_detected_at': row[21],
                        'captcha_resolved_at': row[22],
                        'captcha_detection_reason': row[23]
                    })
                
                return profiles
                
        except Exception as e:
            self.logger.error(f"Error getting profiles for display: {e}")
            return []
    
    def start_cloud_session(self, profile_id: str) -> Dict[str, Any]:
        """Start a profile session in GoLogin cloud."""
        try:
            url = f'{self.api_base}/browser/{profile_id}/web'
            # Use enhanced session with connection pooling
            response = self.session.post(url, timeout=30)
            
            if response.status_code == 202:
                # Store in memory
                self.active_sessions[profile_id] = {
                    'start_time': time.time(),
                    'status': 'running'
                }
                
                # Store in database for persistence
                with DBConnection(self.db_path) as (conn, c):
                    c.execute('''
                        INSERT OR REPLACE INTO active_cloud_sessions 
                        (profile_id, start_time, status, created_at) 
                        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    ''', (profile_id, time.time(), 'running'))
                
                self.logger.info(f"Started cloud session for profile {profile_id}")
                return {
                    'status': 'success',
                    'profile_id': profile_id,
                    'message': 'Cloud session started successfully'
                }
            else:
                self.logger.error(f"Failed to start cloud session for profile {profile_id}: {response.status_code}")
                return {
                    'status': 'failed',
                    'profile_id': profile_id,
                    'error': f"HTTP {response.status_code}",
                    'message': 'Failed to start cloud session'
                }
                
        except Exception as e:
            self.logger.error(f"Error starting cloud session for profile {profile_id}: {e}")
            return {
                'status': 'failed',
                'profile_id': profile_id,
                'error': str(e),
                'message': 'Exception occurred while starting cloud session'
            }
    
    def stop_cloud_session(self, profile_id: str) -> Dict[str, Any]:
        """Stop a profile session in GoLogin cloud."""
        try:
            # First check if there's an active session
            is_session_active = profile_id in self.active_sessions
            session_in_db = False
            
            with DBConnection(self.db_path) as (conn, c):
                c.execute('SELECT COUNT(*) FROM active_cloud_sessions WHERE profile_id = ?', (profile_id,))
                session_in_db = c.fetchone()[0] > 0
            
            if not is_session_active and not session_in_db:
                self.logger.warning(f"No active session found for profile {profile_id}")
                return {
                    'status': 'warning',
                    'profile_id': profile_id,
                    'message': 'No active session found to stop'
                }
            
            headers = {
                'Authorization': f'Bearer {self.gologin_token}',
                'Content-Type': 'application/json'
            }
            
            url = f'{self.api_base}/browser/{profile_id}/web'
            response = requests.delete(url, headers=headers, timeout=30)
            
            # GoLogin might return 404 if session already stopped
            if response.status_code in [200, 204, 404]:
                session_duration = 0
                
                # Get session duration from database if not in memory
                start_time = None
                if profile_id in self.active_sessions:
                    start_time = self.active_sessions[profile_id].get('start_time', time.time())
                    del self.active_sessions[profile_id]
                else:
                    # Check database for session start time
                    with DBConnection(self.db_path) as (conn, c):
                        c.execute('SELECT start_time FROM active_cloud_sessions WHERE profile_id = ?', (profile_id,))
                        row = c.fetchone()
                        if row:
                            start_time = row[0]
                
                if start_time:
                    session_duration = time.time() - start_time
                
                # Remove from database and update profile
                with DBConnection(self.db_path) as (conn, c):
                    c.execute('DELETE FROM active_cloud_sessions WHERE profile_id = ?', (profile_id,))
                    c.execute('''
                        UPDATE gologin_profiles 
                        SET last_warmup_at = CURRENT_TIMESTAMP 
                        WHERE profile_id = ?
                    ''', (profile_id,))
                
                status_msg = 'stopped' if response.status_code in [200, 204] else 'already stopped'
                self.logger.info(f"Cloud session for profile {profile_id} {status_msg}")
                
                return {
                    'status': 'success',
                    'profile_id': profile_id,
                    'session_duration': session_duration,
                    'message': f'Cloud session {status_msg} successfully'
                }
            else:
                error_msg = f"HTTP {response.status_code}"
                try:
                    response_data = response.json()
                    error_msg += f": {response_data.get('message', 'Unknown error')}"
                except:
                    pass
                
                self.logger.error(f"Failed to stop cloud session for profile {profile_id}: {error_msg}")
                return {
                    'status': 'failed',
                    'profile_id': profile_id,
                    'error': error_msg,
                    'message': 'Failed to stop cloud session'
                }
                
        except Exception as e:
            self.logger.error(f"Error stopping cloud session for profile {profile_id}: {e}")
            return {
                'status': 'failed',
                'profile_id': profile_id,
                'error': str(e),
                'message': 'Exception occurred while stopping cloud session'
            }
    
    def get_active_cloud_sessions(self) -> List[Dict[str, Any]]:
        """Get list of currently active cloud sessions."""
        sessions = []
        current_time = time.time()
        
        # Get sessions from database (persistent storage)
        try:
            with DBConnection(self.db_path) as (conn, c):
                c.execute('''
                    SELECT acs.profile_id, acs.start_time, acs.status, gp.profile_name
                    FROM active_cloud_sessions acs
                    LEFT JOIN gologin_profiles gp ON acs.profile_id = gp.profile_id
                    ORDER BY acs.start_time DESC
                ''')
                
                for row in c.fetchall():
                    profile_id, start_time, status, profile_name = row
                    duration = current_time - start_time
                    sessions.append({
                        'profile_id': profile_id,
                        'profile_name': profile_name or profile_id,
                        'status': status or 'running',
                        'duration_seconds': duration,
                        'duration_formatted': f"{int(duration // 60)}m {int(duration % 60)}s"
                    })
        except Exception as e:
            self.logger.error(f"Error getting active cloud sessions from database: {e}")
            # Fallback to memory-based sessions
            for profile_id, session_info in self.active_sessions.items():
                duration = current_time - session_info.get('start_time', current_time)
                sessions.append({
                    'profile_id': profile_id,
                    'profile_name': profile_id,
                    'status': session_info.get('status', 'unknown'),
                    'duration_seconds': duration,
                    'duration_formatted': f"{int(duration // 60)}m {int(duration % 60)}s"
                })
        
        return sessions

    def test_api_connection(self) -> Dict[str, Any]:
        """Test connection to GoLogin API and return comprehensive status information."""
        try:
            # Test basic API access with pagination using enhanced session
            response = self.session.get(f'{self.api_base}/browser/v2', params={'page': 1}, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            profiles_first_page = data.get('profiles', [])
            total_profiles = data.get('allProfilesCount', 0)
            
            # Test profile creation capability (check fingerprint endpoint)
            test_response = self.session.get(f'{self.api_base}/browser/fingerprint?os=win', timeout=10)
            can_create_profiles = test_response.status_code == 200
            
            # Test cloud session capability if we have profiles
            cloud_session_test = False
            if profiles_first_page:
                try:
                    test_profile_id = profiles_first_page[0].get('id')
                    test_url = f'{self.api_base}/browser/{test_profile_id}/web'
                    
                    # Make a HEAD request to test endpoint availability
                    test_response = self.session.head(test_url, timeout=5)
                    cloud_session_test = test_response.status_code in [200, 202, 405]  # 405 = method not allowed but endpoint exists
                except:
                    pass
            
            return {
                'status': 'success',
                'api_accessible': True,
                'profiles_count': len(profiles_first_page),
                'total_profiles': total_profiles,
                'can_create_profiles': can_create_profiles,
                'cloud_session_capability': cloud_session_test,
                'execution_mode': self.execution_mode,
                'use_cloud': self.use_cloud,
                'max_concurrent': self.max_concurrent,
                'available_ports': len(range(self.port_manager.start_port, self.port_manager.end_port + 1)) - len(self.port_manager.used_ports),
                'active_sessions': len(self.active_sessions),
                'browser_version': data.get('currentBrowserV', 'Unknown')
            }
            
        except requests.exceptions.RequestException as e:
            return {
                'status': 'error',
                'api_accessible': False,
                'error': str(e),
                'execution_mode': self.execution_mode,
                'use_cloud': self.use_cloud
            }
        except Exception as e:
            return {
                'status': 'error',
                'api_accessible': False,
                'error': f"Unexpected error: {str(e)}",
                'execution_mode': self.execution_mode,
                'use_cloud': self.use_cloud
            }

    def mark_captcha_detected(self, profile_id: str, reason: str = "Captcha challenge detected") -> bool:
        """Mark a profile as having captcha detected."""
        try:
            with DBConnection(self.db_path) as (conn, c):
                c.execute('''
                    UPDATE gologin_profiles 
                    SET captcha_detected = 1,
                        captcha_detected_at = CURRENT_TIMESTAMP,
                        captcha_detection_reason = ?,
                        captcha_resolved = 0,
                        captcha_resolved_at = NULL
                    WHERE profile_id = ?
                ''', (reason, profile_id))
                
                if c.rowcount > 0:
                    logger.info(f"Marked captcha detected for profile {profile_id}: {reason}")
                    return True
                else:
                    logger.warning(f"Profile {profile_id} not found when marking captcha detected")
                    return False
                    
        except Exception as e:
            logger.error(f"Error marking captcha detected for profile {profile_id}: {e}")
            return False
    
    def mark_captcha_resolved(self, profile_id: str) -> bool:
        """Mark a profile's captcha as resolved."""
        try:
            with DBConnection(self.db_path) as (conn, c):
                c.execute('''
                    UPDATE gologin_profiles 
                    SET captcha_resolved = 1,
                        captcha_resolved_at = CURRENT_TIMESTAMP
                    WHERE profile_id = ?
                ''', (profile_id,))
                
                if c.rowcount > 0:
                    logger.info(f"Marked captcha resolved for profile {profile_id}")
                    return True
                else:
                    logger.warning(f"Profile {profile_id} not found when marking captcha resolved")
                    return False
                    
        except Exception as e:
            logger.error(f"Error marking captcha resolved for profile {profile_id}: {e}")
            return False

if __name__ == "__main__":
    import json
    import os
    from dotenv import load_dotenv
    
    print("=== STARTING GOLOGIN MANAGER TEST ===")
    
    # Load environment variables
    load_dotenv()
    
    print("Testing GoLogin Enhanced Manager...")
    print("=" * 50)
    
    # Check environment variables
    gologin_token = os.getenv('GOLOGIN_TOKEN')
    print(f"GOLOGIN_TOKEN found: {bool(gologin_token)}")
    if gologin_token:
        print(f"Token length: {len(gologin_token)} chars")
        print(f"Token preview: {gologin_token[:30]}...")
    
    print("\nInitializing manager...")
    # Test the enhanced GoLogin manager
    manager = EnhancedGoLoginManager()
    print(f"Manager created - API enabled: {manager.api_enabled}")
    
    print("\nTesting API connection...")
    # Test API connection
    api_status = manager.test_api_connection()
    print("API Status:", json.dumps(api_status, indent=2))
    
    print("\nGetting profiles from database...")
    # Get all profiles
    profiles = manager.get_all_profiles_for_display()
    print(f"Found {len(profiles)} profiles")
    
    if profiles:
        print(f"\nFirst 5 profiles:")
        for profile in profiles[:5]:  # Show first 5
            print(f"- {profile['profile_name']} ({profile['profile_id']}) - {profile['execution_mode']}")
        
        print(f"\nTesting cloud session start with first profile...")
        first_profile = profiles[0]
        session_result = manager.start_cloud_session(first_profile['profile_id'])
        print(f"Session start result: {session_result}")
    else:
        print("No profiles found in database")
    
    print("=" * 50)
    print("Test completed.") 