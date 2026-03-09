"""LLM abstraction layer for multi-backend support.

This module provides a unified interface for different LLM backends:
- OpenAI (GPT-4, GPT-3.5, etc.)
- Google Gemini (via Google AI API)
- Ollama (local LLMs)

Usage:
    from agent_service.llm import LLMClientFactory

    client = LLMClientFactory.create_client(backend="openai")
    response = await client.create_completion(messages, temperature=0.7)
"""

from .base import BaseLLMClient, InstrumentedLLMClient, LLMMessage, LLMResponse
from .factory import LLMClientFactory

__all__ = [
    "BaseLLMClient",
    "InstrumentedLLMClient",
    "LLMMessage",
    "LLMResponse",
    "LLMClientFactory",
]
