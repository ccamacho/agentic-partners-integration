"""Base classes for LLM abstraction layer."""

import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from shared_models import configure_logging

logger = configure_logging("agent-service")


@dataclass
class LLMMessage:
    """A message in a conversation."""

    role: str  # "user", "assistant", or "system"
    content: str

    def to_dict(self) -> Dict[str, str]:
        """Convert to dictionary format."""
        return {"role": self.role, "content": self.content}


@dataclass
class LLMResponse:
    """Response from an LLM completion."""

    content: str
    usage: Dict[str, int]  # {"prompt_tokens": X, "completion_tokens": Y, "total_tokens": Z}
    model: Optional[str] = None
    finish_reason: Optional[str] = None
    latency_ms: Optional[float] = None

    @property
    def total_tokens(self) -> int:
        """Get total tokens used."""
        return self.usage.get("total_tokens", 0)


class BaseLLMClient(ABC):
    """Abstract base class for LLM clients.

    All LLM backend implementations must inherit from this class
    and implement the required methods.
    """

    @abstractmethod
    async def create_completion(
        self,
        messages: List[LLMMessage],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> LLMResponse:
        """Generate a completion from messages.

        Args:
            messages: List of conversation messages
            temperature: Sampling temperature (0.0 to 1.0)
            max_tokens: Maximum tokens to generate
            **kwargs: Additional backend-specific parameters

        Returns:
            LLMResponse containing the generated text and metadata
        """
        pass

    @abstractmethod
    def get_model_name(self) -> str:
        """Get the name of the current model.

        Returns:
            Model identifier string
        """
        pass


class InstrumentedLLMClient(BaseLLMClient):
    """Wraps any BaseLLMClient to automatically capture latency per call.

    This is a transparent decorator -- it passes all calls through to the
    underlying client and adds a latency_ms field to every LLMResponse.
    The agent code doesn't need to change; the factory wraps the client
    when LLM_INSTRUMENTATION=true is set.

    Usage:
        raw_client = OpenAIClient(api_key=..., model="gpt-4")
        client = InstrumentedLLMClient(raw_client)
        response = await client.create_completion(messages)
        print(response.latency_ms)  # e.g. 823.4
    """

    def __init__(self, client: BaseLLMClient) -> None:
        self._client = client

    async def create_completion(
        self,
        messages: List[LLMMessage],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> LLMResponse:
        start = time.perf_counter()
        error_msg: Optional[str] = None
        try:
            response = await self._client.create_completion(
                messages, temperature=temperature, max_tokens=max_tokens, **kwargs
            )
            latency_ms = (time.perf_counter() - start) * 1000
            response.latency_ms = latency_ms

            logger.debug(
                "LLM call completed",
                model=self.get_model_name(),
                latency_ms=round(latency_ms, 1),
                total_tokens=response.total_tokens,
            )

            return response

        except Exception as e:
            latency_ms = (time.perf_counter() - start) * 1000
            error_msg = str(e)

            logger.warning(
                "LLM call failed",
                model=self.get_model_name(),
                latency_ms=round(latency_ms, 1),
                error=error_msg,
            )
            raise

    def get_model_name(self) -> str:
        return self._client.get_model_name()
