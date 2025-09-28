#!/usr/bin/env python3
"""
GoLogin Hybrid Manager

Supports both cloud and local execution modes:
- Cloud mode: Session management with manual browser access
- Local mode: Full WebSocket control with Selenium integration
"""

import os
import sys
import time
import logging
from typing import Dict, List, Optional, Any, Tuple
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

# Add the current directory to path to import local modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from gologin import GoLogin
from gologin_manager_enhanced import EnhancedGoLoginManager
from fix_db_connections import DBConnection
from twitter_automation_patterns import (
    TwitterAutomationManager, PatternType, EngagementConfig,
    automation_manager
)
from global_gologin_session_manager import global_session_manager

class GoLoginHybridManager(EnhancedGoLoginManager):
    """
    Hybrid GoLogin manager supporting both cloud and local execution modes.
    
    Cloud Mode: Uses enhanced cloud session management (current implementation)
    Local Mode: Full WebSocket control with Selenium integration
    """
    
    def __init__(self, db_path='twitter_accounts.db', default_mode='cloud'):
        super().__init__(db_path)
        self.default_mode = default_mode  # 'cloud' or 'local'
        self.local_sessions = {}  # profile_id -> local session data
        
        # Initialize database tables for local session tracking
        self._init_local_session_tables()
        
        # Local execution configuration
        self.local_headless = os.getenv('GOLOGIN_LOCAL_HEADLESS', 'true').lower() == 'true'
        self.local_cleanup_on_stop = os.getenv('GOLOGIN_LOCAL_CLEANUP', 'false').lower() == 'true'
        
        self.logger.debug(f"GoLogin Hybrid Manager initialized (default mode: {default_mode})")
        
        # Initialize automation manager
        self.automation_manager = automation_manager
    
    def _init_local_session_tables(self):
        """Initialize database tables for local session persistence."""
        try:
            with DBConnection(self.db_path) as (conn, c):
                # Active local sessions table
                c.execute('''
                    CREATE TABLE IF NOT EXISTS active_local_sessions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        profile_id TEXT NOT NULL UNIQUE,
                        start_time REAL NOT NULL,
                        debugger_address TEXT,
                        chromium_version TEXT,
                        status TEXT DEFAULT 'active',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                c.execute('CREATE INDEX IF NOT EXISTS idx_active_local_sessions_profile ON active_local_sessions(profile_id)')
                
                self.logger.debug("Local session database tables initialized successfully")
                
        except Exception as e:
            self.logger.error(f"Error initializing local session tables: {e}")
            raise
    
    def _check_local_session_exists(self, profile_id: str) -> bool:
        """Check if a local session exists for the profile (in memory or database)."""
        # First check memory
        if profile_id in self.local_sessions:
            return True
        
        # Then check database
        try:
            with DBConnection(self.db_path) as (conn, c):
                c.execute('''
                    SELECT COUNT(*) FROM active_local_sessions 
                    WHERE profile_id = ? AND status = 'active'
                ''', (profile_id,))
                count = c.fetchone()[0]
                return count > 0
        except Exception as e:
            self.logger.error(f"Error checking local session existence: {e}")
            return False
    
    def start_session_with_control(self, profile_id: str, execution_mode: str = None) -> Dict[str, Any]:
        """
        Start a session with full WebSocket control.
        
        Args:
            profile_id: GoLogin profile ID
            execution_mode: 'cloud', 'local', or 'headless'. If None, uses default_mode
            
        Returns:
            Dict with session info and control capabilities
        """
        execution_mode = execution_mode or self.default_mode
        
        if execution_mode == 'local':
            return self._start_local_session_with_control(profile_id, headless=False)
        elif execution_mode == 'headless':
            return self._start_local_session_with_control(profile_id, headless=True)
        else:
            return self._start_cloud_session_with_manual_access(profile_id)
    
    def _start_local_session_with_control(self, profile_id: str, headless: bool = False) -> Dict[str, Any]:
        """Start a local GoLogin session with full Selenium control."""
        try:
            self.logger.info(f"Starting local session with control for profile {profile_id}")
            
            # Use global session manager to maintain sessions across API requests
            result = global_session_manager.start_local_session(
                profile_id=profile_id,
                gologin_token=self.gologin_token,
                headless=headless
            )
            
            if result['status'] != 'success':
                return result
            
            # Store in database for tracking (both tables for compatibility)
            start_time = result.get('start_time', time.time())
            debugger_address = result.get('debugger_address', '')
            chromium_version = result.get('chromium_version', '')
            execution_mode = result.get('execution_mode', 'local')
            
            with DBConnection(self.db_path) as (conn, c):
                # Store ONLY in local sessions table - NOT in cloud sessions table
                # Local sessions should not appear in "Active Cloud Sessions"
                c.execute('''
                    INSERT OR REPLACE INTO active_local_sessions 
                    (profile_id, start_time, debugger_address, chromium_version, status, created_at) 
                    VALUES (?, ?, ?, ?, 'active', CURRENT_TIMESTAMP)
                ''', (profile_id, start_time, debugger_address, chromium_version))
            
            mode_name = "headless" if headless else "local"
            self.logger.info(f"{mode_name.capitalize()} session started successfully for profile {profile_id}")
            
            # Return the result from global session manager
            return result
            
        except Exception as e:
            self.logger.error(f"Error starting local session for profile {profile_id}: {e}")
            return {
                'status': 'failed',
                'profile_id': profile_id,
                'error': str(e),
                'message': 'Failed to start local session'
            }
    
    def _start_cloud_session_with_manual_access(self, profile_id: str) -> Dict[str, Any]:
        """Start a cloud session with manual browser access (existing implementation)."""
        result = self.start_cloud_session(profile_id)
        
        if result['status'] == 'success':
            return {
                'status': 'limited_success',
                'profile_id': profile_id,
                'execution_mode': 'cloud',
                'control_type': 'manual_only',
                'capabilities': ['session_management', 'manual_browser_access'],
                'message': 'Cloud session started - manual browser access available',
                'limitation_reason': 'Live WebSocket control not available with current GoLogin cloud infrastructure'
            }
        else:
            return result
    
    def stop_session_with_control(self, profile_id: str) -> Dict[str, Any]:
        """Stop a session regardless of execution mode."""
        # Check if it's a local session using global manager
        if global_session_manager.has_active_session(profile_id):
            return self._stop_local_session(profile_id)
        else:
            return self.stop_cloud_session(profile_id)
    
    def _stop_local_session(self, profile_id: str) -> Dict[str, Any]:
        """Stop a local GoLogin session and clean up resources."""
        try:
            # Use global session manager to stop the session
            result = global_session_manager.stop_local_session(
                profile_id=profile_id,
                cleanup=self.local_cleanup_on_stop
            )
            
            if result['status'] == 'success':
                # Update database - remove ONLY from local sessions table
                with DBConnection(self.db_path) as (conn, c):
                    c.execute('DELETE FROM active_local_sessions WHERE profile_id = ?', (profile_id,))
                    c.execute('''
                        UPDATE gologin_profiles 
                        SET last_warmup_at = CURRENT_TIMESTAMP 
                        WHERE profile_id = ?
                    ''', (profile_id,))
                
                self.logger.debug(f"Local session stopped for profile {profile_id}")
            
            return result
            
        except Exception as e:
            self.logger.error(f"Error stopping local session for profile {profile_id}: {e}")
            return {
                'status': 'failed',
                'profile_id': profile_id,
                'error': str(e),
                'message': 'Failed to stop local session'
            }
    
    def navigate_to_url(self, profile_id: str, url: str) -> Dict[str, Any]:
        """Navigate to a URL (only works with local sessions)."""
        if not global_session_manager.has_active_session(profile_id):
            return {
                'status': 'failed',
                'error': 'No active local session found. Navigation only available in local mode.',
                'profile_id': profile_id
            }
        
        try:
            driver = global_session_manager.get_driver(profile_id)
            driver.get(url)
            
            return {
                'status': 'success',
                'profile_id': profile_id,
                'url': url,
                'current_url': driver.current_url,
                'title': driver.title,
                'message': 'Navigation completed successfully'
            }
            
        except Exception as e:
            self.logger.error(f"Error navigating to {url} for profile {profile_id}: {e}")
            return {
                'status': 'failed',
                'profile_id': profile_id,
                'error': str(e),
                'message': 'Navigation failed'
            }
    
    def take_screenshot(self, profile_id: str) -> Dict[str, Any]:
        """Take a screenshot (only works with local sessions)."""
        if not global_session_manager.has_active_session(profile_id):
            return {
                'status': 'failed',
                'error': 'No active local session found. Screenshots only available in local mode.',
                'profile_id': profile_id
            }
        
        try:
            driver = global_session_manager.get_driver(profile_id)
            screenshot_base64 = driver.get_screenshot_as_base64()
            
            # Store screenshot in database (optional)
            with DBConnection(self.db_path) as (conn, c):
                c.execute('''
                    INSERT INTO session_screenshots 
                    (profile_id, screenshot_data, timestamp)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                ''', (profile_id, screenshot_base64))
            
            return {
                'status': 'success',
                'profile_id': profile_id,
                'screenshot_data': screenshot_base64[:100] + '...',  # Truncated for response
                'message': 'Screenshot captured successfully'
            }
            
        except Exception as e:
            self.logger.error(f"Error taking screenshot for profile {profile_id}: {e}")
            return {
                'status': 'failed',
                'profile_id': profile_id,
                'error': str(e),
                'message': 'Screenshot failed'
            }
    
    def execute_javascript(self, profile_id: str, script: str) -> Dict[str, Any]:
        """Execute JavaScript in the browser (only works with local sessions)."""
        if not global_session_manager.has_active_session(profile_id):
            return {
                'status': 'failed',
                'error': 'No active local session found. JavaScript execution only available in local mode.',
                'profile_id': profile_id
            }
        
        try:
            driver = global_session_manager.get_driver(profile_id)
            result = driver.execute_script(script)
            
            return {
                'status': 'success',
                'profile_id': profile_id,
                'script': script[:100] + '...' if len(script) > 100 else script,
                'result': str(result)[:500] if result else None,
                'message': 'JavaScript executed successfully'
            }
            
        except Exception as e:
            self.logger.error(f"Error executing JavaScript for profile {profile_id}: {e}")
            return {
                'status': 'failed',
                'profile_id': profile_id,
                'error': str(e),
                'message': 'JavaScript execution failed'
            }
    
    def get_session_info(self, profile_id: str) -> Dict[str, Any]:
        """Get detailed information about a session."""
        # Check local sessions first using global manager
        if global_session_manager.has_active_session(profile_id):
            session_data = global_session_manager.get_session(profile_id)
            duration = time.time() - session_data.get('start_time', time.time())
            
            try:
                driver = global_session_manager.get_driver(profile_id)
                current_url = driver.current_url if driver else 'Unknown'
                page_title = driver.title if driver else 'Unknown'
            except:
                current_url = 'Unknown'
                page_title = 'Unknown'
            
            return {
                'status': 'success',
                'profile_id': profile_id,
                'execution_mode': 'local',
                'control_type': 'full_selenium',
                'duration_seconds': duration,
                'duration_formatted': f"{int(duration // 60)}m {int(duration % 60)}s",
                'current_url': current_url,
                'page_title': page_title,
                'debugger_address': session_data.get('debugger_address'),
                'chromium_version': session_data.get('chromium_version'),
                'capabilities': [
                    'navigation', 'screenshots', 'element_interaction', 
                    'javascript_execution', 'form_filling', 'automation'
                ]
            }
        
        # Fall back to cloud session info
        active_sessions = self.get_active_cloud_sessions()
        for session in active_sessions:
            if session['profile_id'] == profile_id:
                return {
                    'status': 'success',
                    'profile_id': profile_id,
                    'execution_mode': 'cloud',
                    'control_type': 'manual_only',
                    'duration_seconds': session['duration_seconds'],
                    'duration_formatted': session['duration_formatted'],
                    'capabilities': ['session_management', 'manual_browser_access']
                }
        
        return {
            'status': 'not_found',
            'profile_id': profile_id,
            'message': 'No active session found'
        }
    
    def get_all_active_sessions(self) -> List[Dict[str, Any]]:
        """Get information about all active sessions (both local and cloud)."""
        sessions = []
        current_time = time.time()
        
        # Add local sessions from global manager
        active_local_sessions = global_session_manager.list_active_sessions()
        for profile_id, session_info in active_local_sessions.items():
            sessions.append({
                'profile_id': profile_id,
                'execution_mode': 'local',
                'control_type': 'full_selenium',
                'status': session_info.get('status', 'active'),
                'duration_seconds': session_info['duration'],
                'duration_formatted': f"{int(session_info['duration'] // 60)}m {int(session_info['duration'] % 60)}s",
                'capabilities': ['navigation', 'screenshots', 'automation']
            })
        
        # Add cloud sessions
        cloud_sessions = self.get_active_cloud_sessions()
        for session in cloud_sessions:
            sessions.append({
                'profile_id': session['profile_id'],
                'execution_mode': 'cloud',
                'control_type': 'manual_only',
                'status': session.get('status', 'running'),
                'duration_seconds': session['duration_seconds'],
                'duration_formatted': session['duration_formatted'],
                'capabilities': ['session_management', 'manual_browser_access']
            })
        
        return sessions
    
    def execute_automation_pattern(self, profile_id: str, pattern_type: str, 
                                 custom_config: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Execute a Twitter automation pattern on a local session.
        
        Args:
            profile_id: Profile ID with active local session
            pattern_type: One of 'home_feed', 'communities', 'repost'
            custom_config: Optional configuration overrides
            
        Returns:
            Pattern execution results
        """
        try:
            # Verify it's a local session using global manager
            if not global_session_manager.has_active_session(profile_id):
                return {
                    'status': 'failed',
                    'error': 'No active local session found. Pattern execution requires local mode.',
                    'profile_id': profile_id
                }
            
            # Get driver from global session manager
            driver = global_session_manager.get_driver(profile_id)
            
            if not driver:
                return {
                    'status': 'failed',
                    'error': 'No WebDriver instance found in session',
                    'profile_id': profile_id
                }
            
            # Convert pattern type to enum
            try:
                pattern_enum = PatternType(pattern_type)
            except ValueError:
                return {
                    'status': 'failed',
                    'error': f'Invalid pattern type: {pattern_type}',
                    'valid_patterns': [p.value for p in PatternType],
                    'profile_id': profile_id
                }
            
            # Create custom config if provided
            config = EngagementConfig()
            if custom_config:
                for key, value in custom_config.items():
                    if hasattr(config, key):
                        setattr(config, key, value)
            
            # Execute pattern
            self.logger.info(f"Executing pattern {pattern_type} for profile {profile_id}")
            
            result = self.automation_manager.execute_pattern_for_session(
                session_id=f"{profile_id}_{int(time.time())}",
                driver=driver,
                pattern_type=pattern_enum,
                config=config
            )
            
            # Update session status in global manager
            session_data = global_session_manager.get_session(profile_id)
            if session_data:
                session_data['last_automation'] = {
                    'pattern': pattern_type,
                    'timestamp': time.time(),
                    'result': result
                }
            
            return result
            
        except Exception as e:
            self.logger.error(f"Error executing pattern {pattern_type} for profile {profile_id}: {e}")
            return {
                'status': 'failed',
                'profile_id': profile_id,
                'pattern_type': pattern_type,
                'error': str(e)
            }
    
    def get_available_patterns(self) -> List[Dict[str, Any]]:
        """Get list of available automation patterns."""
        return self.automation_manager.get_available_patterns()

# Example usage and testing
if __name__ == "__main__":
    import asyncio
    
    async def test_hybrid_manager():
        """Test the hybrid manager with both modes."""
        print("🔧 Testing GoLogin Hybrid Manager")
        print("=" * 50)
        
        manager = GoLoginHybridManager(default_mode='local')
        
        # Get available profiles
        profiles = manager.get_all_profiles_for_display()
        if not profiles:
            print("❌ No profiles available for testing")
            return
        
        test_profile = profiles[0]
        profile_id = test_profile['profile_id']
        profile_name = test_profile['profile_name']
        
        print(f"✅ Testing with profile: {profile_name} ({profile_id})")
        
        # Test local session with full control
        print("\n1. Testing Local Session (Full Control)")
        result = manager.start_session_with_control(profile_id, execution_mode='local')
        print(f"Start result: {result['status']}")
        
        if result['status'] == 'success':
            print("✅ Local session with WebSocket control active!")
            
            # Test navigation
            print("\n2. Testing Navigation")
            nav_result = manager.navigate_to_url(profile_id, 'https://httpbin.org/user-agent')
            print(f"Navigation: {nav_result['status']}")
            
            # Wait a moment
            time.sleep(3)
            
            # Test screenshot
            print("\n3. Testing Screenshot")
            screenshot_result = manager.take_screenshot(profile_id)
            print(f"Screenshot: {screenshot_result['status']}")
            
            # Test JavaScript execution
            print("\n4. Testing JavaScript")
            js_result = manager.execute_javascript(profile_id, 'return document.title;')
            print(f"JavaScript: {js_result['status']}")
            
            # Get session info
            print("\n5. Session Info")
            info = manager.get_session_info(profile_id)
            print(f"Current URL: {info.get('current_url', 'Unknown')}")
            print(f"Page Title: {info.get('page_title', 'Unknown')}")
            print(f"Duration: {info.get('duration_formatted', 'Unknown')}")
            
            # Stop session
            print("\n6. Stopping Session")
            stop_result = manager.stop_session_with_control(profile_id)
            print(f"Stop: {stop_result['status']}")
            
        else:
            print(f"❌ Local session failed: {result.get('error', 'Unknown error')}")
            
            # Fallback to cloud session
            print("\n2. Testing Cloud Session (Manual Access)")
            cloud_result = manager.start_session_with_control(profile_id, execution_mode='cloud')
            print(f"Cloud session: {cloud_result['status']}")
            
            if cloud_result['status'] in ['success', 'limited_success']:
                time.sleep(2)
                stop_result = manager.stop_session_with_control(profile_id)
                print(f"Stop: {stop_result['status']}")
        
        print("\n🎉 Hybrid manager test completed!")
    
    # Run the test
    asyncio.run(test_hybrid_manager()) 