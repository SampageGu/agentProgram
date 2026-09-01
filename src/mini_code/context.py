"""Deterministic M2 context compaction and artifact offloading."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from mini_code.persistence import RunStore
from mini_code.tools.coding import CodingToolState


STATE_MARKER = "\n\n[M2 durable state]\n"


class ContextManager:
    def __init__(
        self,
        store: RunStore,
        run_id: str,
        max_chars: int = 50_000,
        artifact_threshold: int = 6_000,
    ) -> None:
        self.store = store
        self.run_id = run_id
        self.max_chars = max_chars
        self.artifact_threshold = artifact_threshold

    def prepare_tool_result(self, name: str, result: dict[str, Any]) -> str:
        serialized = json.dumps(result, ensure_ascii=False)
        if len(serialized) <= self.artifact_threshold:
            return serialized
        artifact_ref = self.store.write_artifact(self.run_id, f"tool-{name}", serialized)
        compact = _compact_tool_result(result, artifact_ref)
        return json.dumps(compact, ensure_ascii=False)

    def refresh_durable_state(
        self,
        messages: list[dict[str, Any]],
        state: CodingToolState | None,
    ) -> None:
        if not messages or messages[0].get("role") != "system" or state is None:
            return
        base = str(messages[0].get("content", "")).split(STATE_MARKER, 1)[0]
        failed_checks = [check for check in state.checks if not check.get("passed")]
        durable = {
            "plan": state.plan,
            "changed_files": sorted(state.changed_files),
            "revision": state.revision,
            "latest_test_passed": state.successful_test_revision == state.revision,
            "latest_diff_viewed": state.diff_revision == state.revision,
            "last_failed_check": failed_checks[-1] if failed_checks else None,
            "diff_summary": state.last_diff[:1_500],
            "delegations": [
                {
                    "child_run_id": item.get("child_run_id"),
                    "status": item.get("status"),
                    "question": str(item.get("question", ""))[:300],
                    "total_tokens": item.get("total_tokens"),
                }
                for item in state.delegations
            ],
            "verified_subagent_evidence": sorted(state.verified_evidence_files),
        }
        messages[0]["content"] = base + STATE_MARKER + json.dumps(durable, ensure_ascii=False)

    def compact(
        self,
        messages: list[dict[str, Any]],
        state: CodingToolState | None,
    ) -> dict[str, Any] | None:
        self.refresh_durable_state(messages, state)
        before = _message_chars(messages)
        if before <= self.max_chars:
            return None
        artifacts: list[str] = []
        protected_from = max(1, len(messages) - 6)
        for index in range(1, protected_from):
            artifacts.extend(self._compact_message(messages[index], index))
        after = _message_chars(messages)
        if after > self.max_chars:
            for index in range(protected_from, max(protected_from, len(messages) - 2)):
                artifacts.extend(self._compact_message(messages[index], index, aggressive=True))
            after = _message_chars(messages)
        if after >= before:
            return None
        return {
            "before_chars": before,
            "after_chars": after,
            "artifact_refs": artifacts,
        }

    def _compact_message(
        self,
        message: dict[str, Any],
        index: int,
        aggressive: bool = False,
    ) -> list[str]:
        refs: list[str] = []
        role = message.get("role")
        content = message.get("content")
        limit = 400 if aggressive else 900
        if isinstance(content, str) and len(content) > limit:
            ref = self.store.write_artifact(self.run_id, f"message-{index}-{role}", content)
            refs.append(ref)
            if role == "tool":
                message["content"] = json.dumps(
                    {
                        "compacted": True,
                        "summary": _head_tail(content, limit),
                        "artifact_ref": ref,
                    },
                    ensure_ascii=False,
                )
            elif role != "user":
                message["content"] = _head_tail(content, limit) + f"\n[artifact: {ref}]"
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list):
            copied = deepcopy(tool_calls)
            changed = False
            for call in copied:
                function = call.get("function") or {}
                arguments = function.get("arguments")
                if isinstance(arguments, str) and len(arguments) > limit:
                    ref = self.store.write_artifact(
                        self.run_id, f"message-{index}-tool-arguments", arguments
                    )
                    refs.append(ref)
                    function["arguments"] = json.dumps(
                        {"compacted": True, "artifact_ref": ref}, ensure_ascii=False
                    )
                    changed = True
            if changed:
                message["tool_calls"] = copied
        return refs


def _compact_tool_result(result: dict[str, Any], artifact_ref: str) -> dict[str, Any]:
    data = result.get("data")
    summary: dict[str, Any] = {}
    if isinstance(data, dict):
        for key in (
            "path", "start_line", "end_line", "total_lines", "exit_code",
            "passed", "revision", "changed_files", "status", "warning",
        ):
            if key in data:
                summary[key] = data[key]
        for key in ("stdout", "stderr", "content", "session_diff", "git_diff"):
            value = data.get(key)
            if isinstance(value, str) and value:
                summary[key] = _head_tail(value, 1_200)
        report = data.get("report")
        if isinstance(report, dict):
            evidence = report.get("evidence")
            summary.update({
                "parent_run_id": data.get("parent_run_id"),
                "child_run_id": data.get("child_run_id"),
                "status": data.get("status"),
                "steps": data.get("steps"),
                "total_tokens": data.get("total_tokens"),
                "verification_required": data.get("verification_required"),
                "report": {
                    "summary": str(report.get("summary", ""))[:1_500],
                    "evidence": evidence[:10] if isinstance(evidence, list) else [],
                    "suspected_root_cause": str(
                        report.get("suspected_root_cause", "")
                    )[:1_000],
                    "recommended_next_steps": list(
                        report.get("recommended_next_steps", [])
                    )[:10],
                    "risks": list(report.get("risks", []))[:10],
                },
            })
    elif isinstance(data, list):
        summary = {"items": data[:10], "total_items": len(data)}
    metadata = dict(result.get("metadata") or {})
    metadata.update({"truncated": True, "artifact_ref": artifact_ref})
    return {
        "ok": result.get("ok", False),
        "data": summary,
        "error": result.get("error"),
        "metadata": metadata,
    }


def _head_tail(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = limit * 2 // 3
    tail = limit - head
    return text[:head] + "\n... compacted ...\n" + text[-tail:]


def _message_chars(messages: list[dict[str, Any]]) -> int:
    return len(json.dumps(messages, ensure_ascii=False))
