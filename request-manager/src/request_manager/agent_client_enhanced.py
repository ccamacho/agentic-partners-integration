"""
Enhanced HTTP client for invoking agents with structured context support.

This extends the base AgentClient with conversation history tracking
and structured context extraction support.
"""

import os
from typing import Any, Dict, List, Optional

import httpx
from shared_models import configure_logging

from .credential_service import CredentialService

logger = configure_logging("request-manager")

# Feature flag
STRUCTURED_CONTEXT_ENABLED = os.getenv("STRUCTURED_CONTEXT_ENABLED", "true").lower() == "true"


class EnhancedAgentClient:
    """
    Enhanced HTTP client for agent-to-agent communication with structured context.

    This client extends the basic agent invocation with:
    - Conversation history tracking
    - Previous agent tracking (for context optimization)
    - Structured context extraction support
    - Automatic credential propagation
    """

    def __init__(
        self,
        agent_service_url: str = "http://agent-service:8080",
        timeout: float = 120.0,
    ):
        """
        Initialize enhanced agent client.

        Args:
            agent_service_url: Base URL for agent service
            timeout: Request timeout in seconds
        """
        self.agent_service_url = agent_service_url.rstrip("/")
        self.client = httpx.AsyncClient(timeout=timeout)

        logger.info(
            "Initialized EnhancedAgentClient",
            agent_service_url=self.agent_service_url,
            timeout=timeout,
            structured_context_enabled=STRUCTURED_CONTEXT_ENABLED,
        )

    async def invoke_agent(
        self,
        agent_name: str,
        session_id: str,
        user_id: str,
        message: str,
        transfer_context: Optional[Dict[str, Any]] = None,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        previous_agent: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Invoke an agent via HTTP with structured context support.

        Args:
            agent_name: Name of agent to invoke (routing-agent, laptop-refresh, etc.)
            session_id: Request manager session ID
            user_id: User identifier (email or ID)
            message: User message to process
            transfer_context: Optional context from previous agent
            conversation_history: Optional conversation history for context extraction
            previous_agent: Optional name of the agent that handled previous turn

        Returns:
            Dictionary with agent response:
            {
                "content": str,
                "agent_id": str,
                "session_id": str,
                "routing_decision": Optional[str],
                "metadata": Optional[Dict]
            }

        Raises:
            httpx.HTTPError: If HTTP request fails

        Example:
            >>> client = EnhancedAgentClient()
            >>> response = await client.invoke_agent(
            ...     agent_name="laptop-refresh-agent",
            ...     session_id="session-123",
            ...     user_id="user@example.com",
            ...     message="What models are available?",
            ...     conversation_history=[
            ...         {"role": "user", "content": "I need a laptop"},
            ...         {"role": "assistant", "content": "What region?"},
            ...         {"role": "user", "content": "EMEA"}
            ...     ],
            ...     previous_agent="routing-agent"
            ... )
        """
        url = f"{self.agent_service_url}/api/v1/agents/{agent_name}/invoke"

        # Build enhanced transfer context
        enhanced_context = transfer_context or {}

        # Add conversation history if provided and feature enabled
        if STRUCTURED_CONTEXT_ENABLED and conversation_history:
            enhanced_context["conversation_history"] = conversation_history
            enhanced_context["previous_agent"] = previous_agent
            enhanced_context["current_agent"] = agent_name
            enhanced_context["enable_context_extraction"] = True

            # Log context optimization info
            is_agent_switch = previous_agent and (previous_agent != agent_name)
            logger.debug(
                "Enhanced context with conversation history",
                agent_name=agent_name,
                history_length=len(conversation_history),
                previous_agent=previous_agent,
                is_agent_switch=is_agent_switch,
                extraction_mode="rewrite" if is_agent_switch else "metadata"
            )

        payload = {
            "session_id": session_id,
            "user_id": user_id,
            "message": message,
            "transfer_context": enhanced_context,
        }

        # Build headers with authentication from credential service
        headers = {}
        auth_header = CredentialService.get_auth_header()
        if auth_header:
            headers["Authorization"] = auth_header

        logger.info(
            "Invoking agent",
            agent_name=agent_name,
            session_id=session_id,
            url=url,
            message_length=len(message),
            has_auth=bool(auth_header),
            has_conversation_history=bool(conversation_history),
            structured_context_enabled=STRUCTURED_CONTEXT_ENABLED,
        )

        try:
            response = await self.client.post(url, json=payload, headers=headers)
            response.raise_for_status()

            data = response.json()

            logger.info(
                "Agent invocation successful",
                agent_name=agent_name,
                session_id=session_id,
                has_routing=bool(data.get("routing_decision")),
                response_length=len(data.get("content", "")),
                context_was_extracted=data.get("metadata", {}).get("context_extracted", False)
            )

            return data

        except httpx.HTTPError as e:
            logger.error(
                "Agent invocation failed",
                agent_name=agent_name,
                session_id=session_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            raise

    async def close(self) -> None:
        """Close the HTTP client."""
        await self.client.aclose()

    async def __aenter__(self):
        """Async context manager enter."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()
