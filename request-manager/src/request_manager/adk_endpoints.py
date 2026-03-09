"""
ADK-Compatible API Endpoints.

Provides Google Agent Development Kit (ADK) compatible endpoints for web UI integration.
"""

from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Header, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

import structlog

from shared_models.database import get_db
from .aaa_middleware import AAAMiddleware
from .communication_strategy import get_communication_strategy, UnifiedRequestProcessor
from shared_models.aaa_service import AAAService
from .schemas import WebRequest
from shared_models.models import IntegrationType

logger = structlog.get_logger()

router = APIRouter(prefix="/adk", tags=["adk"])


# Request/Response Models
class ADKUser(BaseModel):
    """User information in ADK format."""
    email: str
    name: Optional[str] = None
    organization: Optional[str] = None


class ADKChatRequest(BaseModel):
    """Chat request in ADK format."""
    message: str = Field(..., description="User message")
    session_id: Optional[str] = Field(None, description="Conversation session ID")
    user: ADKUser = Field(..., description="User information")
    context: Optional[Dict[str, Any]] = Field(None, description="Additional context")


class ADKChatResponse(BaseModel):
    """Chat response in ADK format."""
    response: str = Field(..., description="Agent response message")
    session_id: str = Field(..., description="Conversation session ID")
    agent: str = Field(..., description="Agent that handled the request")
    user_context: Dict[str, Any] = Field(..., description="User context and permissions")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata")


# Endpoints
@router.post("/chat", response_model=ADKChatResponse)
async def adk_chat(
    request: ADKChatRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Send a message to the agent system (ADK-compatible).

    This endpoint provides an ADK-compatible interface for chat interactions
    with automatic routing to appropriate agents based on user permissions.
    """
    try:
        user_email = request.user.email

        logger.info(
            "ADK chat request",
            user=user_email,
            message=request.message[:100],
            session_id=request.session_id
        )

        # Get user context and allowed agents
        user_context = await AAAMiddleware.get_user_context(db, user_email)
        allowed_agents = user_context.get("allowed_agents", [])

        logger.info(
            "User allowed agents",
            user=user_email,
            allowed_agents=allowed_agents
        )

        # Route ALL messages to routing-agent first
        # The routing-agent will decide whether to:
        # 1. Handle conversational queries itself (greetings, chitchat)
        # 2. Delegate to specialist agents (software-support, network-support) for technical questions
        agent_name = "routing-agent"

        logger.info(
            "Routing to routing-agent for conversation handling",
            user=user_email,
            message=request.message[:100]
        )

        # Call the routing agent (it will handle delegation internally)
        comm_strategy = get_communication_strategy()
        processor = UnifiedRequestProcessor(comm_strategy)

        # Build request for routing agent (using WebRequest schema)
        agent_request = WebRequest(
            integration_type=IntegrationType.WEB,
            user_id=user_email,
            content=request.message,
            metadata={
                "session_id": request.session_id,
                "target_agent": "routing-agent",  # Always start with routing-agent
                "user_context": user_context,
                "source": "adk-web",
                **(request.context or {})
            }
        )

        # Send to agent service and get response
        agent_response = await processor.process_request_sync(agent_request, db)

        # Extract actual handling agent from response metadata
        # If routing-agent delegated, it will include routing info in metadata
        response_metadata = agent_response.get("metadata", {})
        actual_agent = response_metadata.get("handling_agent", "routing-agent")
        routing_reason = response_metadata.get("routing_reason")

        # Use the DB session_id (from the agent pipeline), not the client-provided one.
        # The pipeline creates/looks up a real session in the DB and passes its ID through.
        response_session_id = agent_response.get("session_id") or request.session_id or ""
        response_content = agent_response.get("content", "")
        if response_session_id:
            try:
                await _append_conversation_turn(
                    db, response_session_id,
                    user_message=request.message,
                    agent_response=response_content,
                    agent_name=actual_agent,
                )
            except Exception as hist_err:
                logger.warning(
                    "Failed to store conversation history",
                    session_id=response_session_id,
                    error=str(hist_err),
                )

        # Format response in ADK format
        # agent_response has flat structure: content, metadata, session_id, etc.
        return ADKChatResponse(
            response=response_content,
            session_id=response_session_id,
            agent=actual_agent,  # Use the actual agent that handled it (routing-agent or delegated agent)
            user_context=user_context,
            metadata={
                **response_metadata,
                "initial_router": "routing-agent",
                "routing_reason": routing_reason
            }
        )

    except Exception as e:
        logger.error(
            "ADK chat error",
            user=request.user.email,
            error=str(e)
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process chat request: {str(e)}"
        )


class ADKAuditEntry(BaseModel):
    """Single audit log entry."""
    request_id: str
    timestamp: str
    message: str
    agent: Optional[str] = None
    response_preview: Optional[str] = None
    processing_time_ms: Optional[int] = None
    session_id: str


class ADKAuditResponse(BaseModel):
    """Audit log response."""
    entries: List[ADKAuditEntry]
    total: int
    user_email: str
    user_role: str


@router.get("/audit", response_model=ADKAuditResponse)
async def adk_audit_log(
    user_email: Optional[str] = Header(None, alias="X-User-Email"),
    limit: int = 50,
    db: AsyncSession = Depends(get_db)
):
    """
    Get audit log of request history for the authenticated user.

    Admin users see all users' logs. Regular users see only their own.
    Returns request content, which agent handled it, response preview,
    and processing time.
    """
    try:
        if not user_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="X-User-Email header required"
            )

        from shared_models.models import RequestLog, RequestSession, User
        from sqlalchemy import select

        # Get user info
        user = await AAAService.get_user_by_email(db, user_email)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )

        user_role = user.role.value if user.role else "user"
        is_admin = user_role == "admin"

        # Build query: join request_logs with request_sessions to get user_id
        stmt = (
            select(RequestLog, RequestSession.user_id)
            .join(RequestSession, RequestLog.session_id == RequestSession.session_id)
        )

        # Non-admin users only see their own logs
        if not is_admin:
            stmt = stmt.where(RequestSession.user_id == str(user.user_id))

        stmt = stmt.order_by(RequestLog.created_at.desc()).limit(limit)

        result = await db.execute(stmt)
        rows = result.all()

        # Build count query
        from sqlalchemy import func
        count_stmt = (
            select(func.count())
            .select_from(RequestLog)
            .join(RequestSession, RequestLog.session_id == RequestSession.session_id)
        )
        if not is_admin:
            count_stmt = count_stmt.where(RequestSession.user_id == str(user.user_id))
        total = (await db.execute(count_stmt)).scalar() or 0

        # Look up user emails for admin view
        user_emails: Dict[str, str] = {}
        if is_admin:
            user_ids = {str(row[1]) for row in rows if row[1]}
            if user_ids:
                email_stmt = select(User.user_id, User.primary_email).where(
                    User.user_id.in_(list(user_ids))
                )
                email_result = await db.execute(email_stmt)
                for uid, email in email_result.all():
                    user_emails[str(uid)] = str(email)

        entries = []
        for log, session_user_id in rows:
            response_preview = None
            if log.response_content:
                # Strip thinking tags and truncate
                import re
                clean = re.sub(r"<thinking>[\s\S]*?</thinking>", "", log.response_content).strip()
                response_preview = clean[:200] + "..." if len(clean) > 200 else clean

            entry = ADKAuditEntry(
                request_id=log.request_id,
                timestamp=log.created_at.isoformat() if log.created_at else "",
                message=log.request_content or "",
                agent=log.agent_id,
                response_preview=response_preview,
                processing_time_ms=log.processing_time_ms,
                session_id=log.session_id,
            )

            # For admin, prefix message with user email
            if is_admin and session_user_id:
                email = user_emails.get(str(session_user_id), str(session_user_id))
                entry.message = f"[{email}] {entry.message}"

            entries.append(entry)

        return ADKAuditResponse(
            entries=entries,
            total=total,
            user_email=user_email,
            user_role=user_role,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("ADK audit log error", user=user_email, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get audit log: {str(e)}"
        )


async def _append_conversation_turn(
    db: AsyncSession,
    session_id: str,
    user_message: str,
    agent_response: str,
    agent_name: str,
) -> None:
    """Append a user/agent message pair to the session's conversation_context.

    Stores conversation history as a list of {role, content} dicts inside
    the JSON ``conversation_context`` column, under the key ``"messages"``.
    Keeps at most the last 20 turns (40 entries) to bound storage.
    """
    from shared_models.models import RequestSession
    from sqlalchemy import select

    stmt = select(RequestSession).where(RequestSession.session_id == session_id)
    result = await db.execute(stmt)
    session = result.scalar_one_or_none()
    if not session:
        return

    # Copy to new dict so SQLAlchemy detects the change on JSON column
    ctx = dict(session.conversation_context or {})
    messages = list(ctx.get("messages", []))

    # Strip <thinking> tags from stored history – agents don't need prior reasoning
    import re
    clean_response = re.sub(r"<thinking>[\s\S]*?</thinking>", "", agent_response).strip()

    messages.append({"role": "user", "content": user_message})
    messages.append({"role": "assistant", "content": clean_response, "agent": agent_name})

    # Keep last 20 turns (40 entries)
    if len(messages) > 40:
        messages = messages[-40:]

    ctx["messages"] = messages
    session.conversation_context = ctx  # type: ignore[assignment]

    # Explicitly mark JSON column as modified so SQLAlchemy flushes the change
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(session, "conversation_context")

    await db.commit()
    logger.debug(
        "Stored conversation turn",
        session_id=session_id,
        total_messages=len(messages),
    )
