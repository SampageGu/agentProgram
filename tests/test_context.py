from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from mini_code.context import ContextManager
from mini_code.persistence import RunStore
from mini_code.tools import CodingToolState
from mini_code.workspace import Workspace


class ContextManagerTests(unittest.TestCase):
    def test_offloads_large_results_and_compacts_old_messages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app.py").write_text("value = 1\n", encoding="utf-8")
            store = RunStore(Workspace(root))
            manager = ContextManager(store, "context-run", max_chars=1_500, artifact_threshold=300)
            large_result = {
                "ok": True,
                "data": {"stdout": "line\n" * 500, "exit_code": 1, "passed": False},
                "error": None,
                "metadata": {"truncated": False},
            }

            prepared = json.loads(manager.prepare_tool_result("run_tests", large_result))
            artifact_ref = prepared["metadata"]["artifact_ref"]
            self.assertTrue((root / artifact_ref).is_file())
            self.assertLess(len(json.dumps(prepared)), len(json.dumps(large_result)))

            messages = [{"role": "system", "content": "rules"}, {"role": "user", "content": "task"}]
            for index in range(5):
                messages.extend(
                    [
                        {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [{
                                "id": f"call-{index}",
                                "type": "function",
                                "function": {"name": "read_file", "arguments": "x" * 1_200},
                            }],
                        },
                        {"role": "tool", "tool_call_id": f"call-{index}", "content": "y" * 1_500},
                    ]
                )
            state = CodingToolState(
                plan=[{"step": "fix", "status": "in_progress"}],
                changed_files={"app.py"},
                revision=1,
            )
            result = manager.compact(messages, state)

            self.assertIsNotNone(result)
            self.assertLess(result["after_chars"], result["before_chars"])
            self.assertTrue(result["artifact_refs"])
            self.assertIn("M2 durable state", messages[0]["content"])
            self.assertIn("app.py", messages[0]["content"])

    def test_large_subagent_report_keeps_parent_evidence_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app.py").write_text("value = 1\n", encoding="utf-8")
            manager = ContextManager(
                RunStore(Workspace(root)),
                "delegation-context",
                artifact_threshold=300,
            )
            result = {
                "ok": True,
                "data": {
                    "parent_run_id": "parent",
                    "child_run_id": "child",
                    "status": "completed",
                    "steps": 3,
                    "total_tokens": 1200,
                    "verification_required": "read evidence",
                    "report": {
                        "summary": "located value " + "x" * 500,
                        "evidence": [
                            {"path": "app.py", "lines": "1", "reason": "definition"}
                        ],
                        "suspected_root_cause": "wrong constant",
                        "recommended_next_steps": ["verify app.py"],
                        "risks": [],
                    },
                },
                "error": None,
                "metadata": {"truncated": False},
            }
            prepared = json.loads(manager.prepare_tool_result("delegate_explore", result))
            self.assertEqual(prepared["data"]["child_run_id"], "child")
            self.assertEqual(prepared["data"]["report"]["evidence"][0]["path"], "app.py")
            self.assertIn("artifact_ref", prepared["metadata"])


if __name__ == "__main__":
    unittest.main()
