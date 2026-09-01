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

from mini_code.agent import CodingAgent
from mini_code.events import EventSink
from mini_code.tools import ExploreDelegationConfig, build_coding_registry
from mini_code.workspace import Workspace


class ChildExploreProvider:
    model = "fake-child"
    last_usage = {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}

    def __init__(self, invalid: bool = False) -> None:
        self.calls = 0
        self.invalid = invalid

    def stream_chat(self, messages, tools) -> Iterator[dict[str, Any]]:
        self.calls += 1
        if self.calls == 1:
            yield _chunk(tool="read_file", arguments={"path": "app.py"}, call_id="child-read")
            return
        if self.invalid:
            yield _chunk(content="not-json")
            return
        yield _chunk(content=json.dumps({
            "summary": "app.py 的 value 返回值错误。",
            "evidence": [{"path": "app.py", "lines": "1-2", "reason": "value 实现"}],
            "suspected_root_cause": "返回了 0",
            "recommended_next_steps": ["父 Agent 复核 app.py 后修改为 1"],
            "risks": [],
        }, ensure_ascii=False))


class ParentCodingProvider:
    model = "fake-parent"
    last_usage: dict[str, Any] = {}

    def __init__(self) -> None:
        patch = "--- a/app.py\n+++ b/app.py\n@@ -1,2 +1,2 @@\n def value():\n-    return 0\n+    return 1\n"
        working = {"steps": [
            {"step": "Use child evidence and inspect tests", "status": "completed"},
            {"step": "Fix value", "status": "in_progress"},
            {"step": "Verify and review", "status": "pending"},
        ]}
        done = {"steps": [
            {"step": "Use child evidence and inspect tests", "status": "completed"},
            {"step": "Fix value", "status": "completed"},
            {"step": "Verify and review", "status": "completed"},
        ]}
        self.actions = [
            ("delegate_explore", {"question": "定位 value 的错误实现"}),
            ("read_file", {"path": "app.py"}),
            ("read_file", {"path": "tests/test_app.py"}),
            ("update_plan", working),
            ("apply_patch", {"patch": patch}),
            ("run_tests", {"target": "tests", "framework": "unittest"}),
            ("update_plan", done),
            ("git_diff", {}),
            ("finish_task", {"summary": "Fixed value using verified child evidence"}),
        ]
        self.calls = 0

    def stream_chat(self, messages, tools) -> Iterator[dict[str, Any]]:
        if self.calls >= len(self.actions):
            yield _chunk(content="父子协作完成，测试与 Diff 已验证。")
            return
        name, arguments = self.actions[self.calls]
        self.calls += 1
        yield _chunk(tool=name, arguments=arguments, call_id=f"parent-{self.calls}")


class DelegationTests(unittest.TestCase):
    def test_parent_must_verify_child_evidence_and_delegation_is_limited(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app.py").write_text("def value():\n    return 0\n", encoding="utf-8")
            workspace = Workspace(root)
            registry = build_coding_registry(
                workspace,
                delegation=ExploreDelegationConfig(
                    parent_run_id="parent-tools",
                    provider_factory=ChildExploreProvider,
                    max_delegations=2,
                ),
            )
            delegated = registry.execute("delegate_explore", {"question": "find value"})
            self.assertTrue(delegated["ok"])
            child_id = delegated["data"]["child_run_id"]
            self.assertEqual(delegated["data"]["parent_run_id"], "parent-tools")
            self.assertTrue((root / ".mini-code/runs" / child_id / "events.jsonl").is_file())

            registry.execute("update_plan", {"steps": [
                {"step": "fix", "status": "in_progress"}
            ]})
            patch = "--- a/app.py\n+++ b/app.py\n@@ -1,2 +1,2 @@\n def value():\n-    return 0\n+    return 1\n"
            rejected = registry.execute("apply_patch", {"patch": patch})
            self.assertEqual(rejected["error"]["code"], "subagent_evidence_unverified")
            self.assertTrue(registry.execute("read_file", {"path": "app.py"})["ok"])
            self.assertTrue(registry.execute("apply_patch", {"patch": patch})["ok"])

            self.assertTrue(registry.execute(
                "delegate_explore", {"question": "double-check value"}
            )["ok"])
            limited = registry.execute("delegate_explore", {"question": "third child"})
            self.assertEqual(limited["error"]["code"], "delegation_limit")
            restored = type(registry.coding_state).from_dict(registry.coding_state.to_dict())
            self.assertEqual(restored.delegation_count, 2)
            self.assertIn("app.py", restored.verified_evidence_files)

    def test_parent_child_loop_records_both_traces_and_completes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_fixture(root)
            workspace = Workspace(root)
            parent_id = "parent-loop"
            registry = build_coding_registry(
                workspace,
                delegation=ExploreDelegationConfig(
                    parent_run_id=parent_id,
                    provider_factory=ChildExploreProvider,
                ),
            )
            trace = root / ".mini-code/runs" / parent_id / "events.jsonl"
            result = CodingAgent(
                provider=ParentCodingProvider(),
                workspace=workspace,
                tools=registry,
                sink=EventSink(trace, io.StringIO()),
                max_steps=12,
            ).run("Fix value with an Explore child", run_id=parent_id)

            self.assertEqual(result.status, "completed")
            self.assertIn("return 1", (root / "app.py").read_text(encoding="utf-8"))
            parent_events = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
            parent_types = [event["type"] for event in parent_events]
            self.assertIn("subagent_delegated", parent_types)
            self.assertIn("subagent_result", parent_types)
            self.assertIn("verification_passed", parent_types)
            self.assertIn("completion_verified", parent_types)
            child_id = registry.coding_state.delegations[0]["child_run_id"]
            child_events = [
                json.loads(line)
                for line in (root / ".mini-code/runs" / child_id / "events.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(child_events[0]["data"]["parent_run_id"], parent_id)
            self.assertEqual(child_events[-1]["type"], "subagent_completed")

    def test_failed_child_returns_control_for_direct_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app.py").write_text("def value():\n    return 0\n", encoding="utf-8")
            registry = build_coding_registry(
                Workspace(root),
                delegation=ExploreDelegationConfig(
                    parent_run_id="fallback-parent",
                    provider_factory=lambda: ChildExploreProvider(invalid=True),
                ),
            )
            failed = registry.execute("delegate_explore", {"question": "find value"})
            self.assertEqual(failed["error"]["code"], "subagent_failed")
            self.assertTrue(registry.execute("search_code", {"query": "value"})["ok"])
            self.assertTrue(registry.execute("read_file", {"path": "app.py"})["ok"])
            self.assertFalse(registry.coding_state.delegated_evidence_files)


def _write_fixture(root: Path) -> None:
    (root / "app.py").write_text("def value():\n    return 0\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests/test_app.py").write_text(
        "import unittest\nfrom app import value\n\n"
        "class ValueTests(unittest.TestCase):\n"
        "    def test_value(self):\n        self.assertEqual(value(), 1)\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=root, check=True)


def _chunk(
    content: str | None = None,
    tool: str | None = None,
    arguments: dict[str, Any] | None = None,
    call_id: str = "call",
) -> dict[str, Any]:
    calls = None
    if tool is not None:
        calls = [{
            "index": 0,
            "id": call_id,
            "type": "function",
            "function": {"name": tool, "arguments": json.dumps(arguments or {})},
        }]
    return {"choices": [{"delta": {"content": content, "tool_calls": calls}}]}


if __name__ == "__main__":
    unittest.main()
