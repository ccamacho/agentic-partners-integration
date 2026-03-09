"""Shared health check utilities for all services."""

from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()


class HealthCheckResult:
    """Health check result with detailed status information."""

    def __init__(
        self,
        status: str = "healthy",
        service_name: str = "unknown",
        version: str = "0.1.0",
        database_connected: bool = False,
        services: Dict[str, str] | None = None,
    ):
        self.status = status
        self.service_name = service_name
        self.version = version
        self.database_connected = database_connected
        self.services = services or {}
        self.timestamp = datetime.now(timezone.utc)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON response."""
        return {
            "status": self.status,
            "service": self.service_name,
            "version": self.version,
            "timestamp": self.timestamp.isoformat(),
            "database_connected": self.database_connected,
            "services": self.services,
        }


class HealthChecker:
    """Shared health check utility for all services."""

    def __init__(self, service_name: str, version: str = "0.1.0"):
        self.service_name = service_name
        self.version = version

    async def check_database(self, db: AsyncSession) -> bool:
        """Check database connectivity."""
        try:
            await db.execute(text("SELECT 1"))
            logger.debug("Database health check passed")
            return True
        except Exception as e:
            logger.error("Database health check failed", error=str(e))
            return False

    async def perform_health_check(
        self,
        db: Optional[AsyncSession] = None,
        additional_checks: Dict[str, Callable[[], Any]] | None = None,
    ) -> HealthCheckResult:
        """Perform comprehensive health check."""
        logger.debug("Starting health check", service=self.service_name)

        # Check database
        database_connected = False
        if db:
            database_connected = await self.check_database(db)

        # Run additional custom checks
        services = {}
        if additional_checks:
            for service_name, check_func in additional_checks.items():
                try:
                    result = await check_func()
                    services[service_name] = "healthy" if bool(result) else "unhealthy"
                except Exception as e:
                    services[service_name] = f"error: {str(e)}"
                    logger.error(
                        "Additional health check failed",
                        service=service_name,
                        error=str(e),
                    )

        status = "healthy" if database_connected else "degraded"

        result = HealthCheckResult(
            status=status,
            service_name=self.service_name,
            version=self.version,
            database_connected=database_connected,
            services=services,
        )

        logger.debug(
            "Health check completed",
            service=self.service_name,
            status=status,
            database_connected=database_connected,
        )

        return result


# Convenience function for simple health checks
async def simple_health_check(
    service_name: str,
    version: str = "0.1.0",
    db: Optional[AsyncSession] = None,
) -> Dict[str, Any]:
    """Simple health check for basic services."""
    checker = HealthChecker(service_name, version)
    result = await checker.perform_health_check(db=db)
    return result.to_dict()
