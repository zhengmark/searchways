"""Shared LLM API client — single source of truth for all agents.

Delegates to the active LLM provider (auto-detected from config/env).
All existing callers continue to work without changes.
"""

from app.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_PROVIDER
from app.providers.llm_base import LLMProvider


def _get_provider() -> LLMProvider:
    """Auto-detect and instantiate the active LLM provider.

    Detection order:
    1. LLM_PROVIDER env var (explicit)
    2. Heuristic: if LLM_BASE_URL contains "anthropic" → AnthropicProvider
    3. Default: AnthropicProvider
    """
    provider_name = LLM_PROVIDER.lower()

    if provider_name == "anthropic" or "anthropic" in LLM_BASE_URL.lower():
        from app.providers.anthropic_provider import AnthropicProvider

        return AnthropicProvider(
            api_key=LLM_API_KEY,
            base_url=LLM_BASE_URL,
            model=LLM_MODEL,
        )

    # Future: add more providers here (openai, etc.)
    # Fallback to Anthropic
    from app.providers.anthropic_provider import AnthropicProvider

    return AnthropicProvider(
        api_key=LLM_API_KEY,
        base_url=LLM_BASE_URL,
        model=LLM_MODEL,
    )


# Singleton provider instance (lazy-loaded on first use)
_provider: LLMProvider = None


def _ensure_provider() -> LLMProvider:
    global _provider
    if _provider is None:
        _provider = _get_provider()
    return _provider


# ---------------------------------------------------------------------------
# Public API — backward-compatible with existing callers
# ---------------------------------------------------------------------------


def call_llm(messages: list, system: str = None, max_tokens: int = 4096) -> dict:
    """Call the LLM API and return the response dict. All agents use this."""
    return _ensure_provider().chat(messages, system=system, max_tokens=max_tokens)


def call_llm_with_tools(messages: list, tools: list, system: str = None, max_tokens: int = 4096) -> dict:
    """Call LLM with tool definitions. Returns full response (may contain tool_use blocks).

    Args:
        messages: Message list
        tools: Tool definitions
        system: System prompt
        max_tokens: Max tokens for the response

    Returns:
        Full API response dict. Check content blocks for "tool_use" type.
    """
    return _ensure_provider().chat_with_tools(
        messages,
        tools,
        system=system,
        max_tokens=max_tokens,
    )


def tool_result_message(tool_use_id: str, content: str) -> dict:
    """Create a tool_result user message for continuing a tool-use conversation."""
    return LLMProvider.tool_result_message(tool_use_id, content)


def extract_text(content: list) -> str:
    """Extract plain text from a content block list."""
    return LLMProvider.extract_text(content)


def extract_tool_uses(content: list) -> list:
    """Extract tool_use blocks from a content block list."""
    return LLMProvider.extract_tool_uses(content)


def parse_content(response: dict) -> list:
    """Safely extract content list from API response."""
    return response.get("content", [])
