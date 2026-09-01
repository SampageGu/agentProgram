"""Zero-dependency launcher for a src-layout checkout."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from mini_code.cli import main  # noqa: E402


if __name__ == "__main__":
    main()

