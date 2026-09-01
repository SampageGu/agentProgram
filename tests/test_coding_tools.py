from __future__ import annotations

import tempfile
import subprocess
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from mini_code.tools import build_coding_registry
from mini_code.workspace import Workspace


class CodingToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / "calc.py").write_text(
            "def divide(a, b):\n    return a / b\n",
            encoding="utf-8",
        )
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_calc.py").write_text(
            "import unittest\n\n"
            "from calc import divide\n\n\n"
            "class DivideTests(unittest.TestCase):\n"
            "    def test_zero(self):\n"
            "        with self.assertRaises(ValueError):\n"
            "            divide(1, 0)\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"], cwd=self.root, check=True
        )
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.root, check=True)
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "baseline"], cwd=self.root, check=True)
        self.registry = build_coding_registry(Workspace(self.root))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _plan(self, completed: bool = False) -> None:
        status = "completed" if completed else "in_progress"
        result = self.registry.execute(
            "update_plan", {"steps": [{"step": "Fix and verify", "status": status}]}
        )
        self.assertTrue(result["ok"])

    def _fix_patch(self) -> str:
        return (
            "--- a/calc.py\n"
            "+++ b/calc.py\n"
            "@@ -1,2 +1,4 @@\n"
            " def divide(a, b):\n"
            "+    if b == 0:\n"
            "+        raise ValueError('division by zero')\n"
            "     return a / b\n"
        )

    def test_registry_exposes_m1_tools(self) -> None:
        self.assertEqual(
            self.registry.names(),
            [
                "list_files", "search_code", "read_file", "update_plan",
                "apply_patch", "run_command", "run_tests", "git_diff", "finish_task",
            ],
        )

    def test_patch_requires_plan_and_rejects_protected_path(self) -> None:
        no_plan = self.registry.execute("apply_patch", {"patch": self._fix_patch()})
        self.assertEqual(no_plan["error"]["code"], "plan_required")
        self._plan()
        protected = self.registry.execute(
            "apply_patch",
            {
                "patch": (
                    "--- /dev/null\n+++ b/.env\n@@ -0,0 +1 @@\n+SECRET=value\n"
                )
            },
        )
        self.assertFalse(protected["ok"])
        self.assertFalse((self.root / ".env").exists())

    def test_patch_tests_and_completion_protocol(self) -> None:
        self._plan()
        patched = self.registry.execute("apply_patch", {"patch": self._fix_patch()})
        self.assertTrue(patched["ok"], patched)

        premature = self.registry.execute("finish_task", {"summary": "done"})
        self.assertEqual(premature["error"]["code"], "plan_incomplete")

        tested = self.registry.execute(
            "run_tests", {"target": "tests", "framework": "unittest"}
        )
        self.assertTrue(tested["data"]["passed"], tested)
        self._plan(completed=True)
        diff_result = self.registry.execute("git_diff", {})
        self.assertTrue(diff_result["ok"], diff_result)
        self.assertIn("raise ValueError", diff_result["data"]["session_diff"])
        finished = self.registry.execute("finish_task", {"summary": "Fixed zero division"})
        self.assertTrue(finished["ok"], finished)
        self.assertEqual(finished["data"]["changed_files"], ["calc.py"])
        self.assertTrue(finished["data"]["checks"][-1]["passed"])

    def test_latest_patch_invalidates_previous_test_evidence(self) -> None:
        self._plan()
        self.assertTrue(self.registry.execute("apply_patch", {"patch": self._fix_patch()})["ok"])
        self.assertTrue(self.registry.execute("run_tests", {"framework": "unittest"})["data"]["passed"])
        second_patch = (
            "--- a/calc.py\n+++ b/calc.py\n@@ -2,2 +2,3 @@\n"
            "     if b == 0:\n"
            "+        # Guard invalid input.\n"
            "         raise ValueError('division by zero')\n"
        )
        self.assertTrue(self.registry.execute("apply_patch", {"patch": second_patch})["ok"])
        self._plan(completed=True)
        result = self.registry.execute("finish_task", {"summary": "done"})
        self.assertEqual(result["error"]["code"], "verification_required")

    def test_command_policy_rejects_shell_and_arbitrary_python(self) -> None:
        python_c = self.registry.execute("run_command", {"argv": ["python", "-c", "print(1)"]})
        shell = self.registry.execute("run_command", {"argv": ["cmd", "/c", "dir"]})
        self.assertEqual(python_c["error"]["code"], "command_rejected")
        self.assertEqual(shell["error"]["code"], "command_rejected")


if __name__ == "__main__":
    unittest.main()
