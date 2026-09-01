from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from mini_code.events import AgentEvent, EventSink, normalize_terminal_text


class EventRenderingTests(unittest.TestCase):
    def test_normalizes_escaped_bold_markers_across_stream_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = io.StringIO()
            sink = EventSink(Path(temp_dir) / "events.jsonl", stream=output)

            sink.emit(AgentEvent(type="text_delta", run_id="test", data={"content": "这是 \\*"}))
            sink.emit(
                AgentEvent(
                    type="text_delta",
                    run_id="test",
                    data={"content": "\\***MiniCode Agent** \\*\\*项目"},
                )
            )
            sink.emit(AgentEvent(type="run_completed", run_id="test", data={"message": "完成"}))

            rendered = output.getvalue()
            self.assertIn("这是 MiniCode Agent 项目", rendered)
            self.assertNotIn("\\*", rendered)
            self.assertNotIn("**", rendered)
            self.assertEqual(normalize_terminal_text(r"\*\*标题\*\*"), "标题")


if __name__ == "__main__":
    unittest.main()
