"""Make the ``parser`` package importable when pytest is invoked from the
repo root (the drive-capture tool is standalone, not a uv workspace member)."""

import sys
from pathlib import Path

# tools/drive-capture/ — parent of the ``parser`` package
_TOOL_ROOT = Path(__file__).resolve().parents[2]
if str(_TOOL_ROOT) not in sys.path:
    sys.path.insert(0, str(_TOOL_ROOT))
