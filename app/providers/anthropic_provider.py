"""Anthropic LLM Provider implementation.

Handles all Anthropic-specific API format details:
- anthropic-version header
- thinking: {type: disabled} parameter
- tool_use/tool_result content block format
- Retry logic with exponential backoff
"""

import json
import random
import time

import requests

from app.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from app.providers.llm_base import LLMProvider

_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 2  # seconds, exponential backoff starting point


class AnthropicProvider(LLMProvider):
    """LLM provider for Anthropic-compatible APIs.

    Uses the Anthropic Messages API format with:
    - Header: anthropic-version: 2023-06-01
    - Parameter: thinking: {type: disabled}
    - Content blocks: text, tool_use, tool_result
    """

    def __init__(self, api_key: str = None, base_url: str = None,
                 model: str = None):
        self.api_key = api_key or LLM_API_KEY
        self.base_url = base_url or LLM_BASE_URL
        self.model = model or LLM_MODEL
        self.api_url = f"{self.base_url.rstrip('/')}/v1/messages"

    # ------------------------------------------------------------------
    # Public interface (implements LLMProvider ABC)
    # ------------------------------------------------------------------

    def chat(self, messages: list, system: str = None,
             max_tokens: int = 4096) -> dict:
        """Send a chat request (no tools). Returns standardized response."""
        headers = self._build_headers()
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages,
            "thinking": {"type": "disabled"},
        }
        if system:
            payload["system"] = system
        return self._retry_request(headers, payload)

    def chat_with_tools(self, messages: list, tools: list,
                        system: str = None, max_tokens: int = 4096) -> dict:
        """Send a chat request with tool definitions.

        Returns standardized response. Tool calls appear as tool_use blocks.
        """
        headers = self._build_headers()
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages,
            "tools": tools,
            "thinking": {"type": "disabled"},
        }
        if system:
            payload["system"] = system
        return self._retry_request(headers, payload)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "anthropic-version": "2023-06-01",
        }

    def _retry_request(self, headers: dict, payload: dict,
                       timeout: int = 120) -> dict:
        """Exponential backoff + jitter for 429/5xx/timeout errors."""
        last_error = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = requests.post(
                    self.api_url, headers=headers, json=payload,
                    timeout=timeout,
                )
                if resp.status_code == 429:
                    delay = _RETRY_BASE_DELAY ** (attempt + 1) + random.uniform(0, 1)
                    time.sleep(delay)
                    last_error = resp
                    continue
                if resp.status_code in (401, 403):
                    raise Exception(
                        f"API authentication failed ({resp.status_code}), "
                        f"please check LLM_API_KEY"
                    )
                if resp.status_code >= 500:
                    delay = _RETRY_BASE_DELAY ** (attempt + 1) + random.uniform(0, 1)
                    time.sleep(delay)
                    last_error = resp
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.Timeout:
                last_error = Exception(f"Request timeout ({timeout}s)")
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_RETRY_BASE_DELAY + random.uniform(0, 1))
            except requests.exceptions.ConnectionError:
                last_error = Exception("Cannot connect to LLM API")
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_RETRY_BASE_DELAY + random.uniform(0, 2))
            except requests.exceptions.RequestException as e:
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_RETRY_BASE_DELAY + random.uniform(0, 1))
                last_error = e
            except Exception:
                raise  # don't retry non-network errors (e.g. auth failure)
        if last_error is not None:
            if isinstance(last_error, requests.models.Response):
                last_error = Exception(
                    f"{last_error.status_code} {last_error.reason}"
                )
            raise last_error
        raise Exception("LLM API request failed: exceeded max retries")
