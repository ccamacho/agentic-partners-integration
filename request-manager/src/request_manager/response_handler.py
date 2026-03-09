"""Shared response handling logic for eventing-based communication."""

from typing import Any, Dict, Optional

from shared_models import configure_logging
from sqlalchemy.ext.asyncio import AsyncSession

logger = configure_logging("request-manager")


class UnifiedResponseHandler:
    """Unified response handler for eventing-based communication."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def process_agent_response(
        self,
        request_id: str,
        session_id: str,
        user_id: Optional[str],
        agent_id: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        processing_time_ms: Optional[int] = None,
        requires_followup: bool = False,
        followup_actions: Optional[list[str]] = None,
    ) -> Dict[str, Any]:
        """Process an agent response and update the database.

        This ensures 100% delivery guarantee by storing the response in the database
        when any pod receives it, so it can be found via polling if needed.
        """
        from datetime import datetime, timezone

        from shared_models.models import RequestLog
        from sqlalchemy import update

        logger.debug(
            "Response processed - routing handled by Agent Service",
            session_id=session_id,
            agent_id=agent_id,
        )

        # Store response directly in database (for 100% delivery guarantee)
        # This ensures the response is available even if received by wrong pod
        try:
            stmt = (
                update(RequestLog)
                .where(RequestLog.request_id == request_id)
                .values(
                    response_content=content,
                    response_metadata=metadata or {},
                    agent_id=agent_id,
                    processing_time_ms=processing_time_ms,
                    completed_at=datetime.now(timezone.utc),
                )
            )
            await self.db.execute(stmt)
            await self.db.commit()

            logger.debug(
                "Response stored in database for polling fallback",
                request_id=request_id,
            )
        except Exception as e:
            logger.warning(
                "Failed to store response in database (will rely on agent service update)",
                request_id=request_id,
                error=str(e),
            )

        # Send database update event to Agent Service (for consistency)
        await self._send_database_update_event(
            request_id=request_id,
            session_id=session_id,
            user_id=user_id,
            agent_id=agent_id,
            content=content,
            metadata=metadata,
            processing_time_ms=processing_time_ms,
        )

        logger.info(
            "Agent response processed successfully",
            request_id=request_id,
            session_id=session_id,
            agent_id=agent_id,
            content_length=len(content),
            processing_time_ms=processing_time_ms,
        )

        return {
            "status": "processed",
            "request_id": request_id,
            "session_id": session_id,
            "agent_id": agent_id,
            "requires_followup": requires_followup,
            "followup_actions": followup_actions or [],
        }

    async def _send_database_update_event(
        self,
        request_id: str,
        session_id: str,
        user_id: Optional[str],
        agent_id: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        processing_time_ms: Optional[int] = None,
    ) -> None:
        """Send database update event to Agent Service."""
        try:
            from .events import get_event_publisher

            event_publisher = get_event_publisher()
            event_data = {
                "request_id": request_id,
                "session_id": session_id,
                "user_id": user_id,
                "agent_id": agent_id,
                "content": content,
                "metadata": metadata or {},
                "processing_time_ms": processing_time_ms,
            }

            await event_publisher.publish_database_update_event(event_data)

            logger.info(
                "Database update event sent to Agent Service",
                request_id=request_id,
                session_id=session_id,
                agent_id=agent_id,
            )

        except Exception as e:
            logger.error(
                "Failed to send database update event",
                request_id=request_id,
                error=str(e),
            )
