"""Session management for shared database operations."""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Union

from shared_models import configure_logging
from shared_models.models import RequestSession, SessionStatus
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .session_schemas import SessionCreate, SessionResponse
from .utils import get_enum_value

logger = configure_logging("shared-models")


class BaseSessionManager:
    """Base session management for database operations.

    This class handles core database operations for Request Manager sessions:
    - Session lifecycle (create, update, get)
    - User context and integration metadata storage
    - Current agent assignment tracking
    - Request count tracking
    """

    def __init__(self, db_session: AsyncSession) -> None:
        """Initialize base session manager for database operations only."""
        self.db_session = db_session

    async def create_session(
        self, session_data: SessionCreate, max_retries: int = 3
    ) -> SessionResponse:
        """Create a new Request Manager session in the database.

        Handles unique constraint violations by retrying with exponential backoff.
        The unique constraint on (user_id, integration_type) where status='ACTIVE'
        prevents multiple active sessions per user/integration.

        Args:
            session_data: Session creation data
            max_retries: Maximum number of retry attempts on constraint violation

        Returns:
            SessionResponse for the created or existing session

        Raises:
            IntegrityError: If retries are exhausted and constraint violation persists
        """
        import asyncio

        for attempt in range(max_retries):
            try:
                session = RequestSession(
                    session_id=str(uuid.uuid4()),
                    user_id=session_data.user_id,
                    integration_type=get_enum_value(session_data.integration_type),
                    channel_id=session_data.channel_id,
                    thread_id=session_data.thread_id,
                    external_session_id=session_data.external_session_id,
                    integration_metadata=session_data.integration_metadata,
                    user_context=session_data.user_context,
                    status=SessionStatus.ACTIVE.value,
                    version=0,  # Initial version for optimistic locking
                )

                self.db_session.add(session)
                await self.db_session.commit()
                await self.db_session.refresh(session)

                logger.info(
                    "Session created successfully",
                    session_id=session.session_id,
                    user_id=session_data.user_id,
                    attempt=attempt + 1,
                )
                return SessionResponse.model_validate(session)

            except IntegrityError as e:
                # Rollback the transaction after IntegrityError
                # SQLAlchemy automatically rolls back on exception, but we need to
                # explicitly rollback to clear the session state before reusing it
                await self.db_session.rollback()

                error_str = str(e).lower()
                # Check if it's the unique constraint violation for active sessions
                if "idx_one_active_session_per_user_integration" in error_str or (
                    "unique" in error_str and "active" in error_str
                ):
                    if attempt < max_retries - 1:
                        # Wait with exponential backoff before retrying
                        wait_time = 0.1 * (2**attempt)
                        logger.warning(
                            "Unique constraint violation on session creation, retrying",
                            user_id=session_data.user_id,
                            integration_type=session_data.integration_type,
                            attempt=attempt + 1,
                            wait_time=wait_time,
                        )
                        await asyncio.sleep(wait_time)

                        # Try to get the existing active session instead
                        existing_session = await self.get_active_session(
                            session_data.user_id,
                            get_enum_value(session_data.integration_type),
                        )
                        if existing_session:
                            logger.info(
                                "Found existing active session after constraint violation",
                                session_id=existing_session.session_id,
                                user_id=session_data.user_id,
                            )
                            return SessionResponse.model_validate(existing_session)
                        # Continue to retry if no existing session found
                    else:
                        logger.error(
                            "Failed to create session after max retries",
                            user_id=session_data.user_id,
                            integration_type=session_data.integration_type,
                            error=str(e),
                        )
                        raise
                else:
                    # Not a constraint violation we handle, re-raise
                    raise

        # Should not reach here, but just in case
        raise IntegrityError(
            statement="Failed to create session",
            params=None,
            orig=Exception("Failed to create session after max retries"),
        )

    async def get_active_session(
        self, user_id: str, integration_type: Any
    ) -> Optional[RequestSession]:
        """Get an active session for a user and integration type."""
        stmt = (
            select(RequestSession)
            .where(
                RequestSession.user_id == user_id,
                RequestSession.integration_type == get_enum_value(integration_type),
                RequestSession.status == SessionStatus.ACTIVE.value,
            )
            .order_by(RequestSession.last_request_at.desc())
            .limit(1)
        )
        result = await self.db_session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_session(
        self, session_id: str, for_update: bool = False
    ) -> Optional[SessionResponse]:
        """Get Request Manager session by ID from database.

        Args:
            session_id: The session ID to retrieve
            for_update: If True, use SELECT FOR UPDATE to lock the row
                       (prevents concurrent modifications)
        """
        stmt = select(RequestSession).where(RequestSession.session_id == session_id)

        if for_update:
            # Lock the row to prevent concurrent modifications
            stmt = stmt.with_for_update(skip_locked=True)

        result = await self.db_session.execute(stmt)
        session = result.scalar_one_or_none()

        if session:
            return SessionResponse.model_validate(session)
        return None

    async def update_session(
        self,
        session_id: str,
        agent_id: Optional[str] = None,
        conversation_thread_id: Optional[str] = None,
        status: Optional[Union[SessionStatus, str]] = None,
        conversation_context: Optional[Dict[str, Any]] = None,
        user_context: Optional[Dict[str, Any]] = None,
        expected_version: Optional[int] = None,
        **kwargs: Any,  # Allow additional fields via kwargs
    ) -> Optional[SessionResponse]:
        """Update Request Manager session information in database with optimistic locking.

        Uses optimistic locking via version field to prevent lost updates.
        If expected_version is provided, the update will only succeed if the
        current version matches, preventing concurrent modification conflicts.

        Args:
            session_id: The session ID to update
            agent_id: Optional agent ID to assign
            conversation_thread_id: Optional conversation thread ID
            status: Optional session status
            conversation_context: Optional conversation context dictionary
            user_context: Optional user context dictionary
            expected_version: Optional expected version number for optimistic locking.
                            If provided and version doesn't match, update fails.
            **kwargs: Additional fields to update (e.g., status as string for backward compatibility)

        Returns:
            SessionResponse if update succeeded, None if version mismatch or session not found
        """
        update_data: Dict[str, Any] = {
            "updated_at": datetime.now(timezone.utc),
            "last_request_at": datetime.now(timezone.utc),
        }

        if agent_id is not None:
            update_data["current_agent_id"] = agent_id
        if conversation_thread_id is not None:
            update_data["conversation_thread_id"] = conversation_thread_id
        # Handle status: can be SessionStatus enum, string, or in kwargs
        if status is not None:
            # Convert to string value: SessionStatus enum has .value, strings pass through
            update_data["status"] = status.value if hasattr(status, "value") else status
        elif "status" in kwargs:
            # Backward compatibility: allow status as string in kwargs
            update_data["status"] = kwargs["status"]
        if conversation_context is not None:
            update_data["conversation_context"] = conversation_context
        if user_context is not None:
            update_data["user_context"] = user_context

        # Increment version for optimistic locking
        update_data["version"] = RequestSession.version + 1

        # Build WHERE clause with optional version check
        where_clause = RequestSession.session_id == session_id
        if expected_version is not None:
            # Only update if version matches (optimistic locking)
            where_clause = where_clause & (RequestSession.version == expected_version)

        stmt = (
            update(RequestSession)
            .where(where_clause)
            .values(**update_data)
            .returning(RequestSession)
        )

        result = await self.db_session.execute(stmt)
        await self.db_session.commit()
        updated_session = result.scalar_one_or_none()

        if updated_session:
            logger.debug(
                "Session updated successfully",
                session_id=session_id,
                new_version=updated_session.version,
            )
            return SessionResponse.model_validate(updated_session)
        else:
            if expected_version is not None:
                logger.warning(
                    "Session update failed: version mismatch or session not found",
                    session_id=session_id,
                    expected_version=expected_version,
                )
            return None

    async def increment_request_count(self, session_id: str, request_id: str) -> None:
        """Increment the request count for a session.

        Uses atomic database operations, so this is safe for concurrent access.
        Also increments version for optimistic locking consistency.
        """
        stmt = (
            update(RequestSession)
            .where(RequestSession.session_id == session_id)
            .values(
                total_requests=RequestSession.total_requests + 1,
                last_request_id=request_id,
                last_request_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
                version=RequestSession.version + 1,  # Increment version atomically
            )
        )

        await self.db_session.execute(stmt)
        await self.db_session.commit()
