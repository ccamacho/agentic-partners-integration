"""Factory for creating LLM clients."""

import os
from typing import Optional

from shared_models import configure_logging

from .base import BaseLLMClient, InstrumentedLLMClient
from .gemini_client import GeminiClient
from .ollama_client import OllamaClient
from .openai_client import OpenAIClient

logger = configure_logging("agent-service")


class LLMClientFactory:
    """Factory for creating LLM clients based on backend type.

    Supports:
    - OpenAI (GPT-4, GPT-3.5, etc.)
    - Google Gemini (via Google AI API)
    - Ollama (local LLMs)

    Configuration via environment variables:
        LLM_BACKEND: openai|gemini|ollama
        OPENAI_API_KEY: OpenAI API key
        OPENAI_MODEL: OpenAI model name
        GOOGLE_API_KEY: Google API key
        GEMINI_MODEL: Gemini model name
        OLLAMA_BASE_URL: Ollama server URL
        OLLAMA_MODEL: Ollama model name
        LLM_INSTRUMENTATION: Set to "true" to wrap clients with latency tracking
    """

    @staticmethod
    def create_client(
        backend: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs,
    ) -> BaseLLMClient:
        """Create an LLM client based on backend type.

        When LLM_INSTRUMENTATION=true, the returned client is automatically
        wrapped with InstrumentedLLMClient, which records latency_ms on
        every LLMResponse. This is transparent to all calling code.

        Args:
            backend: LLM backend type (openai, gemini, ollama)
                     If None, uses LLM_BACKEND env var (default: openai)
            model: Model name to use
                   If None, uses backend-specific env var
            **kwargs: Additional backend-specific arguments

        Returns:
            Initialized LLM client (optionally instrumented)

        Raises:
            ValueError: If backend is unknown or required credentials missing
        """
        backend = backend or os.getenv("LLM_BACKEND", "openai")
        backend = backend.lower()

        logger.info("Creating LLM client", backend=backend, model=model)

        if backend == "openai":
            client = LLMClientFactory._create_openai_client(model, **kwargs)
        elif backend == "gemini":
            client = LLMClientFactory._create_gemini_client(model, **kwargs)
        elif backend == "ollama":
            client = LLMClientFactory._create_ollama_client(model, **kwargs)
        else:
            raise ValueError(
                f"Unknown LLM backend: {backend}. "
                f"Supported backends: openai, gemini, ollama"
            )

        if os.getenv("LLM_INSTRUMENTATION", "").lower() == "true":
            logger.info(
                "LLM instrumentation enabled, wrapping client",
                backend=backend,
                model=model,
            )
            client = InstrumentedLLMClient(client)

        return client

    @staticmethod
    def _create_openai_client(
        model: Optional[str] = None, **kwargs
    ) -> OpenAIClient:
        """Create OpenAI client.

        Args:
            model: Model name (default from OPENAI_MODEL env var)
            **kwargs: Additional arguments

        Returns:
            Initialized OpenAI client

        Raises:
            ValueError: If OPENAI_API_KEY not set
        """
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError(
                "OPENAI_API_KEY environment variable must be set for OpenAI backend"
            )

        model = model or os.getenv("OPENAI_MODEL", "gpt-4")

        return OpenAIClient(api_key=api_key, model=model)

    @staticmethod
    def _create_gemini_client(
        model: Optional[str] = None, **kwargs
    ) -> GeminiClient:
        """Create Gemini client.

        Args:
            model: Model name (default from GEMINI_MODEL env var)
            **kwargs: Additional arguments

        Returns:
            Initialized Gemini client

        Raises:
            ValueError: If GOOGLE_API_KEY not set
        """
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError(
                "GOOGLE_API_KEY environment variable must be set for Gemini backend"
            )

        model = model or os.getenv("GEMINI_MODEL", "gemini-1.5-pro")

        return GeminiClient(api_key=api_key, model=model)

    @staticmethod
    def _create_ollama_client(
        model: Optional[str] = None, **kwargs
    ) -> OllamaClient:
        """Create Ollama client.

        Args:
            model: Model name (default from OLLAMA_MODEL env var)
            **kwargs: Additional arguments

        Returns:
            Initialized Ollama client
        """
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        model = model or os.getenv("OLLAMA_MODEL", "llama3.1")

        return OllamaClient(base_url=base_url, model=model)
