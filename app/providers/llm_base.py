"""LLM Provider abstract base class.

Defines the standardized interface that all LLM providers must implement.
The standardized response format follows Anthropic's content block structure
since that's what the existing codebase expects.
"""

from abc import ABC, abstractmethod


class LLMProvider(ABC):
    """Abstract base for LLM API providers.

    All providers must return responses in the standardized format:
        {
            "content": [
                {"type": "text", "text": "..."},
                {"type": "tool_use", "id": "...", "name": "...", "input": {...}},
            ],
            "stop_reason": "end_turn" | "tool_use" | ...
        }
    """

    @abstractmethod
    def chat(self, messages: list, system: str = None,
             max_tokens: int = 4096) -> dict:
        """Send a chat request and return standardized response dict."""
        ...

    @abstractmethod
    def chat_with_tools(self, messages: list, tools: list,
                        system: str = None, max_tokens: int = 4096) -> dict:
        """Send a chat request with tool definitions.

        Returns standardized response dict. Tool calls appear as
        {"type": "tool_use", "id": "...", "name": "...", "input": {...}}
        blocks in the content list.
        """
        ...

    @staticmethod
    def extract_text(response_or_content) -> str:
        """Extract plain text from a response dict or content block list.

        Args:
            response_or_content: Either a full response dict (with 'content' key)
                                 or a list of content blocks directly.

        Returns:
            Concatenated text from all text-type content blocks.
        """
        if isinstance(response_or_content, dict):
            content = response_or_content.get("content", [])
        else:
            content = response_or_content
        parts = []
        for block in content:
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts)

    @staticmethod
    def extract_tool_uses(response_or_content) -> list:
        """Extract tool_use blocks from a response dict or content block list.

        Args:
            response_or_content: Either a full response dict (with 'content' key)
                                 or a list of content blocks directly.

        Returns:
            List of tool_use dicts (each has type/id/name/input keys).
        """
        if isinstance(response_or_content, dict):
            content = response_or_content.get("content", [])
        else:
            content = response_or_content
        return [b for b in content if b.get("type") == "tool_use"]

    @staticmethod
    def tool_result_message(tool_use_id: str, content: str) -> dict:
        """Create a tool_result user message for continuing a tool-use conversation.

        Returns a message dict in the standardized format:
            {"role": "user", "content": [{"type": "tool_result", ...}]}
        """
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": content,
                }
            ],
        }
