"""
Domain and Infrastructure Exception Hierarchy
Following DDD patterns for clear error handling
"""

class DomainException(Exception):
    """Base for business logic errors"""
    def __init__(self, message: str, error_code: str = None):
        super().__init__(message)
        self.error_code = error_code or self.__class__.__name__

class InfrastructureException(Exception):
    """Base for technical errors"""
    def __init__(self, message: str, error_code: str = None):
        super().__init__(message)
        self.error_code = error_code or self.__class__.__name__

# Domain Exceptions (Business Logic)
class ProfileNotFoundException(DomainException):
    """Profile doesn't exist in system"""
    def __init__(self, account_id: str):
        super().__init__(
            f"No GoLogin profile found for account: {account_id}",
            "PROFILE_NOT_FOUND"
        )

class AuthorizationTimeoutException(DomainException):
    """OAuth flow took too long"""
    def __init__(self, timeout_seconds: int):
        super().__init__(
            f"Authorization timeout after {timeout_seconds} seconds",
            "AUTH_TIMEOUT"
        )

class ConcurrentProfileLimitException(DomainException):
    """Max 10 profiles already running"""
    def __init__(self):
        super().__init__(
            "Maximum concurrent profiles (10) already running",
            "CONCURRENT_LIMIT"
        )

class TokenExpiredException(DomainException):
    """OAuth token has expired"""
    def __init__(self, account_id: str):
        super().__init__(
            f"Token expired for account: {account_id}",
            "TOKEN_EXPIRED"
        )

class UserDeniedAuthorizationException(DomainException):
    """User denied OAuth authorization"""
    def __init__(self, account_id: str):
        super().__init__(
            f"User denied authorization for account: {account_id}",
            "USER_DENIED"
        )

class InvalidAPIAppException(DomainException):
    """Invalid API app specified"""
    def __init__(self, api_app: str):
        super().__init__(
            f"Invalid API app: {api_app}. Must be AIOTT1, AIOTT2, or AIOTT3",
            "INVALID_API_APP"
        )

# Infrastructure Exceptions (Technical)
class GoLoginAPIException(InfrastructureException):
    """GoLogin API communication failed"""
    def __init__(self, status_code: int, message: str):
        super().__init__(
            f"GoLogin API error ({status_code}): {message}",
            "GOLOGIN_API_ERROR"
        )

class SeleniumConnectionException(InfrastructureException):
    """Cannot connect to browser"""
    def __init__(self, port: int):
        super().__init__(
            f"Cannot connect to browser on port {port}",
            "BROWSER_CONNECTION_FAILED"
        )

class TwitterAPIException(InfrastructureException):
    """Twitter API error"""
    def __init__(self, status_code: int, message: str):
        super().__init__(
            f"Twitter API error ({status_code}): {message}",
            "TWITTER_API_ERROR"
        )

class DatabaseConnectionException(InfrastructureException):
    """Database connection failed"""
    def __init__(self, message: str):
        super().__init__(
            f"Database connection error: {message}",
            "DATABASE_CONNECTION_ERROR"
        )

class BrowserAutomationException(InfrastructureException):
    """Browser automation failed"""
    def __init__(self, message: str):
        super().__init__(
            f"Browser automation error: {message}",
            "BROWSER_AUTOMATION_ERROR"
        )