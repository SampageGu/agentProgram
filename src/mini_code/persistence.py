"""SQLite run state, JSON checkpoints, artifacts, and resume validation."""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from collections.abc import Iterator
from typing import Any

from mini_code.workspace import Workspace


class PersistenceError(RuntimeError):
    pass


class WorkspaceConflictError(PersistenceError):
    pass


@dataclass
class RunSnapshot:
    run_id: str
    task: str
    status: str
    step: int
    max_steps: int
    messages: list[dict[str, Any]]
    tool_state: dict[str, Any]
    used_tool: bool
    successful_tools: list[str]
    workspace_hash: str
    trace_file: str
    checkpoint_sequence: int = 0
    updated_at: str = ""


class RunStore:
    SCHEMA_VERSION = 1

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace
        self.runtime_dir = workspace.root / ".mini-code"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.database = self.runtime_dir / "state.db"
        self._initialize()

    def save_checkpoint(self, snapshot: RunSnapshot) -> Path:
        _validate_run_id(snapshot.run_id)
        snapshot.workspace_hash = self.workspace.snapshot_hash()
        snapshot.updated_at = datetime.now(UTC).isoformat(timespec="milliseconds")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) FROM checkpoints WHERE run_id = ?",
                (snapshot.run_id,),
            ).fetchone()
            sequence = int(row[0]) + 1
            snapshot.checkpoint_sequence = sequence
            payload = json.dumps(asdict(snapshot), ensure_ascii=False)
            connection.execute(
                """
                INSERT INTO runs(
                    run_id, task, status, step, max_steps, workspace_hash,
                    trace_file, snapshot_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    task=excluded.task, status=excluded.status, step=excluded.step,
                    max_steps=excluded.max_steps, workspace_hash=excluded.workspace_hash,
                    trace_file=excluded.trace_file, snapshot_json=excluded.snapshot_json,
                    updated_at=excluded.updated_at
                """,
                (
                    snapshot.run_id,
                    snapshot.task,
                    snapshot.status,
                    snapshot.step,
                    snapshot.max_steps,
                    snapshot.workspace_hash,
                    snapshot.trace_file,
                    payload,
                    snapshot.updated_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO checkpoints(run_id, sequence, created_at, workspace_hash, snapshot_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (snapshot.run_id, sequence, snapshot.updated_at, snapshot.workspace_hash, payload),
            )
        checkpoint = self.run_dir(snapshot.run_id) / "checkpoints" / f"{sequence:04d}.json"
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        temporary = checkpoint.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {"schema_version": self.SCHEMA_VERSION, **asdict(snapshot)},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(checkpoint)
        return checkpoint

    def load(self, run_id: str) -> RunSnapshot:
        _validate_run_id(run_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT snapshot_json FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise PersistenceError(f"Run not found: {run_id}")
        data = json.loads(row[0])
        return RunSnapshot(**data)

    def mark_status(self, run_id: str, status: str) -> None:
        snapshot = self.load(run_id)
        snapshot.status = status
        snapshot.updated_at = datetime.now(UTC).isoformat(timespec="milliseconds")
        payload = json.dumps(asdict(snapshot), ensure_ascii=False)
        with self._connect() as connection:
            connection.execute(
                "UPDATE runs SET status = ?, snapshot_json = ?, updated_at = ? WHERE run_id = ?",
                (status, payload, snapshot.updated_at, run_id),
            )

    def list_runs(self, limit: int = 100) -> list[RunSnapshot]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT snapshot_json FROM runs ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [RunSnapshot(**json.loads(row[0])) for row in rows]

    def assert_workspace_unchanged(self, snapshot: RunSnapshot) -> None:
        current = self.workspace.snapshot_hash()
        if current != snapshot.workspace_hash:
            raise WorkspaceConflictError(
                "Workspace changed after the latest checkpoint; resume was stopped to avoid "
                "applying stale patches. Restore the checkpoint state or start a new run."
            )

    def write_artifact(self, run_id: str, kind: str, content: str) -> str:
        _validate_run_id(run_id)
        safe_kind = re.sub(r"[^a-zA-Z0-9_-]+", "-", kind).strip("-") or "data"
        directory = self.run_dir(run_id) / "artifacts"
        directory.mkdir(parents=True, exist_ok=True)
        sequence = len(list(directory.glob("*.txt"))) + 1
        path = directory / f"{sequence:04d}-{safe_kind}.txt"
        path.write_text(content, encoding="utf-8", newline="\n")
        return path.relative_to(self.workspace.root).as_posix()

    def run_dir(self, run_id: str) -> Path:
        _validate_run_id(run_id)
        return self.runtime_dir / "runs" / run_id

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs(
                    run_id TEXT PRIMARY KEY,
                    task TEXT NOT NULL,
                    status TEXT NOT NULL,
                    step INTEGER NOT NULL,
                    max_steps INTEGER NOT NULL,
                    workspace_hash TEXT NOT NULL,
                    trace_file TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS checkpoints(
                    run_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    workspace_hash TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    PRIMARY KEY(run_id, sequence),
                    FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE
                );
                """
            )


def _validate_run_id(run_id: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id):
        raise PersistenceError(f"Invalid run id: {run_id!r}")
