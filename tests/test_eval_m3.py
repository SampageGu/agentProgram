from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from mini_code.eval_m3 import _run_tests, load_m3_tasks, materialize_case


class M3EvaluationFixtureTests(unittest.TestCase):
    def test_twenty_versioned_cases_have_valid_public_and_hidden_baselines(self) -> None:
        project = Path(__file__).resolve().parents[1]
        tasks = load_m3_tasks(project / "evals/tasks/m3.json")
        self.assertEqual(len(tasks), 20)
        self.assertEqual(len({task["category"] for task in tasks}), 8)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for task in tasks:
                case = root / task["id"]
                workspace = case / "workspace"
                hidden = case / "hidden"
                materialize_case(task, workspace, hidden)
                self.assertEqual(_run_tests(workspace, workspace / "tests"), 0, task["id"])
                self.assertNotEqual(_run_tests(workspace, hidden), 0, task["id"])


if __name__ == "__main__":
    unittest.main()
