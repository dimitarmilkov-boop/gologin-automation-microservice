from sqlalchemy import Column, String, DateTime, Boolean, Text, Integer, JSON
from sqlalchemy.sql import func
from app.database import Base

class Profile(Base):
    __tablename__ = "profiles"

    id = Column(String, primary_key=True)
    account_id = Column(String, unique=True, index=True, nullable=False)
    name = Column(String)
    proxy = Column(JSON)
    browser_type = Column(String, default="chrome")
    status = Column(String, default="active")
    last_sync = Column(DateTime, server_default=func.now())
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now())

class AuthorizationSession(Base):
    __tablename__ = "authorization_sessions"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(String, index=True, nullable=False)
    api_app = Column(String, nullable=False)
    status = Column(String, default="pending")
    oauth_token = Column(Text)
    oauth_token_secret = Column(Text)
    refresh_token = Column(Text)
    scopes = Column(JSON)
    error_code = Column(String)
    error_message = Column(Text)
    started_at = Column(DateTime, server_default=func.now())
    completed_at = Column(DateTime)
    profile_id = Column(String)
    request_ip = Column(String)

class ApiKey(Base):
    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, unique=True, index=True, nullable=False)
    name = Column(String)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())
    last_used = Column(DateTime)