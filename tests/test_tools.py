from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from mini_code.tools import build_readonly_registry
from mini_code.workspace import Workspace


class ReadOnlyToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / "src").mkdir()
        (self.root / "src" / "service.py").write_text(
            "def load_user(user_id: int):\n    return user_id\n",
            encoding="utf-8",
        )
        self.registry = build_readonly_registry(Workspace(self.root), max_chars=12_000)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_registry_exposes_only_m0_read_tools(self) -> None:
        self.assertEqual(
            self.registry.names(),
            ["list_files", "search_code", "read_file"],
        )

    def test_list_files(self) -> None:
        result = self.registry.execute("list_files", {"path": ".", "depth": 2})
        self.assertTrue(result["ok"])
        paths = {entry["path"] for entry in result["data"]}
        self.assertIn("src/service.py", paths)

    def test_read_file_returns_line_numbers(self) -> None:
        result = self.registry.execute(
            "read_file",
            {"path": "src/service.py", "start_line": 1, "end_line": 2},
        )
        self.assertTrue(result["ok"])
        self.assertIn("1: def load_user", result["data"]["content"])
        self.assertEqual(result["data"]["total_lines"], 2)

    def test_search_code_returns_evidence_location(self) -> None:
        result = self.registry.execute(
            "search_code",
            {"query": "load_user", "path": ".", "max_results": 10},
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"][0]["path"], "src/service.py")
        self.assertEqual(result["data"][0]["line"], 1)

    def test_tools_reject_path_escape(self) -> None:
        result = self.registry.execute("read_file", {"path": "../secret.txt"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "WorkspaceError")

    def test_tools_reject_env_file_containing_api_key(self) -> None:
        (self.root / ".env").write_text("DEEPSEEK_API_KEY=secret\n", encoding="utf-8")
        result = self.registry.execute("read_file", {"path": ".env"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "WorkspaceError")


if __name__ == "__main__":
    unittest.main()
