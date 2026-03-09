import asyncio
import os
from typing import Any, Dict, Optional

import yaml
from agent_service.llm import LLMClientFactory, LLMMessage
from shared_models import configure_logging

from .util import load_config_from_path, resolve_agent_service_path

logger = configure_logging("agent-service")


class Agent:
    """
    Agent that loads configuration from agent YAML files and provides LLM integration.
    """

    def __init__(
        self,
        agent_name: str,
        config: dict[str, Any],
        global_config: dict[str, Any] | None = None,
        system_message: str | None = None,
    ):
        """Initialize agent with provided configuration."""
        self.agent_name = agent_name
        self.config = config
        self.global_config = global_config or {}

        # Initialize LLM client using factory (supports OpenAI, Gemini, Ollama)
        llm_backend = self.config.get("llm_backend") or self.global_config.get(
            "llm_backend"
        )
        llm_model = self.config.get("llm_model") or self.global_config.get("llm_model")

        self.llm_client = LLMClientFactory.create_client(
            backend=llm_backend, model=llm_model
        )

        # Model name is now managed by the LLM client
        self.model: str = self.llm_client.get_model_name()

        self.default_response_config = self._get_response_config()
        self.system_message = system_message or self._get_default_system_message()

        # Defer tools initialization to first use
        self.tools: list[Any] | None = None

        # Load shield configuration for input/output moderation
        # Check if SAFETY environment variables are configured
        safety_model = os.getenv("SAFETY")
        safety_url = os.getenv("SAFETY_URL")
        shields_available = bool(safety_model and safety_url)

        if shields_available:
            self.input_shields = self.config.get("input_shields", [])
            self.output_shields = self.config.get("output_shields", [])
        else:
            # Disable shields if SAFETY environment not configured
            self.input_shields = []
            self.output_shields = []
            if self.config.get("input_shields") or self.config.get("output_shields"):
                logger.warning(
                    "Shields configured in agent but SAFETY/SAFETY_URL environment variables not set. Shields will be disabled.",
                    agent_name=agent_name,
                )

        # Load categories to ignore (for handling false positives)
        self.ignored_input_categories = set(
            self.config.get("ignored_input_shield_categories", [])
        )
        self.ignored_output_categories = set(
            self.config.get("ignored_output_shield_categories", [])
        )

        logger.info(
            "Initialized Agent",
            agent_name=agent_name,
            model="deferred" if self.model is None else self.model,
            tool_count="deferred" if self.tools is None else len(self.tools),
        )
        if self.input_shields:
            logger.info("Input shields configured", shields=self.input_shields)
            if self.ignored_input_categories:
                logger.info(
                    "Ignored input categories", categories=self.ignored_input_categories
                )
        if self.output_shields:
            logger.info("Output shields configured", shields=self.output_shields)
            if self.ignored_output_categories:
                logger.info(
                    "Ignored output categories",
                    categories=self.ignored_output_categories,
                )

    def _get_response_config(self) -> dict[str, Any]:
        """Get response configuration from agent config with defaults."""
        base_config = {
            "stream": False,
            "temperature": 0.7,
        }

        if self.config and "sampling_params" in self.config:
            sampling_params = self.config["sampling_params"]
            if "strategy" in sampling_params:
                strategy = sampling_params["strategy"]
                if "temperature" in strategy:
                    base_config["temperature"] = strategy["temperature"]

        return base_config

    def _get_default_system_message(self) -> str:
        """Get default system message for the agent."""
        if self.config and self.config.get("system_message"):
            message = self.config["system_message"]
            return str(message) if message is not None else ""

        return ""

    async def _run_moderation_shields(
        self,
        content: Any,
        shield_models: list[str],
        check_type: str = "input",
    ) -> tuple[bool, Optional[str]]:
        """
        Run moderation checks using safety shields.

        NOTE: Shields are currently disabled.
        Content moderation can be added later if needed.

        Args:
            content: Either a string (for output) or list of message dicts (for input)
            shield_models: List of moderation model names (deprecated)
            check_type: "input" or "output" for logging purposes

        Returns:
            Tuple of (is_safe, error_message)
            - is_safe: Always True (shields disabled)
            - error_message: Always None
        """
        if not shield_models or len(shield_models) == 0:
            return True, None

        # Content moderation shields not yet implemented
        logger.debug(
            "Shields requested but currently disabled",
            check_type=check_type,
            shield_models=shield_models,
        )
        return True, None  # Pass all content for now

    async def create_response_with_retry(
        self,
        messages: list[Any],
        max_retries: int = 3,
        temperature: float | None = None,
        additional_system_messages: list[str] | None = None,
        authoritative_user_id: str | None = None,
        allowed_tools: list[str] | None = None,
        skip_all_tools: bool = False,
        skip_mcp_servers_only: bool = False,
        current_state_name: str | None = None,
        token_context: str | None = None,
    ) -> tuple[str, bool]:
        """Create a response with retry logic for empty responses and errors."""
        default_response = "I apologize, but I'm having difficulty generating a response right now. Please try again."
        response = default_response
        last_error = None

        for attempt in range(max_retries + 1):  # +1 for initial attempt plus retries
            should_retry = False
            retry_reason = None

            try:
                response = await self.create_response(
                    messages,
                    temperature=temperature,
                    additional_system_messages=additional_system_messages,
                    authoritative_user_id=authoritative_user_id,
                    allowed_tools=allowed_tools,
                    skip_all_tools=skip_all_tools,
                    skip_mcp_servers_only=skip_mcp_servers_only,
                    current_state_name=current_state_name,
                    token_context=token_context,
                )

                # Check if response is empty or contains error
                if response and response.strip():
                    # Check if it's an error message that we should retry
                    if response.startswith("Error: Unable to get response"):
                        last_error = response
                        should_retry = True
                        retry_reason = "error response"
                    else:
                        # Valid response, break out of retry loop
                        break
                else:
                    # Empty response detected
                    should_retry = True
                    retry_reason = "empty response"

            except Exception as e:
                last_error = str(e)
                logger.warning(
                    "Exception on retry attempt",
                    attempt=attempt + 1,
                    max_retries=max_retries + 1,
                    error=str(e),
                    error_type=type(e).__name__,
                )
                should_retry = True
                retry_reason = "exception"

            # Consolidated retry logic
            if should_retry:
                if attempt < max_retries:
                    retry_delay = min(
                        2**attempt, 16
                    )  # Exponential backoff: 1s, 2s, 4s, 8s, 16s max
                    logger.info(
                        "Retrying after failure",
                        reason=retry_reason,
                        attempt=attempt + 1,
                        max_attempts=max_retries + 1,
                        retry_delay=retry_delay,
                    )
                    await asyncio.sleep(retry_delay)
                else:
                    logger.warning(
                        "All retry attempts failed",
                        max_attempts=max_retries + 1,
                        last_error=last_error or "Empty response",
                    )
                    response = default_response
                    break

        response_failed = response == default_response
        return response, response_failed

    async def create_response(
        self,
        messages: list[Any],
        temperature: float | None = None,
        additional_system_messages: list[str] | None = None,
        authoritative_user_id: str | None = None,
        allowed_tools: list[str] | None = None,
        skip_all_tools: bool = False,
        skip_mcp_servers_only: bool = False,
        current_state_name: str | None = None,
        token_context: str | None = None,
    ) -> str:
        """Create a response using LLM client (OpenAI, Gemini, or Ollama).

        Args:
            messages: List of user/assistant messages
            temperature: Optional temperature override
            additional_system_messages: Optional list of additional system messages
            authoritative_user_id: Deprecated (for MCP servers)
            allowed_tools: Deprecated (for MCP servers)
            skip_all_tools: Deprecated (for MCP servers)
            skip_mcp_servers_only: Deprecated (for MCP servers)
            current_state_name: Optional state name for logging
            token_context: Optional context for token counting

        Returns:
            Generated response text
        """
        try:
            # INPUT SHIELD: Check user input before processing (currently no-op)
            if self.input_shields and messages and len(messages) > 0:
                is_safe, error_message = await self._run_moderation_shields(
                    messages, self.input_shields, "input"
                )
                if not is_safe:
                    logger.info(
                        "Input blocked by shield",
                        agent_name=self.agent_name,
                        messages=repr(messages),
                    )
                    return (
                        error_message
                        or "I apologize, but I cannot process that request due to safety concerns."
                    )

            # Build message list: system message(s) + conversation
            llm_messages = []

            # Add main system message
            if self.system_message:
                llm_messages.append(
                    LLMMessage(role="system", content=self.system_message)
                )

            # Add any additional system messages
            if additional_system_messages:
                for sys_msg in additional_system_messages:
                    llm_messages.append(LLMMessage(role="system", content=sys_msg))

            # Add the conversation messages
            for msg in messages:
                if isinstance(msg, dict):
                    llm_messages.append(
                        LLMMessage(role=msg.get("role", "user"), content=msg.get("content", ""))
                    )
                else:
                    # Handle other message formats
                    llm_messages.append(
                        LLMMessage(role="user", content=str(msg))
                    )

            # Get temperature from config or parameter
            temp = temperature if temperature is not None else self.default_response_config.get("temperature", 0.7)

            logger.debug(
                "Calling LLM",
                agent_name=self.agent_name,
                model=self.model,
                message_count=len(llm_messages),
                temperature=temp,
            )

            # Call LLM via our abstraction layer
            response = await self.llm_client.create_completion(
                messages=llm_messages,
                temperature=temp,
            )

            response_text = response.content

            # Token counting (optional)
            try:
                from .token_counter import TokenCounter

                context = token_context or "chat_agent"
                counter = TokenCounter()
                counter.add_tokens(
                    input_tokens=response.usage.get("prompt_tokens", 0),
                    output_tokens=response.usage.get("completion_tokens", 0),
                    context=context,
                )
            except (ImportError, Exception) as e:
                logger.debug(
                    "Token counting not available",
                    error=str(e),
                    error_type=type(e).__name__,
                )

            # Check for empty response
            if not response_text or not response_text.strip():
                logger.warning(
                    "Empty response from LLM",
                    agent_name=self.agent_name,
                    model=self.model,
                )
                return ""  # Return empty to trigger retry logic

            logger.debug(
                "LLM response received",
                agent_name=self.agent_name,
                response_length=len(response_text),
                total_tokens=response.total_tokens,
            )

            # OUTPUT SHIELD: Check agent response before returning (currently no-op)
            if self.output_shields and response_text:
                is_safe, error_message = await self._run_moderation_shields(
                    response_text, self.output_shields, "output"
                )
                if not is_safe:
                    logger.info(
                        "Output blocked by shield",
                        agent_name=self.agent_name,
                        response_text=repr(response_text),
                    )
                    return (
                        error_message
                        or "I apologize, but I cannot provide that response due to safety concerns."
                    )

            return response_text

        except Exception as e:
            logger.error(
                "Error calling LLM",
                agent_name=self.agent_name,
                model=self.model,
                error=str(e),
                error_type=type(e).__name__,
            )
            return f"Error: Unable to get response from LLM: {e}"


class ResponsesAgentManager:
    """Manages multiple agent instances for the application."""

    def __init__(self) -> None:
        self.agents_dict = {}

        # Load the configuration using centralized path resolution
        try:
            config_path = resolve_agent_service_path("config")
            logger.info(
                "ResponsesAgentManager found config", config_path=str(config_path)
            )
        except FileNotFoundError as e:
            logger.error(
                "ResponsesAgentManager config not found",
                error=str(e),
                error_type=type(e).__name__,
            )
            raise

        agent_configs = load_config_from_path(config_path)

        # Load global configuration (config.yaml)
        global_config_path = config_path / "config.yaml"
        global_config: Dict[str, Any] = {}
        if global_config_path.exists():
            with open(global_config_path, "r") as f:
                global_config = yaml.safe_load(f) or {}

        # Create agents for each entry in the configuration
        agents_list = agent_configs.get("agents", [])
        for agent_config in agents_list:
            agent_name = agent_config.get("name")
            if agent_name:
                # Create the agent with the loaded configuration and global config
                self.agents_dict[agent_name] = Agent(
                    agent_name, agent_config, global_config
                )

    def get_agent(self, agent_id: str) -> Any:
        """Get an agent by ID, returning default if not found."""
        if agent_id in self.agents_dict:
            return self.agents_dict[agent_id]

        # If agent_id not found, return first available agent
        if self.agents_dict:
            return next(iter(self.agents_dict.values()))

        # If no agents loaded, raise an error
        raise ValueError(
            f"No agent found with ID '{agent_id}' and no agents are loaded"
        )
