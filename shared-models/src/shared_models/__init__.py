"""Shared models and schemas for Partner Agent Integration."""

__version__ = "0.1.3"

# Export CloudEvent utilities
from .cloudevent_utils import (
    CloudEventHandler,
    create_cloudevent_response,
    parse_cloudevent_from_request,
)
from .database import (
    DatabaseConfig,
    DatabaseManager,
    DatabaseUtils,
    get_database_manager,
    get_db_config,
    get_db_session,
    get_db_session_dependency,
)

# Export CloudEvent utilities
from .events import (
    CloudEventBuilder,
    CloudEventSender,
    EventTypes,
)

# Export FastAPI utilities
from .fastapi_utils import (
    create_health_check_endpoint,
    create_shared_lifespan,
)

# Export health utilities
from .health import HealthChecker, HealthCheckResult, simple_health_check

# Export logging utilities
from .logging import configure_logging

# Export session management
from .session_manager import BaseSessionManager
from .session_schemas import SessionCreate, SessionResponse

# Export user utilities
from .user_utils import (
    get_or_create_canonical_user,
    is_uuid,
    resolve_canonical_user_id,
)

# Export utilities
from .utils import generate_fallback_user_id, get_enum_value

__all__ = [
    "create_health_check_endpoint",
    "create_shared_lifespan",
    "parse_cloudevent_from_request",
    "create_cloudevent_response",
    "get_enum_value",
    "generate_fallback_user_id",
    "get_or_create_canonical_user",
    "is_uuid",
    "resolve_canonical_user_id",
    "CloudEventHandler",
    "DatabaseConfig",
    "DatabaseManager",
    "DatabaseUtils",
    "get_database_manager",
    "get_db_config",
    "get_db_session",
    "get_db_session_dependency",
    "HealthChecker",
    "HealthCheckResult",
    "simple_health_check",
    "configure_logging",
    "CloudEventBuilder",
    "CloudEventSender",
    "EventTypes",
    "BaseSessionManager",
    "SessionCreate",
    "SessionResponse",
]
