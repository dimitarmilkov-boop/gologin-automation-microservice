from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional
import os

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8000
    environment: str = "development"
    debug: bool = False

    database_url: str
    database_pool_size: int = 20
    database_max_overflow: int = 10

    redis_url: str = "redis://localhost:6379/0"

    gologin_token: str
    gologin_api_url: str = "https://api.gologin.com/browser"
    max_concurrent_profiles: int = 10
    profile_sync_interval: int = 900

    api_secret_key: str
    api_algorithm: str = "HS256"
    api_access_token_expire_minutes: int = 30

    aiott_api_url: str
    aiott_api_key: str
    aiott_status_poll_interval: int = 5
    aiott_status_timeout: int = 120

    # Legacy OAuth fields kept optional so existing .env files remain valid
    aiott1_client_id: Optional[str] = None
    aiott1_client_secret: Optional[str] = None
    aiott1_callback_url: Optional[str] = None
    aiott2_client_id: Optional[str] = None
    aiott2_client_secret: Optional[str] = None
    aiott2_callback_url: Optional[str] = None

    sentry_dsn: Optional[str] = None
    log_level: str = "INFO"
    enable_metrics: bool = True

    browser_timeout: int = 30000
    browser_headless: bool = False

settings = Settings()