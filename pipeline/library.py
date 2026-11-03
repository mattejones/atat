"""
library.py — ordering of the CV library's experience files.

Experience files are named {start}-{end}_{company}_{role}.md, e.g.
"2023-2025_acme_head-of-ops.md". A role you still hold ends in "now", "present" or "current"
instead of a year ("2025-now_acme_head-of-ops.md"). Newest-first ordering matters because the
library is sent to the model in that order.
"""

import re
from pathlib import Path

_NAME = re.compile(r"^(\d{4})-(\d{4}|now|present|current)(?=_|\.|-|$)", re.IGNORECASE)
_OPEN_ENDED = ("now", "present", "current")


def _key(path: Path) -> tuple:
    m = _NAME.match(path.name)
    if not m:
        return (0, 0, 0, path.name)           # unparseable names sort last, as before
    start = int(m.group(1))
    end_raw = m.group(2).lower()
    current = end_raw in _OPEN_ENDED
    end = 9999 if current else int(end_raw)
    return (1 if current else 0, start, end, path.name)


def experience_files(directory: Path) -> list:
    """Experience files, newest first: current roles, then by start year, then end year."""
    directory = Path(directory)
    if not directory.exists():
        return []
    return sorted(directory.glob("*.md"), key=_key, reverse=True)
