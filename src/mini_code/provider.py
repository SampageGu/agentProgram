"""Model provider abstraction and DeepSeek OpenAI-compatible streaming client."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import Any, Protocol

from mini_code.config import Settings


class ProviderError(RuntimeError):
    pass


class ModelProvider(Protocol):
    model: str

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> Iterator[dict[str, Any]]: ...


class DeepSeekProvider:
    """Minimal streaming Chat Completions client using only the standard library."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.model = settings.model
        self.last_usage: dict[str, Any] = {}

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> Iterator[dict[str, Any]]:
        self.last_usage = {}
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        request = urllib.request.Request(
            f"{self.settings.base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.settings.api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            "User-Agent": "mini-code-agent/0.7.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=self.settings.request_timeout,
            ) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line or line.startswith(":") or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    try:
                        chunk = json.loads(data)
                        if isinstance(chunk.get("usage"), dict):
                            self.last_usage = chunk["usage"]
                        yield chunk
                    except json.JSONDecodeError as exc:
                        raise ProviderError(f"Invalid SSE JSON: {data[:200]}") from exc
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:2_000]
            raise ProviderError(f"DeepSeek HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise ProviderError(f"Cannot reach DeepSeek API: {exc.reason}") from exc
        except TimeoutError as exc:
            raise ProviderError("DeepSeek request timed out") from exc


class FaultInjectingProvider:
    """Deterministically fail one model call for recovery demonstrations."""

    def __init__(self, wrapped: ModelProvider, fail_on_call: int) -> None:
        if fail_on_call <= 0:
            raise ValueError("fail_on_call must be positive")
        self.wrapped = wrapped
        self.fail_on_call = fail_on_call
        self.calls = 0
        self.model = wrapped.model
        self.last_usage: dict[str, Any] = {}

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> Iterator[dict[str, Any]]:
        self.calls += 1
        self.last_usage = {}
        if self.calls == self.fail_on_call:
            raise ProviderError(f"Injected model failure on call {self.calls}")
        yield from self.wrapped.stream_chat(messages, tools)
        self.last_usage = dict(getattr(self.wrapped, "last_usage", {}))
