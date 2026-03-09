"""Main FastAPI application for Request Manager."""

import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from shared_models import (
    configure_logging,
    create_health_check_endpoint,
    create_shared_lifespan,
    get_db_session_dependency,
)
from shared_models.models import ErrorResponse
from sqlalchemy.ext.asyncio import AsyncSession
from . import __version__
from .communication_strategy import (
    UnifiedRequestProcessor,
    get_communication_strategy,
)
from .credential_service import CredentialService
from .schemas import HealthCheck

# Configure structured logging
SERVICE_NAME = "request-manager"
logger = configure_logging(SERVICE_NAME)


async def _session_cleanup_task() -> None:
    """Background task to periodically clean up expired and inactive sessions."""
    import asyncio

    from shared_models import get_database_manager

    cleanup_interval_hours = int(os.getenv("SESSION_CLEANUP_INTERVAL_HOURS", "24"))
    cleanup_interval_seconds = cleanup_interval_hours * 3600
    inactive_session_retention_days = int(
        os.getenv("INACTIVE_SESSION_RETENTION_DAYS", "30")
    )

    logger.info(
        "Starting session cleanup task",
        cleanup_interval_hours=cleanup_interval_hours,
        inactive_session_retention_days=inactive_session_retention_days,
    )

    while True:
        try:
            await asyncio.sleep(cleanup_interval_seconds)

            db_manager = get_database_manager()
            async with db_manager.get_session() as db:
                from .database_utils import (
                    delete_inactive_sessions,
                    expire_old_sessions,
                )

                # First, expire sessions that have passed their expiration time
                expired_count = await expire_old_sessions(db)

                # Then, delete inactive sessions older than retention period
                deleted_count = await delete_inactive_sessions(
                    db, older_than_days=inactive_session_retention_days
                )

                if expired_count > 0 or deleted_count > 0:
                    logger.info(
                        "Session cleanup completed",
                        expired_count=expired_count,
                        deleted_count=deleted_count,
                    )

        except asyncio.CancelledError:
            logger.info("Session cleanup task cancelled")
            break
        except Exception as e:
            logger.error(
                "Error in session cleanup task",
                error=str(e),
                error_type=type(e).__name__,
            )
            # Continue running even on error
            await asyncio.sleep(60)  # Wait 1 minute before retrying on error


async def _request_manager_startup() -> None:
    """Custom startup logic for Request Manager."""
    import asyncio

    # Initialize unified processor
    global unified_processor
    communication_strategy = get_communication_strategy()

    unified_processor = UnifiedRequestProcessor(communication_strategy)
    logger.info(
        "Initialized unified request processor",
        strategy_type=type(communication_strategy).__name__,
    )

    # Start session cleanup background task
    asyncio.create_task(_session_cleanup_task())
    logger.info("Started session cleanup background task")


# Create lifespan using shared utility with custom startup
def lifespan(app: FastAPI) -> Any:
    return create_shared_lifespan(
        service_name="request-manager",
        version=__version__,
        custom_startup=_request_manager_startup,
    )


# Create FastAPI application
app = FastAPI(
    title="Partner Agent Request Manager",
    description="Request Management Layer for Partner Agent Integration",
    version=__version__,
    lifespan=lifespan,
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include authentication endpoints
from .auth_endpoints import router as auth_router
app.include_router(auth_router)

# Include ADK (Agent Development Kit) web UI compatible endpoints
from .adk_endpoints import router as adk_router
app.include_router(adk_router)


# Add credential management middleware
@app.middleware("http")
async def credential_context_middleware(request: Request, call_next):
    """
    Middleware to extract and store credentials in request context.

    This allows credentials to be accessed anywhere in the call stack
    without passing them through function parameters.
    """
    try:
        # Extract authorization header
        auth_header = request.headers.get("Authorization", "")
        if auth_header:
            CredentialService.set_token(auth_header)

        # Extract user_id if available from query params or headers
        user_id = request.headers.get("X-User-ID") or request.query_params.get("user_id")
        if user_id:
            CredentialService.set_user_id(user_id)

        # Extract session_id if available
        session_id = request.headers.get("X-Session-ID") or request.query_params.get("session_id")
        if session_id:
            CredentialService.set_session_id(session_id)

        # Process request
        response = await call_next(request)

        return response
    finally:
        # Always clean up credentials after request
        CredentialService.clear_credentials()


# Initialize components
unified_processor: Optional[UnifiedRequestProcessor] = None


@app.get("/health")
async def health_check() -> Dict[str, Any]:
    """Health check endpoint - lightweight without database dependency."""
    return {
        "status": "healthy",
        "service": "request-manager",
        "version": __version__,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/health/detailed", response_model=HealthCheck)
async def detailed_health_check(
    db: AsyncSession = Depends(get_db_session_dependency),
) -> HealthCheck:
    """Detailed health check with database dependency for monitoring."""
    result = await create_health_check_endpoint(
        service_name="request-manager",
        version=__version__,
        db=db,
        additional_checks={},
    )

    return HealthCheck(
        status=result["status"],
        database_connected=result["database_connected"],
        services=result["services"],
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Handle HTTP exceptions."""
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(  # type: ignore
            error=exc.detail,
            error_code=f"HTTP_{exc.status_code}",
        ).model_dump(mode="json"),
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle general exceptions."""
    logger.error("Unhandled exception", error=str(exc), path=str(request.url))

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=ErrorResponse(  # type: ignore
            error="Internal server error",
            error_code="INTERNAL_ERROR",
        ).model_dump(mode="json"),
    )


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8080"))
    host = os.getenv("HOST", "0.0.0.0")

    uvicorn.run(
        "request_manager.main:app",
        host=host,
        port=port,
        reload=os.getenv("RELOAD", "false").lower() == "true",
        log_level=os.getenv("LOG_LEVEL", "INFO").lower(),
    )

