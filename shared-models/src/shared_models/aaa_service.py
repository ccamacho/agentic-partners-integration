"""
AAA (Authentication, Authorization, Accounting) Service.

Provides centralized authorization and access control for partner agents.
"""

from typing import Optional, List, Dict, Any
from enum import Enum

import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from .models import User, UserRole

logger = structlog.get_logger()


class AgentAccessLevel(str, Enum):
    """Agent access levels for authorization."""
    FULL = "full"  # Can use all agents
    RESTRICTED = "restricted"  # Can only use allowed_agents list
    NONE = "none"  # Cannot use any agents


class AAAService:
    """Service for managing authentication, authorization, and accounting."""

    # Default agent permissions by role
    ROLE_AGENT_PERMISSIONS = {
        UserRole.ADMIN: AgentAccessLevel.FULL,
        UserRole.MANAGER: AgentAccessLevel.FULL,
        UserRole.ENGINEER: AgentAccessLevel.RESTRICTED,
        UserRole.SUPPORT_STAFF: AgentAccessLevel.RESTRICTED,
        UserRole.USER: AgentAccessLevel.RESTRICTED,
    }

    # Default allowed agents by role (when restricted)
    DEFAULT_ALLOWED_AGENTS = {
        UserRole.ADMIN: [],  # Full access
        UserRole.MANAGER: [],  # Full access
        UserRole.ENGINEER: ["software-support", "network-support"],
        UserRole.SUPPORT_STAFF: ["software-support", "network-support"],
        UserRole.USER: ["software-support"],  # Limited to software support only
    }

    @staticmethod
    async def get_user_by_email(
        db: AsyncSession,
        email: str
    ) -> Optional[User]:
        """
        Get user by email address.

        Args:
            db: Database session
            email: User email address

        Returns:
            User object or None if not found
        """
        try:
            stmt = select(User).where(User.primary_email == email)
            result = await db.execute(stmt)
            return result.scalar_one_or_none()

        except Exception as e:
            logger.error(
                "Failed to get user by email",
                email=email,
                error=str(e)
            )
            return None

    @staticmethod
    async def get_or_create_user(
        db: AsyncSession,
        email: str,
        role: UserRole = UserRole.USER,
        organization: Optional[str] = None,
        department: Optional[str] = None
    ) -> Optional[User]:
        """
        Get existing user or create new one with default permissions.

        Args:
            db: Database session
            email: User email address
            role: User role (default: USER)
            organization: User organization
            department: User department

        Returns:
            User object
        """
        try:
            # Try to get existing user
            user = await AAAService.get_user_by_email(db, email)

            if user:
                logger.debug("Found existing user", email=email, role=user.role)
                return user

            # Create new user with default permissions
            default_agents = AAAService.DEFAULT_ALLOWED_AGENTS.get(role, [])

            user = User(
                primary_email=email,
                role=role.value if isinstance(role, UserRole) else role,
                privileges={},
                allowed_agents=default_agents,
                status="active",
                organization=organization,
                department=department
            )

            db.add(user)
            await db.commit()
            await db.refresh(user)

            logger.info(
                "Created new user",
                email=email,
                role=role,
                allowed_agents=default_agents
            )

            return user

        except Exception as e:
            logger.error(
                "Failed to get or create user",
                email=email,
                error=str(e)
            )
            await db.rollback()
            return None

    @staticmethod
    async def check_agent_access(
        db: AsyncSession,
        user_email: str,
        agent_name: str
    ) -> tuple[bool, Optional[str]]:
        """
        Check if user has access to specific agent.

        Args:
            db: Database session
            user_email: User email address
            agent_name: Agent name to check access for

        Returns:
            Tuple of (has_access: bool, reason: Optional[str])
        """
        try:
            # Get user
            user = await AAAService.get_user_by_email(db, user_email)

            if not user:
                return False, f"User not found: {user_email}"

            # Check if user is active
            if user.status != "active":
                return False, f"User account is {user.status}"

            # Get user's access level
            access_level = AAAService.ROLE_AGENT_PERMISSIONS.get(
                user.role,
                AgentAccessLevel.NONE
            )

            # Full access - allow all agents
            if access_level == AgentAccessLevel.FULL:
                logger.debug(
                    "User has full agent access",
                    user=user_email,
                    role=user.role,
                    agent=agent_name
                )
                return True, None

            # No access
            if access_level == AgentAccessLevel.NONE:
                return False, f"User role '{user.role}' has no agent access"

            # Restricted access - check allowed agents list
            allowed_agents = user.allowed_agents or []

            # Check if agent is in user's allowed list
            if agent_name in allowed_agents:
                logger.debug(
                    "Agent access granted",
                    user=user_email,
                    role=user.role,
                    agent=agent_name
                )
                return True, None

            # Check if any wildcard matches
            for pattern in allowed_agents:
                if pattern.endswith("*") and agent_name.startswith(pattern[:-1]):
                    logger.debug(
                        "Agent access granted via wildcard",
                        user=user_email,
                        pattern=pattern,
                        agent=agent_name
                    )
                    return True, None

            logger.warning(
                "Agent access denied",
                user=user_email,
                role=user.role,
                agent=agent_name,
                allowed_agents=allowed_agents
            )

            return False, (
                f"Agent '{agent_name}' not in allowed list for role '{user.role}'. "
                f"Allowed agents: {', '.join(allowed_agents) if allowed_agents else 'none'}"
            )

        except Exception as e:
            logger.error(
                "Failed to check agent access",
                user=user_email,
                agent=agent_name,
                error=str(e)
            )
            return False, f"Error checking access: {str(e)}"

    @staticmethod
    async def get_user_allowed_agents(
        db: AsyncSession,
        user_email: str
    ) -> List[str]:
        """
        Get list of agents the user can access.

        Args:
            db: Database session
            user_email: User email address

        Returns:
            List of allowed agent names
        """
        try:
            user = await AAAService.get_user_by_email(db, user_email)

            if not user or user.status != "active":
                return []

            # Full access - return all available agents
            access_level = AAAService.ROLE_AGENT_PERMISSIONS.get(
                user.role,
                AgentAccessLevel.NONE
            )

            if access_level == AgentAccessLevel.FULL:
                # Return all known agents
                return ["*"]  # Wildcard means all agents

            # Restricted or no access
            return user.allowed_agents or []

        except Exception as e:
            logger.error(
                "Failed to get allowed agents",
                user=user_email,
                error=str(e)
            )
            return []

    @staticmethod
    async def update_user_permissions(
        db: AsyncSession,
        user_email: str,
        role: Optional[UserRole] = None,
        allowed_agents: Optional[List[str]] = None,
        privileges: Optional[Dict[str, Any]] = None,
        status: Optional[str] = None
    ) -> bool:
        """
        Update user permissions and access control.

        Args:
            db: Database session
            user_email: User email address
            role: New role (optional)
            allowed_agents: New allowed agents list (optional)
            privileges: New privileges dict (optional)
            status: New status (optional)

        Returns:
            True if successful, False otherwise
        """
        try:
            user = await AAAService.get_user_by_email(db, user_email)

            if not user:
                logger.error("Cannot update permissions for non-existent user", email=user_email)
                return False

            # Update fields
            if role is not None:
                user.role = role
            if allowed_agents is not None:
                user.allowed_agents = allowed_agents
            if privileges is not None:
                user.privileges = privileges
            if status is not None:
                user.status = status

            await db.commit()

            logger.info(
                "Updated user permissions",
                email=user_email,
                role=user.role,
                allowed_agents=user.allowed_agents,
                status=user.status
            )

            return True

        except Exception as e:
            logger.error(
                "Failed to update user permissions",
                email=user_email,
                error=str(e)
            )
            await db.rollback()
            return False
