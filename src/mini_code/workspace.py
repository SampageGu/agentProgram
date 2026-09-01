"""Workspace boundary and repository map helpers."""

from __future__ import annotations

import os
import hashlib
from pathlib import Path


IGNORED_NAMES = {
    ".git",
    ".env",
    ".env.local",
    ".env.production",
    ".mini-code",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".coverage",
    "node_modules",
    "dist",
    "build",
}


class WorkspaceError(ValueError):
    pass


class Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve(strict=True)
        if not self.root.is_dir():
            raise WorkspaceError(f"Workspace is not a directory: {self.root}")

    def resolve(self, relative_path: str = ".") -> Path:
        candidate = (self.root / relative_path).resolve(strict=False)
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise WorkspaceError(f"Path escapes workspace: {relative_path}") from exc
        return candidate

    def resolve_for_tool(self, relative_path: str = ".") -> Path:
        """Resolve a model-provided path and reject runtime or secret locations."""
        candidate = self.resolve(relative_path)
        relative_parts = candidate.relative_to(self.root).parts
        blocked = next((part for part in relative_parts if _is_blocked_part(part)), None)
        if blocked is not None:
            raise WorkspaceError(f"Tool access to protected path is forbidden: {blocked}")
        return candidate

    def relative(self, path: Path) -> str:
        return path.resolve(strict=False).relative_to(self.root).as_posix()

    def repository_map(self, max_depth: int = 2, max_entries: int = 200) -> str:
        lines: list[str] = []
        for current, dirs, files in os.walk(self.root):
            current_path = Path(current)
            depth = len(current_path.relative_to(self.root).parts)
            dirs[:] = sorted(d for d in dirs if d not in IGNORED_NAMES)
            if depth >= max_depth:
                dirs[:] = []
            indent = "  " * depth
            if depth == 0:
                lines.append(f"{self.root.name}/")
            for name in sorted(files):
                if name in IGNORED_NAMES:
                    continue
                lines.append(f"{indent}  {name}")
                if len(lines) >= max_entries:
                    lines.append("... repository map truncated ...")
                    return "\n".join(lines)
        return "\n".join(lines)

    def snapshot_hash(self) -> str:
        """Hash relevant workspace contents for resume conflict detection."""

        digest = hashlib.sha256()
        for path in sorted(self.root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(self.root)
            if any(_is_blocked_part(part) for part in relative.parts):
                continue
            try:
                size = path.stat().st_size
                digest.update(relative.as_posix().encode("utf-8"))
                digest.update(str(size).encode("ascii"))
                if size <= 2_000_000:
                    digest.update(path.read_bytes())
                else:
                    digest.update(str(path.stat().st_mtime_ns).encode("ascii"))
            except OSError:
                continue
        return digest.hexdigest()


def _is_blocked_part(name: str) -> bool:
    lowered = name.lower()
    return lowered in IGNORED_NAMES or lowered.startswith(".env.")
