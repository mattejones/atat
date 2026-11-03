"""
profile_shape.py — the shape of the CV profile (paragraphs and length), per instance.

The shared generation prompt used to hard-code "ONE paragraph, 40-60 words", which a person's
own rules then contradicted. The shape now comes from settings (PROFILE_PARAGRAPHS,
PROFILE_WORDS_MIN / PROFILE_WORDS_MAX), is written into the prompt here, and is the same
shape the deterministic judge checks the result against.
"""

import re

import pipeline.config as config

_WORD_FOR = {1: "One", 2: "Two", 3: "Three"}


def paragraphs() -> int:
    return max(1, int(config.PROFILE_PARAGRAPHS))


def word_bounds() -> tuple:
    lo = max(1, int(config.PROFILE_WORDS_MIN))
    hi = max(lo, int(config.PROFILE_WORDS_MAX))
    return lo, hi


def _count_word() -> str:
    n = paragraphs()
    return _WORD_FOR.get(n, str(n))


def schema_text() -> str:
    lo, hi = word_bounds()
    n = paragraphs()
    noun = "paragraph" if n == 1 else "paragraphs"
    sep = "" if n == 1 else " Separate the paragraphs with a blank line (\\n\\n)."
    return f"{_count_word()} {noun}, {lo}-{hi} words in total.{sep}"


def rules_text() -> str:
    lo, hi = word_bounds()
    n = paragraphs()
    lines = [f"- {_count_word()} paragraph{'s' if n != 1 else ''}, {lo}-{hi} words in total"]
    if n == 2:
        lines += [
            "- Paragraph 1 says who the candidate is and how they work",
            "- Paragraph 2 gives the evidence, as one connected argument rather than stacked facts",
        ]
    elif n > 2:
        lines.append("- Each paragraph does one job; together they read as one connected argument")
    lines += [
        "- Open with the candidate's character and way of working, not with a single example or "
        "their years of tenure",
        "- Name no tools, products or vendors in the profile",
        "- Does not repeat information that appears in the experience entries",
    ]
    return "\n".join(lines)


def retry_text() -> str:
    lo, hi = word_bounds()
    n = paragraphs()
    noun = "paragraph" if n == 1 else "paragraphs"
    return (
        f"Return ONLY the profile: {_count_word().lower()} {noun}, {lo}-{hi} words in total, "
        "as plain prose separated by blank lines. No heading, no bullet points."
    )


def apply(prompt: str) -> str:
    """Fill the {{PROFILE_*}} placeholders in a prompt."""
    return (
        prompt.replace("{{PROFILE_SCHEMA}}", schema_text())
              .replace("{{PROFILE_RULES}}", rules_text())
              .replace("{{PROFILE_RETRY}}", retry_text())
    )


def split_paragraphs(text: str) -> list:
    return [p.strip() for p in re.split(r"\n\s*\n", (text or "").strip()) if p.strip()]


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text or ""))
