"""Pydantic schemas for request/response validation."""

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, field_validator
from shared_models.models import IntegrationType


class BaseRequest(BaseModel):
    """Base request schema."""

    integration_type: IntegrationType
    user_id: str = Field(..., min_length=1, max_length=255)
    content: str = Field(..., min_length=1)
    request_type: str = Field(default="message", max_length=100)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("integration_type", mode="before")
    @classmethod
    def normalize_integration_type(cls, v: Any) -> Any:
        """Convert integration_type to uppercase for case-insensitive input."""
        if isinstance(v, str):
            return IntegrationType(v.upper())
        return v


class WebRequest(BaseRequest):
    """Web interface request schema."""

    integration_type: IntegrationType = IntegrationType.WEB
    session_token: Optional[str] = Field(None, max_length=500)
    client_ip: Optional[str] = Field(None, max_length=45)
    user_agent: Optional[str] = Field(None, max_length=500)


class HealthCheck(BaseModel):
    """Health check response schema."""

    status: str = Field(default="healthy")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    version: str = Field(default="0.1.0")
    database_connected: bool = Field(default=False)
    services: Dict[str, str] = Field(default_factory=dict)
