"""
Retry decorators for resilient external API calls
Following tenacity library patterns but with structured logging
"""

import asyncio
import functools
from typing import Callable, Union, Type, Tuple
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import httpx

from app.utils.logger import get_logger
from app.utils.exceptions import GoLoginAPIException, TwitterAPIException

logger = get_logger(__name__)

# Common retry configurations
GOLOGIN_RETRY = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
    reraise=True
)

TWITTER_RETRY = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=20),
    retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
    reraise=True
)

BROWSER_RETRY = retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=2, max=5),
    reraise=True
)

def retry_gologin_api(func: Callable):
    """Retry decorator for GoLogin API calls"""
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await GOLOGIN_RETRY(func)(*args, **kwargs)
        except Exception as e:
            logger.error(
                "gologin_api.retry_exhausted",
                function=func.__name__,
                error=str(e),
                exc_info=True
            )
            raise GoLoginAPIException(500, f"GoLogin API retry exhausted: {str(e)}")
    return wrapper

def retry_twitter_api(func: Callable):
    """Retry decorator for Twitter API calls"""
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await TWITTER_RETRY(func)(*args, **kwargs)
        except Exception as e:
            logger.error(
                "twitter_api.retry_exhausted",
                function=func.__name__,
                error=str(e),
                exc_info=True
            )
            raise TwitterAPIException(500, f"Twitter API retry exhausted: {str(e)}")
    return wrapper

def retry_browser_action(func: Callable):
    """Retry decorator for browser automation actions"""
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await BROWSER_RETRY(func)(*args, **kwargs)
        except Exception as e:
            logger.error(
                "browser.retry_exhausted",
                function=func.__name__,
                error=str(e),
                exc_info=True
            )
            raise
    return wrapper

def with_timeout(seconds: float):
    """Decorator to add timeout to async functions"""
    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await asyncio.wait_for(func(*args, **kwargs), timeout=seconds)
            except asyncio.TimeoutError:
                logger.error(
                    "function.timeout",
                    function=func.__name__,
                    timeout_seconds=seconds
                )
                raise
        return wrapper
    return decorator