"""CloudEvent-driven Agent Service."""

import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from cloudevents.http import CloudEvent, to_structured
from fastapi import Depends, FastAPI, HTTPException, Request, status
from shared_models import (
    BaseSessionManager,
    CloudEventBuilder,
    CloudEventHandler,
    EventTypes,
    configure_logging,
    create_cloudevent_response,
    create_shared_lifespan,
    get_database_manager,
    get_db_session_dependency,
    parse_cloudevent_from_request,
    simple_health_check,
)
from shared_models.models import (
    AgentResponse,
    NormalizedRequest,
    SessionStatus,
)
from sqlalchemy.ext.asyncio import AsyncSession
from . import __version__
from .schemas import AgentInvokeRequest, AgentInvokeResponse
from .session_manager import ResponsesSessionManager

# Configure structured logging
SERVICE_NAME = "agent-service"
logger = configure_logging(SERVICE_NAME)


class AgentConfig:
    """Configuration for agent service."""

    def __init__(self) -> None:
        self.broker_url = os.getenv("BROKER_URL")
        self.communication_mode = os.getenv("COMMUNICATION_MODE", "eventing")

        # BROKER_URL is required only for eventing-based communication
        if self.communication_mode == "eventing" and not self.broker_url:
            raise ValueError(
                "BROKER_URL environment variable is required for eventing mode. "
                "Configure BROKER_URL to point to your Knative broker, "
                "or set COMMUNICATION_MODE=http to use direct HTTP communication."
            )


class AgentService:
    """Service for handling agent interactions."""

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.http_client = httpx.AsyncClient(timeout=30.0)

    def _is_reset_command(self, content: str) -> bool:
        """Check if the content is a reset command."""
        if not content:
            return False

        content_lower = content.strip().lower()
        reset_commands = ["reset", "clear", "restart", "new session"]
        return content_lower in reset_commands

    def _is_tokens_command(self, content: str) -> bool:
        """Check if the content is a tokens command."""
        if not content:
            return False

        content_lower = content.strip().lower()
        tokens_commands = ["**tokens**", "tokens", "token stats", "usage stats"]
        return content_lower in tokens_commands

    async def _handle_reset_command(self, request: NormalizedRequest) -> AgentResponse:
        """Handle reset command by clearing the session."""
        try:
            db_manager = get_database_manager()

            async with db_manager.get_session() as db:
                session_manager = BaseSessionManager(db)

                # Clear the session by setting it to INACTIVE
                await session_manager.update_session(
                    request.session_id,
                    status=SessionStatus.INACTIVE,
                    agent_id=None,
                    conversation_thread_id=None,
                )

                logger.info(
                    "Session reset completed",
                    session_id=request.session_id,
                    user_id=request.user_id,
                    integration_type=request.integration_type,
                )

                # Return a simple reset confirmation
                return self._create_system_response(
                    request=request,
                    content="Session cleared. Starting fresh!",
                )

        except Exception as e:
            logger.error(
                "Failed to reset session", error=str(e), session_id=request.session_id
            )
            return self._create_error_response(
                request=request,
                content="Failed to reset session. Please try again.",
            )

    async def _handle_tokens_command(self, request: NormalizedRequest) -> AgentResponse:
        """Handle tokens command by fetching token statistics from database."""
        try:
            from shared_models.database import get_db_session
            from shared_models.session_token_service import SessionTokenService

            # Debug logging
            logger.debug(
                "Retrieving token stats from database",
                session_id=request.session_id,
            )

            # Query database for token counts
            async with get_db_session() as db:
                token_counts = await SessionTokenService.get_token_counts(
                    db, request.session_id
                )

            if token_counts:
                # Format the response with all token metrics including max values
                token_summary = f"TOKEN_SUMMARY:INPUT:{token_counts['total_input_tokens']}:OUTPUT:{token_counts['total_output_tokens']}:TOTAL:{token_counts['total_tokens']}:CALLS:{token_counts['llm_call_count']}:MAX_SINGLE_INPUT:{token_counts['max_input_tokens']}:MAX_SINGLE_OUTPUT:{token_counts['max_output_tokens']}:MAX_SINGLE_TOTAL:{token_counts['max_total_tokens']}"

                logger.info(
                    "Token statistics retrieved from database",
                    request_id=request.request_id,
                    session_id=request.session_id,
                    total_tokens=token_counts["total_tokens"],
                    call_count=token_counts["llm_call_count"],
                )

                return self._create_agent_response(
                    request=request,
                    content=token_summary,
                    agent_id="system",
                    response_type="tokens",
                    metadata={
                        "total_input_tokens": token_counts["total_input_tokens"],
                        "total_output_tokens": token_counts["total_output_tokens"],
                        "total_tokens": token_counts["total_tokens"],
                        "call_count": token_counts["llm_call_count"],
                        "max_input_tokens": token_counts["max_input_tokens"],
                        "max_output_tokens": token_counts["max_output_tokens"],
                        "max_total_tokens": token_counts["max_total_tokens"],
                    },
                    processing_time_ms=0,
                )
            else:
                # Session not found or no token counts yet
                return self._create_agent_response(
                    request=request,
                    content="TOKEN_SUMMARY:INPUT:0:OUTPUT:0:TOTAL:0:CALLS:0:MAX_SINGLE_INPUT:0:MAX_SINGLE_OUTPUT:0:MAX_SINGLE_TOTAL:0",
                    agent_id="system",
                    response_type="tokens",
                    metadata={
                        "total_input_tokens": 0,
                        "total_output_tokens": 0,
                        "total_tokens": 0,
                        "call_count": 0,
                        "max_input_tokens": 0,
                        "max_output_tokens": 0,
                        "max_total_tokens": 0,
                    },
                    processing_time_ms=0,
                )

        except Exception as e:
            logger.error(
                "Failed to get token statistics",
                error=str(e),
                request_id=request.request_id,
            )
            return self._create_error_response(
                request=request,
                content="Failed to retrieve token statistics. Please try again.",
            )

    async def process_request(self, request: NormalizedRequest) -> AgentResponse:
        """Process a normalized request and return agent response."""
        return await self._process_request_core(request)

    async def _process_request_core(self, request: NormalizedRequest) -> AgentResponse:
        """Core request processing logic."""
        start_time = datetime.now(timezone.utc)

        try:
            # Check for reset command first
            if self._is_reset_command(request.content):
                return await self._handle_reset_command(request)

            # Check for tokens command
            if self._is_tokens_command(request.content):
                return await self._handle_tokens_command(request)

            # Publish processing started event for user notification
            await self._publish_processing_event(request)

            return await self._handle_responses_mode_request(request, start_time)

        except Exception as e:
            logger.error(
                "Failed to process request", error=str(e), request_id=request.request_id
            )

            # Return error response
            return self._create_error_response(
                request=request,
                content=f"I apologize, but I encountered an error processing your request: {str(e)}",
                agent_id="unknown",
            )

    async def publish_response(self, response: AgentResponse) -> bool:
        """Publish agent response as CloudEvent and update database."""
        try:
            # Debug log the response object to see what values it has
            logger.debug(
                "AgentResponse object details",
                request_id=response.request_id,
                session_id=response.session_id,
                user_id=response.user_id,
                agent_id=response.agent_id,
                content_preview=response.content[:100] if response.content else "None",
                response_type=response.response_type,
                processing_time_ms=response.processing_time_ms,
            )

            # Update RequestLog in database
            logger.info(
                "Updating RequestLog in database", request_id=response.request_id
            )
            await self._update_request_log(response)
            logger.info(
                "RequestLog updated successfully", request_id=response.request_id
            )

            event_data = {
                "request_id": response.request_id,
                "session_id": response.session_id,
                "user_id": response.user_id,
                "agent_id": response.agent_id,
                "content": response.content,
                "response_type": response.response_type,
                "metadata": response.metadata,
                "processing_time_ms": response.processing_time_ms,
                "requires_followup": response.requires_followup,
                "followup_actions": response.followup_actions,
                "created_at": response.created_at.isoformat(),
            }

            # Debug log the event_data to see what values are being sent
            logger.debug(
                "Event data being published",
                event_data_keys=list(event_data.keys()),
                event_data_values={
                    key: value
                    for key, value in event_data.items()
                    if key
                    in ["request_id", "session_id", "user_id", "agent_id", "content"]
                },
                response_id=response.request_id,
            )

            logger.debug(
                "Publishing agent response event",
                event_data=event_data,
                response_id=response.request_id,
            )

            # Use shared CloudEvent builder with correct event type
            builder = CloudEventBuilder("agent-service")
            event = builder.create_response_event(
                event_data,
                response.request_id,
                response.agent_id,
                response.session_id,
            )

            headers, body = to_structured(event)

            if self.config.broker_url is None:
                logger.error("Broker URL not configured")
                return False

            response_http = await self.http_client.post(
                self.config.broker_url,
                headers=headers,
                content=body,
            )

            response_http.raise_for_status()
            return True

        except Exception as e:
            logger.error("Failed to publish response event", exc_info=e)
            return False

    def _create_agent_response(
        self,
        request: NormalizedRequest,
        content: str,
        agent_id: str,
        response_type: str = "message",
        metadata: Optional[Dict[str, Any]] = None,
        processing_time_ms: Optional[int] = None,
        start_time: Optional[datetime] = None,
        requires_followup: bool = False,
        followup_actions: Optional[List[str]] = None,
    ) -> AgentResponse:
        """Create an AgentResponse with consistent defaults.

        Args:
            request: The normalized request
            content: Response content
            agent_id: Agent identifier (required)
            response_type: Type of response (default: "message")
            metadata: Optional metadata dictionary
            processing_time_ms: Processing time in milliseconds (if None and start_time provided, will calculate)
            start_time: Start time for processing (used to calculate processing_time_ms if not provided)
            requires_followup: Whether response requires followup
            followup_actions: List of followup actions
        """
        # Calculate processing time if start_time provided and processing_time_ms not specified
        if processing_time_ms is None and start_time is not None:
            processing_time_ms = int(
                (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            )

        # Use provided metadata or empty dict
        response_metadata = dict(metadata) if metadata else {}

        response = AgentResponse(
            request_id=request.request_id,
            session_id=request.session_id,
            user_id=request.user_id,
            agent_id=agent_id,
            content=content,
            response_type=response_type,
            metadata=response_metadata,
            processing_time_ms=processing_time_ms,
            requires_followup=requires_followup,
            followup_actions=followup_actions or [],
            created_at=datetime.now(timezone.utc),
        )
        return response

    def _create_error_response(
        self,
        request: NormalizedRequest,
        content: str,
        agent_id: str = "system",
        start_time: Optional[datetime] = None,
    ) -> AgentResponse:
        """Create an error response with common defaults."""
        return self._create_agent_response(
            request=request,
            content=content,
            agent_id=agent_id,
            response_type="error",
            processing_time_ms=0,
            start_time=start_time,
        )

    def _create_system_response(
        self,
        request: NormalizedRequest,
        content: str,
        start_time: Optional[datetime] = None,
    ) -> AgentResponse:
        """Create a system response with common defaults."""
        return self._create_agent_response(
            request=request,
            content=content,
            agent_id="system",
            processing_time_ms=0,
            start_time=start_time,
        )

    async def _update_request_log(self, response: AgentResponse) -> None:
        """Update RequestLog in database with response content."""
        if response.agent_id is None:
            logger.error(
                "Cannot update request log - response missing agent_id",
                request_id=response.request_id,
                session_id=response.session_id,
            )
            return

        await _update_request_log_unified(
            request_id=response.request_id,
            response_content=response.content,
            agent_id=response.agent_id,
            response_metadata=response.metadata,
            processing_time_ms=response.processing_time_ms,
            db=None,  # Will create its own database session
        )

    async def _handle_responses_mode_request(
        self, request: NormalizedRequest, start_time: datetime
    ) -> AgentResponse:
        """Handle responses mode requests using LangGraph session manager."""
        try:
            # Handle session management (increment request count) for responses mode
            await self._handle_session_management(
                request.session_id, request.request_id
            )

            # Get database session for responses session manager
            db_manager = get_database_manager()

            async with db_manager.get_session() as db:
                # Create responses session manager
                session_manager = ResponsesSessionManager(
                    db_session=db,
                    user_id=request.user_id,
                )

                # Process the message using responses mode with session-specific context
                response_content = await session_manager.handle_responses_message(
                    text=request.content,
                    request_manager_session_id=request.session_id,
                )

                # Create response with automatic timing calculation
                if session_manager.current_agent_name is None:
                    logger.warning(
                        "No agent assigned after processing message - retrying with routing session",
                        request_id=request.request_id,
                        session_id=request.session_id,
                    )
                    # Retry processing the message - this should create a routing session
                    # if one doesn't exist (handle_responses_message handles session creation)
                    try:
                        response_content = (
                            await session_manager.handle_responses_message(
                                text=request.content,
                                request_manager_session_id=request.session_id,
                            )
                        )
                        # Check again after retry
                        if session_manager.current_agent_name is None:
                            logger.error(
                                "No agent assigned after retry - cannot process request",
                                request_id=request.request_id,
                                session_id=request.session_id,
                            )
                            return self._create_error_response(
                                request=request,
                                content="Error: No agent assigned to handle this request",
                            )
                    except Exception as e:
                        logger.error(
                            "Exception while retrying message processing",
                            request_id=request.request_id,
                            session_id=request.session_id,
                            error=str(e),
                        )
                        return self._create_error_response(
                            request=request,
                            content="Error: No agent assigned to handle this request",
                        )

                return self._create_agent_response(
                    request=request,
                    content=response_content,
                    agent_id=session_manager.current_agent_name,
                    start_time=start_time,
                )

        except Exception as e:
            logger.error(
                "Failed to handle responses mode request",
                error=str(e),
                request_id=request.request_id,
                session_id=request.session_id,
            )
            return self._create_error_response(
                request=request,
                content=f"Failed to process responses mode request: {str(e)}",
            )

    async def _publish_processing_event(self, request: NormalizedRequest) -> bool:
        """Publish processing started event for user notification."""
        try:
            event_data = {
                "request_id": request.request_id,
                "session_id": request.session_id,
                "user_id": request.user_id,
                "integration_type": request.integration_type,
                "request_type": request.request_type,
                "content_preview": (
                    request.content[:100] + "..."
                    if len(request.content) > 100
                    else request.content
                ),
                "target_agent_id": request.target_agent_id,
                "started_at": datetime.now(timezone.utc).isoformat(),
            }

            event = CloudEvent(
                {
                    "specversion": "1.0",
                    "type": EventTypes.REQUEST_PROCESSING,
                    "source": "agent-service",
                    "id": str(uuid.uuid4()),
                    "time": datetime.now(timezone.utc).isoformat(),
                    "subject": f"session/{request.session_id}",
                    "datacontenttype": "application/json",
                },
                event_data,
            )

            headers, body = to_structured(event)

            if self.config.broker_url is None:
                logger.error("Broker URL not configured")
                return False

            response = await self.http_client.post(
                self.config.broker_url,
                headers=headers,
                content=body,
            )

            response.raise_for_status()

            logger.info(
                "Processing event published",
                request_id=request.request_id,
                session_id=request.session_id,
                user_id=request.user_id,
            )

            return True

        except Exception as e:
            logger.error("Failed to publish processing event", exc_info=e)
            return False

    async def close(self) -> None:
        """Close HTTP client."""
        await self.http_client.aclose()

    async def _handle_session_management(
        self, session_id: str, request_id: str
    ) -> None:
        """Handle session management including request count increment.

        This method ensures consistent session management across all requests.
        """
        try:
            # Get database session for session management
            db_manager = get_database_manager()
            async with db_manager.get_session() as db:
                session_manager = BaseSessionManager(db)
                await session_manager.increment_request_count(session_id, request_id)

                logger.debug(
                    "Session management completed",
                    session_id=session_id,
                    request_id=request_id,
                )
        except Exception as e:
            logger.warning(
                "Failed to handle session management",
                session_id=session_id,
                request_id=request_id,
                error=str(e),
            )
            # Don't raise exception - session management failure shouldn't stop request processing


# Global agent service instance
_agent_service: Optional[AgentService] = None


async def _agent_service_startup() -> None:
    """Custom startup logic for Agent Service."""
    global _agent_service

    config = AgentConfig()
    _agent_service = AgentService(config)
    logger.info("Agent Service initialized")


async def _agent_service_shutdown() -> None:
    """Custom shutdown logic for Agent Service."""
    global _agent_service

    if _agent_service:
        await _agent_service.close()
        _agent_service = None


# Create lifespan using shared utility with custom startup/shutdown
def lifespan(app: FastAPI) -> Any:
    return create_shared_lifespan(
        service_name="agent-service",
        version=__version__,
        custom_startup=_agent_service_startup,
        custom_shutdown=_agent_service_shutdown,
    )


# Create FastAPI application
app = FastAPI(
    title="Partner Agent Service",
    description="Agent Service for Partner Agent Integration",
    version=__version__,
    lifespan=lifespan,
)


@app.get("/health")
async def health_check() -> Dict[str, Any]:
    """Health check endpoint - lightweight without database dependency."""
    return {
        "status": "healthy",
        "service": "agent-service",
        "version": __version__,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/health/detailed")
async def detailed_health_check(
    db: AsyncSession = Depends(get_db_session_dependency),
) -> Dict[str, Any]:
    """Detailed health check with database dependency for monitoring."""
    return dict(
        await simple_health_check(
            service_name="agent-service",
            version=__version__,
            db=db,
        )
    )


@app.post("/api/v1/events/cloudevents")
async def handle_cloudevent(request: Request) -> Dict[str, Any]:
    """Handle incoming CloudEvents."""
    if not _agent_service:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent service not initialized",
        )

    try:
        # Parse CloudEvent from request using shared utility
        event_data = await parse_cloudevent_from_request(request)

        event_type = event_data.get("type")

        # Handle request events
        if event_type == EventTypes.REQUEST_CREATED:
            return await _handle_request_event_from_data(event_data, _agent_service)

        logger.warning("Unhandled CloudEvent type", event_type=event_type)
        return dict(
            await create_cloudevent_response(
                status="ignored",
                message="Unhandled event type",
                details={"event_type": event_type},
            )
        )

    except Exception as e:
        logger.error("Failed to handle CloudEvent", exc_info=e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to process CloudEvent",
        )


@app.post("/api/v1/agents/{agent_name}/invoke", response_model=AgentInvokeResponse)
async def invoke_agent(
    agent_name: str,
    request: AgentInvokeRequest,
    db: AsyncSession = Depends(get_db_session_dependency),
) -> AgentInvokeResponse:
    """Invoke a specific agent directly via HTTP for A2A communication.

    This endpoint enables agent-to-agent communication by allowing
    one agent (or Request Manager) to invoke another agent synchronously.

    DIRECT INVOCATION (POC): When called with a specialist agent name
    (software-support, network-support), this endpoint directly invokes
    that agent's LLM capabilities, bypassing the routing-agent flow.

    Args:
        agent_name: Name of the agent to invoke (routing-agent, software-support, etc.)
        request: Agent invocation request containing session, user, and message
        db: Database session dependency

    Returns:
        Agent response with content and optional routing decision

    Raises:
        HTTPException: If agent not found or invocation fails
    """
    logger.info(
        "Agent invocation request",
        agent_name=agent_name,
        session_id=request.session_id,
        user_id=request.user_id,
        message_length=len(request.message),
    )

    try:
        if agent_name == "routing-agent":
            # Single-turn routing: Use the routing-agent's LLM with a routing-aware
            # system prompt to classify and respond in one call.
            # The LLM either handles the message conversationally OR returns a
            # routing marker for delegation to a specialist agent.
            logger.info(
                "Routing-agent single-turn evaluation",
                agent_name=agent_name,
                session_id=request.session_id,
            )

            from .langgraph import ResponsesAgentManager

            agent_manager = ResponsesAgentManager()
            agent = agent_manager.get_agent(agent_name)

            # Extract user's allowed agents from transfer_context
            transfer_ctx = request.transfer_context or {}
            user_allowed_agents = transfer_ctx.get("allowed_agents", [])
            has_wildcard = "*" in user_allowed_agents

            # Build the list of agents this user can access
            all_specialists = ["software-support", "network-support"]
            if has_wildcard:
                accessible_agents = all_specialists
            else:
                accessible_agents = [a for a in user_allowed_agents if a in all_specialists]

            # Build agent descriptions for the prompt
            agent_descriptions = {
                "software-support": "Handles software issues, bugs, errors, crashes, application problems, error codes",
                "network-support": "Handles network issues, connectivity, VPN, firewall, DNS, router problems",
            }

            if accessible_agents:
                agents_section = "\n".join(
                    f"- {agent}: {agent_descriptions[agent]}"
                    for agent in accessible_agents
                )
            else:
                agents_section = "(No specialist agents available for this user)"

            # Build list of agents the user CANNOT access for denial instructions
            blocked_agents = [a for a in all_specialists if a not in accessible_agents]
            if blocked_agents:
                blocked_section = (
                    "\n\nIMPORTANT ACCESS RESTRICTION: The user does NOT have access to: "
                    + ", ".join(blocked_agents) + ". "
                    "If the user's question relates to a blocked agent, do NOT use ROUTE:. "
                    "Instead, respond politely explaining that they don't have access to that "
                    "specialist and suggest they contact their administrator for access."
                )
            else:
                blocked_section = ""

            routing_system_prompt = f"""You are a routing agent for a support system. Analyze the user's message and decide how to respond.

Specialist agents the user has access to:
{agents_section}

RULES:
1. If the message is a greeting, chitchat, or general conversation (like "Hello", "Hi", "How are you", "Thanks"), respond conversationally as a friendly routing agent. Introduce yourself briefly and ask how you can help.
2. If the message describes a technical software problem AND the user has access to software-support, respond with EXACTLY this format:
   ROUTE:software-support
   I'll connect you with our software support specialist to help with your issue.
3. If the message describes a network/connectivity problem AND the user has access to network-support, respond with EXACTLY this format:
   ROUTE:network-support
   I'll connect you with our network support specialist to help with your issue.
4. If unclear, ask clarifying questions to determine the right specialist.
5. Use conversation history to understand follow-up questions. If the user references something from earlier, use that context.

IMPORTANT: Only use the ROUTE: prefix when you are confident the message needs a specialist AND the user has access to that specialist. For greetings and general chat, just respond normally without any ROUTE: prefix.{blocked_section}"""

            # Build messages with conversation history
            conversation_history = transfer_ctx.get("conversation_history", [])
            messages = [
                {"role": "system", "content": routing_system_prompt},
            ]
            # Add prior conversation turns for context
            for turn in conversation_history[-20:]:
                role = turn.get("role", "user")
                if role in ("user", "assistant"):
                    messages.append({"role": role, "content": turn.get("content", "")})
            # Add current user message
            messages.append({"role": "user", "content": request.message})

            response_content, failed = await agent.create_response_with_retry(
                messages=messages,
                temperature=0.1,  # Deterministic for routing
                token_context=request.session_id,
            )

            # Parse response for routing decision
            routing_decision = None
            handling_agent = "routing-agent"

            if response_content.startswith("ROUTE:"):
                lines = response_content.split("\n", 1)
                route_line = lines[0].strip()
                target = route_line.replace("ROUTE:", "").strip()

                available_agents = list(agent_manager.agents_dict.keys())
                if target in available_agents:
                    routing_decision = target
                    # Use the message after the ROUTE line as the response
                    response_content = lines[1].strip() if len(lines) > 1 else f"I'll connect you with our {target.replace('-', ' ')} specialist."
                    logger.info(
                        "Routing decision detected",
                        routing_decision=routing_decision,
                        response_preview=response_content[:100],
                    )

            logger.info(
                "Routing-agent evaluation completed",
                agent_name=agent_name,
                handling_agent=handling_agent,
                routing_decision=routing_decision,
                session_id=request.session_id,
                response_length=len(response_content),
            )

            return AgentInvokeResponse(
                content=response_content,
                agent_id=handling_agent,
                session_id=request.session_id,
                routing_decision=routing_decision,
                metadata={
                    **(request.transfer_context or {}),
                    "handling_agent": handling_agent if not routing_decision else routing_decision,
                    "routing_reason": f"Delegated to {routing_decision}" if routing_decision else "Handled by routing-agent",
                },
            )
        else:
            # Specialist agent invocation with mandatory RAG
            # 1. Query RAG API for relevant knowledge
            # 2. Include RAG context in the LLM prompt
            # 3. FAIL if RAG is unavailable (no silent degradation)
            from .langgraph import ResponsesAgentManager

            agent_manager = ResponsesAgentManager()

            try:
                agent = agent_manager.get_agent(agent_name)
                logger.info(
                    "Specialist agent invocation with RAG",
                    agent_name=agent_name,
                    agent_found=True,
                )
            except ValueError as e:
                logger.error(
                    "Agent not found",
                    agent_name=agent_name,
                    error=str(e),
                )
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Agent '{agent_name}' not found",
                )

            # Query RAG API - this MUST succeed for the POC
            rag_endpoint = os.getenv(
                "RAG_API_ENDPOINT",
                "http://partner-rag-api-full:8080/answer"
            )

            logger.info(
                "Querying RAG API",
                agent_name=agent_name,
                rag_endpoint=rag_endpoint,
                query=request.message[:100],
            )

            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    rag_response = await client.post(
                        rag_endpoint,
                        json={
                            "user_query": request.message,
                            "num_sources": 3,
                            "only_high_similarity_nodes": False,
                        },
                    )

                    if rag_response.status_code != 200:
                        error_text = rag_response.text
                        logger.error(
                            "RAG API call failed",
                            status=rag_response.status_code,
                            error=error_text,
                            agent_name=agent_name,
                        )
                        raise HTTPException(
                            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=f"RAG API unavailable (status {rag_response.status_code}): {error_text}",
                        )

                    rag_data = rag_response.json()
                    rag_answer = rag_data.get("response", "")
                    rag_sources = rag_data.get("sources", [])

                    logger.info(
                        "RAG API response received",
                        agent_name=agent_name,
                        answer_length=len(rag_answer),
                        source_count=len(rag_sources),
                    )

            except httpx.HTTPError as e:
                logger.error(
                    "RAG API connection failed",
                    error=str(e),
                    rag_endpoint=rag_endpoint,
                    agent_name=agent_name,
                )
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=f"RAG API unavailable: {str(e)}",
                )

            # Build source references for the response
            source_refs = []
            for src in rag_sources:
                ticket_id = src.get("id", "unknown")
                similarity = src.get("similarity", 0)
                source_refs.append(f"[{ticket_id}] (similarity: {similarity:.1%})")

            # Build LLM prompt with RAG context
            rag_context = f"""## Knowledge Base Results

The following information was retrieved from the support knowledge base for the user's query:

### RAG Answer:
{rag_answer}

### Sources:
{chr(10).join(source_refs) if source_refs else "No matching sources found."}

### Source Details:
"""
            for src in rag_sources[:3]:
                rag_context += f"\n**{src.get('id', 'unknown')}:**\n{src.get('content', '')[:500]}\n"

            # Build messages with conversation history for follow-up context
            specialist_transfer_ctx = request.transfer_context or {}
            conversation_history = specialist_transfer_ctx.get("conversation_history", [])

            messages = []
            # Add prior conversation turns so the specialist can handle follow-ups
            for turn in conversation_history[-10:]:
                role = turn.get("role", "user")
                if role in ("user", "assistant"):
                    messages.append({"role": role, "content": turn.get("content", "")})

            # Add current user query with RAG context
            messages.append(
                {"role": "user", "content": f"""User Query: {request.message}

{rag_context}

Based on the knowledge base results above, provide a helpful response to the user.
Reference the specific ticket IDs and solutions from the knowledge base.
If the knowledge base doesn't have relevant information, say so explicitly."""}
            )

            # Invoke agent with RAG-augmented context
            response_content, failed = await agent.create_response_with_retry(
                messages=messages,
                temperature=0.7,
                token_context=request.session_id,
            )

            if failed:
                logger.warning(
                    "Agent response generation failed",
                    agent_name=agent_name,
                    session_id=request.session_id,
                )

            logger.info(
                "Specialist agent invocation completed with RAG",
                agent_name=agent_name,
                session_id=request.session_id,
                response_length=len(response_content),
                rag_sources=len(rag_sources),
                failed=failed,
            )

            return AgentInvokeResponse(
                content=response_content,
                agent_id=agent_name,
                session_id=request.session_id,
                routing_decision=None,
                metadata={
                    **(request.transfer_context or {}),
                    "rag_sources": [s.get("id") for s in rag_sources],
                    "rag_source_count": len(rag_sources),
                    "rag_used": True,
                },
            )

    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        logger.error(
            "Agent invocation failed",
            agent_name=agent_name,
            session_id=request.session_id,
            error=str(e),
            error_type=type(e).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Agent invocation failed: {str(e)}",
        )


def _create_normalized_request_from_data(
    request_data: Dict[str, Any],
) -> NormalizedRequest:
    """Create a NormalizedRequest from request data with proper validation."""
    # Validate required fields
    required_fields = [
        "request_id",
        "session_id",
        "user_id",
        "integration_type",
        "request_type",
        "content",
    ]
    missing_fields = [field for field in required_fields if not request_data.get(field)]

    if missing_fields:
        raise ValueError(f"Missing required fields in request data: {missing_fields}")

    return NormalizedRequest(
        request_id=request_data["request_id"],
        session_id=request_data["session_id"],
        user_id=request_data["user_id"],
        integration_type=request_data["integration_type"],
        request_type=request_data["request_type"],
        content=request_data["content"],
        integration_context=request_data.get("integration_context", {}),
        user_context=request_data.get("user_context", {}),
        target_agent_id=request_data.get("target_agent_id"),
        requires_routing=request_data.get("requires_routing", True),
        created_at=datetime.fromisoformat(
            request_data.get("created_at", datetime.now().isoformat())
        ),
    )


async def _handle_request_event_from_data(
    event_data: Dict[str, Any], agent_service: AgentService
) -> Dict[str, Any]:
    """Handle request CloudEvent using pre-parsed event data."""
    try:
        # Extract event data using common utility
        request_data = CloudEventHandler.extract_event_data(event_data)

        # Parse normalized request with proper error handling
        try:
            request = _create_normalized_request_from_data(request_data)

            logger.debug(
                "Created NormalizedRequest",
                request_id=request.request_id,
                session_id=request.session_id,
                user_id=request.user_id,
                content_preview=request.content[:100] if request.content else "empty",
            )
        except Exception as e:
            logger.error(
                "Failed to parse normalized request",
                error=str(e),
                event_data=event_data,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid request data: {str(e)}",
            )

        # Process the request
        logger.debug("Calling agent_service.process_request")
        response = await agent_service.process_request(request)

        logger.debug(
            "Agent response created",
            response_id=response.request_id if response else "None",
            response_type=type(response).__name__ if response else "None",
        )

        # Publish response event
        logger.debug("Publishing response event")
        success = await agent_service.publish_response(response)

        logger.info(
            "Request processed",
            request_id=request.request_id,
            session_id=request.session_id,
            agent_id=response.agent_id,
            response_published=success,
        )

        return {
            "request_id": response.request_id,
            "session_id": response.session_id,
            "user_id": response.user_id,
            "agent_id": response.agent_id,
            "content": response.content,
            "response_type": response.response_type,
            "metadata": response.metadata,
            "processing_time_ms": response.processing_time_ms,
            "requires_followup": response.requires_followup,
            "followup_actions": response.followup_actions,
            "created_at": response.created_at.isoformat(),
        }

    except Exception as e:
        logger.error("Failed to handle request event", exc_info=e)
        raise


async def _update_request_log_unified(
    request_id: str,
    response_content: str,
    agent_id: str,
    response_metadata: dict[str, Any] | None = None,
    processing_time_ms: int | None = None,
    db: AsyncSession | None = None,
) -> None:
    """Update RequestLog for any API type."""
    try:
        from shared_models.models import RequestLog
        from sqlalchemy import update

        # Update the RequestLog with response content
        stmt = (
            update(RequestLog)
            .where(RequestLog.request_id == request_id)
            .values(
                response_content=response_content,
                response_metadata=response_metadata or {},
                agent_id=agent_id,
                processing_time_ms=processing_time_ms,
                completed_at=datetime.now(timezone.utc),
            )
        )

        if db:
            await db.execute(stmt)
            await db.commit()
        else:
            # For backward compatibility with existing code that doesn't pass db
            db_manager = get_database_manager()
            async with db_manager.get_session() as session:
                await session.execute(stmt)
                await session.commit()

        logger.info(
            "RequestLog updated",
            request_id=request_id,
            agent_id=agent_id,
            content_length=len(response_content),
        )

    except Exception as e:
        logger.error(
            "Failed to update RequestLog",
            request_id=request_id,
            error=str(e),
        )
        # Don't raise exception - RequestLog update failure shouldn't stop response


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8080"))
    host = os.getenv("HOST", "0.0.0.0")

    uvicorn.run(
        "agent_service.main:app",
        host=host,
        port=port,
        reload=os.getenv("RELOAD", "false").lower() == "true",
        log_level="info",
    )
