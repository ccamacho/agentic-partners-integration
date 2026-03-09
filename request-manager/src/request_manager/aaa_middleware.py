"""
AAA Middleware for Request Manager.

Enforces authentication, authorization, and access control for partner agents.
"""

from typing import Dict, Any
import structlog

from shared_models.aaa_service import AAAService
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()


class AAAMiddleware:
    """Middleware for enforcing AAA policies."""

    @staticmethod
    async def get_user_context(
        db: AsyncSession,
        user_email: str
    ) -> Dict[str, Any]:
        """
        Get user context for agent processing.

        Args:
            db: Database session
            user_email: User email

        Returns:
            User context dictionary
        """
        try:
            user = await AAAService.get_user_by_email(db, user_email)

            if not user:
                return {
                    "email": user_email,
                    "role": "user",
                    "status": "unknown",
                    "allowed_agents": []
                }

            return {
                "user_id": str(user.user_id),
                "email": user.primary_email,
                "role": user.role.value if user.role else "user",
                "status": user.status,
                "organization": user.organization,
                "department": user.department,
                "allowed_agents": await AAAService.get_user_allowed_agents(db, user_email),
                "privileges": user.privileges or {}
            }

        except Exception as e:
            logger.error(
                "Failed to get user context",
                user=user_email,
                error=str(e)
            )
            return {
                "email": user_email,
                "role": "user",
                "status": "error",
                "allowed_agents": []
            }
