from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

import _bootstrap  # noqa: F401

from mini_code.agent import CodingAgent
from mini_code.conversations import ConversationStore
from mini_code.events import EventSink
from mini_code.persistence import RunStore
from mini_code.replay import calculate_metrics, load_trace
from mini_code.tools import build_coding_registry
from mini_code.web import MiniCodeWebServer, WebError, create_web_server
from mini_code.workspace import Workspace
from mini_code.config import Settings


class _UnusedProvider:
    model = "unused"

    def stream_chat(self, messages, tools):
        raise AssertionError("cancelled run must not call the provider")
        yield


class _FakeManager:
    def __init__(self) -> None:
        self.events = [{
            "sequence": 1,
            "type": "run_started",
            "run_id": "web-test",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "data": {"message": "started"},
        }]

    def list_runs(self):
        return [{"run_id": "web-test", "task": "fix", "status": "completed"}]

    def get_run(self, run_id):
        if run_id != "web-test":
            raise WebError("Run not found")
        return {"run_id": run_id, "task": "fix", "status": "completed"}

    def get_events(self, run_id, after=0):
        return [event for event in self.events if event["sequence"] > after]

    def start_run(self, task):
        return "web-test"

    def cancel_run(self, run_id):
        return run_id == "web-test"

    def is_active(self, run_id):
        return False

    def list_conversations(self):
        return [{
            "conversation_id": "chat-test", "title": "fix", "message_count": 1,
            "last_message": "fix", "last_run_id": "web-test", "status": "completed",
            "active": False,
        }]

    def get_conversation(self, conversation_id):
        if conversation_id != "chat-test":
            raise WebError("Conversation not found")
        return {
            "conversation_id": conversation_id,
            "title": "fix",
            "message_count": 1,
            "turns": [{
                "run_id": "web-test", "sequence": 1, "user_message": "fix",
                "run": self.get_run("web-test"),
            }],
        }

    def start_conversation(self, message):
        return {"conversation_id": "chat-test", "run_id": "web-test"}

    def continue_conversation(self, conversation_id, message):
        if conversation_id != "chat-test":
            raise WebError("Conversation not found")
        return {"conversation_id": conversation_id, "run_id": "web-test"}


class M4WebTests(unittest.TestCase):
    def test_conversation_store_persists_ordered_run_links(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Workspace(Path(temp_dir))
            conversation = ConversationStore(workspace).create("修复登录接口的边界问题")
            store = ConversationStore(workspace)
            store.add_run(conversation.conversation_id, "run-one", "先定位问题")
            store.add_run(conversation.conversation_id, "run-two", "继续补测试")

            restored = store.get(conversation.conversation_id)
            self.assertEqual(restored.title, "修复登录接口的边界问题")
            self.assertEqual([item.sequence for item in restored.runs], [1, 2])
            self.assertEqual(restored.runs[-1].user_message, "继续补测试")
            self.assertEqual(store.list()[0].conversation_id, conversation.conversation_id)

    def test_cooperative_cancel_persists_a_resumable_terminal_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Workspace(Path(temp_dir))
            store = RunStore(workspace)
            trace = store.run_dir("cancel-test") / "events.jsonl"
            result = CodingAgent(
                provider=_UnusedProvider(),
                workspace=workspace,
                tools=build_coding_registry(workspace),
                sink=EventSink(trace, stream=io.StringIO()),
                max_steps=2,
                run_store=store,
                should_stop=lambda: True,
            ).run("cancel me", run_id="cancel-test")
            self.assertEqual(result.status, "cancelled")
            self.assertEqual(store.load("cancel-test").status, "cancelled")
            self.assertEqual(calculate_metrics(load_trace(trace)).status, "cancelled")

    def test_localhost_boundary_health_assets_rest_and_sse(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Workspace(Path(temp_dir))
            settings = Settings(api_key="test")
            with self.assertRaises(WebError):
                create_web_server(workspace, settings, host="0.0.0.0", port=0)

        server = MiniCodeWebServer(("127.0.0.1", 0), _FakeManager())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            health, headers = _get_json(base + "/api/health")
            self.assertTrue(health["ok"])
            self.assertEqual(headers["X-Frame-Options"], "DENY")
            with urllib.request.urlopen(base + "/", timeout=3) as response:
                page = response.read().decode("utf-8")
                self.assertEqual(response.headers.get_content_charset(), "utf-8")
            self.assertIn("今天想一起改点什么", page)

            conversations, _ = _get_json(base + "/api/conversations")
            self.assertEqual(conversations["conversations"][0]["conversation_id"], "chat-test")

            request = urllib.request.Request(
                base + "/api/conversations",
                data=json.dumps({"message": "fix"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=3) as response:
                created_chat = json.loads(response.read())
                self.assertEqual(response.status, 202)
            self.assertEqual(created_chat["conversation_id"], "chat-test")

            request = urllib.request.Request(
                base + "/api/conversations/chat-test/messages",
                data=json.dumps({"message": "add tests"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=3) as response:
                continued = json.loads(response.read())
            self.assertEqual(continued["run_id"], "web-test")

            request = urllib.request.Request(
                base + "/api/runs",
                data=json.dumps({"task": "fix"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=3) as response:
                created = json.loads(response.read())
                self.assertEqual(response.status, 202)
            self.assertEqual(created["run_id"], "web-test")

            with urllib.request.urlopen(base + "/api/runs/web-test/stream", timeout=3) as response:
                stream = response.read().decode("utf-8")
            self.assertIn("id: 1", stream)
            self.assertIn("event: run_started", stream)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


def _get_json(url: str):
    with urllib.request.urlopen(url, timeout=3) as response:
        return json.loads(response.read()), response.headers


if __name__ == "__main__":
    unittest.main()
