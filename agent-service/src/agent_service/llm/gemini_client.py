"""Google Gemini client implementation."""

from typing import List, Optional

from google import genai
from shared_models import configure_logging

from .base import BaseLLMClient, LLMMessage, LLMResponse

logger = configure_logging("agent-service")


class GeminiClient(BaseLLMClient):
    """Google Gemini API client implementation.

    Supports Gemini 1.5 Pro, Gemini 1.5 Flash, and other Gemini models.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-1.5-pro",
    ):
        """Initialize Gemini client.

        Args:
            api_key: Google API key
            model: Model to use for completions
        """
        self.client = genai.Client(api_key=api_key)
        self.model_name = model

        logger.info("Initialized Gemini client", model=model)

    async def create_completion(
        self,
        messages: List[LLMMessage],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> LLMResponse:
        """Generate completion using Gemini API.

        Args:
            messages: List of conversation messages
            temperature: Sampling temperature (0.0 to 1.0)
            max_tokens: Maximum tokens to generate
            **kwargs: Additional Gemini parameters

        Returns:
            LLMResponse with generated content
        """
        # Convert messages to new Google GenAI SDK format
        contents = []
        system_instruction = None

        for msg in messages:
            if msg.role == "system":
                system_instruction = msg.content
            elif msg.role == "user":
                contents.append({"role": "user", "parts": [{"text": msg.content}]})
            elif msg.role == "assistant":
                contents.append({"role": "model", "parts": [{"text": msg.content}]})

        logger.debug(
            "Calling Gemini completion",
            model=self.model_name,
            message_count=len(messages),
            temperature=temperature,
        )

        # Configure generation config
        config = genai.types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
            system_instruction=system_instruction
        )

        # Call new Google GenAI SDK (async)
        response = await self.client.aio.models.generate_content(
            model=self.model_name,
            contents=contents,
            config=config
        )

        # Extract content
        content = response.text if hasattr(response, "text") else ""

        # Gemini usage metadata
        usage = {
            "prompt_tokens": response.usage_metadata.prompt_token_count
            if hasattr(response, "usage_metadata")
            else 0,
            "completion_tokens": response.usage_metadata.candidates_token_count
            if hasattr(response, "usage_metadata")
            else 0,
            "total_tokens": response.usage_metadata.total_token_count
            if hasattr(response, "usage_metadata")
            else 0,
        }

        logger.debug(
            "Gemini completion successful",
            total_tokens=usage["total_tokens"],
        )

        return LLMResponse(
            content=content,
            usage=usage,
            model=self.model_name,
            finish_reason="stop",  # Gemini doesn't provide finish_reason
        )

    def get_model_name(self) -> str:
        """Get the current model name.

        Returns:
            Model identifier
        """
        return self.model_name
