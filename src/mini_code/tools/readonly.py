"""Safe read-only tools exposed to the model during M0."""

from __future__ import annotations

import fnmatch
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from mini_code.workspace import IGNORED_NAMES, Workspace, WorkspaceError


ToolHandler = Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler

    def api_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._observers: list[Callable[[str, dict[str, Any], dict[str, Any]], None]] = []

    def register(self, definition: ToolDefinition) -> None:
        if definition.name in self._tools:
            raise ValueError(f"Duplicate tool: {definition.name}")
        self._tools[definition.name] = definition

    def schemas(self) -> list[dict[str, Any]]:
        return [definition.api_schema() for definition in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools)

    def add_observer(
        self,
        observer: Callable[[str, dict[str, Any], dict[str, Any]], None],
    ) -> None:
        self._observers.append(observer)

    def execute(self, name: str, arguments: str | dict[str, Any]) -> dict[str, Any]:
        definition = self._tools.get(name)
        if definition is None:
            return _error("tool_not_found", f"Unknown tool: {name}")
        try:
            parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
            if not isinstance(parsed, dict):
                return _error("invalid_arguments", "Tool arguments must be a JSON object")
            result = definition.handler(**parsed)
            for observer in self._observers:
                observer(name, parsed, result)
            return result
        except json.JSONDecodeError as exc:
            return _error("invalid_json", str(exc))
        except TypeError as exc:
            return _error("invalid_arguments", str(exc))
        except (OSError, WorkspaceError, UnicodeError) as exc:
            return _error(type(exc).__name__, str(exc))


def build_readonly_registry(workspace: Workspace, max_chars: int = 12_000) -> ToolRegistry:
    registry = ToolRegistry()

    def list_files(path: str = ".", depth: int = 2) -> dict[str, Any]:
        if depth < 0 or depth > 5:
            return _error("invalid_depth", "depth must be between 0 and 5")
        base = workspace.resolve_for_tool(path)
        if not base.exists():
            return _error("not_found", f"Path does not exist: {path}")
        if not base.is_dir():
            return _error("not_directory", f"Path is not a directory: {path}")
        entries: list[dict[str, Any]] = []
        for current, dirs, files in os.walk(base):
            current_path = Path(current)
            current_depth = len(current_path.relative_to(base).parts)
            dirs[:] = sorted(d for d in dirs if d not in IGNORED_NAMES)
            if current_depth >= depth:
                dirs[:] = []
            for directory in dirs:
                entries.append(
                    {"path": workspace.relative(current_path / directory), "type": "directory"}
                )
            for filename in sorted(files):
                if filename in IGNORED_NAMES:
                    continue
                file_path = current_path / filename
                entries.append(
                    {
                        "path": workspace.relative(file_path),
                        "type": "file",
                        "size": file_path.stat().st_size,
                    }
                )
            if len(entries) >= 500:
                return _ok(entries[:500], truncated=True)
        return _ok(entries)

    def read_file(path: str, start_line: int = 1, end_line: int = 200) -> dict[str, Any]:
        if start_line < 1 or end_line < start_line:
            return _error("invalid_range", "Require 1 <= start_line <= end_line")
        if end_line - start_line > 499:
            return _error("range_too_large", "At most 500 lines can be read at once")
        file_path = workspace.resolve_for_tool(path)
        if not file_path.is_file():
            return _error("not_file", f"File does not exist: {path}")
        if file_path.stat().st_size > 2_000_000:
            return _error("file_too_large", "M0 refuses files larger than 2 MB")
        raw = file_path.read_bytes()
        if b"\x00" in raw[:8192]:
            return _error("binary_file", "M0 only reads text files")
        text = raw.decode("utf-8")
        lines = text.splitlines()
        selected = lines[start_line - 1 : end_line]
        numbered = "\n".join(
            f"{number}: {content}"
            for number, content in enumerate(selected, start=start_line)
        )
        truncated = len(numbered) > max_chars
        if truncated:
            numbered = numbered[:max_chars] + "\n... output truncated ..."
        return _ok(
            {
                "path": workspace.relative(file_path),
                "start_line": start_line,
                "end_line": min(end_line, len(lines)),
                "total_lines": len(lines),
                "content": numbered,
            },
            truncated=truncated,
        )

    def search_code(
        query: str,
        path: str = ".",
        glob: str = "",
        max_results: int = 50,
    ) -> dict[str, Any]:
        if not query:
            return _error("empty_query", "query must not be empty")
        if max_results < 1 or max_results > 200:
            return _error("invalid_limit", "max_results must be between 1 and 200")
        base = workspace.resolve_for_tool(path)
        if not base.exists():
            return _error("not_found", f"Path does not exist: {path}")
        if shutil.which("rg"):
            return _search_with_rg(workspace, base, query, glob, max_results, max_chars)
        return _search_fallback(workspace, base, query, glob, max_results, max_chars)

    registry.register(
        ToolDefinition(
            name="list_files",
            description="List files and directories inside the workspace. Use it to explore structure.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "default": "."},
                    "depth": {"type": "integer", "minimum": 0, "maximum": 5, "default": 2},
                },
                "additionalProperties": False,
            },
            handler=list_files,
        )
    )
    registry.register(
        ToolDefinition(
            name="search_code",
            description=(
                "Search literal text in workspace files and return file paths, line numbers, and snippets."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "path": {"type": "string", "default": "."},
                    "glob": {"type": "string", "default": ""},
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 200,
                        "default": 50,
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            handler=search_code,
        )
    )
    registry.register(
        ToolDefinition(
            name="read_file",
            description="Read a UTF-8 text file by line range. Line numbers are included in the result.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer", "minimum": 1, "default": 1},
                    "end_line": {"type": "integer", "minimum": 1, "default": 200},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            handler=read_file,
        )
    )
    return registry


def _search_with_rg(
    workspace: Workspace,
    base: Path,
    query: str,
    glob: str,
    max_results: int,
    max_chars: int,
) -> dict[str, Any]:
    command = [
        "rg",
        "-n",
        "--no-heading",
        "--color",
        "never",
        "--fixed-strings",
        "--max-count",
        str(max_results),
    ]
    for ignored in sorted(IGNORED_NAMES):
        command.extend(["-g", f"!{ignored}/**"])
    if glob:
        command.extend(["-g", glob])
    search_target = "." if base == workspace.root else workspace.relative(base)
    command.extend(["--", query, search_target])
    completed = subprocess.run(
        command,
        cwd=workspace.root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
    )
    if completed.returncode not in (0, 1):
        return _error("rg_failed", completed.stderr.strip() or f"rg exited {completed.returncode}")
    matches: list[dict[str, Any]] = []
    for line in completed.stdout.splitlines()[:max_results]:
        file_part, separator, remainder = line.partition(":")
        line_part, separator2, snippet = remainder.partition(":")
        if not separator or not separator2:
            continue
        file_path = Path(file_part)
        if not file_path.is_absolute():
            file_path = workspace.root / file_path
        try:
            relative = workspace.relative(file_path)
        except ValueError:
            continue
        matches.append({"path": relative, "line": int(line_part), "text": snippet[:500]})
    result = _ok(matches, truncated=len(completed.stdout) > max_chars)
    return _limit_json(result, max_chars)


def _search_fallback(
    workspace: Workspace,
    base: Path,
    query: str,
    glob: str,
    max_results: int,
    max_chars: int,
) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    paths = [base] if base.is_file() else base.rglob("*")
    for path in paths:
        if not path.is_file() or any(part in IGNORED_NAMES for part in path.parts):
            continue
        relative = workspace.relative(path)
        if glob and not fnmatch.fnmatch(relative, glob):
            continue
        try:
            if path.stat().st_size > 2_000_000:
                continue
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if query in line:
                    matches.append({"path": relative, "line": line_number, "text": line[:500]})
                    if len(matches) >= max_results:
                        return _limit_json(_ok(matches, truncated=True), max_chars)
        except (OSError, UnicodeError):
            continue
    return _limit_json(_ok(matches), max_chars)


def _ok(data: Any, truncated: bool = False) -> dict[str, Any]:
    return {"ok": True, "data": data, "error": None, "metadata": {"truncated": truncated}}


def _error(code: str, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "data": None,
        "error": {"code": code, "message": message},
        "metadata": {"truncated": False},
    }


def _limit_json(result: dict[str, Any], max_chars: int) -> dict[str, Any]:
    serialized = json.dumps(result, ensure_ascii=False)
    if len(serialized) <= max_chars:
        return result
    data = result.get("data")
    if isinstance(data, list):
        while data and len(json.dumps(result, ensure_ascii=False)) > max_chars:
            data.pop()
        result["metadata"]["truncated"] = True
    return result
