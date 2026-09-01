"""Configuration loading without third-party dependencies."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: Path) -> None:
    """Load simple KEY=VALUE pairs without overriding process variables."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


@dataclass(frozen=True)
class Settings:
    api_key: str
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-flash"
    max_steps: int = 8
    request_timeout: int = 120
    max_tool_chars: int = 12_000
    max_context_chars: int = 50_000
    artifact_threshold: int = 6_000
    input_cost_per_million: float | None = None
    output_cost_per_million: float | None = None

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> "Settings":
        if env_file is not None:
            load_dotenv(env_file)
        api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        if not api_key or api_key == "replace-with-your-key":
            raise ValueError(
                "Missing DEEPSEEK_API_KEY. Copy .env.example to .env and fill in your key."
            )
        return cls(
            api_key=api_key,
            base_url=os.getenv("MINICODE_BASE_URL", "https://api.deepseek.com").rstrip("/"),
            model=os.getenv("MINICODE_MODEL", "deepseek-v4-flash"),
            max_steps=_positive_int("MINICODE_MAX_STEPS", 8),
            request_timeout=_positive_int("MINICODE_REQUEST_TIMEOUT", 120),
            max_tool_chars=_positive_int("MINICODE_MAX_TOOL_CHARS", 12_000),
            max_context_chars=_positive_int("MINICODE_MAX_CONTEXT_CHARS", 50_000),
            artifact_threshold=_positive_int("MINICODE_ARTIFACT_THRESHOLD", 6_000),
            input_cost_per_million=_optional_nonnegative_float(
                "MINICODE_INPUT_COST_PER_MILLION"
            ),
            output_cost_per_million=_optional_nonnegative_float(
                "MINICODE_OUTPUT_COST_PER_MILLION"
            ),
        )


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {value}")
    return value


def _optional_nonnegative_float(name: str) -> float | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc
    if value < 0:
        raise ValueError(f"{name} must be non-negative, got {value}")
    return value
