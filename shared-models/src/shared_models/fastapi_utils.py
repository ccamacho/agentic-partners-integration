"""FastAPI utilities for shared patterns across services."""

from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Awaitable, Callable, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_database_manager
from .health import HealthChecker
from .logging import configure_logging

logger = configure_logging("fastapi-utils")


async def create_health_check_endpoint(
    service_name: str,
    version: str,
    db: AsyncSession,
    additional_checks: Optional[Dict[str, Callable[..., Any]]] = None,
    custom_health_logic: Optional[
        Callable[[AsyncSession], Awaitable[Dict[str, Any]]]
    ] = None,
) -> Dict[str, Any]:
    """
    Create a standardized health check endpoint for any service.

    Args:
        service_name: Name of the service (e.g., "request-manager")
        version: Version of the service
        db: Database session (provided by FastAPI dependency injection)
        additional_checks: Dict of additional health check functions
        custom_health_logic: Custom health check logic that takes db session

    Returns:
        Health check response dictionary
    """
    checker = HealthChecker(service_name, version)

    try:
        # Perform standard health checks
        result = await checker.perform_health_check(
            db=db, additional_checks=additional_checks
        )

        # Apply custom health logic if provided
        custom_status = {}
        if custom_health_logic:
            try:
                custom_status = await custom_health_logic(db)
            except Exception as e:
                logger.error("Custom health check failed", error=str(e))
                custom_status = {"custom_health": "failed", "error": str(e)}

        # Combine results
        response = {
            "status": result.status,
            "service": service_name,
            "version": version,
            "database_connected": result.database_connected,
            "services": result.services,
            **custom_status,
        }

        return response

    except Exception as e:
        logger.error("Health check failed", service=service_name, error=str(e))
        return {
            "status": "unhealthy",
            "service": service_name,
            "version": version,
            "error": str(e),
        }


@asynccontextmanager
async def create_shared_lifespan(
    service_name: str,
    version: str,
    migration_timeout: int = 300,
    custom_startup: Optional[Callable[[], Awaitable[None]]] = None,
    custom_shutdown: Optional[Callable[[], Awaitable[None]]] = None,
    service_client_init: bool = True,
) -> AsyncGenerator[None, None]:
    """
    Create a standardized FastAPI lifespan manager for services.

    Args:
        service_name: Name of the service (e.g., "request-manager")
        version: Version of the service
        migration_timeout: Timeout for database migration waiting
        custom_startup: Custom startup function to call after standard startup
        custom_shutdown: Custom shutdown function to call before standard shutdown
        service_client_init: Whether to initialize shared service clients

    Yields:
        None (standard lifespan pattern)
    """
    # Startup
    logger.info("Starting service", service=service_name, version=version)

    # Wait for database migration to complete
    db_manager = get_database_manager()
    try:
        migration_ready = await db_manager.wait_for_migration(timeout=migration_timeout)
        if not migration_ready:
            raise Exception("Database migration did not complete within timeout")
        logger.info("Database migration verified and ready")

        # Log database configuration and test connection
        await db_manager.log_database_config()

    except Exception as e:
        logger.error("Failed to verify database migration", error=str(e))
        raise

    if service_client_init:
        logger.debug("Service client initialization skipped")

    # Call custom startup function if provided
    if custom_startup:
        try:
            await custom_startup()
            logger.info("Custom startup completed")
        except Exception as e:
            logger.error("Custom startup failed", error=str(e))
            raise

    logger.info("Service startup completed", service=service_name)

    yield

    # Shutdown
    logger.info("Shutting down service", service=service_name)

    # Call custom shutdown function if provided
    if custom_shutdown:
        try:
            await custom_shutdown()
            logger.info("Custom shutdown completed")
        except Exception as e:
            logger.error("Custom shutdown failed", error=str(e))

    # Close database connections
    await db_manager.close()
    logger.info("Service shutdown completed", service=service_name)
