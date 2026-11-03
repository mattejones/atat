"""
privacy.py — facts that live in the CV library but must never reach a CV.

An experience file can hold things the person wants on record for themselves (a confidential
branch closure, a sensitive reason for leaving) without any risk of them being written into a
CV. Mark them in the library file, and they are removed before the text is sent to any model,
shown in a prompt preview, or returned by the MCP library tools:

    ## Private                      <- a heading named "Private" (or "Private notes",
    Closed the Leeds branch ...        "Private: ...", "Private (...)") hides everything up to
                                       the next heading of the same or higher level.
    ## Other things
    ...

    <!-- private -->                 <- or a block, anywhere in a paragraph or list
    The client was X.
    <!-- /private -->

"Private sector experience" is NOT private: the heading must be exactly the word (optionally
followed by notes, a colon, a dash or a parenthesis). An opening <!-- private --> with no
closing tag hides everything after it, which fails safe.

The stripping happens where library files are read (tailorer.load_text for anything under the
CV library, and read_library_file for the MCP tools), so there is one choke point rather than a
check at every prompt. It cannot protect facts copied into personal_additions.md, generation
notes or the job ad, which are not library files.
"""

import re
from pathlib import Path

_BLOCK       = re.compile(r"<!--\s*private\s*-->.*?<!--\s*/private\s*-->", re.IGNORECASE | re.DOTALL)
_OPEN_ONLY   = re.compile(r"<!--\s*private\s*-->.*\Z", re.IGNORECASE | re.DOTALL)
_HEADING     = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_PRIVATE_TXT = re.compile(r"^private(\s+notes?)?(\s*[:(\-–—].*)?$", re.IGNORECASE)


def strip_private(text: str) -> str:
    """Remove private sections and blocks from library text."""
    if not text:
        return text
    text = _BLOCK.sub("", text)
    text = _OPEN_ONLY.sub("", text)        # an unclosed block hides the rest: fail safe

    out: list[str] = []
    skip_level = None
    for line in text.split("\n"):
        m = _HEADING.match(line)
        if m:
            level = len(m.group(1))
            if skip_level is not None and level <= skip_level:
                skip_level = None
            if skip_level is None and _PRIVATE_TXT.match(m.group(2).strip()):
                skip_level = level
                continue
        if skip_level is None:
            out.append(line)
    return "\n".join(out)


def has_private(text: str) -> bool:
    return strip_private(text) != text


def in_library(path: Path) -> bool:
    """True if `path` is a file inside the CV library (the only place private marks apply)."""
    from pipeline.config import CV_LIBRARY_PATH
    try:
        Path(path).resolve().relative_to(Path(CV_LIBRARY_PATH).resolve())
        return True
    except ValueError:
        return False


def read_library_file(path: Path, include_private: bool = False) -> str:
    """Read a CV library file for display or search, hiding private sections by default."""
    raw = Path(path).read_text(encoding="utf-8")
    return raw if include_private else strip_private(raw)
