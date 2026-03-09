"""Consolidated database models for all services."""

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import relationship

from .base import Base, TimestampMixin

# Export Base for use in other modules
__all__ = [
    "Base",
    "IntegrationType",
    "SessionStatus",
    "User",
    "RequestSession",
    "RequestLog",
    "UserIntegrationConfig",
    "ProcessedEvent",
    "UserIntegrationMapping",
    "AgentResponse",
    "NormalizedRequest",
    "ErrorResponse",
]


# Enums used across services
class IntegrationType(str, Enum):
    """Integration type for request sources."""

    WEB = "WEB"


class SessionStatus(str, Enum):
    """Session status for request management."""

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    EXPIRED = "EXPIRED"
    ARCHIVED = "ARCHIVED"


# User Models
class UserRole(str, Enum):
    """User roles for authorization."""
    ADMIN = "admin"
    MANAGER = "manager"
    ENGINEER = "engineer"
    SUPPORT_STAFF = "support_staff"
    USER = "user"


class User(Base, TimestampMixin):  # type: ignore[misc]
    """Canonical user identity across all integrations."""

    __tablename__ = "users"

    user_id = Column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    primary_email = Column(
        String(255), nullable=True, unique=True, index=True
    )  # For display/search - unique to prevent duplicates

    # Authentication
    password_hash = Column(String(255), nullable=True)  # Bcrypt hash
    last_login = Column(TIMESTAMP(timezone=True), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False, index=True)

    # AAA (Authentication, Authorization, Accounting)
    role: Column[UserRole] = Column(
        SQLEnum(UserRole, name='user_role', values_callable=lambda x: [e.value for e in x]),
        default=UserRole.USER.value, nullable=False, index=True
    )
    privileges = Column(JSON, default=dict, nullable=False)  # Fine-grained permissions
    allowed_agents = Column(JSON, default=list, nullable=False)  # Agent access control
    status = Column(String(20), default="active", nullable=False, index=True)

    # Organization structure
    organization = Column(String(255), nullable=True, index=True)
    department = Column(String(255), nullable=True)

    # Relationships
    integration_mappings = relationship(
        "UserIntegrationMapping", back_populates="user", cascade="all, delete-orphan"
    )
    sessions = relationship(
        "RequestSession", back_populates="user", cascade="all, delete-orphan"
    )
    integration_configs = relationship(
        "UserIntegrationConfig", back_populates="user", cascade="all, delete-orphan"
    )


# Request Manager Models
class RequestSession(Base, TimestampMixin):  # type: ignore[misc]
    """User conversation sessions."""

    __tablename__ = "request_sessions"

    id = Column(Integer, primary_key=True)
    session_id = Column(String(36), unique=True, nullable=False, index=True)
    user_id = Column(
        UUID(as_uuid=False),
        ForeignKey("users.user_id"),
        nullable=False,
        index=True,
    )
    integration_type: Column[IntegrationType] = Column(
        SQLEnum(IntegrationType), nullable=False
    )
    status: Column[SessionStatus] = Column(
        SQLEnum(SessionStatus), default=SessionStatus.ACTIVE.value, nullable=False
    )

    # Session context
    channel_id = Column(String(255))
    thread_id = Column(String(255))  # Thread/conversation ID
    external_session_id = Column(String(255))  # External platform session ID

    # Agent tracking
    current_agent_id = Column(String(255))  # Currently assigned agent
    conversation_thread_id = Column(
        String(255)
    )  # LangGraph conversation thread ID

    # Session metadata
    integration_metadata = Column(JSON, default=dict)
    user_context = Column(JSON, default=dict)  # User context from platform
    conversation_context = Column(JSON, default=dict)  # Conversation state

    # Session statistics
    total_requests = Column(Integer, default=0, nullable=False)
    last_request_id = Column(String(36))  # Most recent request ID
    last_request_at = Column(TIMESTAMP(timezone=True))
    expires_at = Column(TIMESTAMP(timezone=True))

    # Token usage tracking
    total_input_tokens = Column(Integer, default=0, nullable=False)
    total_output_tokens = Column(Integer, default=0, nullable=False)
    total_tokens = Column(Integer, default=0, nullable=False)
    llm_call_count = Column(Integer, default=0, nullable=False)
    max_input_tokens_per_call = Column(Integer, default=0, nullable=False)
    max_output_tokens_per_call = Column(Integer, default=0, nullable=False)
    max_total_tokens_per_call = Column(Integer, default=0, nullable=False)

    # Optimistic locking
    version = Column(Integer, default=0, nullable=False, server_default="0")

    # Relationships
    user = relationship("User", back_populates="sessions")
    request_logs = relationship(
        "RequestLog", back_populates="session", cascade="all, delete-orphan"
    )


class RequestLog(Base, TimestampMixin):  # type: ignore[misc]
    """Log of all requests processed."""

    __tablename__ = "request_logs"

    id = Column(Integer, primary_key=True)
    request_id = Column(String(36), unique=True, nullable=False, index=True)
    session_id = Column(
        String(36),
        ForeignKey("request_sessions.session_id"),
        nullable=False,
        index=True,
    )

    # Request details
    request_type = Column(String(50), nullable=False)  # "web"
    request_content = Column(Text, nullable=False)
    normalized_request = Column(JSON)  # Normalized request structure

    # Agent processing
    agent_id = Column(String(255))
    processing_time_ms = Column(Integer)

    # Response details
    response_content = Column(Text)
    response_metadata = Column(JSON, default=dict)

    # CloudEvent tracking
    cloudevent_id = Column(String(36))
    cloudevent_type = Column(String(100))

    # Timing
    completed_at = Column(TIMESTAMP(timezone=True))

    # Pod tracking (for scaled deployments)
    pod_name = Column(String(255), index=True)  # Pod that initiated the request

    # Relationships
    session = relationship("RequestSession", back_populates="request_logs")


# User Integration Models
class UserIntegrationConfig(Base, TimestampMixin):  # type: ignore[misc]
    """Per-user integration configuration."""

    __tablename__ = "user_integration_configs"

    id = Column(Integer, primary_key=True)
    user_id = Column(
        UUID(as_uuid=False),
        ForeignKey("users.user_id"),
        nullable=False,
        index=True,
    )
    integration_type: Column[IntegrationType] = Column(
        SQLEnum(IntegrationType), nullable=False
    )
    enabled = Column(Boolean, default=True, nullable=False)

    # Integration-specific configuration
    config = Column(JSON, nullable=False, default=dict)

    # Delivery preferences
    priority = Column(Integer, default=0, nullable=False)  # Higher = more important
    retry_count = Column(Integer, default=3, nullable=False)
    retry_delay_seconds = Column(Integer, default=60, nullable=False)

    # Metadata
    created_by = Column(String(255))  # Who configured this integration

    # Relationships
    user = relationship("User", back_populates="integration_configs")

    # Ensure one config per user per integration type
    __table_args__ = (
        UniqueConstraint("user_id", "integration_type", name="uq_user_integration"),
    )


class ProcessedEvent(Base, TimestampMixin):  # type: ignore[misc]
    """Track processed CloudEvents to prevent duplicate processing."""

    __tablename__ = "processed_events"

    id = Column(Integer, primary_key=True)
    event_id = Column(String(255), nullable=False, unique=True)  # ce-id
    event_type = Column(String(255), nullable=False)  # ce-type
    event_source = Column(String(255), nullable=False)  # ce-source
    request_id = Column(String(255), nullable=True)  # For correlation
    session_id = Column(String(255), nullable=True)  # For correlation
    processed_by = Column(String(100), nullable=False)  # service name
    processing_result = Column(String(50), nullable=False)  # success/error/skipped
    error_message = Column(Text, nullable=True)

    # Index for fast lookups
    __table_args__ = (
        Index("ix_processed_events_event_id", "event_id"),
        Index("ix_processed_events_request_id", "request_id"),
        Index("ix_processed_events_created_at", "created_at"),
    )


class UserIntegrationMapping(Base, TimestampMixin):  # type: ignore[misc]
    """Mapping between canonical users and integration-specific user IDs."""

    __tablename__ = "user_integration_mappings"

    id = Column(Integer, primary_key=True)
    user_id = Column(
        UUID(as_uuid=False),
        ForeignKey("users.user_id"),
        nullable=False,
        index=True,
    )
    user_email = Column(
        String(255), nullable=False, index=True
    )  # For search/backward compatibility
    integration_type: Column[IntegrationType] = Column(
        SQLEnum(IntegrationType), nullable=False
    )
    integration_user_id = Column(String(255), nullable=False)  # Platform-specific user ID

    # Validation metadata
    last_validated_at = Column(TIMESTAMP(timezone=True))
    validation_attempts = Column(Integer, default=0, nullable=False)
    last_validation_error = Column(Text)

    # Metadata
    created_by = Column(String(255), default="system")

    # Relationships
    user = relationship("User", back_populates="integration_mappings")

    # Ensure one mapping per user per integration type
    # Also ensure one mapping per integration_user_id per integration type (prevents conflicts)
    # NOTE: uq_integration_user_id_type is implemented as a PARTIAL unique INDEX at the database level
    # (not a constraint) that excludes __NOT_FOUND__ sentinel values, allowing multiple users to have
    # __NOT_FOUND__ entries while still preventing duplicate real integration user IDs.
    # See migration 002_partial_unique_constraint_for_sentinel_values.py
    # The UniqueConstraint declaration below is for SQLAlchemy documentation; the actual DB uses a unique index.
    # Also allow lookup by integration_user_id + integration_type
    __table_args__ = (
        UniqueConstraint(
            "user_id", "integration_type", name="uq_user_integration_mapping"
        ),
        UniqueConstraint(
            "integration_user_id",
            "integration_type",
            name="uq_integration_user_id_type",
        ),
        Index("ix_user_integration_mapping_user_type", "user_id", "integration_type"),
        Index(
            "ix_user_integration_mapping_integration",
            "integration_user_id",
            "integration_type",
        ),
        Index(
            "ix_user_integration_mapping_email_type", "user_email", "integration_type"
        ),
    )


# Shared Pydantic models for inter-service communication
class AgentResponse(BaseModel):
    """Shared model for agent responses across all services."""

    request_id: str
    session_id: str
    user_id: str
    agent_id: Optional[str]
    content: str
    response_type: str = Field(default="message")
    metadata: Dict[str, Any] = Field(default_factory=dict)
    processing_time_ms: Optional[int] = None
    requires_followup: bool = Field(default=False)
    followup_actions: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# Shared Pydantic models for inter-service communication
class NormalizedRequest(BaseModel):
    """Normalized internal request format used across all services."""

    request_id: str = Field(..., description="Unique request identifier")
    session_id: str = Field(..., description="Session identifier")
    user_id: str = Field(..., min_length=1, max_length=255)
    integration_type: IntegrationType
    request_type: str = Field(..., max_length=100)
    content: str = Field(..., min_length=1)

    # Integration-specific context
    integration_context: Dict[str, Any] = Field(default_factory=dict)
    user_context: Dict[str, Any] = Field(default_factory=dict)

    # Agent routing
    target_agent_id: Optional[str] = Field(None, max_length=255)
    requires_routing: bool = Field(default=True)

    # Timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("integration_type", mode="before")
    @classmethod
    def normalize_integration_type(cls, v: Any) -> Any:
        """Convert integration_type to uppercase for case-insensitive input."""
        if isinstance(v, str):
            return IntegrationType(v.upper())
        return v

    class Config:
        """Pydantic config."""

        use_enum_values = True


class ErrorResponse(BaseModel):
    """Shared error response schema across all services."""

    error: str = Field(..., description="Error message")
    error_code: str = Field(..., description="Error code")
    request_id: Optional[str] = Field(None, description="Request ID if available")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    details: Optional[Dict[str, Any]] = Field(
        None, description="Additional error details"
    )
