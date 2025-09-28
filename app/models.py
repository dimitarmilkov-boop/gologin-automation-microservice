from sqlalchemy import Column, String, DateTime, Boolean, Text, Integer, JSON
from sqlalchemy.sql import func
from app.database import Base

class Profile(Base):
    __tablename__ = "profiles"

    id = Column(String, primary_key=True)  # GoLogin profile ID
    profile_name = Column(String, unique=True, index=True, nullable=False)
    display_name = Column(String, nullable=True)
    account_id = Column(String, nullable=True)
    proxy = Column(JSON)
    browser_type = Column(String, default="chrome")
    status = Column(String, default="active")
    last_sync = Column(DateTime, server_default=func.now(), onupdate=func.now())
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now())

class AuthorizationSession(Base):
    __tablename__ = "authorization_sessions"

    id = Column(Integer, primary_key=True, index=True)
    profile_id = Column(String, nullable=False)
    profile_name = Column(String, nullable=False)
    api_app = Column(String, nullable=False)
    status = Column(String, default="pending")
    error_code = Column(String)
    error_message = Column(Text)
    result_payload = Column(JSON)
    started_at = Column(DateTime, server_default=func.now())
    completed_at = Column(DateTime)
    request_id = Column(String, index=True)

class ApiKey(Base):
    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, unique=True, index=True, nullable=False)
    name = Column(String)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())
    last_used = Column(DateTime)