"""LLM providers for the orchestration layer. Each returns one tool call or plain text.

  ollama     local model through the Ollama REST API (default llama3.2, runs offline)
  anthropic  Claude through the official anthropic SDK (pip install anthropic)
  openai     OpenAI chat completions over HTTP (needs OPENAI_API_KEY)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

import httpx

from . import config


@dataclass
class ToolCall:
    name: str
    args: dict


class ProviderError(RuntimeError):
    pass


class OllamaProvider:
    def __init__(self) -> None:
        self.model = config.model_name("llama3.2")
        self.host = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")

    async def complete(self, system: str, user: str, tools: list[dict]) -> ToolCall | str:
        payload = {
            "model": self.model,
            "stream": False,
            "options": {"temperature": 0},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "tools": [{"type": "function", "function": t} for t in tools],
        }
        try:
            async with httpx.AsyncClient(timeout=180) as client:
                resp = await client.post(f"{self.host}/api/chat", json=payload)
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"Ollama request failed: {exc}") from exc
        message = resp.json().get("message", {})
        calls = message.get("tool_calls") or []
        if calls:
            fn = calls[0]["function"]
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                args = json.loads(args)
            return ToolCall(fn["name"], args)
        return message.get("content", "")


class AnthropicProvider:
    def __init__(self) -> None:
        try:
            import anthropic
        except ImportError as exc:
            raise ProviderError("install the SDK first: pip install anthropic") from exc
        self.anthropic = anthropic
        self.client = anthropic.AsyncAnthropic()
        self.model = config.model_name("claude-opus-5-5")

    async def complete(self, system: str, user: str, tools: list[dict]) -> ToolCall | str:
        try:
            response = await self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=system,
                messages=[{"role": "user", "content": user}],
                tools=[{"name": t["name"], "description": t["description"],
                        "input_schema": t["parameters"], "strict": True} for t in tools],
                tool_choice={"type": "auto"},
                output_config={"effort": "medium"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except self.anthropic.APIStatusError as exc:
            raise ProviderError(f"Claude API error {exc.status_code}: {exc.message}") from exc
        except self.anthropic.APIConnectionError as exc:
            raise ProviderError("could not reach the Claude API") from exc
        if response.stop_reason == "refusal":
            raise ProviderError("the model declined this request")
        for block in response.content:
            if block.type == "tool_use":
                return ToolCall(block.name, dict(block.input))
        return "".join(b.text for b in response.content if b.type == "text")


class OpenAIProvider:
    def __init__(self) -> None:
        self.key = os.environ.get("OPENAI_API_KEY")
        if not self.key:
            raise ProviderError("set OPENAI_API_KEY to use the openai provider")
        self.model = config.model_name("gpt-4o-mini")

    async def complete(self, system: str, user: str, tools: list[dict]) -> ToolCall | str:
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "tools": [{"type": "function", "function": t} for t in tools],
            "tool_choice": "auto",
        }
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post("https://api.openai.com/v1/chat/completions", json=payload,
                                         headers={"Authorization": f"Bearer {self.key}"})
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"OpenAI request failed: {exc}") from exc
        message = resp.json()["choices"][0]["message"]
        calls = message.get("tool_calls") or []
        if calls:
            fn = calls[0]["function"]
            return ToolCall(fn["name"], json.loads(fn.get("arguments") or "{}"))
        return message.get("content") or ""


PROVIDERS = {"ollama": OllamaProvider, "anthropic": AnthropicProvider, "openai": OpenAIProvider}


def make_provider(name: str):
    if name not in PROVIDERS:
        raise ProviderError(f"unknown provider {name!r}; choose rules, " + ", ".join(PROVIDERS))
    return PROVIDERS[name]()
