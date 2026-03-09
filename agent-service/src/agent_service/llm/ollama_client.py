"""Ollama client implementation for local LLMs."""

from typing import List, Optional

import httpx
from shared_models import configure_logging

from .base import BaseLLMClient, LLMMessage, LLMResponse

logger = configure_logging("agent-service")


class OllamaClient(BaseLLMClient):
    """Ollama API client for local LLM deployment.

    Supports any model available in Ollama (Llama 3.1, Mistral, etc.)
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "llama3.1",
    ):
        """Initialize Ollama client.

        Args:
            base_url: Ollama server URL
            model: Model to use for completions
        """
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.client = httpx.AsyncClient(timeout=120.0)

        logger.info(
            "Initialized Ollama client",
            base_url=base_url,
            model=model,
        )

    async def create_completion(
        self,
        messages: List[LLMMessage],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> LLMResponse:
        """Generate completion using Ollama API.

        Args:
            messages: List of conversation messages
            temperature: Sampling temperature (0.0 to 1.0)
            max_tokens: Maximum tokens to generate
            **kwargs: Additional Ollama parameters

        Returns:
            LLMResponse with generated content
        """
        # Convert messages to Ollama format
        ollama_messages = [msg.to_dict() for msg in messages]

        logger.debug(
            "Calling Ollama completion",
            model=self.model,
            message_count=len(messages),
            temperature=temperature,
        )

        # Build request payload
        payload = {
            "model": self.model,
            "messages": ollama_messages,
            "stream": False,
            "options": {
                "temperature": temperature,
            },
        }

        if max_tokens:
            payload["options"]["num_predict"] = max_tokens

        # Call Ollama API
        url = f"{self.base_url}/api/chat"
        response = await self.client.post(url, json=payload)
        response.raise_for_status()

        data = response.json()

        # Extract content
        content = data.get("message", {}).get("content", "")

        # Ollama usage metadata
        usage = {
            "prompt_tokens": data.get("prompt_eval_count", 0),
            "completion_tokens": data.get("eval_count", 0),
            "total_tokens": data.get("prompt_eval_count", 0)
            + data.get("eval_count", 0),
        }

        logger.debug(
            "Ollama completion successful",
            total_tokens=usage["total_tokens"],
        )

        return LLMResponse(
            content=content,
            usage=usage,
            model=self.model,
            finish_reason=data.get("done_reason", "stop"),
        )

    def get_model_name(self) -> str:
        """Get the current model name.

        Returns:
            Model identifier
        """
        return self.model

