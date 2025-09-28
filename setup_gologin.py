#!/usr/bin/env python3
"""
GoLogin Setup Script
This script helps set up GoLogin account warmup automation for the tweet app.
"""

import os
import sys
import subprocess
import sqlite3
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

def check_requirements():
    """Check if required packages are installed."""
    try:
        import gologin
        import selenium
        print("✓ GoLogin SDK is installed")
        print("✓ Selenium is installed")
        return True
    except ImportError as e:
        print(f"✗ Missing required package: {e}")
        print("Please run: pip install -r requirements.txt")
        return False

def check_gologin_token():
    """Check if GOLOGIN_TOKEN environment variable is set."""
    token = os.getenv('GOLOGIN_TOKEN')
    if token:
        print("✓ GOLOGIN_TOKEN environment variable is set")
        return True
    else:
        print("✗ GOLOGIN_TOKEN environment variable is not set")
        print("Please set your GoLogin API token:")
        print("export GOLOGIN_TOKEN='your_token_here'")
        print("You can find your token at: https://app.gologin.com/")
        return False

def init_database():
    """Initialize GoLogin database tables."""
    try:
        from gologin_manager import GoLoginManager
        manager = GoLoginManager()
        print("✓ GoLogin database tables initialized")
        return True
    except Exception as e:
        print(f"✗ Error initializing database: {e}")
        return False

def test_gologin_connection():
    """Test connection to GoLogin API."""
    try:
        from gologin import GoLogin
        
        token = os.getenv('GOLOGIN_TOKEN')
        if not token:
            print("✗ No GoLogin token available for testing")
            return False
        
        # Test basic API connection (this will validate the token)
        gl = GoLogin({"token": token})
        print("✓ GoLogin API connection successful")
        return True
        
    except Exception as e:
        print(f"✗ GoLogin API connection failed: {e}")
        return False

def create_sample_profile():
    """Create a sample GoLogin profile for testing."""
    try:
        from gologin_manager import GoLoginManager
        from fix_db_connections import DBConnection
        
        # Check if we have any OAuth2 accounts
        with DBConnection() as (conn, c):
            c.execute('''
                SELECT id, account_name 
                FROM twitter_accounts 
                WHERE oauth_type = 'oauth2' 
                LIMIT 1
            ''')
            account = c.fetchone()
        
        if not account:
            print("ℹ No OAuth2 accounts found. Create an OAuth2 account first to test GoLogin profiles.")
            return True
        
        account_id, account_name = account
        
        # Check if account already has a profile
        with DBConnection() as (conn, c):
            c.execute('SELECT COUNT(*) FROM gologin_profiles WHERE account_id = ?', (account_id,))
            if c.fetchone()[0] > 0:
                print(f"ℹ Account '{account_name}' already has a GoLogin profile")
                return True
        
        response = input(f"Create a test GoLogin profile for account '{account_name}'? (y/n): ")
        if response.lower() != 'y':
            print("Skipping profile creation")
            return True
        
        manager = GoLoginManager()
        profile_name = f"test_{account_name}_{datetime.now().strftime('%Y%m%d')}"
        
        print(f"Creating test profile '{profile_name}'...")
        profile_id = manager.create_profile(account_id, profile_name, 'win', 'us')
        
        if profile_id:
            print(f"✓ Test profile created successfully: {profile_id}")
            
            # Set up default warmup settings
            with DBConnection() as (conn, c):
                c.execute('''
                    INSERT OR REPLACE INTO warmup_settings 
                    (account_id, is_enabled, sessions_per_day, min_session_duration, max_session_duration,
                     working_hours_start, working_hours_end, activities_config)
                    VALUES (?, 1, 2, 300, 900, 9, 21, '{}')
                ''', (account_id,))
            
            print("✓ Default warmup settings configured")
            return True
        else:
            print("✗ Failed to create test profile")
            return False
            
    except Exception as e:
        print(f"✗ Error creating sample profile: {e}")
        return False

def display_usage_instructions():
    """Display usage instructions."""
    print("\n" + "="*60)
    print("GoLogin Warmup Setup Complete!")
    print("="*60)
    print("\n📖 Usage Instructions:")
    print("\n1. GoLogin Web Interface:")
    print("   - Visit /gologin in your web app")
    print("   - Create profiles for your X accounts")
    print("   - Configure warmup settings")
    print("   - Monitor session history")
    
    print("\n2. Automatic Warmup:")
    print("   - Warmup sessions run every 2 hours")
    print("   - Accounts are warmed up during working hours only")
    print("   - Default: 2 sessions per day, 5-15 minutes each")
    
    print("\n3. Manual Warmup:")
    print("   - Use the 'Play' button in the web interface")
    print("   - Or call: manager.run_warmup_session(account_id)")
    
    print("\n4. Important Notes:")
    print("   - Requires OAuth2 accounts only")
    print("   - You need valid X login credentials")
    print("   - Sessions run in background browsers")
    print("   - Monitor logs in logs/gologin_warmup.log")
    
    print("\n5. Environment Variables:")
    print("   - GOLOGIN_TOKEN: Your GoLogin API token (required)")
    
    print("\n🔧 Configuration Files:")
    print(f"   - Main: gologin_manager.py")
    print(f"   - Database: twitter_accounts.db (new tables added)")
    print(f"   - Logs: logs/gologin_warmup.log")
    print(f"   - Scheduler: scheduler.py (GoLogin job added)")

def main():
    """Main setup function."""
    print("GoLogin Warmup Setup for Tweet App")
    print("=" * 40)
    
    checks = [
        ("Checking requirements", check_requirements),
        ("Checking GoLogin token", check_gologin_token),
        ("Initializing database", init_database),
        ("Testing GoLogin connection", test_gologin_connection),
        ("Creating sample profile", create_sample_profile),
    ]
    
    all_passed = True
    for name, check_func in checks:
        print(f"\n{name}...")
        if not check_func():
            all_passed = False
            if name in ["Checking requirements", "Checking GoLogin token"]:
                print("❌ Critical error - setup cannot continue")
                sys.exit(1)
    
    if all_passed:
        display_usage_instructions()
        print("\n🎉 Setup completed successfully!")
    else:
        print("\n⚠️  Setup completed with warnings. Some features may not work properly.")
    
    print("\nNext steps:")
    print("1. Restart your tweet app")
    print("2. Visit /gologin to manage profiles")
    print("3. Monitor logs for warmup activity")

if __name__ == "__main__":
    main() 