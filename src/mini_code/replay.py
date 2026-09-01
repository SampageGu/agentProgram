"""Trace replay and deterministic run metrics."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO


class ReplayError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReplayMetrics:
    run_id: str
    status: str
    events: int
    model_steps: int
    tool_calls: int
    tool_failures: int
    patches: int
    verification_passed: int
    verification_failed: int
    checkpoints: int
    compactions: int
    resumes: int
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    duration_ms: int
    changed_files: list[str]


def load_trace(trace_file: Path) -> list[dict[str, Any]]:
    if not trace_file.is_file():
        raise ReplayError(f"Trace not found: {trace_file}")
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(trace_file.read_text(encoding="utf-8").splitlines(), 1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReplayError(f"Invalid JSONL at line {line_number}") from exc
        if not isinstance(event, dict) or "type" not in event or "run_id" not in event:
            raise ReplayError(f"Invalid event at line {line_number}")
        events.append(event)
    if not events:
        raise ReplayError("Trace is empty")
    return events


def calculate_metrics(events: list[dict[str, Any]]) -> ReplayMetrics:
    run_ids = {str(event["run_id"]) for event in events}
    if len(run_ids) != 1:
        raise ReplayError("Trace contains multiple run ids")
    types = [str(event["type"]) for event in events]
    changed_files: set[str] = set()
    input_tokens = output_tokens = total_tokens = 0
    saw_usage = False
    for event in events:
        data = event.get("data") or {}
        if event["type"] == "patch_applied":
            changed_files.update(str(path) for path in data.get("files", []))
        usage = data.get("usage")
        if isinstance(usage, dict) and usage:
            saw_usage = True
            input_tokens += int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0)
            output_tokens += int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0)
            total_tokens += int(usage.get("total_tokens", 0) or 0)
    if saw_usage and total_tokens == 0:
        total_tokens = input_tokens + output_tokens
    started = _parse_timestamp(events[0].get("timestamp"))
    ended = _parse_timestamp(events[-1].get("timestamp"))
    terminal = next(
        (kind for kind in reversed(types) if kind in {
            "run_completed", "run_failed", "run_interrupted", "run_cancelled",
            "budget_exhausted"
        }),
        "running",
    )
    status_map = {
        "run_completed": "completed",
        "run_failed": "failed",
        "run_interrupted": "interrupted",
        "run_cancelled": "cancelled",
        "budget_exhausted": "budget_exhausted",
    }
    return ReplayMetrics(
        run_id=next(iter(run_ids)),
        status=status_map.get(terminal, "running"),
        events=len(events),
        model_steps=types.count("model_started"),
        tool_calls=types.count("tool_started"),
        tool_failures=sum(
            event["type"] == "tool_completed" and not (event.get("data") or {}).get("ok", False)
            for event in events
        ),
        patches=types.count("patch_applied"),
        verification_passed=types.count("verification_passed"),
        verification_failed=types.count("verification_failed"),
        checkpoints=types.count("checkpoint_saved"),
        compactions=types.count("context_compacted"),
        resumes=types.count("run_resumed"),
        input_tokens=input_tokens if saw_usage else None,
        output_tokens=output_tokens if saw_usage else None,
        total_tokens=total_tokens if saw_usage else None,
        duration_ms=max(0, round((ended - started).total_seconds() * 1_000)),
        changed_files=sorted(changed_files),
    )


def replay_trace(
    trace_file: Path,
    stream: TextIO,
    as_json: bool = False,
) -> ReplayMetrics:
    events = load_trace(trace_file)
    metrics = calculate_metrics(events)
    if as_json:
        print(json.dumps({"metrics": asdict(metrics), "events": events}, ensure_ascii=False, indent=2), file=stream)
        return metrics
    print(f"MiniCode Replay: {metrics.run_id}", file=stream)
    previous_time: datetime | None = None
    pending_text: list[str] = []
    pending_sequence = 0
    pending_started: datetime | None = None
    pending_ended: datetime | None = None

    def flush_text() -> None:
        nonlocal previous_time, pending_sequence, pending_started, pending_ended
        if pending_started is None:
            return
        content = "".join(pending_text).replace("\r", " ").replace("\n", " ").strip()
        preview = content if len(content) <= 160 else content[:157] + "..."
        delta_ms = 0 if previous_time is None else round(
            (pending_started - previous_time).total_seconds() * 1_000
        )
        print(
            f"{pending_sequence:04d} +{delta_ms:>5}ms  {'assistant_text':<24} "
            f"{len(content)} chars: {preview}",
            file=stream,
        )
        previous_time = pending_ended
        pending_text.clear()
        pending_sequence = 0
        pending_started = pending_ended = None

    for sequence, event in enumerate(events, 1):
        timestamp = _parse_timestamp(event.get("timestamp"))
        if event["type"] == "text_delta":
            if pending_started is None:
                pending_sequence = sequence
                pending_started = timestamp
            pending_ended = timestamp
            pending_text.append(str((event.get("data") or {}).get("content", "")))
            continue
        flush_text()
        delta_ms = 0 if previous_time is None else round((timestamp - previous_time).total_seconds() * 1_000)
        previous_time = timestamp
        data = event.get("data") or {}
        message = str(data.get("message", ""))
        if event["type"] == "tool_started":
            message = f"{data.get('tool', '')}: {message}"
        print(f"{sequence:04d} +{delta_ms:>5}ms  {event['type']:<24} {message}", file=stream)
    flush_text()
    token_text = "unavailable" if metrics.total_tokens is None else str(metrics.total_tokens)
    print("\nSummary", file=stream)
    print(f"  status={metrics.status} duration_ms={metrics.duration_ms} model_steps={metrics.model_steps}", file=stream)
    print(f"  tools={metrics.tool_calls} tool_failures={metrics.tool_failures} patches={metrics.patches}", file=stream)
    print(
        f"  tests_passed={metrics.verification_passed} tests_failed={metrics.verification_failed} "
        f"resumes={metrics.resumes} compactions={metrics.compactions}",
        file=stream,
    )
    print(f"  tokens={token_text} changed_files={','.join(metrics.changed_files) or '(none)'}", file=stream)
    return metrics


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ReplayError("Event timestamp is missing")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReplayError(f"Invalid event timestamp: {value}") from exc
