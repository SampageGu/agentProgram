from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _bootstrap  # noqa: F401

from mini_code.config import Settings


class SettingsTests(unittest.TestCase):
    def test_loads_deepseek_flash_defaults_from_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / ".env"
            env_file.write_text("DEEPSEEK_API_KEY=test-key\n", encoding="utf-8")
            clean_env = {
                key: value
                for key, value in os.environ.items()
                if not key.startswith("DEEPSEEK_") and not key.startswith("MINICODE_")
            }
            with patch.dict(os.environ, clean_env, clear=True):
                settings = Settings.from_env(env_file)
                self.assertEqual(settings.api_key, "test-key")
                self.assertEqual(settings.model, "deepseek-v4-flash")
                self.assertEqual(settings.base_url, "https://api.deepseek.com")
                self.assertEqual(settings.max_context_chars, 50_000)
                self.assertEqual(settings.artifact_threshold, 6_000)


if __name__ == "__main__":
    unittest.main()
