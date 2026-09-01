from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401


class M1EvalFixtureTests(unittest.TestCase):
    def test_three_versioned_bug_fixtures_start_with_failing_tests(self) -> None:
        root = Path(__file__).resolve().parents[1]
        tasks = json.loads((root / "evals/tasks/m1.json").read_text(encoding="utf-8"))
        self.assertEqual(len(tasks), 3)
        for task in tasks:
            fixture = root / task["template"]
            completed = subprocess.run(
                [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                cwd=fixture,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0, task["id"])


if __name__ == "__main__":
    unittest.main()
