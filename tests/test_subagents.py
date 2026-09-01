from __future__ import annotations

import io
import json
import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from mini_code.subagents import (
    ExploreSubagent,
    ExplorationReport,
    SubagentReportError,
)
from mini_code.workspace import Workspace


class ExploreFakeProvider:
    model = "fake-explore"
    last_usage = {"prompt_tokens": 30, "completion_tokens": 10, "total_tokens": 40}

    def __init__(self, final_answer: str | None = None) -> None:
        self.calls = 0
        self.tool_names: list[str] = []
        self.final_answer = final_answer or json.dumps(
            {
                "summary": "入口常量定义在 app.py。",
                "evidence": [
                    {"path": "app.py", "lines": "1", "reason": "定义 entry 常量"}
                ],
                "suspected_root_cause": "",
                "recommended_next_steps": ["由主 Agent 复核 app.py:1"],
                "risks": [],
            },
            ensure_ascii=False,
        )

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> Iterator[dict[str, Any]]:
        self.tool_names = [tool["function"]["name"] for tool in tools]
        self.calls += 1
        if self.calls == 1:
            yield _chunk(
                tool_calls=[{
                    "index": 0,
                    "id": "explore_read",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path":"app.py"}'},
                }]
            )
            return
        yield _chunk(content=self.final_answer)


class EndlessExploreProvider:
    model = "endless-explore"
    last_usage: dict[str, Any] = {}

    def stream_chat(self, messages, tools):
        yield _chunk(
            tool_calls=[{
                "index": 0,
                "id": "again",
                "type": "function",
                "function": {"name": "read_file", "arguments": '{"path":"app.py"}'},
            }]
        )


class ExpensiveExploreProvider(EndlessExploreProvider):
    model = "expensive-explore"
    last_usage = {"total_tokens": 70_000}


class ExploreSubagentTests(unittest.TestCase):
    def test_child_run_is_read_only_isolated_and_writes_valid_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app.py").write_text("entry = 'main'\n", encoding="utf-8")
            provider = ExploreFakeProvider()
            result = ExploreSubagent(provider, Workspace(root), max_steps=3).run(
                "定位程序入口",
                parent_run_id="parent-123",
                child_run_id="explore-child",
                stream=io.StringIO(),
            )

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.total_tokens, 80)
            self.assertEqual(
                set(provider.tool_names), {"list_files", "search_code", "read_file"}
            )
            self.assertNotIn("apply_patch", provider.tool_names)
            self.assertIsNotNone(result.report)
            self.assertEqual(result.report.evidence[0].path, "app.py")
            self.assertTrue(result.report_file.is_file())
            saved = json.loads(result.report_file.read_text(encoding="utf-8"))
            self.assertEqual(saved["parent_run_id"], "parent-123")
            events = [
                json.loads(line)
                for line in result.trace_file.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(events[0]["type"], "subagent_started")
            self.assertEqual(events[0]["data"]["parent_run_id"], "parent-123")
            self.assertEqual(events[-1]["type"], "subagent_completed")

    def test_invalid_report_is_rejected_and_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app.py").write_text("entry = 'main'\n", encoding="utf-8")
            result = ExploreSubagent(
                ExploreFakeProvider(final_answer='{"summary":"missing fields"}'),
                Workspace(root),
                max_steps=3,
            ).run("定位入口", child_run_id="invalid-child", stream=io.StringIO())
            self.assertEqual(result.status, "invalid_report")
            self.assertIsNone(result.report)
            self.assertIn("report fields", result.report_error)
            self.assertIn("subagent_failed", result.trace_file.read_text(encoding="utf-8"))

    def test_report_rejects_protected_or_nonexistent_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".env").write_text("SECRET=x\n", encoding="utf-8")
            payload = json.dumps({
                "summary": "secret",
                "evidence": [{"path": ".env", "lines": "1", "reason": "secret"}],
                "suspected_root_cause": "",
                "recommended_next_steps": ["none"],
                "risks": [],
            })
            with self.assertRaises(SubagentReportError):
                ExplorationReport.parse(payload, Workspace(root))

            (root / "app.py").write_text("entry = 'main'\n", encoding="utf-8")
            out_of_range = json.dumps({
                "summary": "entry",
                "evidence": [{"path": "app.py", "lines": "9", "reason": "outside"}],
                "suspected_root_cause": "",
                "recommended_next_steps": ["none"],
                "risks": [],
            })
            with self.assertRaises(SubagentReportError):
                ExplorationReport.parse(out_of_range, Workspace(root))

    def test_child_step_budget_is_independent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app.py").write_text("entry = 'main'\n", encoding="utf-8")
            result = ExploreSubagent(
                EndlessExploreProvider(), Workspace(root), max_steps=1
            ).run("持续读取", child_run_id="budget-child", stream=io.StringIO())
            self.assertEqual(result.status, "budget_exhausted")
            self.assertEqual(result.steps, 1)
            self.assertIsNone(result.report_file)

    def test_child_token_budget_stops_before_more_tool_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app.py").write_text("entry = 'main'\n", encoding="utf-8")
            result = ExploreSubagent(
                ExpensiveExploreProvider(),
                Workspace(root),
                max_steps=3,
                max_total_tokens=64_000,
            ).run("控制成本", child_run_id="token-child", stream=io.StringIO())
            self.assertEqual(result.status, "budget_exhausted")
            trace = result.trace_file.read_text(encoding="utf-8")
            self.assertIn('"budget_type": "tokens"', trace)
            self.assertNotIn('"type": "tool_started"', trace)


def _chunk(
    content: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {"choices": [{"delta": {"content": content, "tool_calls": tool_calls}}]}


if __name__ == "__main__":
    unittest.main()
