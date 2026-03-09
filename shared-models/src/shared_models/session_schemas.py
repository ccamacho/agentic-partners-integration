"""Pydantic schemas for session management."""

from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, field_validator
from shared_models.models import IntegrationType, SessionStatus


class SessionCreate(BaseModel):
    """Schema for creating a new session."""

    user_id: str = Field(..., min_length=1, max_length=255)
    integration_type: IntegrationType
    integration_metadata: Dict[str, Any] = Field(default_factory=dict)
    user_context: Dict[str, Any] = Field(default_factory=dict)

    # Integration-specific fields
    channel_id: Optional[str] = Field(None, max_length=255)
    thread_id: Optional[str] = Field(None, max_length=255)
    external_session_id: Optional[str] = Field(None, max_length=255)

    @field_validator("integration_type", mode="before")
    @classmethod
    def normalize_integration_type(cls, v: Any) -> Any:
        """Convert integration_type to uppercase for case-insensitive input."""
        if isinstance(v, str):
            return IntegrationType(v.upper())
        return v


class SessionResponse(BaseModel):
    """Schema for session information."""

    session_id: str
    user_id: str
    integration_type: IntegrationType
    status: SessionStatus
    current_agent_id: Optional[str]
    conversation_thread_id: Optional[str]

    # Context
    conversation_context: Dict[str, Any]
    integration_metadata: Dict[str, Any]
    user_context: Dict[str, Any]

    # Statistics
    total_requests: int
    last_request_id: Optional[str]

    # Optimistic locking
    version: int = Field(default=0, description="Version number for optimistic locking")

    # Timestamps
    created_at: datetime
    updated_at: datetime
    last_request_at: Optional[datetime] = None

    class Config:
        from_attributes = True
