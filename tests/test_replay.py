from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from mini_code.replay import calculate_metrics, load_trace, replay_trace


class ReplayTests(unittest.TestCase):
    def test_replays_timeline_and_calculates_metrics(self) -> None:
        events = [
            _event("run_started", "2026-01-01T00:00:00+00:00"),
            _event("model_started", "2026-01-01T00:00:00.100000+00:00"),
            _event(
                "model_completed",
                "2026-01-01T00:00:00.300000+00:00",
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            ),
            _event("tool_started", "2026-01-01T00:00:00.400000+00:00", tool="apply_patch"),
            _event("tool_completed", "2026-01-01T00:00:00.500000+00:00", ok=True),
            _event("patch_applied", "2026-01-01T00:00:00.600000+00:00", files=["app.py"]),
            _event("verification_failed", "2026-01-01T00:00:00.700000+00:00"),
            _event("checkpoint_saved", "2026-01-01T00:00:00.800000+00:00"),
            _event("run_resumed", "2026-01-01T00:00:01+00:00"),
            _event("verification_passed", "2026-01-01T00:00:01.100000+00:00"),
            _event("run_completed", "2026-01-01T00:00:01.200000+00:00"),
        ]
        metrics = calculate_metrics(events)
        self.assertEqual(metrics.status, "completed")
        self.assertEqual(metrics.total_tokens, 15)
        self.assertEqual(metrics.patches, 1)
        self.assertEqual(metrics.resumes, 1)
        self.assertEqual(metrics.changed_files, ["app.py"])
        self.assertEqual(metrics.duration_ms, 1_200)

        with tempfile.TemporaryDirectory() as temp_dir:
            trace = Path(temp_dir) / "events.jsonl"
            trace.write_text(
                "\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8"
            )
            self.assertEqual(len(load_trace(trace)), len(events))
            output = io.StringIO()
            replay_trace(trace, output)
            self.assertIn("MiniCode Replay: replay-test", output.getvalue())
            self.assertIn("tokens=15", output.getvalue())

    def test_collapses_streamed_text_in_human_replay(self) -> None:
        events = [
            _event("run_started", "2026-01-01T00:00:00+00:00"),
            _event("text_delta", "2026-01-01T00:00:00.100000+00:00", content="hel"),
            _event("text_delta", "2026-01-01T00:00:00.200000+00:00", content="lo"),
            _event("run_completed", "2026-01-01T00:00:00.300000+00:00"),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            trace = Path(temp_dir) / "events.jsonl"
            trace.write_text(
                "\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8"
            )
            output = io.StringIO()
            replay_trace(trace, output)
            rendered = output.getvalue()
            self.assertIn("assistant_text", rendered)
            self.assertIn("5 chars: hello", rendered)
            self.assertNotIn("text_delta", rendered)


def _event(event_type: str, timestamp: str, **data: object) -> dict[str, object]:
    return {
        "type": event_type,
        "run_id": "replay-test",
        "timestamp": timestamp,
        "data": {"message": event_type, **data},
    }


if __name__ == "__main__":
    unittest.main()
