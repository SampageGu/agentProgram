from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from mini_code.agent import CodingAgent, ReadOnlyAgent
from mini_code.events import EventSink
from mini_code.tools import build_coding_registry, build_readonly_registry
from mini_code.workspace import Workspace


class FakeProvider:
    model = "fake-m0-model"

    def __init__(self) -> None:
        self.calls = 0

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> Iterator[dict[str, Any]]:
        self.calls += 1
        if self.calls == 1:
            yield _chunk(
                tool_calls=[
                    {
                        "index": 0,
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "search_", "arguments": '{"query":"entry'},
                    }
                ]
            )
            yield _chunk(
                tool_calls=[
                    {
                        "index": 0,
                        "function": {"name": "code", "arguments": '","path":"."}'},
                    }
                ]
            )
            return
        yield _chunk(content="入口位于 `app.py:1`。")


class MultiTurnFakeProvider:
    model = "fake-multi-turn-model"

    def __init__(self) -> None:
        self.requests: list[list[dict[str, Any]]] = []

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> Iterator[dict[str, Any]]:
        self.requests.append([dict(message) for message in messages])
        if messages[-1]["role"] == "tool":
            question = next(
                message["content"]
                for message in reversed(messages)
                if message["role"] == "user"
            )
            yield _chunk(content=f"已根据工具回答：{question}")
            return
        yield _chunk(
            tool_calls=[
                {
                    "index": 0,
                    "id": f"call_{len(self.requests)}",
                    "type": "function",
                    "function": {
                        "name": "search_code",
                        "arguments": '{"query":"entry","path":"."}',
                    },
                }
            ]
        )


class CodingFakeProvider:
    model = "fake-m1-model"

    def __init__(self) -> None:
        plan_working = {
            "steps": [
                {"step": "Inspect implementation and tests", "status": "completed"},
                {"step": "Fix zero division", "status": "in_progress"},
                {"step": "Run verification and review diff", "status": "pending"},
            ]
        }
        plan_done = {
            "steps": [
                {"step": "Inspect implementation and tests", "status": "completed"},
                {"step": "Fix zero division", "status": "completed"},
                {"step": "Run verification and review diff", "status": "completed"},
            ]
        }
        bad_patch = (
            "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,4 @@\n"
            " def divide(a, b):\n+    if b == 0:\n+        return 0\n     return a / b\n"
        )
        good_patch = (
            "--- a/calc.py\n+++ b/calc.py\n@@ -2,2 +2,2 @@\n"
            "     if b == 0:\n-        return 0\n+        raise ValueError('division by zero')\n"
        )
        self.actions: list[tuple[str, dict[str, Any]]] = [
            ("read_file", {"path": "calc.py"}),
            ("read_file", {"path": "tests/test_calc.py"}),
            ("update_plan", plan_working),
            ("apply_patch", {"patch": bad_patch}),
            ("run_tests", {"target": "tests", "framework": "unittest"}),
            ("apply_patch", {"patch": good_patch}),
            ("run_tests", {"target": "tests", "framework": "unittest"}),
            ("update_plan", plan_done),
            ("git_diff", {}),
            ("finish_task", {"summary": "Fixed and verified zero division"}),
        ]
        self.calls = 0

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> Iterator[dict[str, Any]]:
        if self.calls >= len(self.actions):
            yield _chunk(content="修复完成，测试已经通过，Diff 已检查。")
            return
        name, arguments = self.actions[self.calls]
        self.calls += 1
        yield _chunk(tool_calls=[{
            "index": 0,
            "id": f"m1_call_{self.calls}",
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)},
        }])


def _chunk(
    content: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "choices": [
            {
                "delta": {
                    "content": content,
                    "tool_calls": tool_calls,
                }
            }
        ]
    }


class AgentLoopTests(unittest.TestCase):
    def test_streams_tool_events_and_persists_trace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app.py").write_text("entry = 'main'\n", encoding="utf-8")
            workspace = Workspace(root)
            trace_file = root / ".mini-code" / "runs" / "test" / "events.jsonl"
            output = io.StringIO()
            sink = EventSink(trace_file, stream=output)
            provider = FakeProvider()
            agent = ReadOnlyAgent(
                provider=provider,
                workspace=workspace,
                tools=build_readonly_registry(workspace),
                sink=sink,
                max_steps=3,
            )

            result = agent.run("入口在哪里？", run_id="test")

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.answer, "入口位于 `app.py:1`。")
            self.assertEqual(provider.calls, 2)
            event_types = [event.type for event in sink.events]
            self.assertIn("tool_started", event_types)
            self.assertIn("tool_completed", event_types)
            self.assertEqual(event_types[-1], "run_completed")
            persisted = [json.loads(line) for line in trace_file.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(persisted), len(sink.events))
            self.assertIn("入口位于", output.getvalue())

    def test_carries_messages_and_tool_context_into_follow_up_turn(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app.py").write_text("entry = 'main'\n", encoding="utf-8")
            workspace = Workspace(root)
            provider = MultiTurnFakeProvider()

            first = ReadOnlyAgent(
                provider=provider,
                workspace=workspace,
                tools=build_readonly_registry(workspace),
                sink=EventSink(root / ".mini-code/runs/first/events.jsonl", io.StringIO()),
                max_steps=3,
            ).run("入口在哪里？", run_id="first")
            second = ReadOnlyAgent(
                provider=provider,
                workspace=workspace,
                tools=build_readonly_registry(workspace),
                sink=EventSink(root / ".mini-code/runs/second/events.jsonl", io.StringIO()),
                max_steps=3,
            ).run("刚才那个入口是什么？", run_id="second", history=first.history)

            self.assertEqual(first.status, "completed")
            self.assertEqual(second.status, "completed")
            second_turn_request = provider.requests[2]
            self.assertTrue(
                any(
                    message.get("role") == "user" and message.get("content") == "入口在哪里？"
                    for message in second_turn_request
                )
            )
            self.assertTrue(any(message.get("role") == "tool" for message in second_turn_request))
            self.assertEqual(second.history[-1]["role"], "assistant")

    def test_m1_repairs_failed_test_and_requires_verified_completion(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "calc.py").write_text(
                "def divide(a, b):\n    return a / b\n", encoding="utf-8"
            )
            (root / "tests").mkdir()
            (root / "tests" / "test_calc.py").write_text(
                "import unittest\nfrom calc import divide\n\n"
                "class DivideTests(unittest.TestCase):\n"
                "    def test_zero(self):\n"
                "        with self.assertRaises(ValueError):\n"
                "            divide(1, 0)\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "baseline"], cwd=root, check=True)

            workspace = Workspace(root)
            output = io.StringIO()
            sink = EventSink(root / ".mini-code/runs/m1/events.jsonl", output)
            provider = CodingFakeProvider()
            result = CodingAgent(
                provider=provider,
                workspace=workspace,
                tools=build_coding_registry(workspace),
                sink=sink,
                max_steps=14,
            ).run("Fix zero division and add verification", run_id="m1")

            self.assertEqual(result.status, "completed")
            self.assertIn("raise ValueError", (root / "calc.py").read_text(encoding="utf-8"))
            event_types = [event.type for event in sink.events]
            self.assertIn("verification_failed", event_types)
            self.assertIn("verification_passed", event_types)
            self.assertIn("completion_verified", event_types)
            self.assertLess(
                event_types.index("verification_failed"),
                event_types.index("verification_passed"),
            )


if __name__ == "__main__":
    unittest.main()
