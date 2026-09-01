"""M1 coding tools with workspace, command, and verification boundaries."""

from __future__ import annotations

import os
import re
import io
import difflib
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Callable
from typing import Any

from mini_code.tools.readonly import (
    ToolDefinition,
    ToolRegistry,
    _error,
    _limit_json,
    _ok,
    build_readonly_registry,
)
from mini_code.workspace import Workspace, WorkspaceError


@dataclass
class CodingToolState:
    plan: list[dict[str, str]] = field(default_factory=list)
    changed_files: set[str] = field(default_factory=set)
    revision: int = 0
    successful_test_revision: int = -1
    diff_revision: int = -1
    checks: list[dict[str, Any]] = field(default_factory=list)
    completion_report: dict[str, Any] | None = None
    baselines: dict[str, str | None] = field(default_factory=dict)
    last_diff: str = ""
    delegation_count: int = 0
    delegations: list[dict[str, Any]] = field(default_factory=list)
    delegated_evidence_files: set[str] = field(default_factory=set)
    verified_evidence_files: set[str] = field(default_factory=set)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan": self.plan,
            "changed_files": sorted(self.changed_files),
            "revision": self.revision,
            "successful_test_revision": self.successful_test_revision,
            "diff_revision": self.diff_revision,
            "checks": self.checks,
            "completion_report": self.completion_report,
            "baselines": self.baselines,
            "last_diff": self.last_diff,
            "delegation_count": self.delegation_count,
            "delegations": self.delegations,
            "delegated_evidence_files": sorted(self.delegated_evidence_files),
            "verified_evidence_files": sorted(self.verified_evidence_files),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CodingToolState":
        return cls(
            plan=list(data.get("plan", [])),
            changed_files=set(data.get("changed_files", [])),
            revision=int(data.get("revision", 0)),
            successful_test_revision=int(data.get("successful_test_revision", -1)),
            diff_revision=int(data.get("diff_revision", -1)),
            checks=list(data.get("checks", [])),
            completion_report=data.get("completion_report"),
            baselines=dict(data.get("baselines", {})),
            last_diff=str(data.get("last_diff", "")),
            delegation_count=int(data.get("delegation_count", 0)),
            delegations=list(data.get("delegations", [])),
            delegated_evidence_files=set(data.get("delegated_evidence_files", [])),
            verified_evidence_files=set(data.get("verified_evidence_files", [])),
        )


@dataclass(frozen=True)
class ExploreDelegationConfig:
    parent_run_id: str
    provider_factory: Callable[[], Any]
    max_delegations: int = 2
    child_max_steps: int = 6
    child_max_tokens: int = 32_000


def build_coding_registry(
    workspace: Workspace,
    max_chars: int = 12_000,
    command_timeout: int = 120,
    initial_state: CodingToolState | None = None,
    delegation: ExploreDelegationConfig | None = None,
) -> ToolRegistry:
    registry = build_readonly_registry(workspace, max_chars)
    state = initial_state or CodingToolState()
    setattr(registry, "coding_state", state)

    def observe_tool(
        name: str, arguments: dict[str, Any], result: dict[str, Any]
    ) -> None:
        if name != "read_file" or not result.get("ok"):
            return
        path = str((result.get("data") or {}).get("path", ""))
        if path in state.delegated_evidence_files:
            state.verified_evidence_files.add(path)

    registry.add_observer(observe_tool)

    def delegate_explore(question: str) -> dict[str, Any]:
        if delegation is None:
            return _error("delegation_unavailable", "Explore Subagent is not enabled for this run")
        question = question.strip()
        if not question:
            return _error("invalid_delegation", "question must not be empty")
        if len(question) > 4_000:
            return _error("invalid_delegation", "question exceeds 4000 characters")
        if state.delegation_count >= delegation.max_delegations:
            return _error(
                "delegation_limit",
                f"At most {delegation.max_delegations} Explore Subagents are allowed",
            )
        state.delegation_count += 1
        from mini_code.subagents import ExploreSubagent

        child = ExploreSubagent(
            provider=delegation.provider_factory(),
            workspace=workspace,
            max_steps=delegation.child_max_steps,
            max_tool_chars=max_chars,
            max_total_tokens=delegation.child_max_tokens,
        ).run(
            question,
            parent_run_id=delegation.parent_run_id,
            stream=io.StringIO(),
        )
        record = {
            "child_run_id": child.child_run_id,
            "status": child.status,
            "question": question,
            "trace_file": str(child.trace_file),
            "report_file": str(child.report_file) if child.report_file else None,
            "steps": child.steps,
            "total_tokens": child.total_tokens,
            "report_error": child.report_error,
        }
        if child.report is None:
            state.delegations.append(record)
            return _error(
                "subagent_failed",
                f"Explore child {child.child_run_id} ended as {child.status}; "
                "continue with direct read-only tools",
            )
        report = child.report.to_dict()
        evidence_files = {
            str(item["path"])
            for item in report["evidence"]
            if isinstance(item, dict) and item.get("path")
        }
        state.delegated_evidence_files.update(evidence_files)
        record["report"] = report
        state.delegations.append(record)
        return _limit_json(
            _ok({
                "parent_run_id": delegation.parent_run_id,
                **record,
                "report": report,
                "verification_required": (
                    "The parent must call read_file on at least one evidence path before apply_patch"
                ),
            }),
            max_chars,
        )

    def update_plan(steps: list[dict[str, str]]) -> dict[str, Any]:
        if not steps or len(steps) > 20:
            return _error("invalid_plan", "Plan must contain between 1 and 20 steps")
        allowed = {"pending", "in_progress", "completed"}
        normalized: list[dict[str, str]] = []
        for item in steps:
            if not isinstance(item, dict):
                return _error("invalid_plan", "Every plan step must be an object")
            description = str(item.get("step", "")).strip()
            status = str(item.get("status", "")).strip()
            if not description or status not in allowed:
                return _error("invalid_plan", "Each step needs text and a valid status")
            normalized.append({"step": description[:500], "status": status})
        if sum(item["status"] == "in_progress" for item in normalized) > 1:
            return _error("invalid_plan", "At most one step can be in_progress")
        state.plan = normalized
        return _ok({"steps": state.plan})

    def apply_patch(patch: str) -> dict[str, Any]:
        if not state.plan or not any(item["status"] == "in_progress" for item in state.plan):
            return _error("plan_required", "Create a plan with one in_progress step first")
        if state.delegated_evidence_files and not state.verified_evidence_files:
            return _error(
                "subagent_evidence_unverified",
                "Read at least one file cited by Explore before applying a patch",
            )
        if not patch.strip():
            return _error("empty_patch", "patch must not be empty")
        if len(patch) > 200_000:
            return _error("patch_too_large", "Patch exceeds the 200,000 character limit")
        try:
            updates = _prepare_patch(workspace, patch)
        except (ValueError, WorkspaceError, OSError, UnicodeError) as exc:
            return _error("invalid_patch", str(exc))
        for path, content in updates:
            relative = workspace.relative(path)
            if relative not in state.baselines:
                state.baselines[relative] = (
                    path.read_text(encoding="utf-8") if path.exists() else None
                )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
            if state.baselines[relative] == content:
                state.changed_files.discard(relative)
            else:
                state.changed_files.add(relative)
        state.revision += 1
        return _ok(
            {
                "changed_files": sorted(workspace.relative(path) for path, _ in updates),
                "revision": state.revision,
            }
        )

    def run_command(argv: list[str], timeout: int = command_timeout) -> dict[str, Any]:
        try:
            checked = _validate_command(workspace, argv)
        except (ValueError, WorkspaceError) as exc:
            return _error("command_rejected", str(exc))
        return _run_process(workspace, checked, timeout, max_chars)

    def run_tests(
        target: str = "tests",
        framework: str = "auto",
        timeout: int = command_timeout,
    ) -> dict[str, Any]:
        try:
            target_path = workspace.resolve_for_tool(target)
        except WorkspaceError as exc:
            return _error("invalid_target", str(exc))
        if not target_path.exists():
            return _error("invalid_target", f"Test target does not exist: {target}")
        if framework not in {"auto", "unittest", "pytest"}:
            return _error("invalid_framework", "framework must be auto, unittest, or pytest")
        selected = _detect_test_framework(workspace, framework)
        if selected == "pytest":
            command = [sys.executable, "-m", "pytest", target]
        elif target_path.is_dir():
            command = [sys.executable, "-m", "unittest", "discover", "-s", target, "-v"]
        else:
            module = target.removesuffix(".py").replace("/", ".").replace("\\", ".")
            command = [sys.executable, "-m", "unittest", module, "-v"]
        result = _run_process(workspace, command, timeout, max_chars)
        passed = bool(result.get("ok") and result.get("data", {}).get("exit_code") == 0)
        check = {
            "command": command,
            "exit_code": result.get("data", {}).get("exit_code"),
            "passed": passed,
            "revision": state.revision,
            "stdout_tail": str(result.get("data", {}).get("stdout", ""))[-800:],
            "stderr_tail": str(result.get("data", {}).get("stderr", ""))[-800:],
        }
        state.checks.append(check)
        if passed:
            state.successful_test_revision = state.revision
        if result.get("data") is not None:
            result["data"]["passed"] = passed
            result["data"]["revision"] = state.revision
        return result

    def git_diff() -> dict[str, Any]:
        session_diff = _session_diff(workspace, state)
        git_output = ""
        status_output = ""
        warning = ""
        if shutil.which("git"):
            result = _run_process(
                workspace,
                ["git", "diff", "--no-ext-diff", "--", "."],
                min(command_timeout, 30),
                max_chars,
            )
            if result.get("ok") and result.get("data", {}).get("exit_code") == 0:
                git_output = result["data"].get("stdout", "")
            else:
                warning = (result.get("data") or {}).get("stderr", "git diff unavailable")
            status = _run_process(
                workspace,
                ["git", "status", "--short", "--untracked-files=all", "--", "."],
                min(command_timeout, 30),
                max_chars,
            )
            if status.get("ok") and status.get("data", {}).get("exit_code") == 0:
                status_output = status["data"].get("stdout", "")
        else:
            warning = "git executable was not found; showing the session diff"
        state.last_diff = session_diff
        state.diff_revision = state.revision
        display_session, session_truncated = _clip(session_diff, max_chars * 3 // 4)
        display_git, git_truncated = _clip(git_output, max_chars // 4)
        return _ok(
            {
                "session_diff": display_session,
                "git_diff": display_git,
                "status": status_output,
                "warning": warning,
                "revision": state.revision,
            },
            truncated=session_truncated or git_truncated,
        )

    def finish_task(summary: str, remaining_risks: list[str] | None = None) -> dict[str, Any]:
        incomplete = [item for item in state.plan if item["status"] != "completed"]
        if not state.plan:
            return _error("plan_required", "A plan is required before completion")
        if incomplete:
            return _error("plan_incomplete", "All plan steps must be completed")
        if not state.changed_files:
            return _error("no_changes", "No files were changed")
        if state.successful_test_revision != state.revision:
            return _error(
                "verification_required",
                "Run tests successfully after the latest patch before completion",
            )
        if state.diff_revision != state.revision:
            return _error(
                "diff_required",
                "Call git_diff after the latest patch before completion",
            )
        report = {
            "status": "completed",
            "summary": summary.strip()[:2_000],
            "changed_files": sorted(state.changed_files),
            "checks": state.checks,
            "remaining_risks": [str(risk)[:500] for risk in (remaining_risks or [])],
            "revision": state.revision,
        }
        state.completion_report = report
        return _ok(report)

    _register_m1_tools(
        registry,
        delegate_explore if delegation is not None else None,
        update_plan,
        apply_patch,
        run_command,
        run_tests,
        git_diff,
        finish_task,
    )
    return registry


def _register_m1_tools(
    registry: ToolRegistry,
    delegate_explore: Any,
    update_plan: Any,
    apply_patch: Any,
    run_command: Any,
    run_tests: Any,
    git_diff: Any,
    finish_task: Any,
) -> None:
    if delegate_explore is not None:
        registry.register(ToolDefinition(
            "delegate_explore",
            "Delegate one bounded read-only repository investigation to an isolated Explore Subagent. Review at least one cited file before patching.",
            {
                "type": "object",
                "properties": {"question": {"type": "string", "maxLength": 4000}},
                "required": ["question"],
                "additionalProperties": False,
            },
            delegate_explore,
        ))
    registry.register(ToolDefinition("update_plan", "Create or update the task plan.", {
        "type": "object", "properties": {"steps": {"type": "array", "items": {
            "type": "object", "properties": {
                "step": {"type": "string"},
                "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
            }, "required": ["step", "status"], "additionalProperties": False,
        }}}, "required": ["steps"], "additionalProperties": False,
    }, update_plan))
    registry.register(ToolDefinition("apply_patch", "Apply a unified diff inside the workspace. File deletion and rename are not supported.", {
        "type": "object", "properties": {"patch": {"type": "string"}},
        "required": ["patch"], "additionalProperties": False,
    }, apply_patch))
    registry.register(ToolDefinition("run_command", "Run an allowlisted test, lint, type-check, compile, or read-only Git command without a shell.", {
        "type": "object", "properties": {
            "argv": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 30},
            "timeout": {"type": "integer", "minimum": 1, "maximum": 300},
        }, "required": ["argv"], "additionalProperties": False,
    }, run_command))
    registry.register(ToolDefinition("run_tests", "Run unittest or pytest in the workspace and record verification evidence.", {
        "type": "object", "properties": {
            "target": {"type": "string", "default": "tests"},
            "framework": {"type": "string", "enum": ["auto", "unittest", "pytest"], "default": "auto"},
            "timeout": {"type": "integer", "minimum": 1, "maximum": 300},
        }, "additionalProperties": False,
    }, run_tests))
    registry.register(ToolDefinition("git_diff", "Show the workspace Git diff and short status.", {
        "type": "object", "properties": {}, "additionalProperties": False,
    }, git_diff))
    registry.register(ToolDefinition("finish_task", "Finish only after all plan steps and post-patch tests pass. Returns an evidence-based report.", {
        "type": "object", "properties": {
            "summary": {"type": "string"},
            "remaining_risks": {"type": "array", "items": {"type": "string"}},
        }, "required": ["summary"], "additionalProperties": False,
    }, finish_task))


def _prepare_patch(workspace: Workspace, patch: str) -> list[tuple[Path, str]]:
    lines = patch.replace("\r\n", "\n").split("\n")
    index = 0
    updates: list[tuple[Path, str]] = []
    while index < len(lines):
        if lines[index].startswith(("diff --git ", "index ")) or not lines[index]:
            index += 1
            continue
        if not lines[index].startswith("--- "):
            raise ValueError(f"Expected '---' header at patch line {index + 1}")
        old_name = _patch_path(lines[index][4:])
        index += 1
        if index >= len(lines) or not lines[index].startswith("+++ "):
            raise ValueError("Missing '+++' file header")
        new_name = _patch_path(lines[index][4:])
        index += 1
        if new_name == "/dev/null":
            raise ValueError("File deletion is not supported in M1")
        if old_name != "/dev/null" and old_name != new_name:
            raise ValueError("File rename is not supported in M1")
        target = workspace.resolve_for_tool(new_name)
        if target.exists() and not target.is_file():
            raise ValueError(f"Patch target is not a file: {new_name}")
        if target.exists() and target.stat().st_size > 2_000_000:
            raise ValueError(f"Patch target exceeds 2 MB: {new_name}")
        if old_name == "/dev/null":
            original: list[str] = []
            had_final_newline = True
        else:
            raw = target.read_bytes() if target.exists() else None
            if raw is None:
                raise ValueError(f"Patch source does not exist: {old_name}")
            if b"\x00" in raw[:8192]:
                raise ValueError(f"Binary files are not supported: {old_name}")
            text = raw.decode("utf-8")
            original = text.splitlines()
            had_final_newline = text.endswith(("\n", "\r"))
        patched, index = _apply_hunks(original, lines, index, new_name)
        content = "\n".join(patched)
        if patched and (had_final_newline or old_name == "/dev/null"):
            content += "\n"
        updates.append((target, content))
    if not updates:
        raise ValueError("Patch contains no file updates")
    if len({path for path, _ in updates}) != len(updates):
        raise ValueError("Each file may appear only once per patch")
    return updates


def _apply_hunks(
    original: list[str], patch_lines: list[str], index: int, name: str
) -> tuple[list[str], int]:
    output: list[str] = []
    source_index = 0
    found_hunk = False
    header = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
    while index < len(patch_lines) and patch_lines[index].startswith("@@ "):
        found_hunk = True
        match = header.match(patch_lines[index])
        if not match:
            raise ValueError(f"Invalid hunk header for {name}: {patch_lines[index]}")
        old_start = int(match.group(1))
        old_count = int(match.group(2) or "1")
        new_count = int(match.group(4) or "1")
        expected_index = 0 if old_start == 0 else old_start - 1
        if expected_index < source_index or expected_index > len(original):
            raise ValueError(f"Invalid hunk position for {name}")
        output.extend(original[source_index:expected_index])
        source_index = expected_index
        index += 1
        seen_old = seen_new = 0
        while index < len(patch_lines) and not patch_lines[index].startswith(("@@ ", "--- ", "diff --git ")):
            line = patch_lines[index]
            if line == "\\ No newline at end of file":
                index += 1
                continue
            if not line and seen_old >= old_count and seen_new >= new_count:
                break
            prefix = line[:1]
            value = line[1:]
            if prefix == " ":
                if source_index >= len(original) or original[source_index] != value:
                    raise ValueError(f"Patch context mismatch in {name}")
                output.append(value)
                source_index += 1
                seen_old += 1
                seen_new += 1
            elif prefix == "-":
                if source_index >= len(original) or original[source_index] != value:
                    raise ValueError(f"Patch removal mismatch in {name}")
                source_index += 1
                seen_old += 1
            elif prefix == "+":
                output.append(value)
                seen_new += 1
            else:
                break
            index += 1
            if seen_old == old_count and seen_new == new_count:
                break
        if seen_old != old_count or seen_new != new_count:
            raise ValueError(f"Hunk line count mismatch in {name}")
    if not found_hunk:
        raise ValueError(f"No hunks found for {name}")
    output.extend(original[source_index:])
    return output, index


def _patch_path(raw: str) -> str:
    value = raw.split("\t", 1)[0].strip()
    if value == "/dev/null":
        return value
    if value.startswith(("a/", "b/")):
        value = value[2:]
    if not value or value.startswith(('"', "'")):
        raise ValueError("Empty or quoted patch paths are not supported")
    return value


def _validate_command(workspace: Workspace, argv: list[str]) -> list[str]:
    if not isinstance(argv, list) or not argv or not all(isinstance(arg, str) for arg in argv):
        raise ValueError("argv must be a non-empty string array")
    if len(argv) > 30 or any(len(arg) > 1_000 for arg in argv):
        raise ValueError("Command exceeds argument limits")
    executable = Path(argv[0]).name.lower()
    args = argv[1:]
    if executable in {"python", "python.exe", "py", "py.exe"}:
        if len(args) < 2 or args[0] != "-m" or args[1] not in {
            "unittest", "pytest", "compileall", "ruff", "mypy"
        }:
            raise ValueError("Python is limited to approved -m test/lint/compile modules")
        argv = [sys.executable, *args]
    elif executable in {"pytest", "pytest.exe", "ruff", "ruff.exe", "mypy", "mypy.exe"}:
        pass
    elif executable in {"git", "git.exe"}:
        if not args or args[0] not in {"diff", "status"}:
            raise ValueError("Only read-only git diff/status commands are allowed")
    else:
        raise ValueError(f"Executable is not allowlisted: {argv[0]}")
    for arg in argv[1:]:
        if arg.startswith("-") or arg in {"discover", "."}:
            continue
        if any(token in arg for token in ("://", "&&", "||", ";", "|", ">", "<")):
            raise ValueError("Shell or network-like arguments are forbidden")
        if ("/" in arg or "\\" in arg) and not arg.startswith("["):
            workspace.resolve_for_tool(arg)
    return argv


def _run_process(
    workspace: Workspace, argv: list[str], timeout: int, max_chars: int
) -> dict[str, Any]:
    if timeout < 1 or timeout > 300:
        return _error("invalid_timeout", "timeout must be between 1 and 300 seconds")
    started = time.monotonic()
    try:
        completed = subprocess.run(
            argv,
            cwd=workspace.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env=_safe_environment(),
        )
    except subprocess.TimeoutExpired as exc:
        return _error("command_timeout", f"Command timed out after {timeout}s: {exc.cmd}")
    except OSError as exc:
        return _error("command_failed", str(exc))
    stdout = completed.stdout
    stderr = completed.stderr
    truncated = len(stdout) + len(stderr) > max_chars
    if truncated:
        stdout_budget = max_chars * 3 // 4
        stderr_budget = max_chars - stdout_budget
        stdout = stdout[:stdout_budget] + "\n... stdout truncated ..."
        stderr = stderr[:stderr_budget] + "\n... stderr truncated ..."
    data = {
        "argv": argv,
        "exit_code": completed.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "duration_ms": round((time.monotonic() - started) * 1_000),
    }
    return _ok(data, truncated=truncated)


def _safe_environment() -> dict[str, str]:
    blocked = ("KEY", "TOKEN", "SECRET", "PASSWORD", "DEEPSEEK", "MINICODE")
    return {
        name: value
        for name, value in os.environ.items()
        if not any(part in name.upper() for part in blocked)
        and not name.upper().startswith("GIT_CONFIG_")
    }


def _session_diff(workspace: Workspace, state: CodingToolState) -> str:
    chunks: list[str] = []
    for relative in sorted(state.changed_files):
        before = state.baselines.get(relative)
        path = workspace.resolve_for_tool(relative)
        after = path.read_text(encoding="utf-8") if path.exists() else None
        before_lines = [] if before is None else before.splitlines(keepends=True)
        after_lines = [] if after is None else after.splitlines(keepends=True)
        chunks.extend(
            difflib.unified_diff(
                before_lines,
                after_lines,
                fromfile="/dev/null" if before is None else f"a/{relative}",
                tofile="/dev/null" if after is None else f"b/{relative}",
            )
        )
    return "".join(chunks)


def _clip(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit] + "\n... output truncated ...", True


def _detect_test_framework(workspace: Workspace, requested: str) -> str:
    if requested != "auto":
        return requested
    pytest_markers = ["pytest.ini", "conftest.py"]
    if any((workspace.root / marker).exists() for marker in pytest_markers):
        return "pytest"
    pyproject = workspace.root / "pyproject.toml"
    if pyproject.is_file() and "pytest" in pyproject.read_text(encoding="utf-8", errors="ignore"):
        return "pytest"
    return "unittest"
