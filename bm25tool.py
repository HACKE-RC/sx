"""Compatibility wrapper for legacy imports.

Prefer importing from `sx_search.engine`.
"""

from pathlib import Path
import sys

_SRC = Path(__file__).resolve().parent / "src"
if _SRC.is_dir():
    sys.path.insert(0, str(_SRC))

from sx_search.engine import *  # noqa: F401,F403
