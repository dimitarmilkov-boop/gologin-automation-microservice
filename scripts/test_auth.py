#!/usr/bin/env python3

import sys
import os
import asyncio
import json
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.authorization import AuthorizationService
from app.services.profile_manager import ProfileManager
from app.database import SessionLocal
from app.schemas import ApiApp

async def test_authorization_flow():
    print("Testing GoLogin Authorization Flow")
    print("=" * 50)

    db = SessionLocal()
    profile_manager = ProfileManager()

    try:
        await profile_manager.initialize()
        print("✓ Profile manager initialized")

        await profile_manager.sync_profiles()
        print("✓ Profiles synced")

        auth_service = AuthorizationService(profile_manager, db)

        test_account = "testuser123"  # Replace with actual test account
        test_app = ApiApp.AIOTT1

        print(f"\nTesting authorization for: {test_account}")
        print(f"API App: {test_app}")

        result = await auth_service.authorize_account(
            account_id=test_account,
            api_app=test_app,
            force_reauth=True
        )

        print(f"\nAuthorization Result:")
        print(json.dumps(result, indent=2, default=str))

        if result["status"] == "success":
            print("✓ Authorization successful!")

            status_check = await auth_service.check_authorization_status(test_account, test_app.value)
            print(f"\nStatus Check:")
            print(json.dumps(status_check, indent=2, default=str))

        else:
            print("✗ Authorization failed!")
            print(f"Error: {result.get('message', 'Unknown error')}")

    except Exception as e:
        print(f"✗ Test failed with error: {str(e)}")
        import traceback
        traceback.print_exc()

    finally:
        await profile_manager.cleanup()
        db.close()
        print("\n✓ Cleanup completed")

if __name__ == "__main__":
    asyncio.run(test_authorization_flow())