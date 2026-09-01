"""Command-line entry point for MiniCode milestones M0 through M4."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from mini_code.agent import CodingAgent, ReadOnlyAgent
from mini_code.config import Settings
from mini_code.context import ContextManager
from mini_code.events import EventSink, normalize_terminal_text
from mini_code.eval_m0 import run_m0_eval
from mini_code.eval_m1 import run_m1_eval
from mini_code.eval_m3 import run_m3_eval
from mini_code.provider import DeepSeekProvider, FaultInjectingProvider
from mini_code.persistence import PersistenceError, RunStore, WorkspaceConflictError
from mini_code.replay import ReplayError, replay_trace
from mini_code.security import run_security_audit
from mini_code.subagents import ExploreSubagent
from mini_code.tools import (
    CodingToolState,
    ExploreDelegationConfig,
    build_coding_registry,
    build_readonly_registry,
)
from mini_code.workspace import Workspace, WorkspaceError
from mini_code.web import WebError, serve_web


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mini-code",
        description="MiniCode M5.2: a resumable parent Agent with controlled Explore delegation.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    ask = subparsers.add_parser("ask", help="Ask a question about a local repository")
    ask.add_argument("task", help="Natural-language repository question")
    ask.add_argument("--workspace", default=".", help="Repository root (default: current dir)")
    ask.add_argument("--env-file", default=".env", help="Path to .env containing DeepSeek key")
    ask.add_argument("--max-steps", type=int, default=None, help="Override Agent step budget")

    chat = subparsers.add_parser("chat", help="Start a multi-turn repository conversation")
    chat.add_argument("--workspace", default=".", help="Repository root (default: current dir)")
    chat.add_argument("--env-file", default=".env", help="Path to .env containing DeepSeek key")
    chat.add_argument("--max-steps", type=int, default=None, help="Agent step budget per turn")

    run = subparsers.add_parser("run", help="Run a checkpointed M2 coding task")
    run.add_argument("task", help="Natural-language coding task")
    run.add_argument("--workspace", default=".", help="Target repository root")
    run.add_argument("--env-file", default=".env", help="Path to .env containing DeepSeek key")
    run.add_argument("--max-steps", type=int, default=None, help="Override Agent step budget")
    run.add_argument(
        "--fail-model-call",
        type=int,
        default=None,
        help="Inject one provider failure for M3 recovery testing",
    )
    run.add_argument(
        "--no-subagents",
        action="store_true",
        help="Disable M5 delegation for a single-Agent comparison run",
    )

    resume = subparsers.add_parser("resume", help="Resume an interrupted M2 coding run")
    resume.add_argument("run_id", help="Run id from the interrupted coding task")
    resume.add_argument("--workspace", default=".", help="Original repository root")
    resume.add_argument("--env-file", default=".env", help="Path to .env containing DeepSeek key")
    resume.add_argument("--max-steps", type=int, default=None, help="New total Agent step budget")
    resume.add_argument("--no-subagents", action="store_true", help="Resume without new delegation")

    status = subparsers.add_parser("status", help="Show the latest persisted run state")
    status.add_argument("run_id", help="Coding run id")
    status.add_argument("--workspace", default=".", help="Original repository root")

    replay = subparsers.add_parser("replay", help="Replay a run timeline and print metrics")
    replay.add_argument("run_id", help="Run id")
    replay.add_argument("--workspace", default=".", help="Original repository root")
    replay.add_argument("--json", action="store_true", help="Print machine-readable replay JSON")

    security = subparsers.add_parser("security-check", help="Run the offline M3 policy probes")
    security.add_argument("--workspace", default=".", help="Repository root")

    demo = subparsers.add_parser("demo-m1", help="Copy and run the disposable M1 bug-fix demo")
    demo.add_argument("--env-file", default=".env", help="Path to .env containing DeepSeek key")
    demo.add_argument("--max-steps", type=int, default=None, help="Override Agent step budget")
    demo.add_argument("--expect-pause", action="store_true", help=argparse.SUPPRESS)

    trace = subparsers.add_parser("trace", help="Print a saved JSONL run trace")
    trace.add_argument("run_id", help="Run id shown by the ask command")
    trace.add_argument("--workspace", default=".", help="Repository root")

    evaluate = subparsers.add_parser("eval-m0", help="Run the 5-case DeepSeek M0 acceptance suite")
    evaluate.add_argument("--workspace", default=".", help="MiniCode repository root")
    evaluate.add_argument("--env-file", default=".env", help="Path to .env containing DeepSeek key")
    evaluate.add_argument(
        "--tasks",
        default="evals/tasks/m0.json",
        help="M0 task definition file",
    )
    evaluate_m1 = subparsers.add_parser("eval-m1", help="Run the 3-case DeepSeek M1 coding suite")
    evaluate_m1.add_argument("--workspace", default=".", help="MiniCode repository root")
    evaluate_m1.add_argument("--env-file", default=".env", help="Path to .env containing DeepSeek key")
    evaluate_m1.add_argument("--tasks", default="evals/tasks/m1.json", help="M1 task definition file")
    evaluate_m3 = subparsers.add_parser("eval-m3", help="Run the 20-case M3 coding benchmark")
    evaluate_m3.add_argument("--workspace", default=".", help="MiniCode repository root")
    evaluate_m3.add_argument("--env-file", default=".env", help="Path to .env containing DeepSeek key")
    evaluate_m3.add_argument("--tasks", default="evals/tasks/m3.json", help="M3 task definition file")
    evaluate_m3.add_argument("--limit", type=int, default=None, help="Run only the first N cases")
    web = subparsers.add_parser("web", help="Start the local M4 REST/SSE console")
    web.add_argument("--workspace", default=".", help="Fixed target repository root")
    web.add_argument("--env-file", default=".env", help="Path to .env containing DeepSeek key")
    web.add_argument("--host", default="127.0.0.1", help="Localhost address")
    web.add_argument("--port", type=int, default=8765, help="Listening port")
    web.add_argument("--demo", action="store_true", help="Serve a disposable bug-fix workspace")
    explore = subparsers.add_parser("explore", help="Run the standalone M5.1 Explore Subagent")
    explore.add_argument("task", help="Bounded repository investigation")
    explore.add_argument("--workspace", default=".", help="Repository root")
    explore.add_argument("--env-file", default=".env", help="Path to .env containing DeepSeek key")
    explore.add_argument("--max-steps", type=int, default=8, help="Child step budget (1-8)")
    explore.add_argument("--parent-run-id", default=None, help="Optional parent Run id")
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "trace":
        _print_trace(Path(args.workspace), args.run_id)
        return
    if args.command == "eval-m0":
        raise SystemExit(_run_eval(args))
    if args.command == "eval-m1":
        raise SystemExit(_run_eval_m1(args))
    if args.command == "eval-m3":
        raise SystemExit(_run_eval_m3(args))
    if args.command == "web":
        raise SystemExit(_run_web(args))
    if args.command == "explore":
        raise SystemExit(_run_explore(args))
    if args.command == "chat":
        raise SystemExit(_run_chat(args))
    if args.command == "run":
        raise SystemExit(_run_coding_task(args))
    if args.command == "resume":
        raise SystemExit(_run_resume(args))
    if args.command == "status":
        raise SystemExit(_run_status(args))
    if args.command == "replay":
        raise SystemExit(_run_replay(args))
    if args.command == "security-check":
        raise SystemExit(_run_security_check(args))
    if args.command == "demo-m1":
        raise SystemExit(_run_m1_demo(args))
    exit_code = _run_ask(args)
    raise SystemExit(exit_code)


def _run_ask(args: argparse.Namespace) -> int:
    try:
        workspace = Workspace(Path(args.workspace))
        env_file = Path(args.env_file)
        settings = Settings.from_env(env_file)
        max_steps = args.max_steps or settings.max_steps
        if max_steps <= 0:
            raise ValueError("--max-steps must be positive")
    except (ValueError, WorkspaceError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    run_id = _new_run_id()
    trace_file = workspace.root / ".mini-code" / "runs" / run_id / "events.jsonl"
    sink = EventSink(trace_file)
    agent = ReadOnlyAgent(
        provider=DeepSeekProvider(settings),
        workspace=workspace,
        tools=build_readonly_registry(workspace, settings.max_tool_chars),
        sink=sink,
        max_steps=max_steps,
    )
    result = agent.run(args.task, run_id=run_id)
    print(f"\nRun ID: {result.run_id}")
    print(f"Trace: {result.trace_file}")
    return 0 if result.status == "completed" else 1


def _run_chat(args: argparse.Namespace) -> int:
    try:
        workspace = Workspace(Path(args.workspace))
        settings = Settings.from_env(Path(args.env_file))
        max_steps = args.max_steps or settings.max_steps
        if max_steps <= 0:
            raise ValueError("--max-steps must be positive")
    except (ValueError, WorkspaceError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    provider = DeepSeekProvider(settings)
    tools = build_readonly_registry(workspace, settings.max_tool_chars)
    history: list[dict[str, object]] = []
    print("MiniCode M0.1 multi-turn chat")
    print("Commands: /help  /history  /clear  /exit")

    while True:
        try:
            task = input("\nMiniCode> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nSession ended.")
            return 0
        if not task:
            continue
        if task == "/exit":
            print("Session ended.")
            return 0
        if task == "/help":
            _print_chat_help()
            continue
        if task == "/history":
            _print_chat_history(history)
            continue
        if task == "/clear":
            history.clear()
            print("Conversation context cleared.")
            continue
        if task.startswith("/"):
            print(f"Unknown command: {task}. Use /help.")
            continue

        run_id = _new_run_id()
        trace_file = workspace.root / ".mini-code" / "runs" / run_id / "events.jsonl"
        agent = ReadOnlyAgent(
            provider=provider,
            workspace=workspace,
            tools=tools,
            sink=EventSink(trace_file),
            max_steps=max_steps,
        )
        result = agent.run(task, run_id=run_id, history=history)
        if result.status == "completed":
            history = result.history
        print(f"Run ID: {result.run_id}")
        print(f"Trace: {result.trace_file}")
        if result.status != "completed":
            print(f"Turn status: {result.status}; prior context was kept.")


def _run_coding_task(args: argparse.Namespace) -> int:
    try:
        workspace = Workspace(Path(args.workspace))
        settings = Settings.from_env(Path(args.env_file))
        max_steps = args.max_steps or max(settings.max_steps, 16)
        if max_steps <= 0:
            raise ValueError("--max-steps must be positive")
    except (ValueError, WorkspaceError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    run_id = _new_run_id()
    trace_file = workspace.root / ".mini-code" / "runs" / run_id / "events.jsonl"
    tools = build_coding_registry(
        workspace,
        max_chars=settings.max_tool_chars,
        command_timeout=settings.request_timeout,
        delegation=(
            None
            if getattr(args, "no_subagents", False)
            else ExploreDelegationConfig(
                parent_run_id=run_id,
                provider_factory=lambda: DeepSeekProvider(settings),
            )
        ),
    )
    store = RunStore(workspace)
    context = ContextManager(
        store,
        run_id,
        max_chars=settings.max_context_chars,
        artifact_threshold=settings.artifact_threshold,
    )
    result = CodingAgent(
        provider=_coding_provider(settings, getattr(args, "fail_model_call", None)),
        workspace=workspace,
        tools=tools,
        sink=EventSink(trace_file),
        max_steps=max_steps,
        run_store=store,
        context_manager=context,
    ).run(args.task, run_id=run_id)
    print(f"\nRun ID: {result.run_id}")
    print(f"Trace: {result.trace_file}")
    if result.status == "budget_exhausted" and getattr(args, "allow_budget_exhausted", False):
        print("Expected M2 pause reached. Use status and resume with the Run ID and workspace above.")
        return 0
    return 0 if result.status == "completed" else 1


def _run_resume(args: argparse.Namespace) -> int:
    try:
        workspace = Workspace(Path(args.workspace))
        settings = Settings.from_env(Path(args.env_file))
        store = RunStore(workspace)
        snapshot = store.load(args.run_id)
        if snapshot.status == "completed":
            raise PersistenceError(f"Run {args.run_id} is already completed")
        store.assert_workspace_unchanged(snapshot)
        max_steps = args.max_steps or max(snapshot.max_steps + 8, snapshot.step + 1)
        if max_steps <= snapshot.step:
            raise ValueError(
                f"--max-steps must be greater than saved step {snapshot.step}"
            )
    except WorkspaceConflictError as exc:
        print(f"Resume conflict: {exc}", file=sys.stderr)
        return 3
    except (ValueError, WorkspaceError, PersistenceError) as exc:
        print(f"Resume error: {exc}", file=sys.stderr)
        return 2

    trace_file = store.run_dir(snapshot.run_id) / "events.jsonl"
    tools = build_coding_registry(
        workspace,
        max_chars=settings.max_tool_chars,
        command_timeout=settings.request_timeout,
        initial_state=CodingToolState.from_dict(snapshot.tool_state),
        delegation=(
            None
            if getattr(args, "no_subagents", False)
            else ExploreDelegationConfig(
                parent_run_id=snapshot.run_id,
                provider_factory=lambda: DeepSeekProvider(settings),
            )
        ),
    )
    context = ContextManager(
        store,
        snapshot.run_id,
        max_chars=settings.max_context_chars,
        artifact_threshold=settings.artifact_threshold,
    )
    result = CodingAgent(
        provider=DeepSeekProvider(settings),
        workspace=workspace,
        tools=tools,
        sink=EventSink(trace_file),
        max_steps=max_steps,
        run_store=store,
        context_manager=context,
    ).run(snapshot.task, run_id=snapshot.run_id, resume_snapshot=snapshot)
    print(f"\nRun ID: {result.run_id}")
    print(f"Trace: {result.trace_file}")
    return 0 if result.status == "completed" else 1


def _run_status(args: argparse.Namespace) -> int:
    try:
        workspace = Workspace(Path(args.workspace))
        snapshot = RunStore(workspace).load(args.run_id)
    except (ValueError, WorkspaceError, PersistenceError) as exc:
        print(f"Status error: {exc}", file=sys.stderr)
        return 2
    state = CodingToolState.from_dict(snapshot.tool_state)
    print(f"Run ID: {snapshot.run_id}")
    print(f"Status: {snapshot.status}")
    print(f"Step: {snapshot.step}/{snapshot.max_steps}")
    print(f"Checkpoint: {snapshot.checkpoint_sequence}")
    print(f"Updated: {snapshot.updated_at}")
    print(f"Changed files: {', '.join(sorted(state.changed_files)) or '(none)'}")
    if state.plan:
        print("Plan:")
        for item in state.plan:
            print(f"  [{item['status']}] {item['step']}")
    return 0


def _run_replay(args: argparse.Namespace) -> int:
    try:
        workspace = Workspace(Path(args.workspace))
        trace_file = workspace.resolve(f".mini-code/runs/{args.run_id}/events.jsonl")
        replay_trace(trace_file, sys.stdout, as_json=args.json)
    except (ValueError, WorkspaceError, ReplayError) as exc:
        print(f"Replay error: {exc}", file=sys.stderr)
        return 2
    return 0


def _run_security_check(args: argparse.Namespace) -> int:
    try:
        workspace = Workspace(Path(args.workspace))
        report_file, results = run_security_audit(workspace)
    except (ValueError, WorkspaceError) as exc:
        print(f"Security audit error: {exc}", file=sys.stderr)
        return 2
    blocked = sum(result.blocked for result in results)
    for result in results:
        print(
            f"{'BLOCK' if result.blocked else 'FAIL ':<5} "
            f"{result.probe_id:<24} {result.error_code}"
        )
    print(f"\nM3 security result: {blocked}/{len(results)} blocked")
    print(f"Report: {report_file}")
    return 0 if blocked == len(results) else 1


def _run_m1_demo(args: argparse.Namespace) -> int:
    project_root = Path(__file__).resolve().parents[2]
    template = project_root / "examples" / "m1_zero_division"
    if not template.is_dir():
        print(f"Demo template not found: {template}", file=sys.stderr)
        return 2
    milestone = "m2" if args.expect_pause else "m1"
    demo_id = f"{milestone}-{_new_run_id()}"
    target = project_root / ".mini-code" / "demos" / demo_id
    shutil.copytree(template, target)
    print(f"Disposable demo workspace: {target}")
    coding_args = argparse.Namespace(
        task=(
            "修复 calculator.py 中除数为零时崩溃的问题，使 safe_divide 返回 None；"
            "先检查实现和测试，完成修改、测试与 Diff 验证。"
        ),
        workspace=str(target),
        env_file=args.env_file,
        max_steps=args.max_steps,
        allow_budget_exhausted=args.expect_pause,
    )
    return _run_coding_task(coding_args)


def _print_chat_help() -> None:
    print("/help     Show commands")
    print("/history  Show user and assistant messages in this process")
    print("/clear    Clear in-process conversation context")
    print("/exit     End the session")


def _print_chat_history(history: list[dict[str, object]]) -> None:
    visible = [
        message
        for message in history
        if message.get("role") in {"user", "assistant"} and message.get("content")
    ]
    if not visible:
        print("No conversation history.")
        return
    for index, message in enumerate(visible, start=1):
        role = "You" if message["role"] == "user" else "MiniCode"
        content = normalize_terminal_text(str(message["content"]))
        print(f"{index}. {role}: {content}")


def _print_trace(workspace_path: Path, run_id: str) -> None:
    workspace = Workspace(workspace_path)
    trace_file = workspace.resolve(f".mini-code/runs/{run_id}/events.jsonl")
    if not trace_file.is_file():
        raise SystemExit(f"Trace not found: {trace_file}")
    for line in trace_file.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        print(
            f"{event['timestamp']}  {event['type']:<20} "
            f"{event.get('data', {}).get('message', '')}"
        )


def _run_eval(args: argparse.Namespace) -> int:
    try:
        workspace = Workspace(Path(args.workspace))
        settings = Settings.from_env(Path(args.env_file))
        tasks_file = workspace.resolve(args.tasks)
        report_file, results = run_m0_eval(workspace, settings, tasks_file)
    except (ValueError, WorkspaceError) as exc:
        print(f"Evaluation configuration error: {exc}", file=sys.stderr)
        return 2
    passed = sum(result.passed for result in results)
    print(f"\nM0 result: {passed}/{len(results)} passed")
    print(f"Report: {report_file}")
    return 0 if passed == len(results) else 1


def _run_eval_m1(args: argparse.Namespace) -> int:
    try:
        workspace = Workspace(Path(args.workspace))
        settings = Settings.from_env(Path(args.env_file))
        tasks_file = workspace.resolve(args.tasks)
        report_file, results = run_m1_eval(workspace, settings, tasks_file)
    except (ValueError, WorkspaceError) as exc:
        print(f"Evaluation configuration error: {exc}", file=sys.stderr)
        return 2
    passed = sum(result.passed for result in results)
    print(f"\nM1 result: {passed}/{len(results)} passed")
    print(f"Report: {report_file}")
    return 0 if passed == len(results) else 1


def _run_eval_m3(args: argparse.Namespace) -> int:
    try:
        workspace = Workspace(Path(args.workspace))
        settings = Settings.from_env(Path(args.env_file))
        tasks_file = workspace.resolve(args.tasks)
        report_file, results = run_m3_eval(
            workspace, settings, tasks_file, limit=args.limit
        )
    except (ValueError, WorkspaceError) as exc:
        print(f"Evaluation configuration error: {exc}", file=sys.stderr)
        return 2
    passed = sum(result.passed for result in results)
    print(f"\nM3 result: {passed}/{len(results)} passed")
    print(f"Report: {report_file}")
    return 0 if passed == len(results) else 1


def _run_web(args: argparse.Namespace) -> int:
    try:
        workspace_path = Path(args.workspace)
        if args.demo:
            project_root = Path(__file__).resolve().parents[2]
            template = project_root / "examples" / "m1_zero_division"
            target = project_root / ".mini-code" / "demos" / f"m4-{_new_run_id()}"
            shutil.copytree(template, target)
            workspace_path = target
            print(f"Disposable M4 workspace: {target}")
        workspace = Workspace(workspace_path)
        settings = Settings.from_env(Path(args.env_file))
        serve_web(workspace, settings, args.host, args.port)
    except (ValueError, WorkspaceError, WebError, OSError) as exc:
        print(f"M4 Web error: {exc}", file=sys.stderr)
        return 2
    return 0


def _run_explore(args: argparse.Namespace) -> int:
    try:
        workspace = Workspace(Path(args.workspace))
        settings = Settings.from_env(Path(args.env_file))
        result = ExploreSubagent(
            provider=DeepSeekProvider(settings),
            workspace=workspace,
            max_steps=args.max_steps,
            max_tool_chars=settings.max_tool_chars,
        ).run(args.task, parent_run_id=args.parent_run_id)
    except (ValueError, WorkspaceError) as exc:
        print(f"Explore error: {exc}", file=sys.stderr)
        return 2
    print(f"\nChild Run ID: {result.child_run_id}")
    print(f"Parent Run ID: {result.parent_run_id or '(standalone)'}")
    print(f"Status: {result.status}")
    print(f"Trace: {result.trace_file}")
    if result.report_file is not None:
        print(f"Report: {result.report_file}")
    if result.report_error is not None:
        print(f"Report error: {result.report_error}", file=sys.stderr)
    return 0 if result.status == "completed" else 1


def _new_run_id() -> str:
    import uuid

    return uuid.uuid4().hex[:12]


def _coding_provider(settings: Settings, fail_model_call: int | None = None):
    provider = DeepSeekProvider(settings)
    if fail_model_call is not None:
        return FaultInjectingProvider(provider, fail_model_call)
    return provider


if __name__ == "__main__":
    main()
