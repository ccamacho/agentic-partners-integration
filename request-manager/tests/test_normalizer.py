"""Tests for request normalizer."""

from request_manager.normalizer import RequestNormalizer
from request_manager.schemas import WebRequest
from shared_models.models import IntegrationType


class TestRequestNormalizer:
    """Test cases for RequestNormalizer."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.normalizer = RequestNormalizer()
        self.session_id = "test-session-123"

    def test_normalize_web_request(self) -> None:
        """Test web request normalization."""
        web_request = WebRequest(
            user_id="webuser123",
            content="I want to refresh my laptop",
            session_token="token123",
            client_ip="192.168.1.1",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        )

        normalized = self.normalizer.normalize_request(web_request, self.session_id)

        assert normalized.integration_type == IntegrationType.WEB
        assert normalized.integration_context["platform"] == "web"
        assert normalized.integration_context["client_ip"] == "192.168.1.1"
        assert normalized.user_context["browser"] == "chrome"
        assert normalized.user_context["os"] == "windows"
        assert normalized.user_context["is_mobile"] is False

    def test_user_agent_parsing(self) -> None:
        """Test user agent parsing."""
        test_cases = [
            (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
                {"browser": "chrome", "os": "windows", "is_mobile": False},
            ),
            (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.1 Safari/605.1.15",
                {"browser": "safari", "os": "macos", "is_mobile": False},
            ),
            (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 14_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1",
                {"browser": "safari", "os": "ios", "is_mobile": True},
            ),
        ]

        for user_agent, expected in test_cases:
            result = self.normalizer._parse_user_agent(user_agent)
            for key, value in expected.items():
                assert result[key] == value
