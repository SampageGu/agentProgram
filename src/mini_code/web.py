"""Local M4 REST/SSE control plane for the existing CodingAgent."""

from __future__ import annotations

import io
import json
import mimetypes
import re
import threading
import time
import uuid
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from mini_code.agent import CodingAgent
from mini_code.config import Settings
from mini_code.conversations import ConversationStore
from mini_code.context import ContextManager
from mini_code.events import EventSink
from mini_code.persistence import PersistenceError, RunSnapshot, RunStore
from mini_code.provider import DeepSeekProvider, ModelProvider
from mini_code.replay import ReplayError, calculate_metrics, load_trace
from mini_code.tools import CodingToolState, ExploreDelegationConfig, build_coding_registry
from mini_code.workspace import Workspace


class WebError(RuntimeError):
    pass


class WebRunManager:
    """Own background runs while persistence remains the source of truth."""

    def __init__(
        self,
        workspace: Workspace,
        settings: Settings,
        provider_factory: Callable[[Settings], ModelProvider] = DeepSeekProvider,
    ) -> None:
        self.workspace = workspace
        self.settings = settings
        self.provider_factory = provider_factory
        self.store = RunStore(workspace)
        self.conversations = ConversationStore(workspace)
        self._jobs: dict[str, tuple[threading.Thread, threading.Event]] = {}
        self._job_errors: dict[str, str] = {}
        self._lock = threading.Lock()

    def start_run(
        self,
        task: str,
        *,
        conversation_id: str | None = None,
        user_message: str | None = None,
    ) -> str:
        task = task.strip()
        if not task:
            raise WebError("task must not be empty")
        if len(task) > 8_000:
            raise WebError("task is too long (maximum 8000 characters)")
        run_id = uuid.uuid4().hex[:12]
        cancel_event = threading.Event()
        thread = threading.Thread(
            target=self._execute,
            args=(run_id, task, cancel_event),
            name=f"mini-code-{run_id}",
            daemon=True,
        )
        with self._lock:
            if any(existing.is_alive() for existing, _ in self._jobs.values()):
                raise WebError("another run is active in this workspace")
            if conversation_id is not None:
                self.conversations.add_run(conversation_id, run_id, user_message or task)
            self._jobs[run_id] = (thread, cancel_event)
        thread.start()
        return run_id

    def start_conversation(self, message: str) -> dict[str, str]:
        conversation = self.conversations.create(message)
        try:
            run_id = self.start_run(
                message,
                conversation_id=conversation.conversation_id,
                user_message=message,
            )
        except Exception:
            self.conversations.delete_if_empty(conversation.conversation_id)
            raise
        return {"conversation_id": conversation.conversation_id, "run_id": run_id}

    def continue_conversation(self, conversation_id: str, message: str) -> dict[str, str]:
        conversation = self.conversations.get(conversation_id)
        prior = conversation.runs[-6:]
        history = "\n".join(
            f"{item.sequence}. {item.user_message[:600]}" for item in prior
        )
        effective_task = (
            "This is a follow-up in an existing coding conversation. The workspace contains "
            "the authoritative result of earlier turns. Inspect it before making claims.\n\n"
            f"Earlier user requests:\n{history}\n\nCurrent user request:\n{message.strip()}"
        )
        run_id = self.start_run(
            effective_task,
            conversation_id=conversation_id,
            user_message=message,
        )
        return {"conversation_id": conversation_id, "run_id": run_id}

    def list_conversations(self) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        for conversation in self.conversations.list():
            payload = self.conversations.to_dict(conversation)
            last_run = conversation.runs[-1].run_id if conversation.runs else None
            payload["last_run_id"] = last_run
            payload["active"] = bool(last_run and self.is_active(last_run))
            if last_run:
                try:
                    payload["status"] = self.store.load(last_run).status
                except PersistenceError:
                    payload["status"] = "starting" if self.is_active(last_run) else "unknown"
            else:
                payload["status"] = "empty"
            payloads.append(payload)
        return payloads

    def get_conversation(self, conversation_id: str) -> dict[str, Any]:
        conversation = self.conversations.get(conversation_id)
        payload = self.conversations.to_dict(conversation)
        turns: list[dict[str, Any]] = []
        for link in conversation.runs:
            try:
                run = self.get_run(link.run_id)
            except PersistenceError:
                run = {
                    "run_id": link.run_id,
                    "status": "starting" if self.is_active(link.run_id) else "unknown",
                    "active": self.is_active(link.run_id),
                }
            turns.append({**asdict(link), "run": run})
        payload["turns"] = turns
        payload.pop("runs", None)
        return payload

    def cancel_run(self, run_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(run_id)
        if job is None or not job[0].is_alive():
            return False
        job[1].set()
        return True

    def is_active(self, run_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(run_id)
        return job is not None and job[0].is_alive()

    def list_runs(self) -> list[dict[str, Any]]:
        return [self._snapshot_payload(snapshot) for snapshot in self.store.list_runs()]

    def get_run(self, run_id: str) -> dict[str, Any]:
        snapshot = self.store.load(run_id)
        payload = self._snapshot_payload(snapshot)
        trace = self.store.run_dir(run_id) / "events.jsonl"
        if trace.is_file():
            try:
                payload["metrics"] = asdict(calculate_metrics(load_trace(trace)))
            except ReplayError:
                payload["metrics"] = None
        else:
            payload["metrics"] = None
        payload["error"] = self._job_errors.get(run_id)
        return payload

    def get_events(self, run_id: str, after: int = 0) -> list[dict[str, Any]]:
        if after < 0:
            raise WebError("after must be non-negative")
        trace = self.store.run_dir(run_id) / "events.jsonl"
        if not trace.exists():
            if self.is_active(run_id):
                return []
            raise PersistenceError(f"Run not found: {run_id}")
        events = load_trace(trace)
        return [
            {"sequence": sequence, **event}
            for sequence, event in enumerate(events, 1)
            if sequence > after
        ]

    def _execute(self, run_id: str, task: str, cancel_event: threading.Event) -> None:
        try:
            trace_file = self.store.run_dir(run_id) / "events.jsonl"
            tools = build_coding_registry(
                self.workspace,
                max_chars=self.settings.max_tool_chars,
                command_timeout=self.settings.request_timeout,
                delegation=ExploreDelegationConfig(
                    parent_run_id=run_id,
                    provider_factory=lambda: self.provider_factory(self.settings),
                ),
            )
            CodingAgent(
                provider=self.provider_factory(self.settings),
                workspace=self.workspace,
                tools=tools,
                sink=EventSink(trace_file, stream=io.StringIO()),
                max_steps=max(self.settings.max_steps, 16),
                run_store=self.store,
                context_manager=ContextManager(
                    self.store,
                    run_id,
                    self.settings.max_context_chars,
                    self.settings.artifact_threshold,
                ),
                should_stop=cancel_event.is_set,
            ).run(task, run_id=run_id)
        except Exception as exc:  # keep the HTTP server alive and expose a compact error
            with self._lock:
                self._job_errors[run_id] = f"{type(exc).__name__}: {exc}"

    def _snapshot_payload(self, snapshot: RunSnapshot) -> dict[str, Any]:
        state = CodingToolState.from_dict(snapshot.tool_state)
        return {
            "run_id": snapshot.run_id,
            "task": snapshot.task,
            "status": snapshot.status,
            "step": snapshot.step,
            "max_steps": snapshot.max_steps,
            "updated_at": snapshot.updated_at,
            "checkpoint": snapshot.checkpoint_sequence,
            "changed_files": sorted(state.changed_files),
            "plan": state.plan,
            "checks": state.checks,
            "completion_report": state.completion_report,
            "delegations": state.delegations,
            "verified_evidence_files": sorted(state.verified_evidence_files),
            "diff": state.last_diff[:100_000],
            "active": self.is_active(snapshot.run_id),
        }


class MiniCodeWebServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        manager: WebRunManager,
        static_dir: Path | None = None,
    ) -> None:
        self.manager = manager
        self.static_dir = static_dir or Path(__file__).with_name("web_static")
        super().__init__(address, MiniCodeRequestHandler)


class MiniCodeRequestHandler(BaseHTTPRequestHandler):
    server: MiniCodeWebServer
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self._json({"ok": True, "service": "mini-code-m4", "version": 2})
            return
        if parsed.path == "/api/runs":
            self._api_call(lambda: self._json({"runs": self.server.manager.list_runs()}))
            return
        if parsed.path == "/api/conversations":
            self._api_call(
                lambda: self._json({"conversations": self.server.manager.list_conversations()})
            )
            return
        conversation_match = re.fullmatch(
            r"/api/conversations/([A-Za-z0-9_-]{1,80})", parsed.path
        )
        if conversation_match:
            self._api_call(
                lambda: self._json(
                    self.server.manager.get_conversation(conversation_match.group(1))
                )
            )
            return
        match = re.fullmatch(r"/api/runs/([A-Za-z0-9_-]{1,80})(?:/(events|stream))?", parsed.path)
        if match:
            run_id, action = match.groups()
            if action == "stream":
                self._stream(run_id, parsed.query)
                return
            if action == "events":
                self._api_call(lambda: self._events(run_id, parsed.query))
                return
            self._api_call(lambda: self._json(self.server.manager.get_run(run_id)))
            return
        self._static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/runs":
            self._api_call(self._create_run)
            return
        if parsed.path == "/api/conversations":
            self._api_call(self._create_conversation)
            return
        conversation_match = re.fullmatch(
            r"/api/conversations/([A-Za-z0-9_-]{1,80})/messages", parsed.path
        )
        if conversation_match:
            self._api_call(
                lambda: self._continue_conversation(conversation_match.group(1))
            )
            return
        match = re.fullmatch(r"/api/runs/([A-Za-z0-9_-]{1,80})/cancel", parsed.path)
        if match:
            self._api_call(lambda: self._cancel_run(match.group(1)))
            return
        self._json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def _create_run(self) -> None:
        body = self._read_json()
        task = body.get("task")
        if not isinstance(task, str):
            raise WebError("task must be a string")
        run_id = self.server.manager.start_run(task)
        self._json({"run_id": run_id, "status": "starting"}, HTTPStatus.ACCEPTED)

    def _create_conversation(self) -> None:
        body = self._read_json()
        message = body.get("message")
        if not isinstance(message, str):
            raise WebError("message must be a string")
        result = self.server.manager.start_conversation(message)
        self._json({**result, "status": "starting"}, HTTPStatus.ACCEPTED)

    def _continue_conversation(self, conversation_id: str) -> None:
        body = self._read_json()
        message = body.get("message")
        if not isinstance(message, str):
            raise WebError("message must be a string")
        result = self.server.manager.continue_conversation(conversation_id, message)
        self._json({**result, "status": "starting"}, HTTPStatus.ACCEPTED)

    def _cancel_run(self, run_id: str) -> None:
        accepted = self.server.manager.cancel_run(run_id)
        self._json(
            {"run_id": run_id, "cancel_requested": accepted},
            HTTPStatus.ACCEPTED if accepted else HTTPStatus.CONFLICT,
        )

    def _events(self, run_id: str, query: str) -> None:
        after = _after_value(query, self.headers.get("Last-Event-ID"))
        self._json({"events": self.server.manager.get_events(run_id, after)})

    def _stream(self, run_id: str, query: str) -> None:
        after = _after_value(query, self.headers.get("Last-Event-ID"))
        events = self.server.manager.get_events(run_id, after)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self._security_headers()
        self.end_headers()
        last_heartbeat = time.monotonic()
        try:
            while True:
                for event in events:
                    after = int(event["sequence"])
                    payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                    block = f"id: {after}\nevent: {event['type']}\ndata: {payload}\n\n"
                    self.wfile.write(block.encode("utf-8"))
                    self.wfile.flush()
                if not self.server.manager.is_active(run_id):
                    return
                if time.monotonic() - last_heartbeat >= 10:
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
                    last_heartbeat = time.monotonic()
                time.sleep(0.2)
                events = self.server.manager.get_events(run_id, after)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _static(self, request_path: str) -> None:
        names = {
            "/": "index.html",
            "/app.js": "app.js",
            "/styles.css": "styles.css",
            "/diff.css": "diff.css",
        }
        name = names.get(request_path)
        if name is None:
            self._json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
            return
        path = self.server.static_dir / name
        if not path.is_file():
            self._json({"error": "asset_not_found"}, HTTPStatus.NOT_FOUND)
            return
        content = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type == "application/javascript":
            content_type += "; charset=utf-8"
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self._security_headers()
        self.end_headers()
        self.wfile.write(content)

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise WebError("invalid Content-Length") from exc
        if length <= 0 or length > 16_384:
            raise WebError("request body must be between 1 and 16384 bytes")
        try:
            value = json.loads(self.rfile.read(length))
        except json.JSONDecodeError as exc:
            raise WebError("request body must be valid JSON") from exc
        if not isinstance(value, dict):
            raise WebError("request body must be a JSON object")
        return value

    def _api_call(self, operation: Callable[[], None]) -> None:
        try:
            operation()
        except (WebError, PersistenceError, ReplayError, ValueError) as exc:
            status = HTTPStatus.NOT_FOUND if "not found" in str(exc).lower() else HTTPStatus.BAD_REQUEST
            self._json({"error": str(exc)}, status)

    def _json(self, value: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        content = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self._security_headers()
        self.end_headers()
        self.wfile.write(content)

    def _security_headers(self) -> None:
        self.send_header("Connection", "close")
        self.close_connection = True
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'",
        )

    def log_message(self, format: str, *args: Any) -> None:
        return


def create_web_server(
    workspace: Workspace,
    settings: Settings,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> MiniCodeWebServer:
    if host not in {"127.0.0.1", "localhost"}:
        raise WebError("M4 only binds to localhost; remote exposure is not supported")
    if not 0 <= port <= 65_535:
        raise WebError("port must be between 0 and 65535")
    return MiniCodeWebServer((host, port), WebRunManager(workspace, settings))


def serve_web(workspace: Workspace, settings: Settings, host: str, port: int) -> None:
    server = create_web_server(workspace, settings, host, port)
    actual_host, actual_port = server.server_address[:2]
    print(f"MiniCode M4 Web: http://{actual_host}:{actual_port}")
    print(f"Workspace: {workspace.root}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("\nM4 Web stopped.")
    finally:
        server.server_close()


def _after_value(query: str, last_event_id: str | None) -> int:
    raw = parse_qs(query).get("after", [last_event_id or "0"])[0]
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise WebError("after must be an integer") from exc
    if value < 0:
        raise WebError("after must be non-negative")
    return value
