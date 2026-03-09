"""Shared CloudEvent utilities for all services."""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import structlog
from cloudevents.http import CloudEvent, to_structured

logger = structlog.get_logger()


# CloudEvent type constants
class EventTypes:
    """CloudEvent type constants for the partner agent system."""

    # Request events
    REQUEST_CREATED = "com.partner-agent.request.created"
    REQUEST_PROCESSING = "com.partner-agent.request.processing"

    # Response events
    AGENT_RESPONSE_READY = "com.partner-agent.agent.response-ready"

    # Database update events
    DATABASE_UPDATE_REQUESTED = "com.partner-agent.request.database-update"

    # Session events
    SESSION_CREATE_OR_GET = "com.partner-agent.session.create-or-get"
    SESSION_READY = "com.partner-agent.session.ready"


class CloudEventBuilder:
    """Builder for creating standardized CloudEvents."""

    def __init__(self, source: str, service_name: str = "partner-agent"):
        self.source = source
        self.service_name = service_name
        self.base_attributes = {
            "source": source,
            "specversion": "1.0",
            "datacontenttype": "application/json",
        }

    def create_request_event(
        self,
        request_data: Dict[str, Any],
        request_id: Optional[str] = None,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> CloudEvent:
        """Create a request created event."""
        event_id = request_id or str(uuid.uuid4())

        attributes = {
            **self.base_attributes,
            "type": EventTypes.REQUEST_CREATED,
            "id": event_id,
            "time": datetime.now(timezone.utc).isoformat(),
        }

        # Add optional attributes
        if user_id:
            attributes["userid"] = user_id
        if session_id:
            attributes["sessionid"] = session_id

        return CloudEvent(attributes, request_data)

    def create_response_event(
        self,
        response_data: Dict[str, Any],
        request_id: str,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> CloudEvent:
        """Create an agent response ready event."""
        attributes = {
            **self.base_attributes,
            "type": EventTypes.AGENT_RESPONSE_READY,
            "id": str(uuid.uuid4()),
            "time": datetime.now(timezone.utc).isoformat(),
            "requestid": request_id,
        }

        # Add optional attributes
        if agent_id:
            attributes["agentid"] = agent_id
        if session_id:
            attributes["sessionid"] = session_id

        return CloudEvent(attributes, response_data)

    def create_session_create_or_get_event(
        self,
        session_data: Dict[str, Any],
        event_id: Optional[str] = None,
        user_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> CloudEvent:
        """Create a session create-or-get event."""
        event_id = event_id or str(uuid.uuid4())

        attributes = {
            **self.base_attributes,
            "type": EventTypes.SESSION_CREATE_OR_GET,
            "id": event_id,
            "time": datetime.now(timezone.utc).isoformat(),
        }

        # Add optional attributes
        if user_id:
            attributes["userid"] = user_id
        if correlation_id:
            attributes["correlationid"] = correlation_id

        return CloudEvent(attributes, session_data)

    def create_session_ready_event(
        self,
        session_data: Dict[str, Any],
        event_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> CloudEvent:
        """Create a session ready event."""
        attributes = {
            **self.base_attributes,
            "type": EventTypes.SESSION_READY,
            "id": event_id or str(uuid.uuid4()),
            "time": datetime.now(timezone.utc).isoformat(),
        }

        # Add optional attributes
        if correlation_id:
            attributes["correlationid"] = correlation_id
        if session_id:
            attributes["sessionid"] = session_id

        return CloudEvent(attributes, session_data)


class CloudEventSender:
    """Sender for CloudEvents to brokers."""

    def __init__(self, broker_url: str, service_name: str):
        self.broker_url = broker_url
        self.service_name = service_name
        self.builder = CloudEventBuilder(service_name, service_name)

    async def send_request_event(
        self,
        request_data: Dict[str, Any],
        request_id: Optional[str] = None,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> bool:
        """Send a request created event."""
        try:
            event = self.builder.create_request_event(
                request_data, request_id, user_id, session_id
            )
            return await self._send_event(event)
        except Exception as e:
            logger.error("Failed to send request event", error=str(e))
            return False

    async def send_response_event(
        self,
        response_data: Dict[str, Any],
        request_id: str,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> bool:
        """Send an agent response ready event."""
        try:
            event = self.builder.create_response_event(
                response_data, request_id, agent_id, session_id
            )
            return await self._send_event(event)
        except Exception as e:
            logger.error("Failed to send response event", error=str(e))
            return False

    async def send_session_create_or_get_event(
        self,
        session_data: Dict[str, Any],
        event_id: Optional[str] = None,
        user_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> bool:
        """Send a session create-or-get event."""
        try:
            event = self.builder.create_session_create_or_get_event(
                session_data, event_id, user_id, correlation_id
            )
            return await self._send_event(event)
        except Exception as e:
            logger.error("Failed to send session create-or-get event", error=str(e))
            return False

    async def send_session_ready_event(
        self,
        session_data: Dict[str, Any],
        event_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> bool:
        """Send a session ready event."""
        try:
            event = self.builder.create_session_ready_event(
                session_data, event_id, correlation_id, session_id
            )
            return await self._send_event(event)
        except Exception as e:
            logger.error("Failed to send session ready event", error=str(e))
            return False

    async def _send_event(self, event: CloudEvent) -> bool:
        """Send a CloudEvent to the broker."""
        try:
            import httpx

            logger.debug(
                "Sending CloudEvent to broker",
                broker_url=self.broker_url,
                event_type=event["type"],
                event_id=event["id"],
            )

            # Convert to structured format
            headers, data = to_structured(event)

            async with httpx.AsyncClient() as client:
                logger.debug(
                    "Making HTTP POST request to broker", broker_url=self.broker_url
                )
                response = await client.post(
                    self.broker_url,
                    headers=headers,
                    content=data,
                    timeout=30.0,
                )
                logger.debug(
                    "HTTP response received",
                    status_code=response.status_code,
                    broker_url=self.broker_url,
                )
                response.raise_for_status()

                logger.debug(
                    "CloudEvent sent successfully",
                    event_type=event["type"],
                    event_id=event["id"],
                    status_code=response.status_code,
                )
                return True

        except Exception as e:
            logger.error(
                "Failed to send CloudEvent",
                event_type=event["type"],
                event_id=event["id"],
                broker_url=self.broker_url,
                error=str(e),
                error_type=type(e).__name__,
            )
            return False
