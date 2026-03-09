"""
Context-based credential management for async request handling.

This module provides thread-safe, async-safe credential storage using Python's
contextvars. This allows credentials to be set once per request and retrieved
anywhere in the call stack without passing them through function parameters.
"""

from contextvars import ContextVar
from typing import Optional

import structlog

logger = structlog.get_logger()


# Context variables for request-scoped storage
_current_user_id: ContextVar[Optional[str]] = ContextVar('current_user_id', default=None)
_current_user_token: ContextVar[Optional[str]] = ContextVar('current_user_token', default=None)
_current_session_id: ContextVar[Optional[str]] = ContextVar('current_session_id', default=None)


class CredentialService:
    """
    Service for managing request-scoped credentials using contextvars.

    This provides a clean way to propagate authentication and session information
    through async call stacks without polluting function signatures.

    Features:
    - Async-safe (each request has isolated context)
    - No global state pollution
    - Clean function signatures (no auth parameters)
    - Easy credential refresh
    - Automatic cleanup

    Example:
        # At middleware/entry point
        CredentialService.set_user_id("user@example.com")
        CredentialService.set_token("Bearer xyz...")
        CredentialService.set_session_id("session-123")

        # Anywhere in the call stack
        auth_header = CredentialService.get_auth_header()

        # Cleanup (automatic in middleware)
        CredentialService.clear_credentials()
    """

    @staticmethod
    def set_user_id(user_id: str) -> None:
        """
        Set the current request's user ID.

        Args:
            user_id: The user identifier

        Example:
            >>> CredentialService.set_user_id("user@example.com")
        """
        _current_user_id.set(user_id)
        logger.debug(f"Set user_id in context: {user_id}")

    @staticmethod
    def get_user_id() -> Optional[str]:
        """
        Get the current request's user ID.

        Returns:
            The user ID, or None if not set

        Example:
            >>> user_id = CredentialService.get_user_id()
            >>> print(user_id)  # "user@example.com"
        """
        return _current_user_id.get()

    @staticmethod
    def set_token(token: str) -> None:
        """
        Set the current request's authentication token.

        Args:
            token: The authentication token (can include "Bearer " prefix or not)

        Example:
            >>> CredentialService.set_token("Bearer abc123...")
        """
        _current_user_token.set(token)
        # Don't log the actual token for security
        logger.debug("Set authentication token in context")

    @staticmethod
    def get_token() -> Optional[str]:
        """
        Get the current request's authentication token.

        Returns:
            The token, or None if not set

        Example:
            >>> token = CredentialService.get_token()
            >>> headers = {"Authorization": token}
        """
        return _current_user_token.get()

    @staticmethod
    def set_session_id(session_id: str) -> None:
        """
        Set the current request's session ID.

        Args:
            session_id: The session identifier

        Example:
            >>> CredentialService.set_session_id("session-123")
        """
        _current_session_id.set(session_id)
        logger.debug(f"Set session_id in context: {session_id}")

    @staticmethod
    def get_session_id() -> Optional[str]:
        """
        Get the current request's session ID.

        Returns:
            The session ID, or None if not set

        Example:
            >>> session_id = CredentialService.get_session_id()
        """
        return _current_session_id.get()

    @staticmethod
    def clear_credentials() -> None:
        """
        Clear all credentials from the current context.

        Should be called after request processing completes.

        Example:
            >>> CredentialService.clear_credentials()
        """
        _current_user_id.set(None)
        _current_user_token.set(None)
        _current_session_id.set(None)
        logger.debug("Cleared all credentials from context")

    @staticmethod
    def get_auth_header() -> Optional[str]:
        """
        Get the Authorization header value for HTTP requests.

        Returns:
            The header value with "Bearer " prefix, or None if no token

        Example:
            >>> auth_header = CredentialService.get_auth_header()
            >>> headers = {"Authorization": auth_header}
        """
        token = CredentialService.get_token()
        if not token:
            return None

        # Ensure "Bearer " prefix
        if not token.startswith("Bearer "):
            return f"Bearer {token}"
        return token

