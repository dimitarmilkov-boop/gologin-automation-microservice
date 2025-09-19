#!/usr/bin/env python3

import sys
import os
import secrets
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings
from app.database import Base
from app.models import ApiKey
from app.auth import create_api_key

def setup_database():
    print("Setting up GoLogin Automation database...")

    engine = create_engine(settings.database_url)

    print("Creating tables...")
    Base.metadata.create_all(bind=engine)

    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()

    try:
        existing_key = db.query(ApiKey).first()
        if not existing_key:
            master_key = secrets.token_urlsafe(32)
            create_api_key("Master API Key", master_key, db)
            print(f"Master API Key created: {master_key}")
            print("Save this key securely - it won't be shown again!")
        else:
            print("API keys already exist in database")

        print("Database setup completed successfully!")

    except Exception as e:
        print(f"Error setting up database: {str(e)}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    setup_database()