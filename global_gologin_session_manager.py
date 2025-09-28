"""
Global GoLogin Session Manager

Maintains GoLogin sessions across API requests using a singleton pattern.
This solves the issue where each API request creates a new manager instance.
"""

import threading
import time
import logging
from typing import Dict, Any, Optional, List
from selenium import webdriver
from gologin import GoLogin
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

# Import browser startup handler
try:
    from browser_startup_handler import startup_handler
except ImportError:
    startup_handler = None

class GlobalGoLoginSessionManager:
    """
    Singleton session manager that maintains active GoLogin sessions across API requests.
    Thread-safe and persistent.
    """
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(GlobalGoLoginSessionManager, cls).__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
            
        self.local_sessions = {}  # profile_id -> session data
        self.logger = logging.getLogger(__name__)
        self._session_lock = threading.Lock()
        self._initialized = True
        
        self.logger.debug("Global GoLogin Session Manager initialized")
    
    @classmethod
    def get_instance(cls):
        """Get the singleton instance of the GlobalGoLoginSessionManager."""
        return cls()
    
    def start_local_session(self, profile_id: str, gologin_token: str, headless: bool = True) -> Dict[str, Any]:
        """Start a local GoLogin session with full Selenium control."""
        with self._session_lock:
            try:
                self.logger.info(f"Starting global local session for profile {profile_id}")
                
                # Check if session already exists
                if profile_id in self.local_sessions:
                    existing_session = self.local_sessions[profile_id]
                    if existing_session.get('status') == 'active':
                        self.logger.info(f"Local session already exists for profile {profile_id}")
                        return {
                            'status': 'success',
                            'profile_id': profile_id,
                            'message': 'Session already active',
                            'execution_mode': 'local',
                            'control_type': 'full_selenium',
                            'debugger_address': existing_session.get('debugger_address'),
                            'chromium_version': existing_session.get('chromium_version'),
                            'capabilities': [
                                'navigation', 'screenshots', 'element_interaction', 
                                'javascript_execution', 'form_filling', 'automation'
                            ],
                            'start_time': existing_session.get('start_time')
                        }
                
                # Initialize GoLogin for local execution
                gl_config = {
                    "token": gologin_token,
                    "profile_id": profile_id,
                    "timeout": 30,  # Set timeout for HTTP requests
                    "retry_count": 2  # Limit retries
                }
                
                # Base automation parameters for all sessions
                base_params = [
                    "--lang=en-US",                           # Force English language
                    "--accept-lang=en-US,en;q=0.9",         # Force English accept language
                    "--disable-blink-features=AutomationControlled", # Anti-detection
                    "--no-first-run",                        # Skip first run wizard
                    "--disable-default-apps",                # Skip default apps
                    "--disable-popup-blocking",              # Allow popups for automation
                    "--disable-translate",                   # Disable translation prompts
                    "--disable-background-timer-throttling"  # Maintain performance
                ]
                
                # Add headless parameters if requested
                if headless:
                    gl_config["extra_params"] = base_params + [
                        "--headless=new",                           # Use new headless mode (less detectable)
                        "--no-sandbox", 
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                        "--window-size=1920,1080",
                        "--disable-web-security",              # For server environments
                        "--disable-features=VizDisplayCompositor", # Optimize rendering
                        "--disable-backgrounding-occluded-windows", # Server optimization
                        "--disable-renderer-backgrounding",        # Keep processes active
                        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",  # Use supported Chrome version
                        
                        # Enhanced stealth parameters
                        "--disable-blink-features=AutomationControlled",  # Remove automation indicators
                        "--exclude-switches=enable-automation",           # Remove automation flag
                        "--disable-extensions-except=",                   # Disable extension warnings
                        "--disable-plugins-discovery",                    # Disable plugin detection
                        "--no-first-run",                                # Skip first run setup
                        "--no-default-browser-check",                    # Skip default browser check
                        "--disable-default-apps",                        # Skip default apps
                        "--disable-component-extensions-with-background-pages", # Reduce fingerprinting
                        "--disable-background-networking",               # Reduce network fingerprinting
                        "--disable-sync",                                # Disable sync
                        "--disable-translate",                          # Disable translate prompts
                        "--disable-ipc-flooding-protection",           # Allow normal IPC
                        "--enable-features=NetworkService,NetworkServiceLogging", # Modern networking
                        "--force-color-profile=srgb",                  # Consistent color profile
                        "--metrics-recording-only",                    # Reduce telemetry
                        "--no-crash-upload",                          # No crash reports
                        "--disable-background-timer-throttling",      # Maintain performance
                        "--disable-features=TranslateUI",             # Disable translate UI
                        "--disable-features=BlinkGenPropertyTrees",   # Reduce detection
                        "--disable-domain-reliability",               # Reduce tracking
                        "--disable-client-side-phishing-detection",   # Reduce detection calls
                    ]
                else:
                    # Non-headless mode still gets language forcing and basic automation params
                    gl_config["extra_params"] = base_params
                
                gl = GoLogin(gl_config)
                
                # Start browser and get WebSocket URL
                try:
                    debugger_address = gl.start()
                except Exception as gologin_error:
                    # Handle GoLogin library errors that might not be proper exceptions
                    self.logger.error(f"GoLogin start error: {gologin_error}")
                    self.logger.error(f"GoLogin error type: {type(gologin_error)}")
                    # Convert to proper exception if needed
                    if not isinstance(gologin_error, BaseException):
                        raise Exception(f"GoLogin error: {gologin_error}")
                    else:
                        raise gologin_error
                
                if not debugger_address:
                    return {
                        'status': 'failed',
                        'error': 'Failed to get debugger address from GoLogin',
                        'profile_id': profile_id
                    }
                
                # Get Chromium version for WebDriver
                chromium_version = gl.get_chromium_version()
                
                # Install and configure WebDriver
                service = Service(ChromeDriverManager(driver_version=chromium_version).install())
                
                chrome_options = webdriver.ChromeOptions()
                chrome_options.add_experimental_option("debuggerAddress", debugger_address)
                
                # Minimal configuration - most settings are handled by GoLogin extra_params
                # Note: Language and automation settings are passed to GoLogin's browser startup
                # When connecting to existing browser via debuggerAddress, redundant args are avoided
                
                # Create WebDriver instance
                driver = webdriver.Chrome(service=service, options=chrome_options)
                
                # Apply advanced stealth via CDP (Chrome DevTools Protocol) - executes before any page loads
                if headless:
                    try:
                        # Enable CDP runtime
                        driver.execute_cdp_cmd('Runtime.enable', {})
                        
                        # Inject stealth script that runs before any page loads
                        stealth_cdp_script = """
                        // Ultimate stealth mode - runs before page load
                        (() => {
                            const originalDescriptor = Object.getOwnPropertyDescriptor(Navigator.prototype, 'webdriver');
                            if (originalDescriptor) {
                                delete Navigator.prototype.webdriver;
                            }
                            
                            // Override at the prototype level
                            Object.defineProperty(Navigator.prototype, 'webdriver', {
                                get: () => undefined,
                                configurable: true
                            });
                            
                            // Remove automation indicators early
                            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
                            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
                            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;
                            
                            // Override screen properties before detection
                            Object.defineProperties(screen, {
                                width: { get: () => 1920, configurable: true },
                                height: { get: () => 1080, configurable: true },
                                availWidth: { get: () => 1920, configurable: true },
                                availHeight: { get: () => 1040, configurable: true }
                            });
                            
                            // Override window properties early
                            Object.defineProperties(window, {
                                outerWidth: { get: () => 1920, configurable: true },
                                outerHeight: { get: () => 1080, configurable: true },
                                innerWidth: { get: () => 1920, configurable: true },
                                innerHeight: { get: () => 1040, configurable: true }
                            });
                            
                            console.log('CDP stealth injected');
                        })();
                        """
                        
                        driver.execute_cdp_cmd('Runtime.addBinding', {'name': 'cdpStealth'})
                        driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
                            'source': stealth_cdp_script
                        })
                        
                        self.logger.debug("Applied CDP-based stealth injection")
                        
                    except Exception as e:
                        self.logger.warning(f"CDP stealth injection failed: {e}")
                        # Continue anyway
                
                # Execute anti-detection scripts
                try:
                    # Comprehensive stealth JavaScript to avoid headless detection
                    stealth_script = """
                    // Override webdriver property
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined,
                    });

                    // Override automation flags
                    delete window.navigator.webdriver;
                    window.navigator.chrome = {
                        runtime: {},
                        loadTimes: function() {},
                        csi: function() {},
                        app: {
                            isInstalled: false,
                            InstallState: {
                                DISABLED: 'disabled',
                                INSTALLED: 'installed',
                                NOT_INSTALLED: 'not_installed'
                            },
                            RunningState: {
                                CANNOT_RUN: 'cannot_run',
                                READY_TO_RUN: 'ready_to_run',
                                RUNNING: 'running'
                            }
                        }
                    };

                    // Override permissions query to avoid headless detection
                    const originalQuery = window.navigator.permissions.query;
                    window.navigator.permissions.query = (parameters) => (
                        parameters.name === 'notifications' ?
                            Promise.resolve({ state: Notification.permission }) :
                            originalQuery(parameters)
                    );

                    // Spoof plugins
                    Object.defineProperty(navigator, 'plugins', {
                        get: () => [1, 2, 3, 4, 5].map(() => ({ 
                            0: {type: "application/x-google-chrome-pdf", suffixes: "pdf", description: "Portable Document Format"},
                            description: "Portable Document Format", 
                            filename: "internal-pdf-viewer", 
                            length: 1, 
                            name: "Chrome PDF Plugin" 
                        })),
                    });

                    // Spoof languages
                    Object.defineProperty(navigator, 'languages', {
                        get: () => ['en-US', 'en'],
                    });

                    // Spoof screen dimensions (avoid 0x0 which indicates headless)
                    Object.defineProperty(screen, 'width', { get: () => 1920 });
                    Object.defineProperty(screen, 'height', { get: () => 1080 });
                    Object.defineProperty(screen, 'availWidth', { get: () => 1920 });
                    Object.defineProperty(screen, 'availHeight', { get: () => 1040 });
                    Object.defineProperty(screen, 'colorDepth', { get: () => 24 });
                    Object.defineProperty(screen, 'pixelDepth', { get: () => 24 });

                    // Spoof window dimensions
                    Object.defineProperty(window, 'outerWidth', { get: () => 1920 });
                    Object.defineProperty(window, 'outerHeight', { get: () => 1080 });
                    Object.defineProperty(window, 'innerWidth', { get: () => 1920 });
                    Object.defineProperty(window, 'innerHeight', { get: () => 1040 });

                    // Override Date to avoid timezone detection issues
                    const getTimezoneOffset = Date.prototype.getTimezoneOffset;
                    Date.prototype.getTimezoneOffset = function() {
                        return -180; // UTC+3 (Turkey timezone)
                    };

                    // Mock battery API
                    Object.defineProperty(navigator, 'getBattery', {
                        get: () => () => Promise.resolve({
                            charging: true,
                            chargingTime: 0,
                            dischargingTime: Infinity,
                            level: 1
                        }),
                    });

                    // Mock connection API
                    Object.defineProperty(navigator, 'connection', {
                        get: () => ({
                            effectiveType: '4g',
                            rtt: 100,
                            downlink: 2,
                            saveData: false
                        }),
                    });

                    // Override WebGL vendor to avoid detection
                    const getParameter = WebGLRenderingContext.prototype.getParameter;
                    WebGLRenderingContext.prototype.getParameter = function(parameter) {
                        if (parameter === 37445) {
                            return 'Intel Inc.';
                        }
                        if (parameter === 37446) {
                            return 'Intel Iris OpenGL Engine';
                        }
                        return getParameter(parameter);
                    };

                    // Mock media devices
                    Object.defineProperty(navigator, 'mediaDevices', {
                        get: () => ({
                            enumerateDevices: () => Promise.resolve([
                                { deviceId: 'default', kind: 'audioinput', label: '', groupId: '' },
                                { deviceId: 'default', kind: 'audiooutput', label: '', groupId: '' },
                                { deviceId: 'default', kind: 'videoinput', label: '', groupId: '' }
                            ])
                        }),
                    });

                    // Override hardware concurrency
                    Object.defineProperty(navigator, 'hardwareConcurrency', {
                        get: () => 8,
                    });

                    // Override platform
                    Object.defineProperty(navigator, 'platform', {
                        get: () => 'Win32',
                    });

                    // Mock notification API
                    if ('Notification' in window) {
                        Object.defineProperty(Notification, 'permission', {
                            get: () => 'default',
                        });
                    }

                    // Spoof performance timing to look realistic
                    if (window.performance && window.performance.timing) {
                        const now = Date.now();
                        Object.defineProperty(window.performance.timing, 'navigationStart', {
                            get: () => now - Math.random() * 1000,
                        });
                    }

                    console.log('Stealth mode activated - headless detection bypassed');
                    """
                    
                    driver.execute_script(stealth_script)
                    self.logger.debug("Applied comprehensive anti-detection scripts")
                    
                except Exception as e:
                    self.logger.warning(f"Could not execute all anti-detection scripts: {e}")
                    # Continue anyway - the browser might still work
                
                # Schedule post-launch tasks (Cloudflare solving, etc.)
                if startup_handler:
                    startup_handler.schedule_post_launch_tasks(profile_id, driver, delay_seconds=20)
                    self.logger.info(f"Scheduled post-launch tasks for profile {profile_id}")
                
                # Store session data globally
                execution_mode = 'headless' if headless else 'local'
                session_data = {
                    'gologin_instance': gl,
                    'driver': driver,
                    'debugger_address': debugger_address,
                    'chromium_version': chromium_version,
                    'start_time': time.time(),
                    'execution_mode': execution_mode,
                    'headless': headless,
                    'status': 'active',
                    'gologin_token': gologin_token
                }
                
                self.local_sessions[profile_id] = session_data
                
                self.logger.debug(f"Global {execution_mode} session started successfully for profile {profile_id}")
                
                return {
                    'status': 'success',
                    'profile_id': profile_id,
                    'execution_mode': execution_mode,
                    'control_type': 'full_selenium',
                    'debugger_address': debugger_address,
                    'chromium_version': chromium_version,
                    'headless': headless,
                    'capabilities': [
                        'navigation', 'screenshots', 'element_interaction', 
                        'javascript_execution', 'form_filling', 'automation'
                    ],
                    'message': f'Global {execution_mode} session with full WebSocket control active',
                    # Don't include session_data with WebDriver - not JSON serializable
                    'start_time': session_data['start_time']
                }
                
            except Exception as e:
                self.logger.error(f"Error starting global local session for profile {profile_id}: {e}")
                self.logger.error(f"Exception type: {type(e)}")
                import traceback
                self.logger.error(f"Full traceback: {traceback.format_exc()}")
                
                # Provide more specific error messages for common issues
                error_message = str(e)
                if "exceptions must derive from BaseException" in error_message:
                    error_message = "GoLogin library network error. Please check your internet connection and try again."
                elif "SSL" in error_message or "geo.myip.link" in error_message:
                    error_message = "Network connectivity issue with GoLogin services. Please check your internet connection."
                elif "ChromeDriver" in error_message:
                    error_message = "Chrome WebDriver not found. Please install Chrome and ensure it's in your PATH."
                
                return {
                    'status': 'failed',
                    'profile_id': profile_id,
                    'error': error_message,
                    'message': 'Failed to start global local session'
                }
    
    def stop_local_session(self, profile_id: str, cleanup: bool = False) -> Dict[str, Any]:
        """Stop a local GoLogin session and clean up resources."""
        with self._session_lock:
            try:
                # Cancel any pending startup tasks
                if startup_handler:
                    startup_handler.cancel_tasks_for_profile(profile_id)
                
                if profile_id not in self.local_sessions:
                    return {
                        'status': 'warning',
                        'profile_id': profile_id,
                        'message': 'No active local session found'
                    }
                
                session_data = self.local_sessions[profile_id]
                start_time = session_data.get('start_time', time.time())
                session_duration = time.time() - start_time
                
                # Close WebDriver first (more aggressive cleanup)
                driver_closed = False
                try:
                    driver = session_data.get('driver')
                    if driver:
                        # Close all windows first
                        try:
                            driver.close()
                        except:
                            pass
                        # Then quit the driver
                        driver.quit()
                        driver_closed = True
                        self.logger.debug(f"WebDriver closed successfully for {profile_id}")
                except Exception as e:
                    self.logger.warning(f"Error closing WebDriver for {profile_id}: {e}")
                
                # Stop GoLogin instance
                try:
                    gl = session_data.get('gologin_instance')
                    if gl:
                        gl.stop()
                        
                        # Optionally delete profile if cleanup is enabled
                        if cleanup:
                            gl.delete(profile_id)
                            self.logger.info(f"Deleted profile {profile_id} after session stop")
                except Exception as e:
                    self.logger.warning(f"Error stopping GoLogin instance for {profile_id}: {e}")
                
                # Additional cleanup: kill any remaining Chrome processes for this profile
                if not driver_closed:
                    try:
                        import psutil
                        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                            if proc.info['name'] and 'chrome' in proc.info['name'].lower():
                                cmdline = proc.info['cmdline']
                                if cmdline and any(profile_id in str(arg) for arg in cmdline):
                                    proc.terminate()
                                    self.logger.info(f"Terminated Chrome process for profile {profile_id}")
                    except ImportError:
                        self.logger.warning("psutil not available for process cleanup")
                    except Exception as e:
                        self.logger.warning(f"Error during process cleanup for {profile_id}: {e}")
                
                # Remove from active sessions
                del self.local_sessions[profile_id]
                
                self.logger.debug(f"Global local session stopped for profile {profile_id}")
                
                return {
                    'status': 'success',
                    'profile_id': profile_id,
                    'session_duration': session_duration,
                    'message': f'Global local session stopped (Duration: {session_duration:.1f}s)'
                }
                
            except Exception as e:
                self.logger.error(f"Error stopping global local session for profile {profile_id}: {e}")
                return {
                    'status': 'failed',
                    'profile_id': profile_id,
                    'error': str(e),
                    'message': 'Failed to stop global local session'
                }
    
    def get_session(self, profile_id: str) -> Optional[Dict[str, Any]]:
        """Get session data for a profile."""
        with self._session_lock:
            return self.local_sessions.get(profile_id)
    
    def has_active_session(self, profile_id: str) -> bool:
        """Check if profile has an active local session."""
        with self._session_lock:
            session = self.local_sessions.get(profile_id)
            return session is not None and session.get('status') == 'active'
    
    def get_driver(self, profile_id: str) -> Optional[webdriver.Chrome]:
        """Get the WebDriver instance for a profile."""
        with self._session_lock:
            session = self.local_sessions.get(profile_id)
            if session and session.get('status') == 'active':
                return session.get('driver')
            return None
    
    def is_session_ready(self, profile_id: str) -> bool:
        """Check if a session is fully ready for automation."""
        with self._session_lock:
            session = self.local_sessions.get(profile_id)
            if not session or session.get('status') != 'active':
                return False
            
            # Check if WebDriver is responsive
            try:
                driver = session.get('driver')
                if driver:
                    # Try a simple command to test responsiveness
                    driver.current_url
                    return True
            except Exception as e:
                self.logger.warning(f"Session {profile_id} not ready: {e}")
                return False
            
            return False
    
    def list_active_sessions(self) -> Dict[str, Dict[str, Any]]:
        """List all active local sessions."""
        with self._session_lock:
            return {
                profile_id: {
                    'start_time': session['start_time'],
                    'duration': time.time() - session['start_time'],
                    'execution_mode': session['execution_mode'],
                    'status': session['status'],
                    'debugger_address': session.get('debugger_address'),
                    'chromium_version': session.get('chromium_version')
                }
                for profile_id, session in self.local_sessions.items()
                if session.get('status') == 'active'
            }
    
    def cleanup_stale_sessions(self, max_age_hours: float = 2.0):
        """Clean up sessions older than max_age_hours."""
        with self._session_lock:
            current_time = time.time()
            max_age_seconds = max_age_hours * 3600
            
            stale_profiles = []
            for profile_id, session in self.local_sessions.items():
                if (current_time - session.get('start_time', current_time)) > max_age_seconds:
                    stale_profiles.append(profile_id)
            
            for profile_id in stale_profiles:
                self.logger.info(f"Cleaning up stale session for profile {profile_id}")
                self.stop_local_session(profile_id, cleanup=False)

    def restart_session_due_to_challenge(self, profile_id: str) -> Dict[str, Any]:
        """
        Restart a session specifically due to persistent Cloudflare challenges.
        This will stop the current session and start a new one to get fresh fingerprint/IP.
        """
        try:
            self.logger.info(f"🔄 Restarting session for profile {profile_id} due to persistent Cloudflare challenge")
            
            # Stop current session
            stop_result = self.stop_local_session(profile_id, cleanup=True)
            if not stop_result.get('success'):
                self.logger.warning(f"⚠️ Could not cleanly stop session for {profile_id}: {stop_result}")
            
            # Wait a moment for cleanup
            time.sleep(3)
            
            # Start new session
            # Note: This would need the gologin_token to be passed in or stored
            self.logger.info(f"🚀 Starting fresh session for profile {profile_id}")
            
            return {
                'success': True,
                'action': 'session_restarted',
                'profile_id': profile_id,
                'reason': 'persistent_cloudflare_challenge',
                'timestamp': time.time()
            }
            
        except Exception as e:
            self.logger.error(f"Error restarting session for {profile_id}: {e}")
            return {
                'success': False,
                'action': 'restart_failed',
                'profile_id': profile_id,
                'error': str(e)
            }
    
    def get_challenge_persistent_sessions(self) -> List[str]:
        """
        Get list of profile IDs that have persistent Cloudflare challenges.
        """
        persistent_profiles = []
        
        for profile_id, session_data in self.local_sessions.items():
            # Check if session has been running for a while but still on challenge page
            if session_data and 'driver' in session_data:
                try:
                    driver = session_data['driver']
                    current_url = driver.current_url
                    page_title = driver.title.lower()
                    
                    # Check if still on challenge page after running for > 2 minutes
                    session_age = time.time() - session_data.get('start_time', 0)
                    if (session_age > 120 and  # Running for more than 2 minutes
                        ('/account/access' in current_url or 'bir dakika' in page_title)):
                        persistent_profiles.append(profile_id)
                        
                except Exception:
                    continue
                    
        return persistent_profiles

# Global singleton instance
global_session_manager = GlobalGoLoginSessionManager() 