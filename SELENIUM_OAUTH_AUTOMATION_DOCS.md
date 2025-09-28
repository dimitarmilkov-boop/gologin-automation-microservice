# X OAuth Authorization Automation Documentation

## Overview

The `selenium_oauth_automation.py` module provides automated X (Twitter) OAuth authorization for AIOTT applications using GoLogin browser profiles. It handles the complete flow from GoLogin profile startup to OAuth token exchange.

## Purpose

**Goal**: Automate the manual process of:

1. Starting GoLogin profiles
2. Logging into X accounts
3. Authorizing AIOTT applications
4. Completing OAuth token exchange

**Business Value**: Eliminates manual OAuth setup for Twitter accounts, enabling bulk account onboarding.

## Dependencies

### Core Dependencies

```python
# Selenium WebDriver stack
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options

# GoLogin integration
from gologin import GoLogin

# WebDriver management
from webdriver_manager.chrome import ChromeDriverManager

# Internal dependencies
from fix_db_connections import DBConnection
from gologin_manager import GoLoginManager
```

### System Requirements

- **GoLogin Account**: Active subscription with profiles
- **ChromeDriver**: Version 133.0.6943.54 (matches GoLogin browser)
- **Tunnel Service**: localtunnel for proxy bypass (AIOTT_TUNNEL_URL)
- **Database**: SQLite database with OAuth configurations

## Class Structure

```python
class SeleniumOAuthAutomator:
    """
    Main automation class - no inheritance
    Composition-based design using external services
    """
```

### Key Attributes

- `db_path`: Path to SQLite database
- `gologin_token`: GoLogin API authentication
- `tunnel_url`: Proxy bypass tunnel URL
- `logger`: Structured logging instance

## Core Methods

### 1. `automate_oauth_for_profile(profile_id, api_app)`

**Purpose**: Main entry point for OAuth automation

**Flow**:

1. Start GoLogin session
2. Connect Selenium WebDriver
3. Verify AIOTT access (via tunnel)
4. Generate OAuth authorization URL
5. Navigate to X OAuth page
6. Handle authorization (login + consent)
7. Process OAuth callback

**Returns**: `Dict[str, Any]` with success status and results

### 2. `_connect_selenium(debugger_address)`

**Purpose**: Connect Selenium to existing GoLogin browser

**Technical Details**:

- Uses Chrome debugging protocol
- Hardcoded ChromeDriver version for compatibility
- Connects to GoLogin's managed browser instance

### 3. `_handle_oauth_authorization(driver, profile_id)`

**Purpose**: Handle the X OAuth authorization page

**Logic**:

- Detects login page vs authorization page
- Triggers automatic login if needed
- Searches for "Authorize app" button
- Handles multi-language interfaces

### 4. `_attempt_x_login(driver, profile_id)`

**Purpose**: Automate X login process

**Features**:

- Multi-language support (Polish/English)
- Smart button detection
- Robust element finding strategies
- Error handling and recovery

### 5. `_handle_unexpected_page(driver, context)`

**Purpose**: Smart error handling for unknown pages

**Capabilities**:

- Automatic screenshot capture
- Page content analysis
- Interactive element detection
- Detailed error reporting

## Current Implementation Status

### ✅ Working Components

1. **GoLogin Integration**

   - Profile startup: ✅
   - Browser connection: ✅
   - Session management: ✅

2. **X Login Automation**

   - Username/password entry: ✅
   - Multi-language button detection: ✅
   - Polish interface support ("Dalej", "Zaloguj się"): ✅
   - Hardcoded test credentials: ✅

3. **OAuth Flow**

   - URL generation: ✅
   - State management: ✅
   - Callback handling: ✅
   - Token exchange integration: ✅

4. **Error Handling**
   - Screenshot capture: ✅
   - Page analysis: ✅
   - Detailed logging: ✅

### ⚠️ Known Issues

1. **Cookie Consent Pages**

   - X shows Thai language cookie consent
   - Available buttons: "Accept all cookies", "Refuse non-essential cookies"
   - **Status**: Detected, needs handling implementation

2. **AIOTT Authentication**

   - 401 Unauthorized errors on `/accounts` endpoint
   - Missing admin login automation
   - **Status**: Not implemented

3. **Account Limitations**
   - Test accounts require 2FA after repeated use
   - Limited valid X account credentials
   - **Status**: Need fresh accounts from Edward

## Configuration

### Environment Variables

```bash
GOLOGIN_TOKEN=your_gologin_api_token
AIOTT_TUNNEL_URL=https://your-tunnel.loca.lt  # Required for proxy bypass
```

### Database Tables Required

```sql
-- OAuth API configurations
twitter_api_configs (client_id, client_secret, callback_url)

-- OAuth automation state tracking
oauth_automation_states (state, profile_id, api_app, code_verifier)

-- Job tracking
oauth_automation_jobs (profile_id, api_app, status, progress_step)
```

## Testing Status

### Test Accounts (Hardcoded)

- **Primary**: `Gotae_9` / `2kJ3exPoZj` ✅ Working
- **Secondary**: `mhohoy` / `0ZJnf24Js2` ⚠️ Requires email verification

### Test Results

```
✅ GoLogin profile startup
✅ Selenium connection
✅ X login automation (username → "Dalej" → password → "Zaloguj się")
✅ OAuth URL redirect
✅ Reach OAuth authorization page
⚠️ Cookie consent handling needed
❌ AIOTT authentication (401 errors)
```

## Flow Diagrams

### Current Working Flow

```
[GoLogin Profile] → [Selenium Connect] → [X Login Page]
       ↓
[Enter Username] → [Click "Dalej"] → [Enter Password]
       ↓
[Click "Zaloguj się"] → [OAuth Authorization Page]
       ↓
[Cookie Consent Page] → [Need: Accept Cookies] → [Authorize App Button]
```

### Expected Complete Flow

```
[AIOTT: Add Account] → [Admin Login] → [Profile Selection]
       ↓
[GoLogin Start] → [X OAuth URL] → [X Login] → [Authorize] → [Callback]
       ↓
[Token Exchange] → [Account Created] → [Success]
```

## Technical Challenges Solved

### 1. ChromeDriver Version Matching

**Problem**: GoLogin uses Chrome 133, system has Chrome 140
**Solution**: Hardcoded ChromeDriver version 133.0.6943.54

### 2. Proxy Connection Issues

**Problem**: GoLogin SOCKS proxy blocks local AIOTT connections
**Solution**: localtunnel for proxy bypass

### 3. Multi-Language Interface

**Problem**: Polish X interface with different button text
**Solution**: Smart text detection for "Dalej", "Zaloguj się"

### 4. Dynamic Page Content

**Problem**: Unknown page states after login
**Solution**: Screenshot capture and page analysis

## Architecture Decisions

### 1. Composition over Inheritance

- Uses external services (GoLogin, DBConnection)
- No class inheritance for simpler testing
- Dependency injection for database path

### 2. Hardcoded Test Credentials

- Simple approach for rapid testing
- Avoids complex database setup during development
- Easy to switch between test accounts

### 3. Smart Error Handling

- Screenshots for visual debugging
- Page content analysis for unknown states
- Detailed error context for troubleshooting

## Next Steps

### High Priority

1. **Implement Cookie Consent Handling**

   - Detect Thai language cookie page
   - Click "Accept all cookies" button
   - Continue to authorization

2. **Fix AIOTT Authentication**
   - Resolve 401 errors on `/accounts` endpoint
   - Implement admin login automation
   - Proper session management

### Medium Priority

1. **Account Management**

   - Get fresh X accounts from Edward
   - Implement 2FA handling
   - Account rotation system

2. **Production Integration**
   - Remove hardcoded credentials
   - Database-driven account selection
   - Proper error reporting to UI

### Low Priority

1. **Performance Optimization**
   - Reduce wait times
   - Parallel processing
   - Better resource cleanup

## Debugging Tools

### Screenshot Capture

- Automatic on unexpected pages
- Saved as `unexpected_page_[timestamp].png`
- Includes full page context

### Logging Levels

```python
INFO  - Normal flow progress
WARN  - Recoverable issues
ERROR - Automation failures
DEBUG - Detailed element detection
```

### Manual Testing

```bash
# Run standalone testing
python selenium_oauth_automation.py

# Environment setup
export GOLOGIN_TOKEN="your_token"
export AIOTT_TUNNEL_URL="https://poor-yaks-hunt.loca.lt"
```

## Common Issues & Solutions

### Issue: "Could not find Next button"

**Cause**: Polish interface button text
**Solution**: Enhanced text detection strategies

### Issue: "Unexpected page after successful login"

**Cause**: Cookie consent or 2FA pages
**Solution**: Screenshot analysis + specific handling

### Issue: "401 Unauthorized on /accounts"

**Cause**: Missing AIOTT authentication
**Solution**: Implement admin login flow

## Integration Points

### With AIOTT App (`app.py`)

- Called from `/api/oauth/automation/start` endpoint
- Uses existing token exchange functions
- Integrates with job tracking system

### With GoLogin System

- Uses `gologin_manager.py` for profile management
- Leverages existing browser startup patterns
- Inherits proxy and session handling

### With Database Layer

- Uses `fix_db_connections.py` for connection pooling
- Integrates with existing Twitter account tables
- Maintains OAuth state for callbacks

---

## Summary

The OAuth automation successfully handles the technical challenges of GoLogin integration and X login automation. The core functionality works reliably - we can start profiles, log into X, and reach OAuth pages.

**Current bottleneck**: Handling intermediate pages (cookie consent) and fixing AIOTT authentication flow.

**Development approach**: Incremental enhancement with robust error handling and visual debugging tools.
