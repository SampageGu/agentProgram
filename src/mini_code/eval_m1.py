"""Three-case real-model evaluation suite for milestone M1."""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
import uuid

from mini_code.agent import CodingAgent
from mini_code.config import Settings
from mini_code.events import EventSink
from mini_code.provider import DeepSeekProvider
from mini_code.tools import build_coding_registry
from mini_code.workspace import Workspace


@dataclass(frozen=True)
class M1EvalResult:
    case_id: str
    passed: bool
    agent_status: str
    tests_passed: bool
    changed_files: list[str]
    verification_failures: int
    trace_file: str


def run_m1_eval(
    project: Workspace,
    settings: Settings,
    tasks_file: Path,
) -> tuple[Path, list[M1EvalResult]]:
    tasks = json.loads(tasks_file.read_text(encoding="utf-8"))
    if not isinstance(tasks, list) or len(tasks) != 3:
        raise ValueError("M1 eval must contain exactly 3 task objects")
    suite_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + f"-{uuid.uuid4().hex[:6]}"
    suite_dir = project.root / ".mini-code" / "evals" / f"m1-{suite_id}"
    provider = DeepSeekProvider(settings)
    results: list[M1EvalResult] = []

    for index, task in enumerate(tasks, 1):
        case_id = str(task["id"])
        source = project.resolve(str(task["template"]))
        grader_tests = suite_dir / case_id / "grader_tests"
        shutil.copytree(source / "tests", grader_tests)
        workspace_dir = suite_dir / case_id / "workspace"
        shutil.copytree(source, workspace_dir)
        workspace = Workspace(workspace_dir)
        trace_file = workspace_dir / ".mini-code" / "runs" / case_id / "events.jsonl"
        sink = EventSink(trace_file, stream=io.StringIO())
        tools = build_coding_registry(
            workspace,
            max_chars=settings.max_tool_chars,
            command_timeout=settings.request_timeout,
        )
        print(f"[{index}/3] {case_id}: {task['task']}")
        run = CodingAgent(
            provider=provider,
            workspace=workspace,
            tools=tools,
            sink=sink,
            max_steps=max(settings.max_steps, 16),
        ).run(str(task["task"]), run_id=f"{suite_id}-{case_id}")
        test_run = subprocess.run(
            [
                sys.executable,
                "-m",
                "unittest",
                "discover",
                "-s",
                str(grader_tests),
                "-v",
            ],
            cwd=workspace.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=min(settings.request_timeout, 300),
            check=False,
            env=_grader_environment(),
        )
        state = tools.coding_state
        tests_passed = test_run.returncode == 0
        passed = run.status == "completed" and tests_passed and bool(state.changed_files)
        result = M1EvalResult(
            case_id=case_id,
            passed=passed,
            agent_status=run.status,
            tests_passed=tests_passed,
            changed_files=sorted(state.changed_files),
            verification_failures=sum(
                event.type == "verification_failed" for event in sink.events
            ),
            trace_file=str(trace_file),
        )
        results.append(result)
        print(f"      {'PASS' if passed else 'FAIL'} | status={run.status} | tests={tests_passed}")

    report = {
        "suite": "m1",
        "suite_id": suite_id,
        "model": settings.model,
        "passed": sum(result.passed for result in results),
        "total": len(results),
        "results": [asdict(result) for result in results],
    }
    report_file = suite_dir / "report.json"
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report_file, results


def _grader_environment() -> dict[str, str]:
    import os

    blocked = ("KEY", "TOKEN", "SECRET", "PASSWORD", "DEEPSEEK", "MINICODE")
    return {
        name: value
        for name, value in os.environ.items()
        if not any(part in name.upper() for part in blocked)
        and not name.upper().startswith("GIT_CONFIG_")
    }
