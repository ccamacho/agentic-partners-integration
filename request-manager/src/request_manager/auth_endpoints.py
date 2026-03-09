"""Authentication endpoints for user login and token management."""

import time
from collections import defaultdict
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from shared_models.auth_service import AuthService
from shared_models.database import get_db

logger = structlog.get_logger()
router = APIRouter(prefix="/auth", tags=["Authentication"])

security = HTTPBearer()


# ---------------------------------------------------------------------------
# In-memory sliding-window rate limiter
# ---------------------------------------------------------------------------
class _RateLimiter:
    """Per-IP sliding-window rate limiter stored in process memory."""

    def __init__(self, max_requests: int, window_seconds: int) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str) -> bool:
        """Return True if the request is allowed, False if rate-limited."""
        now = time.monotonic()
        window_start = now - self.window_seconds
        timestamps = self._hits[key]
        self._hits[key] = [t for t in timestamps if t > window_start]
        if len(self._hits[key]) >= self.max_requests:
            return False
        self._hits[key].append(now)
        return True


_login_limiter = _RateLimiter(max_requests=10, window_seconds=60)
_refresh_limiter = _RateLimiter(max_requests=30, window_seconds=60)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# Request/Response Models
class LoginRequest(BaseModel):
    """Login request with email and password."""
    email: str = Field(..., description="User email address")
    password: str = Field(..., description="User password")


class LoginResponse(BaseModel):
    """Login response with JWT token and user info."""
    token: str = Field(..., description="JWT access token")
    token_type: str = Field(default="bearer", description="Token type")
    user: dict = Field(..., description="User information")


class UserResponse(BaseModel):
    """User information response."""
    user_id: str
    email: str
    role: str
    allowed_agents: list
    organization: Optional[str] = None
    department: Optional[str] = None


# Dependency to extract and validate JWT token
async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db)
) -> dict:
    """Extract and validate JWT token, return user payload."""
    token = credentials.credentials

    # Verify token
    payload = AuthService.verify_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Verify user still exists and is active
    user = await AuthService.get_user_by_email(db, payload["email"])
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return payload


# Endpoints
@router.post("/login", response_model=LoginResponse)
async def login(
    request: LoginRequest,
    raw_request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Authenticate user and return JWT token.

    This endpoint validates user credentials and returns a JWT token
    that can be used for subsequent authenticated requests.
    Rate-limited to 10 attempts per minute per IP.
    """
    ip = _client_ip(raw_request)
    if not _login_limiter.check(ip):
        logger.warning("Login rate limited", ip=ip, email=request.email)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Please try again later.",
        )

    try:
        user = await AuthService.authenticate_user(db, request.email, request.password)

        if not user:
            logger.warning("Login failed", email=request.email)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Generate JWT token
        token = AuthService.generate_token(
            user_id=user.user_id,
            email=user.primary_email,
            role=user.role,
            allowed_agents=user.allowed_agents
        )

        logger.info("User logged in successfully", email=user.primary_email)

        return LoginResponse(
            token=token,
            token_type="bearer",
            user={
                "user_id": user.user_id,
                "email": user.primary_email,
                "role": user.role,
                "allowed_agents": user.allowed_agents,
                "organization": user.organization,
                "department": user.department
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Login error", email=request.email, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Login failed"
        )


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    current_user: dict = Depends(get_current_user)
):
    """
    Get current authenticated user information.

    Returns user details from the JWT token payload.
    """
    return UserResponse(
        user_id=current_user["user_id"],
        email=current_user["email"],
        role=current_user["role"],
        allowed_agents=current_user["allowed_agents"],
        organization=current_user.get("organization"),
        department=current_user.get("department")
    )


@router.post("/refresh", response_model=LoginResponse)
async def refresh_token(
    raw_request: Request,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Refresh JWT token before expiration.

    Tokens expire in 5 minutes for security. Web UI should call this endpoint
    every 4 minutes to get a fresh token before the current one expires.
    Rate-limited to 30 requests per minute per IP.
    """
    ip = _client_ip(raw_request)
    if not _refresh_limiter.check(ip):
        logger.warning("Token refresh rate limited", ip=ip, email=current_user.get("email"))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many refresh attempts. Please try again later.",
        )

    try:
        # Get fresh user data from database
        user = await AuthService.get_user_by_email(db, current_user["email"])

        if not user or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found or inactive",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Generate new JWT token with fresh expiration
        new_token = AuthService.generate_token(
            user_id=user.user_id,
            email=user.primary_email,
            role=user.role,
            allowed_agents=user.allowed_agents
        )

        logger.info("Token refreshed", email=user.primary_email)

        return LoginResponse(
            token=new_token,
            token_type="bearer",
            user={
                "user_id": user.user_id,
                "email": user.primary_email,
                "role": user.role,
                "allowed_agents": user.allowed_agents,
                "organization": user.organization,
                "department": user.department
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Token refresh error", email=current_user.get("email"), error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Token refresh failed"
        )


