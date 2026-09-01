from __future__ import annotations

import io
import json
import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from mini_code.agent import CodingAgent
from mini_code.context import ContextManager
from mini_code.events import EventSink
from mini_code.persistence import RunStore
from mini_code.provider import ProviderError
from mini_code.tools import CodingToolState, build_coding_registry
from mini_code.workspace import Workspace


def _chunk(
    content: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {"choices": [{"delta": {"content": content, "tool_calls": tool_calls}}]}


class ScriptedProvider:
    model = "fake-m2-model"

    def __init__(self, actions: list[tuple[str, dict[str, Any]]], fail_after: bool = False) -> None:
        self.actions = actions
        self.fail_after = fail_after
        self.calls = 0
        self.requests: list[list[dict[str, Any]]] = []

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> Iterator[dict[str, Any]]:
        self.requests.append(messages)
        if self.calls < len(self.actions):
            name, arguments = self.actions[self.calls]
            self.calls += 1
            yield _chunk(tool_calls=[{
                "index": 0,
                "id": f"call-{self.calls}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }])
            return
        if self.fail_after:
            raise ProviderError("simulated provider interruption")
        yield _chunk(content="恢复后的任务已经验证完成。")


class ResumeAgentTests(unittest.TestCase):
    def test_resumes_after_patch_without_repeating_exploration_or_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "value.py").write_text("def value():\n    return 1\n", encoding="utf-8")
            (root / "tests").mkdir()
            (root / "tests/test_value.py").write_text(
                "import unittest\nfrom value import value\n\n"
                "class ValueTests(unittest.TestCase):\n"
                "    def test_value(self):\n        self.assertEqual(value(), 2)\n",
                encoding="utf-8",
            )
            workspace = Workspace(root)
            store = RunStore(workspace)
            run_id = "resume-test"
            trace = store.run_dir(run_id) / "events.jsonl"
            working_plan = {"steps": [
                {"step": "Inspect", "status": "completed"},
                {"step": "Fix", "status": "in_progress"},
                {"step": "Verify", "status": "pending"},
            ]}
            patch = (
                "--- a/value.py\n+++ b/value.py\n@@ -1,2 +1,2 @@\n"
                " def value():\n-    return 1\n+    return 2\n"
            )
            first_provider = ScriptedProvider(
                [
                    ("read_file", {"path": "value.py"}),
                    ("update_plan", working_plan),
                    ("apply_patch", {"patch": patch}),
                ],
                fail_after=True,
            )
            first_tools = build_coding_registry(workspace)
            first = CodingAgent(
                provider=first_provider,
                workspace=workspace,
                tools=first_tools,
                sink=EventSink(trace, io.StringIO()),
                max_steps=10,
                run_store=store,
                context_manager=ContextManager(store, run_id),
            ).run("Change value to 2 and verify", run_id=run_id)

            self.assertEqual(first.status, "failed")
            snapshot = store.load(run_id)
            self.assertEqual(snapshot.step, 3)
            self.assertEqual(snapshot.status, "failed")
            store.assert_workspace_unchanged(snapshot)

            done_plan = {"steps": [
                {"step": "Inspect", "status": "completed"},
                {"step": "Fix", "status": "completed"},
                {"step": "Verify", "status": "completed"},
            ]}
            second_provider = ScriptedProvider([
                ("run_tests", {"target": "tests", "framework": "unittest"}),
                ("update_plan", done_plan),
                ("git_diff", {}),
                ("finish_task", {"summary": "Changed value and verified tests"}),
            ])
            resumed_tools = build_coding_registry(
                workspace,
                initial_state=CodingToolState.from_dict(snapshot.tool_state),
            )
            resumed = CodingAgent(
                provider=second_provider,
                workspace=workspace,
                tools=resumed_tools,
                sink=EventSink(trace, io.StringIO()),
                max_steps=12,
                run_store=store,
                context_manager=ContextManager(store, run_id),
            ).run(snapshot.task, run_id=run_id, resume_snapshot=snapshot)

            self.assertEqual(resumed.status, "completed")
            self.assertEqual((root / "value.py").read_text(encoding="utf-8").count("return 2"), 1)
            first_resume_request = second_provider.requests[0]
            self.assertTrue(any(message.get("role") == "tool" for message in first_resume_request))
            final_snapshot = store.load(run_id)
            self.assertEqual(final_snapshot.status, "completed")
            event_types = [
                json.loads(line)["type"]
                for line in trace.read_text(encoding="utf-8").splitlines()
            ]
            self.assertIn("run_resumed", event_types)
            self.assertIn("checkpoint_saved", event_types)


if __name__ == "__main__":
    unittest.main()
