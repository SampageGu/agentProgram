"""Durable M4.1 conversation metadata stored beside run checkpoints."""

from __future__ import annotations

import re
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from collections.abc import Iterator

from mini_code.persistence import PersistenceError
from mini_code.workspace import Workspace


@dataclass(frozen=True)
class ConversationRun:
    run_id: str
    sequence: int
    user_message: str
    created_at: str


@dataclass(frozen=True)
class Conversation:
    conversation_id: str
    title: str
    created_at: str
    updated_at: str
    runs: list[ConversationRun]


class ConversationStore:
    """Small SQLite repository for Conversation -> Run relationships."""

    def __init__(self, workspace: Workspace) -> None:
        runtime_dir = workspace.root / ".mini-code"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        self.database = runtime_dir / "state.db"
        self._initialize()

    def create(self, first_message: str) -> Conversation:
        message = _validate_message(first_message)
        conversation_id = uuid.uuid4().hex[:12]
        now = _now()
        title = _title(message)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO conversations(conversation_id, title, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (conversation_id, title, now, now),
            )
        return Conversation(conversation_id, title, now, now, [])

    def add_run(self, conversation_id: str, run_id: str, user_message: str) -> None:
        _validate_conversation_id(conversation_id)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id):
            raise PersistenceError(f"Invalid run id: {run_id!r}")
        message = _validate_message(user_message)
        now = _now()
        with self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM conversations WHERE conversation_id = ?", (conversation_id,)
            ).fetchone()
            if exists is None:
                raise PersistenceError(f"Conversation not found: {conversation_id}")
            row = connection.execute(
                """
                SELECT COALESCE(MAX(sequence), 0)
                FROM conversation_runs WHERE conversation_id = ?
                """,
                (conversation_id,),
            ).fetchone()
            sequence = int(row[0]) + 1
            connection.execute(
                """
                INSERT INTO conversation_runs(
                    conversation_id, run_id, sequence, user_message, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (conversation_id, run_id, sequence, message, now),
            )
            connection.execute(
                "UPDATE conversations SET updated_at = ? WHERE conversation_id = ?",
                (now, conversation_id),
            )

    def delete_if_empty(self, conversation_id: str) -> None:
        """Remove a conversation whose first run could not be started."""
        _validate_conversation_id(conversation_id)
        with self._connect() as connection:
            connection.execute(
                """
                DELETE FROM conversations
                WHERE conversation_id = ? AND NOT EXISTS(
                    SELECT 1 FROM conversation_runs WHERE conversation_id = ?
                )
                """,
                (conversation_id, conversation_id),
            )

    def get(self, conversation_id: str) -> Conversation:
        _validate_conversation_id(conversation_id)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT conversation_id, title, created_at, updated_at
                FROM conversations WHERE conversation_id = ?
                """,
                (conversation_id,),
            ).fetchone()
            if row is None:
                raise PersistenceError(f"Conversation not found: {conversation_id}")
            runs = connection.execute(
                """
                SELECT run_id, sequence, user_message, created_at
                FROM conversation_runs
                WHERE conversation_id = ? ORDER BY sequence ASC
                """,
                (conversation_id,),
            ).fetchall()
        return Conversation(
            conversation_id=row[0],
            title=row[1],
            created_at=row[2],
            updated_at=row[3],
            runs=[ConversationRun(*item) for item in runs],
        )

    def list(self, limit: int = 100) -> list[Conversation]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        with self._connect() as connection:
            ids = connection.execute(
                """
                SELECT conversation_id FROM conversations
                ORDER BY updated_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self.get(row[0]) for row in ids]

    def to_dict(self, conversation: Conversation) -> dict[str, object]:
        return {
            **asdict(conversation),
            "message_count": len(conversation.runs),
            "last_message": conversation.runs[-1].user_message if conversation.runs else "",
        }

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database, timeout=10)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations(
                    conversation_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversation_runs(
                    conversation_id TEXT NOT NULL,
                    run_id TEXT NOT NULL UNIQUE,
                    sequence INTEGER NOT NULL,
                    user_message TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(conversation_id, sequence),
                    FOREIGN KEY(conversation_id)
                        REFERENCES conversations(conversation_id) ON DELETE CASCADE
                );
                """
            )
            connection.execute("PRAGMA optimize")


def _validate_conversation_id(conversation_id: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", conversation_id):
        raise PersistenceError(f"Invalid conversation id: {conversation_id!r}")


def _validate_message(message: str) -> str:
    value = message.strip()
    if not value:
        raise PersistenceError("message must not be empty")
    if len(value) > 8_000:
        raise PersistenceError("message is too long (maximum 8000 characters)")
    return value


def _title(message: str) -> str:
    first_line = " ".join(message.splitlines()[0].split())
    return first_line[:42] + ("…" if len(first_line) > 42 else "")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")
