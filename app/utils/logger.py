"""
Structured JSON Logging with Correlation IDs
Following DDD guide specifications
"""

import structlog
import uuid
import sys
from contextvars import ContextVar
from typing import Any, Dict
from datetime import datetime
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings

# Context variable for request tracking
request_id_var: ContextVar[str] = ContextVar('request_id', default='')

def setup_logging(log_level: str = None, json_output: bool = True):
    """Configure structured logging"""

    level = log_level or settings.log_level

    processors = [
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        add_request_id,  # Custom processor
        add_app_context,  # Custom processor
    ]

    if json_output and settings.environment == "production":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

def add_request_id(logger, method_name, event_dict):
    """Add request ID to all log entries"""
    request_id = request_id_var.get()
    if request_id:
        event_dict['request_id'] = request_id
    return event_dict

def add_app_context(logger, method_name, event_dict):
    """Add application context to logs"""
    event_dict['service'] = 'gologin-automation'
    event_dict['environment'] = settings.environment
    return event_dict

def get_logger(name: str = None) -> structlog.BoundLogger:
    """Get a configured logger instance"""
    return structlog.get_logger(name)

class RequestIDMiddleware(BaseHTTPMiddleware):
    """Middleware to add request ID to all requests"""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get('X-Request-ID', str(uuid.uuid4()))
        request_id_var.set(request_id)

        response = await call_next(request)
        response.headers['X-Request-ID'] = request_id
        return response

# Domain event logging helpers
def log_authorization_started(logger: structlog.BoundLogger,
                             profile_id: str,
                             account_id: str,
                             api_app: str):
    """Log authorization start event"""
    logger.info(
        "authorization.started",
        profile_id=profile_id,
        account_id=account_id,
        api_app=api_app
    )

def log_authorization_completed(logger: structlog.BoundLogger,
                               profile_id: str,
                               account_id: str,
                               duration_seconds: float):
    """Log authorization completion event"""
    logger.info(
        "authorization.completed",
        profile_id=profile_id,
        account_id=account_id,
        duration_seconds=duration_seconds
    )

def log_authorization_failed(logger: structlog.BoundLogger,
                            profile_id: str,
                            account_id: str,
                            error: str,
                            error_code: str = None):
    """Log authorization failure event"""
    logger.error(
        "authorization.failed",
        profile_id=profile_id,
        account_id=account_id,
        error=error,
        error_code=error_code,
        exc_info=True
    )

def log_profile_sync_completed(logger: structlog.BoundLogger,
                              profiles_synced: int,
                              new_profiles: int,
                              updated_profiles: int):
    """Log profile sync completion"""
    logger.info(
        "profile_sync.completed",
        profiles_synced=profiles_synced,
        new_profiles=new_profiles,
        updated_profiles=updated_profiles
    )

def log_gologin_api_call(logger: structlog.BoundLogger,
                        endpoint: str,
                        profile_id: str = None,
                        status_code: int = None,
                        duration_ms: float = None):
    """Log GoLogin API calls"""
    logger.debug(
        "gologin_api.call",
        endpoint=endpoint,
        profile_id=profile_id,
        status_code=status_code,
        duration_ms=duration_ms
    )

def log_browser_action(logger: structlog.BoundLogger,
                      action: str,
                      profile_id: str,
                      success: bool,
                      details: str = None):
    """Log browser automation actions"""
    logger.debug(
        "browser.action",
        action=action,
        profile_id=profile_id,
        success=success,
        details=details
    )