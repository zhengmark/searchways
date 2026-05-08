"""Shared LLM API client — single source of truth for all agents."""
import json
import time
import requests
from app.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL

API_URL = f"{LLM_BASE_URL.rstrip('/')}/v1/messages"
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 2  # 秒，指数退避起点


def _retry_request(headers: dict, payload: dict, timeout: int = 120) -> dict:
    """带指数退避的 API 请求，处理 429/5xx."""
    last_error = None
    for attempt in range(_MAX_RETRIES):
        try:
            resp = requests.post(API_URL, headers=headers, json=payload, timeout=timeout)
            if resp.status_code == 429:
                delay = _RETRY_BASE_DELAY ** (attempt + 1)
                time.sleep(delay)
                last_error = resp
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.Timeout:
            last_error = Exception(f"请求超时({timeout}s)")
            if attempt < _MAX_RETRIES - 1:
                time.sleep(_RETRY_BASE_DELAY)
        except requests.exceptions.RequestException as e:
            if attempt < _MAX_RETRIES - 1:
                time.sleep(_RETRY_BASE_DELAY)
            last_error = e
    if last_error is not None:
        if isinstance(last_error, requests.models.Response):
            last_error = Exception(f"{last_error.status_code} {last_error.reason}")
        raise last_error
    raise Exception("LLM API 请求失败：超过最大重试次数")


def call_llm(messages: list, system: str = None, max_tokens: int = 4096) -> dict:
    """Call the LLM API and return the response dict. All agents use this."""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LLM_API_KEY}",
        "anthropic-version": "2023-06-01",
    }
    payload = {
        "model": LLM_MODEL,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system:
        payload["system"] = system
    return _retry_request(headers, payload)


def call_llm_with_tools(messages: list, tools: list, system: str = None,
                        max_tokens: int = 4096) -> dict:
    """Call LLM with tool definitions. Returns full response (may contain tool_use blocks).

    Args:
        messages: Anthropic-format message list
        tools: Tool definitions (Anthropic tool format)
        system: System prompt
        max_tokens: Max tokens for the response

    Returns:
        Full API response dict. Check content blocks for "tool_use" type.
    """
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LLM_API_KEY}",
        "anthropic-version": "2023-06-01",
    }
    payload = {
        "model": LLM_MODEL,
        "max_tokens": max_tokens,
        "messages": messages,
        "tools": tools,
    }
    if system:
        payload["system"] = system
    return _retry_request(headers, payload)


def tool_result_message(tool_use_id: str, content: str) -> dict:
    """Create a tool_result user message for continuing a tool-use conversation."""
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


def extract_text(content: list) -> str:
    """Extract plain text from an Anthropic content block list."""
    parts = []
    for block in content:
        if block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts)


def extract_tool_uses(content: list) -> list[dict]:
    """Extract tool_use blocks from an Anthropic content block list."""
    return [b for b in content if b.get("type") == "tool_use"]


def parse_content(response: dict) -> list:
    """Safely extract content list from API response."""
    return response.get("content", [])
