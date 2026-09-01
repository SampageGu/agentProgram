from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from mini_code.persistence import RunSnapshot, RunStore, WorkspaceConflictError
from mini_code.workspace import Workspace


class RunStoreTests(unittest.TestCase):
    def test_saves_sqlite_json_checkpoint_and_detects_external_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "app.py"
            source.write_text("value = 1\n", encoding="utf-8")
            workspace = Workspace(root)
            store = RunStore(workspace)
            checkpoint = store.save_checkpoint(
                RunSnapshot(
                    run_id="run1",
                    task="change value",
                    status="running",
                    step=2,
                    max_steps=10,
                    messages=[{"role": "user", "content": "change value"}],
                    tool_state={"revision": 1},
                    used_tool=True,
                    successful_tools=["apply_patch"],
                    workspace_hash="",
                    trace_file=str(root / ".mini-code/runs/run1/events.jsonl"),
                )
            )

            self.assertTrue(store.database.is_file())
            self.assertTrue(checkpoint.is_file())
            loaded = store.load("run1")
            self.assertEqual(loaded.step, 2)
            self.assertEqual(loaded.checkpoint_sequence, 1)
            store.write_artifact("run1", "tool", "large output")
            store.assert_workspace_unchanged(loaded)

            source.write_text("value = 2\n", encoding="utf-8")
            with self.assertRaises(WorkspaceConflictError):
                store.assert_workspace_unchanged(loaded)


if __name__ == "__main__":
    unittest.main()
