from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from mini_code.workspace import Workspace, WorkspaceError


class WorkspaceTests(unittest.TestCase):
    def test_rejects_path_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Workspace(Path(temp_dir))
            with self.assertRaises(WorkspaceError):
                workspace.resolve("../secret.txt")

    def test_repository_map_ignores_runtime_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "src").mkdir()
            (root / "src" / "app.py").write_text("print('ok')", encoding="utf-8")
            (root / ".mini-code").mkdir()
            (root / ".mini-code" / "secret.txt").write_text("hidden", encoding="utf-8")
            repository_map = Workspace(root).repository_map()
            self.assertIn("app.py", repository_map)
            self.assertNotIn("secret.txt", repository_map)


if __name__ == "__main__":
    unittest.main()
