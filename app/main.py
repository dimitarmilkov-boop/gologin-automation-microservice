from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import uvicorn
import asyncio
import sentry_sdk
from sentry_sdk.integrations.asgi import SentryAsgiMiddleware

from app.config import settings
from app.api.routes import router as api_router
from app.database import engine, Base
from app.services.gologin_service import GoLoginService
from app.services.oauth_service import OAuthService
from app.services.automation import ProfileAutomator
from app.services.workers.sync_worker import ProfileSyncWorker
from app.services.workers.cleanup_worker import CleanupWorker
from app.services.workers.monitor_worker import MonitorWorker
from app.utils.logger import setup_logging, get_logger, RequestIDMiddleware

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Setup structured logging
    setup_logging(settings.log_level, json_output=(settings.environment == "production"))
    logger = get_logger(__name__)

    logger.info(
        "service.starting",
        version="1.0.0",
        environment=settings.environment
    )

    # Create database tables
    Base.metadata.create_all(bind=engine)

    # Initialize services
    gologin_service = GoLoginService()
    await gologin_service.initialize()
    app.state.gologin_service = gologin_service

    oauth_service = OAuthService()
    app.state.oauth_service = oauth_service

    profile_automator = ProfileAutomator(gologin_service, oauth_service)
    app.state.profile_automator = profile_automator

    # Initialize background workers
    background_tasks = []

    # Profile sync worker
    sync_worker = ProfileSyncWorker(gologin_service)
    background_tasks.append(asyncio.create_task(sync_worker.run()))
    app.state.sync_worker = sync_worker

    # Cleanup worker
    cleanup_worker = CleanupWorker()
    background_tasks.append(asyncio.create_task(cleanup_worker.run()))
    app.state.cleanup_worker = cleanup_worker

    # Monitor worker
    monitor_worker = MonitorWorker()
    background_tasks.append(asyncio.create_task(monitor_worker.run()))
    app.state.monitor_worker = monitor_worker

    app.state.background_tasks = background_tasks

    logger.info(
        "service.started",
        background_workers=len(background_tasks),
        max_concurrent_profiles=settings.max_concurrent_profiles
    )

    yield

    logger.info("service.shutting_down")

    # Stop workers
    sync_worker.stop()
    cleanup_worker.stop()
    monitor_worker.stop()

    # Cancel background tasks
    for task in background_tasks:
        task.cancel()

    # Wait for tasks to complete
    await asyncio.gather(*background_tasks, return_exceptions=True)

    # Cleanup services
    await gologin_service.cleanup()

    logger.info("service.shutdown_complete")

app = FastAPI(
    title="GoLogin Automation Service",
    description="Automated Twitter/X OAuth reauthorization service using GoLogin profiles",
    version="1.0.0",
    lifespan=lifespan
)

# Add request ID middleware for correlation
app.add_middleware(RequestIDMiddleware)

if settings.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        traces_sample_rate=0.1,
    )
    app.add_middleware(SentryAsgiMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://thefeedwire.com", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")

@app.get("/")
async def root():
    return {
        "service": "GoLogin Automation",
        "status": "operational",
        "version": "1.0.0",
        "environment": settings.environment
    }

if __name__ == "__main__":
    # Setup logging before starting
    setup_logging(settings.log_level, json_output=(settings.environment == "production"))

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_config=None  # Use our custom logging
    )