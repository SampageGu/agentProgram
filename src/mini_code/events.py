"""Typed runtime events and JSONL trace persistence."""

from __future__ import annotations

import json
import sys
from io import StringIO
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO


@dataclass(frozen=True)
class AgentEvent:
    type: str
    run_id: str
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat(timespec="milliseconds")
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EventSink:
    """Persist every event and render a compact streaming CLI view."""

    def __init__(self, trace_file: Path, stream: TextIO | None = None) -> None:
        self.trace_file = trace_file
        self.trace_file.parent.mkdir(parents=True, exist_ok=True)
        self.stream = stream or sys.stdout
        self.events: list[AgentEvent] = []
        self._text_renderer = TerminalTextRenderer(self.stream)

    def emit(self, event: AgentEvent) -> None:
        self.events.append(event)
        with self.trace_file.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
        self._render(event)

    def _render(self, event: AgentEvent) -> None:
        if event.type == "text_delta":
            self._text_renderer.feed(event.data.get("content", ""))
            return
        self._text_renderer.flush()
        labels = {
            "run_started": "启动",
            "repository_mapped": "仓库",
            "model_started": "模型",
            "model_completed": "模型",
            "tool_started": "工具",
            "tool_completed": "结果",
            "plan_updated": "计划",
            "patch_applied": "修改",
            "verification_passed": "验证",
            "verification_failed": "验证",
            "completion_verified": "协议",
            "context_compacted": "压缩",
            "checkpoint_saved": "存档",
            "run_resumed": "恢复",
            "run_interrupted": "中断",
            "run_cancelled": "取消",
            "run_completed": "完成",
            "run_failed": "失败",
            "budget_exhausted": "预算",
            "subagent_started": "子代理",
            "subagent_completed": "子代理",
            "subagent_failed": "子代理",
            "subagent_delegated": "委派",
            "subagent_result": "子代理",
        }
        label = labels.get(event.type, event.type)
        message = event.data.get("message", "")
        print(f"\n[{label}] {message}", flush=True, file=self.stream)


class TerminalTextRenderer:
    """Render streamed model text without leaking escaped Markdown bold markers."""

    def __init__(self, stream: TextIO) -> None:
        self.stream = stream
        self._pending_backslash = False
        self._pending_stars = 0

    def feed(self, content: str) -> None:
        rendered: list[str] = []
        for character in content:
            if self._pending_backslash:
                self._pending_backslash = False
                if character == "*":
                    self._pending_stars += 1
                    continue
                if character in r"_`#[]()~>+-.":
                    self._flush_stars(rendered)
                    rendered.append(character)
                    continue
                self._flush_stars(rendered)
                rendered.extend(("\\", character))
                continue
            if character == "\\":
                self._pending_backslash = True
            elif character == "*":
                self._pending_stars += 1
            else:
                self._flush_stars(rendered)
                rendered.append(character)
        if rendered:
            print("".join(rendered), end="", flush=True, file=self.stream)

    def flush(self) -> None:
        rendered: list[str] = []
        self._flush_stars(rendered)
        if self._pending_backslash:
            rendered.append("\\")
            self._pending_backslash = False
        if rendered:
            print("".join(rendered), end="", flush=True, file=self.stream)

    def _flush_stars(self, rendered: list[str]) -> None:
        if self._pending_stars == 1:
            rendered.append("*")
        self._pending_stars = 0


def normalize_terminal_text(content: str) -> str:
    """Apply the streaming terminal rules to a complete history message."""

    stream = StringIO()
    renderer = TerminalTextRenderer(stream)
    renderer.feed(content)
    renderer.flush()
    return stream.getvalue()
