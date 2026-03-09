"""Main FastAPI application for Request Manager."""

import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from shared_models import (
    CloudEventHandler,
    CloudEventSender,
    EventTypes,
    configure_logging,
    create_cloudevent_response,
    create_health_check_endpoint,
    create_shared_lifespan,
    get_db_session_dependency,
    parse_cloudevent_from_request,
)
from shared_models.models import ErrorResponse
from sqlalchemy.ext.asyncio import AsyncSession
from . import __version__
from .communication_strategy import (
    UnifiedRequestProcessor,
    check_communication_strategy,
    get_communication_strategy,
)
from .credential_service import CredentialService
from .response_handler import UnifiedResponseHandler
from .schemas import (
    BaseRequest,
    HealthCheck,
)

# Configure structured logging
SERVICE_NAME = "request-manager"
logger = configure_logging(SERVICE_NAME)


async def _session_cleanup_task() -> None:
    """Background task to periodically clean up expired and inactive sessions."""
    import asyncio

    from shared_models import get_database_manager

    cleanup_interval_hours = int(os.getenv("SESSION_CLEANUP_INTERVAL_HOURS", "24"))
    cleanup_interval_seconds = cleanup_interval_hours * 3600
    inactive_session_retention_days = int(
        os.getenv("INACTIVE_SESSION_RETENTION_DAYS", "30")
    )

    logger.info(
        "Starting session cleanup task",
        cleanup_interval_hours=cleanup_interval_hours,
        inactive_session_retention_days=inactive_session_retention_days,
    )

    while True:
        try:
            await asyncio.sleep(cleanup_interval_seconds)

            db_manager = get_database_manager()
            async with db_manager.get_session() as db:
                from .database_utils import (
                    delete_inactive_sessions,
                    expire_old_sessions,
                )

                # First, expire sessions that have passed their expiration time
                expired_count = await expire_old_sessions(db)

                # Then, delete inactive sessions older than retention period
                deleted_count = await delete_inactive_sessions(
                    db, older_than_days=inactive_session_retention_days
                )

                if expired_count > 0 or deleted_count > 0:
                    logger.info(
                        "Session cleanup completed",
                        expired_count=expired_count,
                        deleted_count=deleted_count,
                    )

        except asyncio.CancelledError:
            logger.info("Session cleanup task cancelled")
            break
        except Exception as e:
            logger.error(
                "Error in session cleanup task",
                error=str(e),
                error_type=type(e).__name__,
            )
            # Continue running even on error
            await asyncio.sleep(60)  # Wait 1 minute before retrying on error


async def _request_manager_startup() -> None:
    """Custom startup logic for Request Manager."""
    import asyncio

    # Initialize unified processor
    global unified_processor
    communication_strategy = get_communication_strategy()

    unified_processor = UnifiedRequestProcessor(communication_strategy)
    logger.info(
        "Initialized unified request processor",
        strategy_type=type(communication_strategy).__name__,
    )

    # Start single per-pod polling task
    from .communication_strategy import get_pod_name

    pod_name = get_pod_name()
    if pod_name:
        from .communication_strategy import _start_pod_polling_task

        await _start_pod_polling_task(pod_name)
        logger.info(
            "Started single per-pod polling task",
            pod_name=pod_name,
        )
    else:
        logger.warning(
            "Pod name not found in environment (HOSTNAME or POD_NAME) - single pod polling not started"
        )

    # Start session cleanup background task
    asyncio.create_task(_session_cleanup_task())
    logger.info("Started session cleanup background task")


# Create lifespan using shared utility with custom startup
def lifespan(app: FastAPI) -> Any:
    return create_shared_lifespan(
        service_name="request-manager",
        version=__version__,
        custom_startup=_request_manager_startup,
    )


# Create FastAPI application
app = FastAPI(
    title="Partner Agent Request Manager",
    description="Request Management Layer for Partner Agent Integration",
    version=__version__,
    lifespan=lifespan,
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include authentication endpoints
from .auth_endpoints import router as auth_router
app.include_router(auth_router)

# Include ADK (Agent Development Kit) web UI compatible endpoints
from .adk_endpoints import router as adk_router
app.include_router(adk_router)


# Add credential management middleware
@app.middleware("http")
async def credential_context_middleware(request: Request, call_next):
    """
    Middleware to extract and store credentials in request context.

    This allows credentials to be accessed anywhere in the call stack
    without passing them through function parameters.
    """
    try:
        # Extract authorization header
        auth_header = request.headers.get("Authorization", "")
        if auth_header:
            CredentialService.set_token(auth_header)

        # Extract user_id if available from query params or headers
        user_id = request.headers.get("X-User-ID") or request.query_params.get("user_id")
        if user_id:
            CredentialService.set_user_id(user_id)

        # Extract session_id if available
        session_id = request.headers.get("X-Session-ID") or request.query_params.get("session_id")
        if session_id:
            CredentialService.set_session_id(session_id)

        # Process request
        response = await call_next(request)

        return response
    finally:
        # Always clean up credentials after request
        CredentialService.clear_credentials()


# Initialize components
unified_processor: Optional[UnifiedRequestProcessor] = None


@app.get("/health")
async def health_check() -> Dict[str, Any]:
    """Health check endpoint - lightweight without database dependency."""
    return {
        "status": "healthy",
        "service": "request-manager",
        "version": __version__,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/health/detailed", response_model=HealthCheck)
async def detailed_health_check(
    db: AsyncSession = Depends(get_db_session_dependency),
) -> HealthCheck:
    """Detailed health check with database dependency for monitoring."""
    result = await create_health_check_endpoint(
        service_name="request-manager",
        version=__version__,
        db=db,
        additional_checks={"communication_strategy": check_communication_strategy},
    )

    return HealthCheck(
        status=result["status"],
        database_connected=result["database_connected"],
        services=result["services"],
    )


@app.post("/api/v1/events/cloudevents")
async def handle_cloudevent(
    request: Request,
    db: AsyncSession = Depends(get_db_session_dependency),
) -> Dict[str, Any]:
    """Handle incoming CloudEvents (e.g., from agent responses)."""
    try:
        # Parse CloudEvent from request using shared utility
        event_data = await parse_cloudevent_from_request(request)

        event_id = event_data.get("id")
        event_type = event_data.get("type")
        event_source = event_data.get("source")

        logger.info(
            "CloudEvent received",
            event_id=event_id,
            event_type=event_type,
            event_source=event_source,
        )

        # Validate required CloudEvent fields (type and source are required per spec)
        if not event_type or not event_source:
            logger.warning(
                "CloudEvent missing required fields",
                event_id=event_id,
                has_type=bool(event_type),
                has_source=bool(event_source),
            )
            return await create_cloudevent_response(
                status="error",
                message="CloudEvent missing required fields (type, source)",
                details={"event_id": event_id},
            )

        # ✅ CIRCUIT BREAKER: Prevent feedback loops by ignoring self-generated events
        # Exception: Allow session events from request-manager (we send them to ourselves)
        if (
            "request-manager" in event_source or event_source == "request-manager"
        ) and event_type not in [
            EventTypes.SESSION_CREATE_OR_GET,
            EventTypes.SESSION_READY,
        ]:
            logger.info(
                "Ignoring self-generated event to prevent feedback loop",
                event_id=event_id,
                event_type=event_type,
                event_source=event_source,
            )
            return {"status": "ignored", "reason": "self-generated event"}

        # ✅ ATOMIC EVENT CLAIMING: Use check-and-set pattern to prevent duplicate processing
        # This provides 100% guarantee - only one pod can claim and process an event
        if event_id:
            from .database_utils import try_claim_event_for_processing

            event_claimed = await try_claim_event_for_processing(
                db,
                event_id,
                event_type,
                event_source,
                "request-manager",
            )

            if not event_claimed:
                logger.info(
                    "Event already claimed by another pod - skipping duplicate",
                    event_id=event_id,
                    event_type=event_type,
                    event_source=event_source,
                )
                return {
                    "status": "skipped",
                    "reason": "duplicate event (already claimed by another pod)",
                    "event_id": event_id,
                }

        # Handle session create-or-get events
        if event_type == EventTypes.SESSION_CREATE_OR_GET:
            from .session_events import _handle_session_create_or_get_event

            return await _handle_session_create_or_get_event(event_data, db)

        # Handle session ready events
        if event_type == EventTypes.SESSION_READY:
            from .session_events import _handle_session_ready_event

            return await _handle_session_ready_event(event_data, db)

        # Handle request created events (from integration dispatcher)
        if event_type == EventTypes.REQUEST_CREATED:
            return await _handle_request_created_event_from_data(event_data, db)

        # Handle agent response events
        if event_type == EventTypes.AGENT_RESPONSE_READY:
            # Use the already parsed event data from shared utility
            return await _handle_agent_response_event_from_data(event_data, db)

        logger.warning("Unhandled CloudEvent type", event_type=event_type)
        return await create_cloudevent_response(
            status="ignored",
            message="Unhandled event type",
            details={"event_type": event_type},
        )

    except Exception as e:
        logger.error("Failed to handle CloudEvent", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to process CloudEvent",
        )


async def _process_request_adaptive(
    request: BaseRequest,
    db: AsyncSession,
    timeout: int = int(os.getenv("AGENT_TIMEOUT", "120")),
    is_cloudevent_request: bool = False,
) -> Dict[str, Any]:
    """Process a request synchronously and return the actual AI response.

    All user-facing endpoints return immediate responses. Service-to-service
    communication uses CloudEvents/eventing.

    Args:
        is_cloudevent_request: If True, this is a CloudEvent request
                             (doesn't need pod_name since the caller handles responses separately).
                             If False, this is a regular request-manager endpoint (needs pod_name for polling).
    """
    if not unified_processor:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unified processor not initialized",
        )

    try:
        # Use unified processor for all requests (eventing-based communication)
        return await unified_processor.process_request_sync(
            request, db, timeout, set_pod_name=not is_cloudevent_request
        )
    except Exception as e:
        logger.error("Failed to process request", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process request",
        )


async def _handle_request_created_event_from_data(
    event_data: Dict[str, Any], db: AsyncSession
) -> Dict[str, Any]:
    """Handle request created CloudEvent."""
    from shared_models.models import IntegrationType

    # Extract event metadata
    event_id = event_data.get("id")

    try:
        # Extract request data from CloudEvent
        request_data = CloudEventHandler.extract_event_data(event_data)

        # Convert to appropriate request schema based on integration_type
        integration_type_str = request_data.get("integration_type")
        if not integration_type_str:
            logger.error("Missing integration_type in request event data")
            return await create_cloudevent_response(
                status="error",
                message="Missing integration_type",
                details={"event_id": event_id},
            )

        integration_type = IntegrationType(integration_type_str.upper())

        # Validate required fields
        user_id = request_data.get("user_id")
        content = request_data.get("content")
        if not user_id or not content:
            logger.error(
                "Missing required fields in request event data",
                user_id=bool(user_id),
                content=bool(content),
            )
            return await create_cloudevent_response(
                status="error",
                message="Missing required fields (user_id, content)",
                details={"event_id": event_id},
            )

        # Extract session_id if provided (e.g., from X-Session-ID header)
        # This allows continuing an existing session
        provided_session_id = request_data.get("session_id")
        metadata = request_data.get("metadata", {})
        if provided_session_id:
            metadata["session_id"] = provided_session_id
            logger.info(
                "Found session_id in request data, will attempt to reuse session",
                session_id=provided_session_id,
                integration_type=integration_type_str,
                user_id=user_id,
            )
        else:
            logger.debug(
                "No session_id provided in request data, will create or find session by user_id",
                integration_type=integration_type_str,
                user_id=user_id,
                request_data_keys=list(request_data.keys()),
            )

        # Extract common base fields
        base_fields = {
            "user_id": str(user_id),
            "content": str(content),
            "integration_type": integration_type,
            "metadata": metadata,
        }

        request = BaseRequest(**base_fields)

        # Process the request using the existing adaptive processor
        logger.info(
            "Processing request from CloudEvent",
            integration_type=integration_type_str,
            user_id=request.user_id,
            event_id=event_id,
        )

        result = await _process_request_adaptive(
            request, db, is_cloudevent_request=True
        )

        # Record successful event processing to prevent duplicate processing
        # This is critical for preventing race conditions when multiple pods receive the same event
        if event_id:
            from shared_models import EventTypes

            from .database_utils import record_processed_event

            await record_processed_event(
                db,
                event_id,
                EventTypes.REQUEST_CREATED,
                event_data.get("source", "request-manager"),
                result.get("request_id") if isinstance(result, dict) else None,
                result.get("session_id") if isinstance(result, dict) else None,
                "request-manager",
                "success",
            )

        return result

    except Exception as e:
        logger.error(
            "Failed to handle request created event",
            event_id=event_id,
            error=str(e),
            exc_info=True,
        )

        # Record failed event processing
        if event_id:
            from shared_models import EventTypes

            from .database_utils import record_processed_event

            await record_processed_event(
                db,
                event_id,
                EventTypes.REQUEST_CREATED,
                event_data.get("source", "request-manager"),
                None,  # request_id unknown on error
                None,  # session_id unknown on error
                "request-manager",
                "error",
                str(e),
            )

        return await create_cloudevent_response(
            status="error",
            message="Failed to process request event",
            details={"event_id": event_id, "error": str(e)},
        )


async def _handle_agent_response_event_from_data(
    event_data: Dict[str, Any], db: AsyncSession
) -> Dict[str, Any]:
    """Handle agent response CloudEvent using unified response handler with pre-parsed data."""

    # Extract event metadata using common utility
    event_id, event_type, event_source = CloudEventHandler.get_event_metadata(
        event_data
    )

    try:
        # Extract response data using common utility
        response_data = CloudEventHandler.extract_event_data(event_data)
        request_id, session_id, agent_id, content, user_id = (
            CloudEventHandler.extract_response_data(response_data)
        )

        # Log session_id presence for debugging
        logger.info(
            "Extracted response data from CloudEvent",
            request_id=request_id,
            session_id=session_id,
            has_session_id_in_response_data=bool(response_data.get("session_id")),
            response_data_keys=list(response_data.keys()),
        )

        # Use unified response handler (payload is in response_data = event_data["data"])
        response_handler = UnifiedResponseHandler(db)
        result = await response_handler.process_agent_response(
            request_id=request_id,
            session_id=session_id,
            user_id=user_id,
            agent_id=agent_id,
            content=content,
            metadata=response_data.get("metadata", {}),
            processing_time_ms=response_data.get("processing_time_ms"),
            requires_followup=response_data.get("requires_followup", False),
            followup_actions=response_data.get("followup_actions", []),
        )

        # Resolve any waiting response futures for this request (fast path)
        # Note: If pod_name is NULL, ANY pod that receives the response event can immediately
        # process it if it has a waiting future. This provides the fastest possible response.
        # If no future found (wrong pod or no waiting request), response is still stored in database
        # and will be picked up by database polling in wait_for_response
        try:
            from request_manager.communication_strategy import resolve_response_future

            # Construct complete response_data dict with all required fields (payload is in response_data)
            complete_response_data = {
                "request_id": request_id,
                "session_id": session_id,
                "agent_id": agent_id,
                "content": content,
                "metadata": response_data.get("metadata", {}),
                "processing_time_ms": response_data.get("processing_time_ms"),
                "requires_followup": response_data.get("requires_followup", False),
                "followup_actions": response_data.get("followup_actions", []),
            }

            future_resolved = resolve_response_future(
                request_id, complete_response_data
            )
            if future_resolved:
                logger.info(
                    "Response future resolved via event (fast path)",
                    request_id=request_id,
                )
            else:
                # No waiting future found - response is stored in database
                # The correct pod's polling will find it (or any pod if pod_name is NULL)
                logger.debug(
                    "No waiting response future found - response stored in database, will be picked up by polling",
                    request_id=request_id,
                )
        except Exception as e:
            logger.debug(
                "Error resolving response future",
                request_id=request_id,
                error=str(e),
            )

        # Only forward response to Integration Dispatcher if it was actually processed
        if result.get("status") == "processed":
            # Ensure response_data has session_id before forwarding
            if "session_id" not in response_data:
                response_data["session_id"] = session_id
                logger.warning(
                    "Added missing session_id to response_data before forwarding",
                    request_id=request_id,
                    session_id=session_id,
                )
            logger.info(
                "Forwarding response to Integration Dispatcher",
                request_id=request_id,
                session_id=response_data.get("session_id"),
                has_session_id=bool(response_data.get("session_id")),
            )
            await _forward_response_to_integration_dispatcher(
                response_data, is_routing_response=False
            )
        else:
            logger.info(
                "Skipping Integration Dispatcher forwarding for duplicate response",
                request_id=request_id,
                status=result.get("status"),
                reason=result.get("reason"),
            )

        logger.info(
            "Agent response received and processed",
            request_id=request_id,
            session_id=session_id,
            agent_id=agent_id,
            status=result.get("status"),
        )

        # Record successful event processing
        from .database_utils import record_processed_event

        await record_processed_event(
            db,
            event_id,
            event_type,
            event_source,
            request_id,
            session_id,
            "request-manager",
            "success",
        )

        return {"status": "processed", "request_id": request_id}

    except Exception as e:
        logger.error("Failed to handle agent response event", error=str(e))

        # Record failed event processing
        from .database_utils import record_processed_event

        await record_processed_event(
            db,
            event_id,
            event_type,
            event_source,
            response_data.get("request_id") if "response_data" in locals() else None,
            response_data.get("session_id") if "response_data" in locals() else None,
            "request-manager",
            "error",
            str(e),
        )
        raise


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Handle HTTP exceptions."""
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(  # type: ignore
            error=exc.detail,
            error_code=f"HTTP_{exc.status_code}",
        ).model_dump(mode="json"),
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle general exceptions."""
    logger.error("Unhandled exception", error=str(exc), path=str(request.url))

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=ErrorResponse(  # type: ignore
            error="Internal server error",
            error_code="INTERNAL_ERROR",
        ).model_dump(mode="json"),
    )


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8080"))
    host = os.getenv("HOST", "0.0.0.0")

    uvicorn.run(
        "request_manager.main:app",
        host=host,
        port=port,
        reload=os.getenv("RELOAD", "false").lower() == "true",
        log_level=os.getenv("LOG_LEVEL", "INFO").lower(),
    )


async def _forward_response_to_integration_dispatcher(
    event_data: Dict[str, Any], is_routing_response: bool
) -> bool:
    """Forward agent response to Integration Dispatcher for delivery to user."""
    try:
        # Don't forward pure routing responses (just agent names) to users
        if is_routing_response:
            logger.info(
                "Skipping delivery of routing response to user",
                request_id=event_data.get("request_id"),
                agent_id=event_data.get("agent_id"),
                routing_response=event_data.get("content", "").strip(),
            )
            return True  # Success, but intentionally not delivered

        # Send response event for Integration Dispatcher to deliver
        broker_url = os.getenv("BROKER_URL", "http://knative-broker:8080")
        event_sender = CloudEventSender(broker_url, "request-manager")

        template_variables = event_data.get("template_variables", {})

        delivery_event_data = {
            "request_id": event_data.get("request_id"),
            "session_id": event_data.get("session_id"),
            "user_id": event_data.get("user_id"),
            "subject": event_data.get("subject"),
            "content": event_data.get("content"),
            "template_variables": template_variables,
            "agent_id": event_data.get("agent_id"),
        }

        # Send response event using shared utilities
        success = await event_sender.send_response_event(
            delivery_event_data,
            event_data.get("request_id"),  # type: ignore[arg-type]
            event_data.get("agent_id"),
            event_data.get("session_id"),
        )

        if success:
            logger.info(
                "Agent response forwarded to Integration Dispatcher",
                request_id=event_data.get("request_id"),
                session_id=event_data.get("session_id"),
                agent_id=event_data.get("agent_id"),
            )
        else:
            logger.error(
                "Failed to forward agent response to Integration Dispatcher",
                request_id=event_data.get("request_id"),
                session_id=event_data.get("session_id"),
                agent_id=event_data.get("agent_id"),
            )

        return success

    except Exception as e:
        logger.error(
            "Error forwarding response to Integration Dispatcher",
            error=str(e),
            request_id=event_data.get("request_id"),
            session_id=event_data.get("session_id"),
        )
        return False

