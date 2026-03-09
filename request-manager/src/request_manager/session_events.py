"""Session event handling for eventing-based session management."""

import asyncio
from typing import Any, Dict, Optional

from shared_models import (
    BaseSessionManager,
    SessionCreate,
    SessionResponse,
    configure_logging,
    create_cloudevent_response,
    resolve_canonical_user_id,
)
from shared_models.cloudevent_utils import CloudEventHandler
from sqlalchemy.ext.asyncio import AsyncSession

from .communication_strategy import _session_futures_registry

logger = configure_logging("request-manager")


async def _handle_session_create_or_get_event(
    event_data: Dict[str, Any], db: AsyncSession
) -> Dict[str, Any]:
    """Handle session create-or-get event with atomic claiming.

    This handler processes session creation events with atomic claiming
    to prevent race conditions. Only one pod can process each event.
    """
    event_id = event_data.get("id")
    correlation_id = event_data.get("correlationid") or event_data.get("correlation_id")

    try:
        # Extract session request data
        session_request = CloudEventHandler.extract_event_data(event_data)
        user_id = session_request.get("user_id")
        integration_type_str = session_request.get("integration_type")

        if not user_id:
            logger.error("Missing user_id in session create event", event_id=event_id)
            return await create_cloudevent_response(
                status="error",
                message="Missing user_id in session request",
                details={"event_id": event_id},
            )

        if not integration_type_str:
            logger.error(
                "Missing integration_type in session create event", event_id=event_id
            )
            return await create_cloudevent_response(
                status="error",
                message="Missing integration_type in session request",
                details={"event_id": event_id},
            )

        # Resolve canonical user_id
        from shared_models.models import IntegrationType

        integration_type = IntegrationType(integration_type_str.upper())
        canonical_user_id = await resolve_canonical_user_id(
            user_id, integration_type=integration_type, db=db
        )

        # Check for existing active session first (fast path)
        session_manager = BaseSessionManager(db)
        existing_session = await session_manager.get_active_session(
            canonical_user_id, integration_type
        )

        if existing_session:
            logger.info(
                "Found existing active session",
                session_id=existing_session.session_id,
                user_id=canonical_user_id,
                event_id=event_id,
            )
            # Publish SESSION_READY event
            await _publish_session_ready_event(
                existing_session, correlation_id, event_id
            )
            return await create_cloudevent_response(
                status="success",
                message="Existing session found",
                details={
                    "session_id": existing_session.session_id,
                    "event_id": event_id,
                },
            )

        # Create new session
        session_data = SessionCreate(
            user_id=canonical_user_id,
            integration_type=integration_type,
            channel_id=session_request.get("channel_id"),
            thread_id=session_request.get("thread_id"),
            external_session_id=session_request.get("external_session_id"),
            integration_metadata=session_request.get("integration_metadata", {}),
            user_context=session_request.get("user_context", {}),
        )

        try:
            new_session = await session_manager.create_session(session_data)
            logger.info(
                "Created new session via event",
                session_id=new_session.session_id,
                user_id=canonical_user_id,
                event_id=event_id,
            )

            # Publish SESSION_READY event
            await _publish_session_ready_event(new_session, correlation_id, event_id)

            return await create_cloudevent_response(
                status="success",
                message="Session created successfully",
                details={
                    "session_id": new_session.session_id,
                    "event_id": event_id,
                },
            )
        except Exception as e:
            logger.error(
                "Failed to create session via event",
                user_id=canonical_user_id,
                event_id=event_id,
                error=str(e),
            )
            # Try one more time to get existing session (might have been created by another pod)
            existing_session = await session_manager.get_active_session(
                canonical_user_id, integration_type
            )
            if existing_session:
                logger.info(
                    "Found existing session after creation failure",
                    session_id=existing_session.session_id,
                    user_id=canonical_user_id,
                    event_id=event_id,
                )
                await _publish_session_ready_event(
                    existing_session, correlation_id, event_id
                )
                return await create_cloudevent_response(
                    status="success",
                    message="Existing session found after creation failure",
                    details={
                        "session_id": existing_session.session_id,
                        "event_id": event_id,
                    },
                )
            raise

    except Exception as e:
        logger.error(
            "Failed to handle session create-or-get event",
            event_id=event_id,
            error=str(e),
        )
        return await create_cloudevent_response(
            status="error",
            message="Failed to process session create event",
            details={"event_id": event_id, "error": str(e)},
        )


async def _handle_session_ready_event(
    event_data: Dict[str, Any], db: AsyncSession
) -> Dict[str, Any]:
    """Handle session ready event - resolves waiting futures."""
    event_id = event_data.get("id")
    correlation_id = event_data.get("correlationid") or event_data.get("correlation_id")

    if not correlation_id:
        logger.warning("Session ready event missing correlation_id", event_id=event_id)
        return await create_cloudevent_response(
            status="error",
            message="Missing correlation_id in session ready event",
            details={"event_id": event_id},
        )

    # Extract session data
    session_data = CloudEventHandler.extract_event_data(event_data)
    session_id = session_data.get("session_id")

    logger.debug(
        "Session ready event received",
        event_id=event_id,
        correlation_id=correlation_id,
        session_id=session_id,
    )

    # Resolve waiting future if it exists
    if correlation_id in _session_futures_registry:
        future = _session_futures_registry[correlation_id]
        if not future.done():
            try:
                session_response = SessionResponse.model_validate(session_data)
                future.set_result(session_response)
                logger.info(
                    "Resolved session future",
                    correlation_id=correlation_id,
                    session_id=session_id,
                )
            except Exception as e:
                logger.error(
                    "Failed to resolve session future",
                    correlation_id=correlation_id,
                    error=str(e),
                )
                future.set_exception(e)
        else:
            logger.debug(
                "Session future already resolved",
                correlation_id=correlation_id,
            )
    else:
        logger.debug(
            "No waiting future for session event",
            correlation_id=correlation_id,
        )

    return await create_cloudevent_response(
        status="success",
        message="Session ready event processed",
        details={"event_id": event_id, "session_id": session_id},
    )


async def _publish_session_ready_event(
    session: SessionResponse,
    correlation_id: Optional[str],
    original_event_id: Optional[str],
) -> bool:
    """Publish SESSION_READY event."""
    try:
        import os

        from shared_models import CloudEventSender

        broker_url = os.getenv("BROKER_URL", "http://knative-broker:8080")
        event_sender = CloudEventSender(broker_url, "request-manager")

        session_data = session.model_dump(mode="json")
        success = await event_sender.send_session_ready_event(
            session_data=session_data,
            correlation_id=correlation_id,
            session_id=session.session_id,
        )

        if success:
            logger.info(
                "Published SESSION_READY event",
                session_id=session.session_id,
                correlation_id=correlation_id,
                original_event_id=original_event_id,
            )
        else:
            logger.error(
                "Failed to publish SESSION_READY event",
                session_id=session.session_id,
                correlation_id=correlation_id,
            )

        return success
    except Exception as e:
        logger.error(
            "Error publishing SESSION_READY event",
            session_id=session.session_id,
            correlation_id=correlation_id,
            error=str(e),
        )
        return False


async def wait_for_session_ready(
    correlation_id: str, timeout: float = 1.0, db: Optional[AsyncSession] = None
) -> Optional[SessionResponse]:
    """Wait for SESSION_READY event with timeout.

    Args:
        correlation_id: The correlation ID from the SESSION_CREATE_OR_GET event
        timeout: Maximum time to wait in seconds
        db: Optional database session for fallback lookup

    Returns:
        SessionResponse if event received, None if timeout
    """
    # Create future for this correlation_id
    future: asyncio.Future[SessionResponse] = asyncio.Future()
    _session_futures_registry[correlation_id] = future

    try:
        logger.debug(
            "Waiting for SESSION_READY event",
            correlation_id=correlation_id,
            timeout=timeout,
        )

        # Wait for event with timeout
        session = await asyncio.wait_for(future, timeout=timeout)

        logger.info(
            "Received SESSION_READY event",
            correlation_id=correlation_id,
            session_id=session.session_id,
        )

        return session

    except asyncio.TimeoutError:
        logger.warning(
            "Timeout waiting for SESSION_READY event",
            correlation_id=correlation_id,
            timeout=timeout,
        )

        # Fallback: Try to get session from database if correlation_id is session_id
        if db:
            try:
                session_manager = BaseSessionManager(db)
                fallback_session = await session_manager.get_session(correlation_id)
                if fallback_session:
                    logger.info(
                        "Found session in database after timeout",
                        correlation_id=correlation_id,
                        session_id=fallback_session.session_id,
                    )
                    return fallback_session
            except Exception as e:
                logger.error(
                    "Failed to get session from database after timeout",
                    correlation_id=correlation_id,
                    error=str(e),
                )

        return None

    finally:
        # Clean up future
        if correlation_id in _session_futures_registry:
            del _session_futures_registry[correlation_id]
