"""Make the src-layout package importable without installing the project."""

from __future__ import annotations

import sys
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
src_value = str(SRC_ROOT)
if src_value not in sys.path:
    sys.path.insert(0, src_value)

