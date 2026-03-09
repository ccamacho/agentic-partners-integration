#!/usr/bin/env python3
"""
Token Counter Utility for Agent Service

Provides thread-safe token counting for LLM calls in agent service.
"""

import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from shared_models import configure_logging

logger = configure_logging("agent-service")


@dataclass
class TokenUsage:
    """Token usage data for a single LLM call"""

    input_tokens: int
    output_tokens: int
    total_tokens: int
    model: Optional[str] = None
    context: Optional[str] = None
    timestamp: Optional[float] = None

    def __post_init__(self) -> None:
        if self.timestamp is None:
            import time

            self.timestamp = time.time()


@dataclass
class TokenStats:
    """Aggregate token statistics"""

    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    call_count: int = 0
    max_input_tokens: int = 0
    max_output_tokens: int = 0
    max_total_tokens: int = 0
    calls: List[TokenUsage] = field(default_factory=list)

    def add_usage(self, usage: TokenUsage) -> None:
        """Add a token usage record"""
        self.total_input_tokens += usage.input_tokens
        self.total_output_tokens += usage.output_tokens
        self.total_tokens += usage.total_tokens
        self.call_count += 1

        # Update maximum values
        self.max_input_tokens = max(self.max_input_tokens, usage.input_tokens)
        self.max_output_tokens = max(self.max_output_tokens, usage.output_tokens)
        self.max_total_tokens = max(self.max_total_tokens, usage.total_tokens)

        self.calls.append(usage)


class TokenCounter:
    """Thread-safe global token counter"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls) -> "TokenCounter":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if not getattr(self, "_initialized", False):
            self._stats_lock = threading.Lock()
            self._stats = TokenStats()
            self._context_stats: Dict[str, TokenStats] = {}
            self._initialized = True

    def add_tokens(
        self,
        input_tokens: int,
        output_tokens: int,
        model: Optional[str] = None,
        context: Optional[str] = None,
    ) -> None:
        """Add token usage with optional context"""
        total_tokens = input_tokens + output_tokens
        usage = TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            model=model,
            context=context,
        )

        with self._stats_lock:
            self._stats.add_usage(usage)

            if context:
                if context not in self._context_stats:
                    self._context_stats[context] = TokenStats()
                self._context_stats[context].add_usage(usage)

