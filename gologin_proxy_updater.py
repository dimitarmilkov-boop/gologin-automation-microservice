#!/usr/bin/env python3
"""
GoLogin Proxy Updater

Integrates with GoLogin API to automatically update profile proxies
when Royal Proxy rotation occurs due to persistent Cloudflare challenges.
"""

import requests
import logging
import time
from typing import Dict, Any, Optional
from proxy_manager import RoyalProxyManager

class GoLoginProxyUpdater:
    """
    Handles updating GoLogin profile proxies via API when proxy rotation occurs.
    """
    
    def __init__(self, gologin_token: str):
        self.logger = logging.getLogger(__name__)
        self.gologin_token = gologin_token
        self.base_url = "https://api.gologin.com"
        self.headers = {
            'Authorization': f'Bearer {gologin_token}',
            'Content-Type': 'application/json'
        }
    
    def get_profile_proxy(self, profile_id: str) -> Dict[str, Any]:
        """
        Get current proxy configuration for a GoLogin profile.
        
        Args:
            profile_id: GoLogin profile ID
            
        Returns:
            Dict containing proxy configuration or None if error
        """
        try:
            url = f"{self.base_url}/browser/{profile_id}"
            response = requests.get(url, headers=self.headers)
            
            if response.status_code == 200:
                profile_data = response.json()
                proxy_config = profile_data.get('proxy', {})
                
                self.logger.info(f"📥 Retrieved proxy config for {profile_id}: {proxy_config.get('host', 'none')}:{proxy_config.get('port', 'none')}")
                return proxy_config
            else:
                self.logger.error(f"Failed to get profile {profile_id}: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            self.logger.error(f"Error getting profile proxy for {profile_id}: {e}")
            return None
    
    def update_profile_proxy(self, profile_id: str, proxy_config: Dict[str, Any]) -> bool:
        """
        Update proxy configuration for a GoLogin profile.
        
        Args:
            profile_id: GoLogin profile ID
            proxy_config: New proxy configuration
            
        Returns:
            True if successful, False otherwise
        """
        try:
            url = f"{self.base_url}/browser/{profile_id}/proxy"
            
            # Prepare proxy data for GoLogin API
            proxy_data = {
                "mode": "http",  # Royal Proxy uses HTTP
                "host": proxy_config.get('host'),
                "port": int(proxy_config.get('port')),
                "username": proxy_config.get('username'),
                "password": proxy_config.get('password'),
                "customName": f"Royal-{proxy_config.get('country', 'unknown').upper()}-{proxy_config.get('session_id', 'auto')}"
            }
            
            response = requests.patch(url, headers=self.headers, json=proxy_data)
            
            if response.status_code == 200:
                self.logger.info(f"✅ Successfully updated proxy for {profile_id}")
                self.logger.info(f"   🌍 New proxy: {proxy_config.get('country', 'unknown').upper()}")
                self.logger.info(f"   🔗 Session: {proxy_config.get('session_id', 'unknown')}")
                return True
            else:
                self.logger.error(f"Failed to update proxy for {profile_id}: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            self.logger.error(f"Error updating profile proxy for {profile_id}: {e}")
            return False
    
    def rotate_profile_proxy(self, profile_id: str, proxy_manager: RoyalProxyManager, 
                           avoid_countries: list = None) -> Dict[str, Any]:
        """
        Rotate proxy for a GoLogin profile and update it via API.
        
        Args:
            profile_id: GoLogin profile ID
            proxy_manager: RoyalProxyManager instance
            avoid_countries: List of countries to avoid
            
        Returns:
            Dict with rotation result
        """
        try:
            self.logger.info(f"🔄 Starting proxy rotation for GoLogin profile {profile_id}")
            
            # Get current proxy info
            current_proxy = self.get_profile_proxy(profile_id)
            current_country = None
            
            if current_proxy and current_proxy.get('customName'):
                # Try to extract country from custom name (e.g., "Royal-US-abc123")
                custom_name = current_proxy['customName']
                if custom_name.startswith('Royal-'):
                    parts = custom_name.split('-')
                    if len(parts) >= 2:
                        current_country = parts[1].lower()
            
            # Rotate to new proxy
            new_proxy_config = proxy_manager.rotate_proxy(profile_id, avoid_countries)
            
            if not new_proxy_config:
                return {
                    'success': False,
                    'error': 'Failed to generate new proxy configuration'
                }
            
            # Update GoLogin profile with new proxy
            update_success = self.update_profile_proxy(profile_id, new_proxy_config)
            
            if update_success:
                # Test the new proxy
                test_result = proxy_manager.test_proxy(profile_id)
                
                return {
                    'success': True,
                    'action': 'proxy_rotated_and_updated',
                    'old_country': current_country,
                    'new_country': new_proxy_config['country'],
                    'new_session': new_proxy_config['session_id'],
                    'new_ip': test_result.get('ip') if test_result.get('success') else None,
                    'gologin_updated': True
                }
            else:
                return {
                    'success': False,
                    'error': 'Failed to update GoLogin profile proxy',
                    'new_proxy_generated': True
                }
                
        except Exception as e:
            self.logger.error(f"Error rotating profile proxy for {profile_id}: {e}")
            return {
                'success': False,
                'error': str(e)
            }

class IntegratedCloudflareProxyHandler:
    """
    Combines Cloudflare challenge detection with automatic GoLogin proxy rotation.
    """
    
    def __init__(self, gologin_token: str, proxy_manager: RoyalProxyManager):
        self.logger = logging.getLogger(__name__)
        self.gologin_updater = GoLoginProxyUpdater(gologin_token)
        self.proxy_manager = proxy_manager
        self.challenge_history = {}  # profile_id -> attempts
    
    def handle_persistent_challenge(self, profile_id: str) -> Dict[str, Any]:
        """
        Handle persistent Cloudflare challenges with full GoLogin integration.
        
        Args:
            profile_id: GoLogin profile ID
            
        Returns:
            Dict with handling result
        """
        try:
            self.logger.info(f"🚨 INTEGRATED CHALLENGE HANDLER: Processing {profile_id}")
            
            # Track challenge attempts
            if profile_id not in self.challenge_history:
                self.challenge_history[profile_id] = []
            
            self.challenge_history[profile_id].append(time.time())
            
            # Determine countries to avoid based on recent failures
            attempts_last_hour = [t for t in self.challenge_history[profile_id] if time.time() - t < 3600]
            
            # TERMINATION LOGIC: If too many attempts, terminate session instead of continuing
            if len(attempts_last_hour) > 2:  # More than 2 attempts in last hour = terminate
                self.logger.error(f"💀 TERMINATION TRIGGERED for {profile_id}")
                self.logger.error(f"   📊 Attempts in last hour: {len(attempts_last_hour)}")
                self.logger.error(f"   🚫 Exceeded maximum retry limit (2 attempts)")
                self.logger.error(f"   ⏰ Recent attempts: {[time.strftime('%H:%M:%S', time.localtime(t)) for t in attempts_last_hour[-3:]]}")
                
                return {
                    'success': False,
                    'should_terminate': True,
                    'action': 'session_terminated',
                    'termination_reason': f'Too many challenge failures ({len(attempts_last_hour)} attempts in last hour)',
                    'attempts_last_hour': len(attempts_last_hour),
                    'max_attempts_allowed': 2,
                    'profile_id': profile_id
                }
            
            # Continue with normal proxy rotation if under the limit
            if len(attempts_last_hour) > 1:  # 2nd attempt - be more aggressive with country avoidance
                self.logger.warning(f"⚠️ Profile {profile_id} has {len(attempts_last_hour)} challenge attempts in last hour")
                avoid_countries = ['tr', 'ru', 'cn', 'ir', 'pk']  # Avoid problematic regions
            else:
                avoid_countries = ['tr']  # Always avoid Turkey for now
            
            # Rotate proxy and update GoLogin profile
            rotation_result = self.gologin_updater.rotate_profile_proxy(
                profile_id, 
                self.proxy_manager, 
                avoid_countries=avoid_countries
            )
            
            if rotation_result['success']:
                self.logger.info(f"🎉 FULL ROTATION SUCCESS for {profile_id}")
                self.logger.info(f"   🔄 {rotation_result.get('old_country', 'unknown')} → {rotation_result['new_country']}")
                self.logger.info(f"   📍 New IP: {rotation_result.get('new_ip', 'testing...')}")
                self.logger.info(f"   ✅ GoLogin profile updated")
                
                # Critical: Browser session must be restarted to use new proxy
                self.logger.warning(f"🔄 CRITICAL: Browser session for {profile_id} MUST BE RESTARTED to use new proxy")
                self.logger.warning(f"AUTOMATION_ALERT: Profile {profile_id} - proxy updated in GoLogin, restart browser session immediately")
                
                return {
                    'success': True,
                    'action': 'full_proxy_rotation',
                    'requires_restart': True,
                    'old_country': rotation_result.get('old_country'),
                    'new_country': rotation_result['new_country'],
                    'new_ip': rotation_result.get('new_ip'),
                    'attempts_last_hour': len(attempts_last_hour),
                    'should_terminate': False
                }
            else:
                error = rotation_result.get('error', 'Unknown error')
                self.logger.error(f"❌ ROTATION FAILED for {profile_id}: {error}")
                
                return {
                    'success': False,
                    'action': 'rotation_failed',
                    'error': error,
                    'requires_manual_intervention': True,
                    'attempts_last_hour': len(attempts_last_hour),
                    'should_terminate': False
                }
                
        except Exception as e:
            self.logger.error(f"Error in integrated challenge handler for {profile_id}: {e}")
            return {
                'success': False,
                'action': 'error',
                'error': str(e),
                'should_terminate': False
            }

# Example usage
if __name__ == "__main__":
    import os
    logging.basicConfig(level=logging.INFO)
    
    # Example credentials (replace with real ones)
    gologin_token = os.getenv('GOLOGIN_TOKEN', 'your_gologin_token_here')
    
    # Initialize proxy manager
    proxy_manager = RoyalProxyManager(
        username=os.getenv('ROYAL_PROXY_USERNAME'),
        password=os.getenv('ROYAL_PROXY_PASSWORD'),
        api_token=os.getenv('ROYAL_PROXY_API_TOKEN')
    )
    
    # Initialize integrated handler
    handler = IntegratedCloudflareProxyHandler(gologin_token, proxy_manager)
    
    # Test with a profile
    test_profile = "test_profile_id"
    result = handler.handle_persistent_challenge(test_profile)
    
    print(f"Result: {result}") 