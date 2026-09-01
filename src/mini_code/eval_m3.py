"""Versioned 20-task M3 coding evaluation with hidden graders and metrics."""

from __future__ import annotations

import io
import json
import math
import os
import shutil
import statistics
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mini_code import __version__
from mini_code.agent import PROMPT_VERSION, CodingAgent
from mini_code.config import Settings
from mini_code.context import ContextManager
from mini_code.events import EventSink
from mini_code.persistence import RunStore
from mini_code.provider import DeepSeekProvider
from mini_code.replay import calculate_metrics, load_trace
from mini_code.tools import build_coding_registry
from mini_code.workspace import Workspace


@dataclass(frozen=True)
class M3EvalResult:
    case_id: str
    category: str
    passed: bool
    agent_status: str
    public_tests_passed: bool
    hidden_tests_passed: bool
    regression: bool
    changed_files: list[str]
    unrelated_files: list[str]
    steps: int
    tool_calls: int
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    duration_ms: int
    trace_file: str


CASE_SPECS: dict[str, tuple[str, str, str]] = {
    "abs_diff": ("def abs_diff(a, b):\n    return a - b\n", "self.assertEqual(abs_diff(5, 2), 3)", "self.assertEqual(abs_diff(2, 5), 3)"),
    "clamp": ("def clamp(value, lower, upper):\n    if value < lower: return lower\n    if value > upper: return upper\n    return upper\n", "self.assertEqual(clamp(-1, 0, 10), 0)", "self.assertEqual(clamp(4, 0, 10), 4)"),
    "safe_divide": ("def safe_divide(a, b):\n    return a / b\n", "self.assertEqual(safe_divide(8, 2), 4)", "self.assertIsNone(safe_divide(8, 0))"),
    "first_or_none": ("def first_or_none(values):\n    return values[0]\n", "self.assertEqual(first_or_none([3]), 3)", "self.assertIsNone(first_or_none([]))"),
    "normalize_email": ("def normalize_email(value):\n    return value.lower()\n", "self.assertEqual(normalize_email('A@B.COM'), 'a@b.com')", "self.assertEqual(normalize_email(' A@B.COM '), 'a@b.com')"),
    "palindrome": ("def palindrome(text):\n    return text == text[::-1]\n", "self.assertTrue(palindrome('level'))", "self.assertTrue(palindrome('Level'))"),
    "factorial": ("def factorial(n):\n    if n <= 1: return n\n    return n * factorial(n - 1)\n", "self.assertEqual(factorial(3), 6)", "self.assertEqual(factorial(0), 1)"),
    "fibonacci": ("def fibonacci(n):\n    if n <= 1: return 1\n    return fibonacci(n-1) + fibonacci(n-2)\n", "self.assertEqual(fibonacci(1), 1)", "self.assertEqual(fibonacci(0), 0)"),
    "unique_order": ("def unique_order(items):\n    return list(dict.fromkeys(reversed(items)))\n", "self.assertEqual(unique_order(['a']), ['a'])", "self.assertEqual(unique_order(['a', 'b', 'c', 'a']), ['a', 'b', 'c'])"),
    "chunks": ("def chunks(items, size):\n    return [items[i:i+size] for i in range(0, len(items)-size+1, size)]\n", "self.assertEqual(chunks([1,2,3,4], 2), [[1,2],[3,4]])", "self.assertEqual(chunks([1,2,3], 2), [[1,2],[3]])"),
    "parse_bool": ("def parse_bool(value):\n    return value == 'true'\n", "self.assertTrue(parse_bool('true'))", "self.assertTrue(parse_bool(' TRUE '))"),
    "average": ("def average(values):\n    return sum(values) / len(values)\n", "self.assertEqual(average([2,4]), 3)", "self.assertIsNone(average([]))"),
    "slugify": ("def slugify(text):\n    return text.lower().replace(' ', '-')\n", "self.assertEqual(slugify('Hello World'), 'hello-world')", "self.assertEqual(slugify('Hello   World'), 'hello-world')"),
    "retry_delay": ("def retry_delay(base, attempt):\n    return base * attempt\n", "self.assertEqual(retry_delay(2, 1), 2)", "self.assertEqual(retry_delay(2, 3), 8)"),
    "page_offset": ("def page_offset(page, size):\n    return size if page == 1 else (page - 1) * size\n", "self.assertEqual(page_offset(2, 10), 10)", "self.assertEqual(page_offset(1, 10), 0)"),
    "merge_defaults": ("def merge_defaults(defaults, overrides):\n    return overrides | defaults\n", "self.assertEqual(merge_defaults({'a':1}, {'b':2}), {'a':1,'b':2})", "self.assertEqual(merge_defaults({'a':1}, {'a':2}), {'a':2})"),
    "bounded_add": ("def bounded_add(a, b, lower, upper):\n    return min(a + b, upper)\n", "self.assertEqual(bounded_add(8, 8, 0, 10), 10)", "self.assertEqual(bounded_add(-8, 1, 0, 10), 0)"),
    "count_words": ("def count_words(text):\n    return len(text.split(' '))\n", "self.assertEqual(count_words('one two'), 2)", "self.assertEqual(count_words('  one   two  '), 2)"),
    "extension": ("def extension(filename):\n    return filename.rsplit('.', 1)[-1].lower()\n", "self.assertEqual(extension('app.PY'), 'py')", "self.assertEqual(extension('README'), '')"),
    "coalesce": ("def coalesce(primary, fallback):\n    return primary or fallback\n", "self.assertEqual(coalesce('x', 'y'), 'x')", "self.assertEqual(coalesce(0, 9), 0)"),
}


def run_m3_eval(
    project: Workspace,
    settings: Settings,
    tasks_file: Path,
    limit: int | None = None,
) -> tuple[Path, list[M3EvalResult]]:
    tasks = load_m3_tasks(tasks_file)
    if limit is not None:
        if limit < 1 or limit > len(tasks):
            raise ValueError(f"limit must be between 1 and {len(tasks)}")
        tasks = tasks[:limit]
    suite_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + f"-{uuid.uuid4().hex[:6]}"
    suite_dir = project.root / ".mini-code" / "evals" / f"m3-{suite_id}"
    provider = DeepSeekProvider(settings)
    results: list[M3EvalResult] = []

    for index, task in enumerate(tasks, 1):
        case_id = str(task["id"])
        case_dir = suite_dir / case_id
        workspace_dir = case_dir / "workspace"
        hidden_dir = case_dir / "hidden_tests"
        materialize_case(task, workspace_dir, hidden_dir)
        workspace = Workspace(workspace_dir)
        baseline_public = _run_tests(workspace.root, workspace.root / "tests")
        baseline_hidden = _run_tests(workspace.root, hidden_dir)
        if baseline_public != 0 or baseline_hidden == 0:
            raise ValueError(f"Invalid baseline fixture: {case_id}")
        run_id = f"{suite_id}-{case_id}"
        store = RunStore(workspace)
        trace_file = store.run_dir(run_id) / "events.jsonl"
        sink = EventSink(trace_file, stream=io.StringIO())
        tools = build_coding_registry(workspace, settings.max_tool_chars, settings.request_timeout)
        started = time.monotonic()
        run = CodingAgent(
            provider=provider,
            workspace=workspace,
            tools=tools,
            sink=sink,
            max_steps=max(settings.max_steps, 16),
            run_store=store,
            context_manager=ContextManager(
                store, run_id, settings.max_context_chars, settings.artifact_threshold
            ),
        ).run(str(task["task"]), run_id=run_id)
        duration_ms = round((time.monotonic() - started) * 1_000)
        public_passed = _run_tests(workspace.root, workspace.root / "tests") == 0
        hidden_passed = _run_tests(workspace.root, hidden_dir) == 0
        state = tools.coding_state
        expected = {"solution.py", "tests/test_public.py"}
        unrelated = sorted(path for path in state.changed_files if path not in expected)
        metrics = calculate_metrics(load_trace(trace_file))
        passed = run.status == "completed" and public_passed and hidden_passed
        result = M3EvalResult(
            case_id=case_id,
            category=str(task["category"]),
            passed=passed,
            agent_status=run.status,
            public_tests_passed=public_passed,
            hidden_tests_passed=hidden_passed,
            regression=baseline_public == 0 and not public_passed,
            changed_files=sorted(state.changed_files),
            unrelated_files=unrelated,
            steps=run.steps,
            tool_calls=metrics.tool_calls,
            input_tokens=metrics.input_tokens,
            output_tokens=metrics.output_tokens,
            total_tokens=metrics.total_tokens,
            duration_ms=duration_ms,
            trace_file=str(trace_file),
        )
        results.append(result)
        print(f"[{index}/{len(tasks)}] {'PASS' if passed else 'FAIL'} {case_id} steps={run.steps}")

    report_file = suite_dir / "report.json"
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text(
        json.dumps(_build_report(suite_id, settings, results), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report_file, results


def load_m3_tasks(tasks_file: Path) -> list[dict[str, Any]]:
    tasks = json.loads(tasks_file.read_text(encoding="utf-8"))
    if not isinstance(tasks, list) or len(tasks) != 20:
        raise ValueError("M3 eval must contain exactly 20 task objects")
    ids = [str(task.get("id", "")) for task in tasks]
    specs = [str(task.get("spec", "")) for task in tasks]
    if len(set(ids)) != 20 or any(spec not in CASE_SPECS for spec in specs):
        raise ValueError("M3 task ids must be unique and every spec must be registered")
    return tasks


def materialize_case(task: dict[str, Any], workspace: Path, hidden_dir: Path) -> None:
    source, public_assertion, hidden_assertion = CASE_SPECS[str(task["spec"])]
    workspace.mkdir(parents=True, exist_ok=False)
    (workspace / "tests").mkdir()
    hidden_dir.mkdir(parents=True, exist_ok=False)
    (workspace / "solution.py").write_text(source, encoding="utf-8")
    (workspace / "tests/test_public.py").write_text(
        _test_module(str(task["spec"]), public_assertion), encoding="utf-8"
    )
    (hidden_dir / "test_hidden.py").write_text(
        _test_module(str(task["spec"]), hidden_assertion), encoding="utf-8"
    )


def _test_module(function_name: str, assertion: str) -> str:
    return (
        "import unittest\n"
        f"from solution import {function_name}\n\n"
        "class SolutionTests(unittest.TestCase):\n"
        "    def test_behavior(self):\n"
        f"        {assertion}\n"
    )


def _run_tests(workspace: Path, tests: Path) -> int:
    completed = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", str(tests), "-v"],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env=_safe_environment(),
    )
    return completed.returncode


def _safe_environment() -> dict[str, str]:
    blocked = ("KEY", "TOKEN", "SECRET", "PASSWORD", "DEEPSEEK", "MINICODE")
    return {
        key: value for key, value in os.environ.items()
        if not any(word in key.upper() for word in blocked)
        and not key.upper().startswith("GIT_CONFIG_")
    }


def _build_report(
    suite_id: str, settings: Settings, results: list[M3EvalResult]
) -> dict[str, Any]:
    total = len(results)
    durations = sorted(result.duration_ms for result in results)
    changed = sum(len(result.changed_files) for result in results)
    unrelated = sum(len(result.unrelated_files) for result in results)
    token_values = [result.total_tokens for result in results if result.total_tokens is not None]
    input_tokens = sum(result.input_tokens or 0 for result in results)
    output_tokens = sum(result.output_tokens or 0 for result in results)
    has_prices = (
        settings.input_cost_per_million is not None
        and settings.output_cost_per_million is not None
    )
    estimated_cost = None
    if has_prices and token_values:
        estimated_cost = (
            input_tokens * settings.input_cost_per_million
            + output_tokens * settings.output_cost_per_million
        ) / 1_000_000
    return {
        "suite": "m3",
        "suite_id": suite_id,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "model": settings.model,
        "code_version": __version__,
        "prompt_version": PROMPT_VERSION,
        "pricing_snapshot": {
            "input_usd_per_million": settings.input_cost_per_million,
            "output_usd_per_million": settings.output_cost_per_million,
            "source": "user-configured environment values",
        },
        "total": total,
        "metrics": {
            "task_success_rate": sum(result.passed for result in results) / total,
            "public_test_pass_rate": sum(result.public_tests_passed for result in results) / total,
            "regression_rate": sum(result.regression for result in results) / total,
            "unrelated_change_rate": unrelated / changed if changed else 0.0,
            "mean_steps": statistics.mean(result.steps for result in results),
            "p50_duration_ms": _percentile(durations, 0.50),
            "p95_duration_ms": _percentile(durations, 0.95),
            "total_tokens": sum(token_values) if token_values else None,
            "token_coverage": len(token_values) / total,
            "estimated_cost_usd": estimated_cost,
            "recovery_success_rate": None,
        },
        "limitations": [
            "Cost is null unless provider usage and both explicit pricing values are configured.",
            "Recovery is validated by the deterministic fault suite, not every coding case.",
        ],
        "results": [asdict(result) for result in results],
    }


def _percentile(sorted_values: list[int], percentile: float) -> int:
    if not sorted_values:
        return 0
    index = max(0, math.ceil(len(sorted_values) * percentile) - 1)
    return sorted_values[index]
