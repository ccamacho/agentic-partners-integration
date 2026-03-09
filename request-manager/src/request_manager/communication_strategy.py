"""Communication strategy for A2A (Agent-to-Agent) HTTP calls."""

import os
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import HTTPException, status
from shared_models import SessionResponse, configure_logging
from shared_models.models import NormalizedRequest
from sqlalchemy.ext.asyncio import AsyncSession

from .agent_client_enhanced import EnhancedAgentClient
from .normalizer import RequestNormalizer

logger = configure_logging("request-manager")


def _should_filter_sessions_by_integration_type() -> bool:
    """Check if sessions should be filtered by integration type.

    Returns:
        True if sessions should be separated by integration type (legacy behavior)
        False if a single session should be maintained across all integration types (default)
    """
    return os.getenv("SESSION_PER_INTEGRATION_TYPE", "false").lower() == "true"


def _get_session_timeout_hours() -> int:
    """Get session timeout in hours from environment variable.

    Returns:
        Session timeout in hours (default: 336 hours = 2 weeks)
    """
    return int(os.getenv("SESSION_TIMEOUT_HOURS", "336"))


def get_pod_name() -> Optional[str]:
    """Get pod name from environment variable."""
    return os.getenv("HOSTNAME") or os.getenv("POD_NAME")


async def create_or_get_session_shared(
    request: Any, db: AsyncSession
) -> Optional[SessionResponse]:
    """Shared session management logic for all communication strategies.

    This function handles the common pattern of:
    1. Looking for existing active sessions for the user
    2. Reusing existing sessions if found (updating timestamp)
    3. Creating new sessions if none found

    Args:
        request: The request object containing user_id, integration_type, etc.
        db: Database session for queries and updates

    Returns:
        SessionResponse object for the session (existing or newly created)
    """
    # Resolve user_id to canonical user_id if it's an email address
    from shared_models import SessionResponse, resolve_canonical_user_id
    from shared_models.models import RequestSession, SessionStatus
    from sqlalchemy import select

    canonical_user_id = await resolve_canonical_user_id(
        request.user_id,
        integration_type=getattr(request, "integration_type", None),
        db=db,
    )

    # Check if a session_id was provided in metadata (e.g., from X-Session-ID header in email reply, or thread metadata)
    # This allows integrations to provide a session_id to continue an existing session
    request_metadata = getattr(request, "metadata", {}) or {}
    provided_session_id = request_metadata.get("session_id")

    # If a session_id is provided, try to use it first
    if provided_session_id:
        logger.debug(
            "Session ID provided in request metadata, attempting to use it",
            provided_session_id=provided_session_id,
            canonical_user_id=canonical_user_id,
        )
        # Verify the provided session_id exists and belongs to this user
        stmt = select(RequestSession).where(
            RequestSession.session_id == provided_session_id,
            RequestSession.user_id == canonical_user_id,
            RequestSession.status == SessionStatus.ACTIVE.value,
        )
        result = await db.execute(stmt)
        provided_session = result.scalar_one_or_none()

        if provided_session:
            # Check if session is expired
            now = datetime.now(timezone.utc)
            if provided_session.expires_at is None or provided_session.expires_at > now:
                # Valid session found - update activity timestamp and return it
                provided_session.last_request_at = datetime.now(timezone.utc)  # type: ignore[assignment]
                await db.commit()
                logger.info(
                    "Reusing provided session from metadata",
                    session_id=provided_session_id,
                    canonical_user_id=canonical_user_id,
                )
                return SessionResponse.model_validate(provided_session)
            else:
                logger.warning(
                    "Provided session_id is expired, will create new session",
                    session_id=provided_session_id,
                    canonical_user_id=canonical_user_id,
                )
        else:
            logger.warning(
                "Provided session_id not found or doesn't belong to user, will create new session",
                provided_session_id=provided_session_id,
                canonical_user_id=canonical_user_id,
            )

    # Check if we should filter by integration type
    filter_by_integration_type = _should_filter_sessions_by_integration_type()

    # Get current time for expiration checks
    now = datetime.now(timezone.utc)

    # Try to find existing active session (not expired)
    # Use SELECT FOR UPDATE to lock rows and prevent concurrent session creation
    where_conditions = [
        RequestSession.user_id == canonical_user_id,
        RequestSession.status == SessionStatus.ACTIVE.value,
        # Filter out expired sessions
        ((RequestSession.expires_at.is_(None)) | (RequestSession.expires_at > now)),
    ]

    # Optionally filter by integration type based on env var
    if filter_by_integration_type:
        where_conditions.append(
            RequestSession.integration_type == request.integration_type
        )

    # Use SELECT FOR UPDATE SKIP LOCKED to prevent race conditions
    # SKIP LOCKED allows other transactions to proceed if row is locked
    stmt = (
        select(RequestSession)
        .where(*where_conditions)
        .order_by(RequestSession.last_request_at.desc())
        .with_for_update(skip_locked=True)
    )

    result = await db.execute(stmt)
    existing_sessions = result.scalars().all()

    # Debug logging for session lookup
    logger.debug(
        "Session lookup results",
        canonical_user_id=canonical_user_id,
        original_user_id=request.user_id,
        integration_type=(
            request.integration_type.value
            if hasattr(request.integration_type, "value")
            else str(request.integration_type)
        ),
        filter_by_integration_type=filter_by_integration_type,
        found_sessions_count=len(existing_sessions),
    )

    if existing_sessions:
        # Use the most recent session (first in the ordered list)
        existing_session = existing_sessions[0]

        # If we found multiple sessions, clean up the old ones
        if len(existing_sessions) > 1:
            logger.warning(
                "Multiple active sessions found for user, cleaning up old sessions",
                user_id=canonical_user_id,
                original_user_id=request.user_id,
                integration_type=request.integration_type,
                session_count=len(existing_sessions),
                selected_session_id=existing_session.session_id,
                all_session_ids=[s.session_id for s in existing_sessions],
                filter_by_integration_type=filter_by_integration_type,
            )

            # Use the cleanup utility function
            from shared_models import get_enum_value

            from .database_utils import cleanup_old_sessions

            # Pass integration_type only if filtering by it, otherwise None
            # Convert enum to string value for consistency
            cleanup_integration_type = (
                get_enum_value(request.integration_type)
                if filter_by_integration_type
                else None
            )

            deactivated_count = await cleanup_old_sessions(
                db=db,
                user_id=canonical_user_id,
                integration_type=cleanup_integration_type,
            )

            logger.info(
                "Session cleanup completed",
                user_id=canonical_user_id,
                original_user_id=request.user_id,
                deactivated_count=deactivated_count,
            )

        # Update activity timestamp
        existing_session.last_request_at = datetime.now(timezone.utc)  # type: ignore[assignment]
        await db.commit()
        logger.info(
            "Reusing existing session",
            session_id=existing_session.session_id,
            current_agent_id=existing_session.current_agent_id,
            user_id=canonical_user_id,
            original_user_id=request.user_id,
            integration_type=(
                existing_session.integration_type.value
                if hasattr(existing_session.integration_type, "value")
                else str(existing_session.integration_type)
            ),
            filter_by_integration_type=filter_by_integration_type,
        )
        return SessionResponse.model_validate(existing_session)

    # Create new session via direct database access
    from datetime import timedelta

    from shared_models import BaseSessionManager, SessionCreate

    session_timeout_hours = _get_session_timeout_hours()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=session_timeout_hours)

    request_integration_type = getattr(request, "integration_type", None)
    if request_integration_type is None:
        from shared_models.models import IntegrationType

        request_integration_type = IntegrationType.WEB

    session_manager = BaseSessionManager(db)
    session_data = SessionCreate(
        user_id=canonical_user_id,
        integration_type=request_integration_type,
        channel_id=getattr(request, "channel_id", None),
        thread_id=getattr(request, "thread_id", None),
        external_session_id=None,
        integration_metadata=request.metadata or {},
        user_context={},
    )

    try:
        session_response = await session_manager.create_session(session_data)

        # Update expires_at separately since it's not in SessionCreate
        if expires_at:
            from sqlalchemy import update as sql_update

            update_stmt = (
                sql_update(RequestSession)
                .where(RequestSession.session_id == session_response.session_id)
                .values(expires_at=expires_at)
            )
            await db.execute(update_stmt)
            await db.commit()
            # Re-fetch the session to get updated expires_at
            select_stmt = select(RequestSession).where(
                RequestSession.session_id == session_response.session_id
            )
            result = await db.execute(select_stmt)
            updated_session = result.scalar_one_or_none()
            if updated_session:
                session_response = SessionResponse.model_validate(updated_session)

        logger.info(
            "Created new session via direct DB access",
            session_id=session_response.session_id,
            user_id=canonical_user_id,
            original_user_id=request.user_id,
        )
        return session_response
    except Exception as e:
        # If creation failed, try one more time to get existing session
        logger.warning(
            "Session creation failed, checking for existing session",
            user_id=canonical_user_id,
            error=str(e),
        )
        existing_session_obj = await session_manager.get_active_session(
            canonical_user_id, request_integration_type
        )
        if existing_session_obj:
            logger.info(
                "Found existing session after creation failure",
                session_id=existing_session_obj.session_id,
                user_id=canonical_user_id,
            )
            return SessionResponse.model_validate(existing_session_obj)
        # Re-raise the original exception if no existing session found
        raise


class CommunicationStrategy(ABC):
    """Abstract base class for communication strategies."""

    async def create_or_get_session(
        self, request: Any, db: AsyncSession
    ) -> Optional[SessionResponse]:
        """Create or get session using shared session management logic.

        This method is implemented in the base class since all communication
        strategies use identical session management logic.
        """
        return await create_or_get_session_shared(request, db)

    @abstractmethod
    async def send_request(self, normalized_request: NormalizedRequest) -> bool:
        """Send a request to the agent service."""
        pass

    @abstractmethod
    async def wait_for_response(
        self, request_id: str, timeout: int, db: Optional[AsyncSession] = None
    ) -> Dict[str, Any]:
        """Wait for response from the agent service."""
        pass


def get_communication_strategy() -> "DirectHTTPStrategy":
    """Get the A2A communication strategy."""
    logger.info("Using DirectHTTPStrategy (A2A communication)")
    return DirectHTTPStrategy()


class DirectHTTPStrategy(CommunicationStrategy):
    """Communication strategy using direct HTTP calls to agents (A2A).

    Uses synchronous HTTP calls for agent-to-agent communication.
    """

    def __init__(self) -> None:
        agent_service_url = os.getenv(
            "AGENT_SERVICE_URL", "http://agent-service:8080"
        )
        timeout = float(os.getenv("AGENT_TIMEOUT", "120"))
        # Use EnhancedAgentClient for structured context support
        self.agent_client = EnhancedAgentClient(
            agent_service_url=agent_service_url, timeout=timeout
        )
        logger.info(
            "Initialized DirectHTTPStrategy with EnhancedAgentClient",
            agent_service_url=agent_service_url,
            timeout=timeout,
        )

    async def send_request(self, normalized_request: NormalizedRequest) -> bool:
        """Send request via direct HTTP call to agent.

        Note: This method is kept for interface compatibility but not used
        in DirectHTTPStrategy since we call and wait synchronously.
        """
        logger.debug(
            "DirectHTTPStrategy.send_request called (unused in sync flow)",
            request_id=normalized_request.request_id,
        )
        return True

    async def wait_for_response(
        self, request_id: str, timeout: int, db: Optional[AsyncSession] = None
    ) -> Dict[str, Any]:
        """Wait for response - unused in DirectHTTPStrategy.

        DirectHTTPStrategy makes synchronous HTTP calls, so responses
        are returned immediately from invoke_agent_with_routing.
        """
        logger.debug(
            "DirectHTTPStrategy.wait_for_response called (unused in sync flow)",
            request_id=request_id,
        )
        return {}

    async def invoke_agent_with_routing(
        self,
        normalized_request: NormalizedRequest,
        db: AsyncSession,
        target_agent: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Invoke agent directly or with automatic routing.

        If target_agent is provided (from HybridRouter), calls it directly.
        Otherwise starts with routing-agent for backwards compatibility.

        Args:
            normalized_request: The normalized request to process
            db: Database session for updating session state
            target_agent: Optional pre-determined target agent (bypasses routing-agent)

        Returns:
            Final agent response with content
        """
        current_agent = target_agent or "routing-agent"
        user_message = normalized_request.content
        transfer_context: Dict[str, Any] = {}
        max_routing_hops = 5  # Prevent infinite routing loops

        # Extract allowed_agents for authorization enforcement
        # allowed_agents is nested: user_context -> user_context -> allowed_agents
        # because adk_endpoints passes user_context inside request.metadata
        # which gets merged into normalized_request.user_context by the normalizer
        inner_ctx = normalized_request.user_context.get("user_context", {})
        allowed_agents = inner_ctx.get("allowed_agents", [])
        logger.info(
            "User allowed agents for routing authorization",
            user_id=normalized_request.user_id,
            allowed_agents=allowed_agents,
        )
        # Get conversation history for context extraction
        conversation_history = await self._get_conversation_history(
            normalized_request.session_id,
            db
        )

        # Track previous agent for context optimization
        previous_agent: Optional[str] = None

        # Include allowed_agents in transfer_context so routing-agent
        # can make permission-aware routing decisions
        transfer_context["allowed_agents"] = allowed_agents

        for hop in range(max_routing_hops):
            logger.info(
                "Invoking agent",
                agent_name=current_agent,
                session_id=normalized_request.session_id,
                hop=hop + 1,
                previous_agent=previous_agent,
                has_conversation_history=bool(conversation_history),
            )

            # Invoke agent via HTTP with conversation history
            response = await self.agent_client.invoke_agent(
                agent_name=current_agent,
                session_id=normalized_request.session_id,
                user_id=normalized_request.user_id,
                message=user_message,
                transfer_context=transfer_context,
                conversation_history=conversation_history,
                previous_agent=previous_agent,
            )

            # Check for routing decision
            routing_decision = response.get("routing_decision")

            if routing_decision:
                # AUTHORIZATION ENFORCEMENT: Block routing to agents
                # the user does not have access to. This is the hard gate
                # that prevents unauthorized access regardless of LLM output.
                user_has_wildcard = "*" in allowed_agents
                if not user_has_wildcard and routing_decision not in allowed_agents:
                    logger.warning(
                        "AUTHORIZATION BLOCKED: Routing to unauthorized agent",
                        user_id=normalized_request.user_id,
                        requested_agent=routing_decision,
                        allowed_agents=allowed_agents,
                        session_id=normalized_request.session_id,
                    )
                    # Return an access denied response instead of routing
                    agent_display = routing_decision.replace("-", " ").title()
                    allowed_display = ", ".join(
                        a.replace("-", " ").title() for a in allowed_agents
                    ) if allowed_agents else "none"
                    return {
                        "content": (
                            f"I understand your question relates to {agent_display}, "
                            f"but your account does not have access to that agent. "
                            f"Your current permissions allow access to: **{allowed_display}**. "
                            f"Please contact your administrator if you need access to additional agents."
                        ),
                        "agent_id": "routing-agent",
                        "session_id": normalized_request.session_id,
                        "routing_decision": None,
                        "metadata": {
                            "handling_agent": "routing-agent",
                            "routing_reason": "Access denied - unauthorized agent",
                            "blocked_agent": routing_decision,
                            "allowed_agents": allowed_agents,
                        },
                    }

                logger.info(
                    "Routing decision received (authorized)",
                    from_agent=current_agent,
                    to_agent=routing_decision,
                    session_id=normalized_request.session_id,
                )

                # Update transfer context
                transfer_context = response.get("metadata") or {}
                transfer_context["allowed_agents"] = allowed_agents

                # Track previous agent for context optimization
                previous_agent = current_agent

                # Route to specialist agent
                current_agent = routing_decision

                # Continue loop to invoke specialist agent
                continue
            else:
                # No routing decision - this is the final response
                logger.info(
                    "Final response received",
                    agent_name=current_agent,
                    session_id=normalized_request.session_id,
                    response_length=len(response.get("content", "")),
                )

                # Format response for compatibility with existing code
                return {
                    "request_id": normalized_request.request_id,
                    "session_id": normalized_request.session_id,
                    "agent_id": response.get("agent_id", current_agent),
                    "content": response.get("content", ""),
                    "metadata": response.get("metadata", {}),
                    "processing_time_ms": 0,  # Not tracked in direct HTTP
                    "requires_followup": False,
                    "followup_actions": [],
                }

        # Max hops reached
        logger.error(
            "Max routing hops reached",
            max_hops=max_routing_hops,
            session_id=normalized_request.session_id,
        )
        raise Exception(f"Max routing hops ({max_routing_hops}) exceeded")

    async def _get_conversation_history(
        self,
        session_id: str,
        db: AsyncSession,
    ) -> list:
        """
        Get conversation history from session's conversation_context.

        The conversation_context JSON column stores a ``messages`` key with
        a list of ``{"role": "user"/"assistant", "content": "..."}`` dicts,
        written by ``_append_conversation_turn`` in adk_endpoints.py.

        Args:
            session_id: The session ID
            db: Database session

        Returns:
            List of conversation messages in format:
            [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]
        """
        try:
            from shared_models.models import RequestSession
            from sqlalchemy import select

            stmt = select(RequestSession).where(
                RequestSession.session_id == session_id
            )
            result = await db.execute(stmt)
            session = result.scalar_one_or_none()

            if not session:
                logger.debug("Session not found", session_id=session_id)
                return []

            ctx = session.conversation_context or {}
            messages = ctx.get("messages", [])

            if not messages:
                logger.debug(
                    "No conversation history found",
                    session_id=session_id,
                )
                return []

            # Limit to recent messages (last 20 turns = 40 entries)
            if len(messages) > 40:
                messages = messages[-40:]

            logger.debug(
                "Retrieved conversation history",
                session_id=session_id,
                message_count=len(messages),
            )

            return messages

        except Exception as e:
            logger.warning(
                "Failed to retrieve conversation history, using empty history",
                session_id=session_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            return []


class UnifiedRequestProcessor:
    """Unified request processor for A2A communication."""

    def __init__(self, strategy: CommunicationStrategy) -> None:
        self.strategy = strategy

    def _extract_session_data(self, session: Any) -> tuple[str, str]:
        """Extract session_id and current_agent_id from session data.

        Handles SessionResponse objects (from agent client) and SessionResponse objects (from session manager).
        """
        # Both agent client and session manager now return SessionResponse objects
        return session.session_id, session.current_agent_id

    async def process_request_sync(
        self,
        request: Any,
        db: AsyncSession,
        timeout: int = int(os.getenv("AGENT_TIMEOUT", "120")),
        set_pod_name: bool = True,
    ) -> Dict[str, Any]:
        """Process a request synchronously via A2A HTTP calls."""
        normalized_request, session_id, current_agent_id = await self._prepare_request(
            request, db, set_pod_name=set_pod_name
        )

        logger.info(
            "Processing request via A2A",
            request_id=normalized_request.request_id,
        )

        target_agent = normalized_request.target_agent_id
        if target_agent:
            logger.info(
                "Using pre-determined target agent (bypassing routing-agent)",
                target_agent=target_agent,
                request_id=normalized_request.request_id,
            )

        import time
        start_time = time.monotonic()

        response = await self.strategy.invoke_agent_with_routing(
            normalized_request, db, target_agent=target_agent
        )

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        response["processing_time_ms"] = elapsed_ms

        await self._complete_request_log(
            request_id=normalized_request.request_id,
            agent_id=response.get("agent_id", ""),
            response_content=response.get("content", ""),
            response_metadata=response.get("metadata", {}),
            processing_time_ms=elapsed_ms,
            db=db,
        )

        logger.info(
            "Request processed successfully",
            request_id=normalized_request.request_id,
            session_id=session_id,
            user_id=request.user_id,
            agent_id=response.get("agent_id"),
            processing_time_ms=elapsed_ms,
        )

        return response

    async def _prepare_request(
        self, request: Any, db: AsyncSession, set_pod_name: bool = True
    ) -> tuple[NormalizedRequest, str, str]:
        """Common request preparation logic: session management, normalization, and RequestLog creation.

        Args:
            set_pod_name: If True, set pod_name for requests that wait for responses.
                         If False, don't set pod_name.

        Returns:
            tuple: (normalized_request, session_id, current_agent_id)
        """
        normalizer = RequestNormalizer()

        # Delegate session management to the communication strategy
        logger.debug("Creating or getting session", user_id=request.user_id)
        session = await self.strategy.create_or_get_session(request, db)

        # Check if session creation failed
        if not session:
            logger.error("Failed to create or find session", user_id=request.user_id)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create session",
            )

        logger.info(
            "Session created/found successfully",
            session_id=session.session_id,
            user_id=request.user_id,
        )

        # Normalize the request
        session_id, current_agent_id = self._extract_session_data(session)
        normalized_request = normalizer.normalize_request(
            request, session_id, current_agent_id
        )

        # Agent-service uses email instead of canonical UUID
        # Look up user email from canonical user_id and replace in NormalizedRequest
        try:
            from shared_models.models import User
            from shared_models.user_utils import is_uuid
            from sqlalchemy import select

            # Only look up email if user_id is a UUID (canonical user_id)
            if is_uuid(normalized_request.user_id):
                stmt = select(User).where(User.user_id == normalized_request.user_id)
                result = await db.execute(stmt)
                user = result.scalar_one_or_none()
                if user and user.primary_email:
                    user_email = str(user.primary_email)
                    # Replace UUID with email for agent-service communication
                    normalized_request.user_id = user_email
                    logger.debug(
                        "Replaced canonical user_id with email for agent-service",
                        canonical_user_id=request.user_id,
                        user_email=user_email,
                    )
                else:
                    # User has no email - leave UUID as-is
                    # The session_manager will detect it's a UUID and won't use it as authoritative_user_id
                    # This will cause the MCP server to raise an error when no email is available (correct behavior)
                    logger.warning(
                        "User has no email address - cannot perform email-based lookups",
                        canonical_user_id=normalized_request.user_id,
                    )
        except Exception as e:
            logger.warning(
                "Failed to retrieve user email for normalization",
                user_id=normalized_request.user_id,
                error=str(e),
            )
            # If lookup fails and user_id is a UUID, leave it as-is
            # The session_manager will detect it's a UUID and won't use it as authoritative_user_id
            # This will cause the MCP server to raise an error when no email is available (correct behavior)

        # Create initial RequestLog entry for tracking
        await self._create_request_log_entry(
            normalized_request, db, set_pod_name=set_pod_name
        )

        return normalized_request, session_id, current_agent_id

    async def _create_request_log_entry(
        self,
        normalized_request: NormalizedRequest,
        db: AsyncSession,
        set_pod_name: bool = True,
    ) -> None:
        """Create initial RequestLog entry for tracking.

        Args:
            set_pod_name: If True, set pod_name for requests that wait for responses.
                         If False, don't set pod_name.
        """
        from .database_utils import create_request_log_entry_unified

        await create_request_log_entry_unified(
            request_id=normalized_request.request_id,
            session_id=normalized_request.session_id,
            user_id=normalized_request.user_id,
            content=normalized_request.content,
            request_type=normalized_request.request_type,
            integration_type=normalized_request.integration_type,
            integration_context=normalized_request.integration_context,
            db=db,
            set_pod_name=set_pod_name,
        )

    async def _complete_request_log(
        self,
        request_id: str,
        agent_id: str,
        response_content: str,
        response_metadata: dict,
        processing_time_ms: int,
        db: AsyncSession,
    ) -> None:
        """Update RequestLog with response data for accounting.

        Called after an A2A request completes to record which agent handled it,
        the response content, and how long it took.
        """
        try:
            from datetime import datetime, timezone

            from shared_models.models import RequestLog
            from sqlalchemy import update

            stmt = (
                update(RequestLog)
                .where(RequestLog.request_id == request_id)
                .values(
                    agent_id=agent_id,
                    response_content=response_content,
                    response_metadata=response_metadata,
                    processing_time_ms=processing_time_ms,
                    completed_at=datetime.now(timezone.utc),
                )
            )
            await db.execute(stmt)
            await db.commit()

            logger.debug(
                "RequestLog accounting completed",
                request_id=request_id,
                agent_id=agent_id,
                processing_time_ms=processing_time_ms,
            )
        except Exception as e:
            logger.warning(
                "Failed to complete RequestLog accounting",
                request_id=request_id,
                error=str(e),
            )
