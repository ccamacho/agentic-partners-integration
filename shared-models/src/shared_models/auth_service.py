"""Authentication service for user login and token management."""

import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

import bcrypt
import jwt
import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import User

logger = structlog.get_logger()

# JWT configuration
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
if not JWT_SECRET_KEY:
    if os.getenv("ENVIRONMENT", "development").lower() in ("production", "prod", "staging"):
        raise RuntimeError("JWT_SECRET_KEY must be set in production/staging environments")
    JWT_SECRET_KEY = "dev-secret-key-change-in-production"  # noqa: S105
    logger.warning("Using default JWT secret — NOT safe for production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_MINUTES = int(os.getenv("JWT_EXPIRATION_MINUTES", "5"))  # 5 minutes for security


class AuthService:
    """Authentication service for user management."""

    @staticmethod
    def hash_password(password: str) -> str:
        """Hash a password using bcrypt."""
        salt = bcrypt.gensalt()
        hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
        return hashed.decode('utf-8')

    @staticmethod
    def verify_password(password: str, password_hash: str) -> bool:
        """Verify a password against its hash."""
        try:
            return bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))
        except Exception as e:
            logger.error("Password verification failed", error=str(e))
            return False

    @staticmethod
    def generate_token(user_id: str, email: str, role: str, allowed_agents: list) -> str:
        """Generate a JWT token for authenticated user.

        Token expires in JWT_EXPIRATION_MINUTES (default 5 minutes) for security.
        Web UI should refresh tokens before expiration.
        """
        payload = {
            "user_id": user_id,
            "email": email,
            "role": role,
            "allowed_agents": allowed_agents,
            "exp": datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRATION_MINUTES),
            "iat": datetime.now(timezone.utc)
        }
        token = jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
        return token

    @staticmethod
    def verify_token(token: str) -> Optional[Dict[str, Any]]:
        """Verify and decode a JWT token."""
        try:
            payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
            return payload
        except jwt.ExpiredSignatureError:
            logger.warning("Token expired")
            return None
        except jwt.InvalidTokenError as e:
            logger.warning("Invalid token", error=str(e))
            return None

    @staticmethod
    async def authenticate_user(
        db: AsyncSession,
        email: str,
        password: str
    ) -> Optional[User]:
        """Authenticate user by email and password."""
        try:
            # Find user by email
            stmt = select(User).where(User.primary_email == email)
            result = await db.execute(stmt)
            user = result.scalar_one_or_none()

            if not user:
                logger.warning("User not found", email=email)
                return None

            # Check if user is active
            if not user.is_active:
                logger.warning("User account is inactive", email=email)
                return None

            # Check if password is set
            if not user.password_hash:
                logger.warning("User has no password set", email=email)
                return None

            # Verify password
            if not AuthService.verify_password(password, user.password_hash):
                logger.warning("Invalid password", email=email)
                return None

            # Update last login time
            stmt = (
                update(User)
                .where(User.user_id == user.user_id)
                .values(last_login=datetime.now(timezone.utc))
            )
            await db.execute(stmt)
            await db.commit()

            logger.info("User authenticated successfully", email=email, user_id=user.user_id)
            return user

        except Exception as e:
            logger.error("Authentication error", email=email, error=str(e))
            return None

    @staticmethod
    async def set_user_password(
        db: AsyncSession,
        email: str,
        password: str
    ) -> bool:
        """Set or update user password."""
        try:
            password_hash = AuthService.hash_password(password)

            stmt = (
                update(User)
                .where(User.primary_email == email)
                .values(password_hash=password_hash)
            )
            result = await db.execute(stmt)
            await db.commit()

            if result.rowcount > 0:
                logger.info("Password updated successfully", email=email)
                return True
            else:
                logger.warning("User not found for password update", email=email)
                return False

        except Exception as e:
            logger.error("Failed to set password", email=email, error=str(e))
            await db.rollback()
            return False

    @staticmethod
    async def get_user_by_email(db: AsyncSession, email: str) -> Optional[User]:
        """Get user by email address."""
        try:
            stmt = select(User).where(User.primary_email == email)
            result = await db.execute(stmt)
            return result.scalar_one_or_none()
        except Exception as e:
            logger.error("Failed to get user", email=email, error=str(e))
            return None
