from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import threading
import time
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
from mini_code.web import MiniCodeWebServer, WebError, WebRunManager, create_web_server
from mini_code.workspace import Workspace
from mini_code.config import Settings


class _UnusedProvider:
    model = "unused"

    def stream_chat(self, messages, tools):
        raise AssertionError("cancelled run must not call the provider")
        yield


class _ChatProvider:
    model = "fake-chat"
    last_usage = {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}

    def stream_chat(self, messages, tools):
        if tools:
            raise AssertionError("chat route must not expose tools")
        yield {"choices": [{"delta": {"content": "你好！有什么想一起完成的吗？"}}]}


class _ReadProvider:
    model = "fake-read"
    last_usage = {"total_tokens": 8}

    def __init__(self) -> None:
        self.calls = 0

    def stream_chat(self, messages, tools):
        self.calls += 1
        names = {item["function"]["name"] for item in tools}
        if names != {"list_files", "search_code", "read_file"}:
            raise AssertionError(f"unexpected read route tools: {names}")
        if self.calls == 1:
            yield {"choices": [{"delta": {"tool_calls": [{
                "index": 0,
                "id": "read-list",
                "function": {"name": "list_files", "arguments": "{}"},
            }]}}]}
            return
        yield {"choices": [{"delta": {"content": "入口信息已根据只读证据确认。"}}]}


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

    def start_conversation(self, message, mode="auto"):
        return {
            "conversation_id": "chat-test", "run_id": "web-test",
            "intent": "chat", "requested_mode": mode, "route_reason": "fake",
        }

    def continue_conversation(self, conversation_id, message, mode="auto"):
        if conversation_id != "chat-test":
            raise WebError("Conversation not found")
        return {
            "conversation_id": conversation_id, "run_id": "web-test",
            "intent": "chat", "requested_mode": mode, "route_reason": "fake",
        }


class M4WebTests(unittest.TestCase):
    def test_conversation_schema_migrates_existing_m41_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime = root / ".mini-code"
            runtime.mkdir()
            database = runtime / "state.db"
            connection = sqlite3.connect(database)
            try:
                connection.executescript(
                    """
                    CREATE TABLE conversations(
                        conversation_id TEXT PRIMARY KEY, title TEXT NOT NULL,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    CREATE TABLE conversation_runs(
                        conversation_id TEXT NOT NULL, run_id TEXT NOT NULL UNIQUE,
                        sequence INTEGER NOT NULL, user_message TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        PRIMARY KEY(conversation_id, sequence)
                    );
                    INSERT INTO conversations VALUES(
                        'legacy', '旧会话', '2026-09-01T00:00:00+00:00',
                        '2026-09-01T00:00:00+00:00'
                    );
                    INSERT INTO conversation_runs VALUES(
                        'legacy', 'legacy-run', 1, '旧版编码任务',
                        '2026-09-01T00:00:00+00:00'
                    );
                    """
                )
                connection.commit()
            finally:
                connection.close()
            store = ConversationStore(Workspace(root))
            connection = sqlite3.connect(store.database)
            try:
                columns = {
                    row[1] for row in connection.execute(
                        "PRAGMA table_info(conversation_runs)"
                    ).fetchall()
                }
            finally:
                connection.close()
            self.assertIn("requested_mode", columns)
            self.assertIn("intent", columns)
            migrated = store.get("legacy")
            self.assertEqual(migrated.runs[0].requested_mode, "code")
            self.assertEqual(migrated.runs[0].intent, "code")

    def test_conversation_store_persists_ordered_run_links(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Workspace(Path(temp_dir))
            conversation = ConversationStore(workspace).create("修复登录接口的边界问题")
            store = ConversationStore(workspace)
            store.add_run(conversation.conversation_id, "run-one", "先定位问题")
            store.add_run(
                conversation.conversation_id,
                "run-two",
                "继续补测试",
                requested_mode="code",
                intent="code",
            )

            restored = store.get(conversation.conversation_id)
            self.assertEqual(restored.title, "修复登录接口的边界问题")
            self.assertEqual([item.sequence for item in restored.runs], [1, 2])
            self.assertEqual(restored.runs[-1].user_message, "继续补测试")
            self.assertEqual(restored.runs[-1].requested_mode, "code")
            self.assertEqual(restored.runs[-1].intent, "code")
            self.assertEqual(store.list()[0].conversation_id, conversation.conversation_id)

    def test_auto_greeting_completes_without_repository_or_tool_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Workspace(Path(temp_dir))
            manager = WebRunManager(
                workspace,
                Settings(api_key="test"),
                provider_factory=lambda settings: _ChatProvider(),
            )
            created = manager.start_conversation("你好")
            self.assertEqual(created["intent"], "chat")
            for _ in range(100):
                if not manager.is_active(created["run_id"]):
                    break
                time.sleep(0.01)
            events = manager.get_events(created["run_id"])
            types = [event["type"] for event in events]
            self.assertIn("intent_routed", types)
            self.assertIn("run_completed", types)
            self.assertNotIn("repository_mapped", types)
            self.assertNotIn("tool_started", types)
            self.assertNotIn("tool_completed", types)
            self.assertEqual(manager.get_run(created["run_id"])["status"], "completed")

    def test_auto_repository_question_uses_only_read_tools(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Workspace(Path(temp_dir))
            (workspace.root / "app.py").write_text("print('hello')\n", encoding="utf-8")
            provider = _ReadProvider()
            manager = WebRunManager(
                workspace,
                Settings(api_key="test"),
                provider_factory=lambda settings: provider,
            )
            created = manager.start_conversation("项目入口在哪里？")
            self.assertEqual(created["intent"], "read")
            for _ in range(100):
                if not manager.is_active(created["run_id"]):
                    break
                time.sleep(0.01)
            events = manager.get_events(created["run_id"])
            tools = [
                event["data"].get("tool")
                for event in events
                if event["type"] == "tool_started"
            ]
            self.assertEqual(tools, ["list_files"])
            self.assertFalse({"apply_patch", "run_tests", "delegate_explore"} & set(tools))
            self.assertEqual(manager.get_run(created["run_id"])["status"], "completed")

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
            self.assertIn('data-mode="auto"', page)
            with urllib.request.urlopen(base + "/app.js", timeout=3) as response:
                script = response.read().decode("utf-8")
            self.assertIn("latestModelText", script)

            conversations, _ = _get_json(base + "/api/conversations")
            self.assertEqual(conversations["conversations"][0]["conversation_id"], "chat-test")

            request = urllib.request.Request(
                base + "/api/conversations",
                data=json.dumps({"message": "fix", "mode": "chat"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=3) as response:
                created_chat = json.loads(response.read())
                self.assertEqual(response.status, 202)
            self.assertEqual(created_chat["conversation_id"], "chat-test")
            self.assertEqual(created_chat["requested_mode"], "chat")

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
