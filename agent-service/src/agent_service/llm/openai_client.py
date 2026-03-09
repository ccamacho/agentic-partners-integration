"""OpenAI client implementation."""

from typing import List, Optional

from openai import AsyncOpenAI
from shared_models import configure_logging

from .base import BaseLLMClient, LLMMessage, LLMResponse

logger = configure_logging("agent-service")


class OpenAIClient(BaseLLMClient):
    """OpenAI API client implementation.

    Supports GPT-4, GPT-3.5, and other OpenAI models.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4",
    ):
        """Initialize OpenAI client.

        Args:
            api_key: OpenAI API key
            model: Model to use for completions
        """
        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model

        logger.info("Initialized OpenAI client", model=model)

    async def create_completion(
        self,
        messages: List[LLMMessage],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> LLMResponse:
        """Generate completion using OpenAI API.

        Args:
            messages: List of conversation messages
            temperature: Sampling temperature (0.0 to 1.0)
            max_tokens: Maximum tokens to generate
            **kwargs: Additional OpenAI parameters

        Returns:
            LLMResponse with generated content
        """
        # Convert LLMMessage to OpenAI format
        openai_messages = [msg.to_dict() for msg in messages]

        logger.debug(
            "Calling OpenAI completion",
            model=self.model,
            message_count=len(messages),
            temperature=temperature,
        )

        # Call OpenAI API
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=openai_messages,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )

        # Extract response
        content = response.choices[0].message.content or ""
        finish_reason = response.choices[0].finish_reason

        # Build usage dict
        usage = {
            "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
            "completion_tokens": response.usage.completion_tokens
            if response.usage
            else 0,
            "total_tokens": response.usage.total_tokens if response.usage else 0,
        }

        logger.debug(
            "OpenAI completion successful",
            total_tokens=usage["total_tokens"],
            finish_reason=finish_reason,
        )

        return LLMResponse(
            content=content,
            usage=usage,
            model=response.model,
            finish_reason=finish_reason,
        )

    def get_model_name(self) -> str:
        """Get the current model name.

        Returns:
            Model identifier
        """
        return self.model
