"""Shared LLM API client — single source of truth for all agents."""
import requests
from agent.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL

API_URL = f"{LLM_BASE_URL.rstrip('/')}/v1/messages"


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
    resp = requests.post(API_URL, headers=headers, json=payload, timeout=120)
    resp.raise_for_status()
    return resp.json()
