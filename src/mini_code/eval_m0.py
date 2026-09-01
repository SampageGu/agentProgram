"""Small real-model acceptance suite for milestone M0."""

from __future__ import annotations

import io
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mini_code.agent import ReadOnlyAgent
from mini_code.config import Settings
from mini_code.events import EventSink
from mini_code.provider import DeepSeekProvider
from mini_code.tools import build_readonly_registry
from mini_code.workspace import Workspace


@dataclass(frozen=True)
class EvalCaseResult:
    case_id: str
    passed: bool
    status: str
    path_hits: list[str]
    missing_terms: list[str]
    tool_calls: int
    answer: str
    trace_file: str


def run_m0_eval(
    workspace: Workspace,
    settings: Settings,
    tasks_file: Path,
) -> tuple[Path, list[EvalCaseResult]]:
    tasks = json.loads(tasks_file.read_text(encoding="utf-8"))
    if not isinstance(tasks, list) or len(tasks) != 5:
        raise ValueError("M0 eval must contain exactly 5 task objects")
    suite_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    suite_dir = workspace.root / ".mini-code" / "evals" / suite_id
    results: list[EvalCaseResult] = []
    provider = DeepSeekProvider(settings)

    for index, task in enumerate(tasks, 1):
        case_id = str(task["id"])
        print(f"[{index}/5] {case_id}: {task['question']}")
        trace_file = suite_dir / case_id / "events.jsonl"
        sink = EventSink(trace_file, stream=io.StringIO())
        agent = ReadOnlyAgent(
            provider=provider,
            workspace=workspace,
            tools=build_readonly_registry(workspace, settings.max_tool_chars),
            sink=sink,
            max_steps=settings.max_steps,
        )
        run = agent.run(str(task["question"]), run_id=f"{suite_id}-{case_id}")
        result = _grade(task, run.status, run.answer, sink, trace_file)
        results.append(result)
        print(
            f"      {'PASS' if result.passed else 'FAIL'} | "
            f"tools={result.tool_calls} | status={result.status}"
        )

    summary = {
        "suite": "m0",
        "suite_id": suite_id,
        "model": settings.model,
        "base_url": settings.base_url,
        "passed": sum(result.passed for result in results),
        "total": len(results),
        "results": [asdict(result) for result in results],
    }
    report_file = suite_dir / "report.json"
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report_file, results


def _grade(
    task: dict[str, Any],
    status: str,
    answer: str,
    sink: EventSink,
    trace_file: Path,
) -> EvalCaseResult:
    lowered = answer.lower()
    expected_paths = [str(path) for path in task.get("expected_paths", [])]
    path_hits = [path for path in expected_paths if path.lower() in lowered]
    expected_terms = [str(term) for term in task.get("expected_terms", [])]
    missing_terms = [term for term in expected_terms if term.lower() not in lowered]
    tool_calls = sum(event.type == "tool_started" for event in sink.events)
    passed = (
        status == "completed"
        and bool(path_hits)
        and not missing_terms
        and tool_calls >= 1
    )
    return EvalCaseResult(
        case_id=str(task["id"]),
        passed=passed,
        status=status,
        path_hits=path_hits,
        missing_terms=missing_terms,
        tool_calls=tool_calls,
        answer=answer,
        trace_file=str(trace_file),
    )

