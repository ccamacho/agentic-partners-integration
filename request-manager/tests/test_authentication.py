"""Tests for authentication functionality.

Note: Tests for removed endpoints (web, generic, CLI) and removed auth
functions (verify_web_api_key, validate_jwt_token, get_current_user) have been
removed as part of dead code cleanup. Auth endpoints (login, refresh, me) are
tested via auth_endpoints or integration tests.
"""

from fastapi.testclient import TestClient
from request_manager.main import app


def test_app_import() -> None:
    """Smoke test: app imports successfully."""
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data.get("status") == "healthy"
    assert data.get("service") == "request-manager"
