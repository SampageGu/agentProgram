from __future__ import annotations

import unittest
from collections.abc import Iterator
from typing import Any

import _bootstrap  # noqa: F401

from mini_code.provider import FaultInjectingProvider, ProviderError


class BaseProvider:
    model = "base"
    last_usage = {"total_tokens": 3}

    def stream_chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> Iterator[dict[str, Any]]:
        yield {"choices": [{"delta": {"content": "ok"}}]}


class FaultInjectionTests(unittest.TestCase):
    def test_fails_exactly_one_configured_model_call(self) -> None:
        provider = FaultInjectingProvider(BaseProvider(), fail_on_call=2)
        self.assertEqual(len(list(provider.stream_chat([], []))), 1)
        with self.assertRaises(ProviderError):
            list(provider.stream_chat([], []))
        self.assertEqual(len(list(provider.stream_chat([], []))), 1)
        self.assertEqual(provider.last_usage["total_tokens"], 3)


if __name__ == "__main__":
    unittest.main()
