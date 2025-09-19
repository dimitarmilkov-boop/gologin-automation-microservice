#!/usr/bin/env python3

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Test DDD structure imports
def test_ddd_imports():
    try:
        print("Testing DDD structure imports...")

        # Test config
        from app.config import settings
        print("Config imported successfully")

        # Test utils
        from app.utils.exceptions import DomainException, InfrastructureException
        from app.utils.logger import get_logger, setup_logging
        print("Utils imported successfully")

        # Test API layer
        from app.api.routes import router
        from app.api.dependencies import get_gologin_service
        from app.api.responses import success_response, error_response
        print("API layer imported successfully")

        # Test services
        from app.services.gologin_service import GoLoginService
        from app.services.oauth_service import OAuthService
        from app.services.automation import ProfileAutomator, BrowserController
        print("Services imported successfully")

        # Test workers
        from app.services.workers.sync_worker import ProfileSyncWorker
        from app.services.workers.cleanup_worker import CleanupWorker
        from app.services.workers.monitor_worker import MonitorWorker
        print("Workers imported successfully")

        # Test schemas and models
        from app.schemas import AuthorizationRequest, AuthorizationResponse
        from app.models import Profile, AuthorizationSession
        print("Schemas and models imported successfully")

        print("All DDD structure imports successful!")
        return True

    except Exception as e:
        print(f"DDD import error: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

# Test basic functionality
def test_basic_functionality():
    try:
        print("\nTesting basic functionality...")

        from app.schemas import AuthorizationRequest, ApiApp
        from app.utils.exceptions import ProfileNotFoundException
        from app.utils.logger import setup_logging, get_logger

        # Test schema validation
        request = AuthorizationRequest(
            account_id="testuser",
            api_app=ApiApp.AIOTT1
        )
        print(f"Schema validation successful: {request.account_id}, {request.api_app}")

        # Test exception hierarchy
        exc = ProfileNotFoundException("test_user")
        print(f"Exception created: {exc.error_code} - {str(exc)}")

        # Test logger setup (without actual initialization)
        logger = get_logger(__name__)
        print("Logger created successfully")

        # Test config loading
        from app.config import settings
        print(f"Config loaded: max_profiles={settings.max_concurrent_profiles}")

        return True

    except Exception as e:
        print(f"Functionality test error: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("GoLogin Automation - DDD Structure Tests")
    print("=" * 50)

    success = True
    success &= test_ddd_imports()
    success &= test_basic_functionality()

    if success:
        print("\nAll DDD structure tests passed!")
        print("Ready to test the restructured application!")
    else:
        print("\nSome tests failed!")
        sys.exit(1)