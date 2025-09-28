#!/usr/bin/env python3
"""
Basic app test - Test FastAPI without database/services
"""

from fastapi import FastAPI
from app.config import settings

def create_test_app():
    """Create a minimal FastAPI app for testing"""
    
    app = FastAPI(
        title="GoLogin Automation Service - Test",
        description="Basic test without database/services",
        version="1.0.0-test"
    )
    
    @app.get("/")
    async def root():
        return {
            "service": "GoLogin Automation - TEST MODE",
            "status": "operational",
            "version": "1.0.0-test",
            "environment": settings.environment,
            "message": "Basic FastAPI app is working!"
        }
    
    @app.get("/health")
    async def health_check():
        return {
            "status": "healthy",
            "environment": settings.environment,
            "config_loaded": True,
            "database": "skipped",
            "services": "skipped"
        }
    
    @app.get("/config-test")
    async def config_test():
        """Test if configuration is working"""
        return {
            "environment": settings.environment,
            "debug": settings.debug,
            "log_level": settings.log_level,
            "max_concurrent_profiles": settings.max_concurrent_profiles,
            "message": "Configuration is loaded correctly!"
        }
    
    return app

if __name__ == "__main__":
    import uvicorn
    
    print("🚀 Starting basic GoLogin Automation test...")
    print("📋 This test skips database and service initialization")
    print("🌐 Available endpoints:")
    print("   http://localhost:8000/          - Root endpoint")
    print("   http://localhost:8000/health    - Health check")
    print("   http://localhost:8000/config-test - Config test")
    print("   http://localhost:8000/docs      - API documentation")
    
    app = create_test_app()
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info"
    )
