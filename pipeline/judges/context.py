"""
context.py — what a judge needs to know beyond the one section it is looking at.

The judges used to see only the section text, so they could not tell a claim the person
really made from one lifted out of the job ad, could not tell a phrase the library licenses
from an invention, and never saw the person's own standing rules. A JudgeContext carries:

  section             which section is being judged (profile, experience, ...)
  jd_text             the job ad
  library_text        the library as the model saw it: META, personas, experience, skills,
                      with private sections already stripped
  personal_additions  the person's own standing rules (confidentiality, terminology)
  cv_text             the whole CV, for checks that count across sections

Everything is optional; a judge that is handed no context behaves as it always did.
"""

import re
from dataclasses import dataclass, field

# Words that carry no meaning for "is this phrase copied from the ad?" checks.
STOPWORDS = frozenset("""
a about above after again against all also am an and any are as at be because been before being
below between both but by can could did do does doing down during each few for from further had
has have having he her here hers him his how i if in into is it its just me more most my no nor
not of off on once only or other our out over own same she should so some such than that the
their them then there these they this those through to too under until up very was we were what
when where which while who whom why will with would you your within across per via etc
""".split())

_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9'-]*")


def tokens(text: str) -> list:
    """[(lower-case token, start, end), ...] for the words in text."""
    return [(m.group().lower().strip("'-"), m.start(), m.end()) for m in _TOKEN.finditer(text or "")]


def stem(token: str) -> str:
    """A crude stem: the first six letters, so regulate/regulated/regulatory/regulation agree."""
    return token[:6]


def content_tokens(text: str) -> list:
    """Tokens that are not stopwords and are long enough to mean something."""
    return [t for t in tokens(text) if len(t[0]) >= 5 and t[0] not in STOPWORDS]


@dataclass
class JudgeContext:
    section:            str = ""
    jd_text:            str = ""
    library_text:       str = ""
    personal_additions: str = ""
    cv_text:            str = ""
    role_weights:       list = field(default_factory=list)   # pipeline.weights.library_weights()
    _cache: dict = field(default_factory=dict, repr=False)

    def _memo(self, key, fn):
        if key not in self._cache:
            self._cache[key] = fn()
        return self._cache[key]

    @property
    def known_text(self) -> str:
        """Everything the person's own materials license: library plus their standing rules."""
        return f"{self.library_text}\n{self.personal_additions}"

    def jd_ngrams(self, n: int = 4) -> set:
        return self._memo(("jd", n), lambda: _ngrams(self.jd_text, n))

    def known_ngrams(self, n: int = 4) -> set:
        return self._memo(("known", n), lambda: _ngrams(self.known_text, n))

    def known_stems(self) -> set:
        return self._memo("known_stems", lambda: {stem(t[0]) for t in content_tokens(self.known_text)})

    def ad_only_stems(self) -> set:
        """Stems of meaningful words in the ad that appear nowhere in the person's materials."""
        return self._memo(
            "ad_only",
            lambda: {stem(t[0]) for t in content_tokens(self.jd_text)} - self.known_stems(),
        )

    def frequent_known_keys(self, key, min_count: int = 5) -> set:
        """Words the person's own materials use often: their domain vocabulary. `key` normalises a
        word (plurals); the result is the set of normalised words used `min_count`+ times."""
        def build():
            counts: dict = {}
            for t in tokens(self.known_text):
                if len(t[0]) >= 4 and t[0] not in STOPWORDS:
                    counts[key(t[0])] = counts.get(key(t[0]), 0) + 1
            return {k for k, n in counts.items() if n >= min_count}
        return self._memo(("freq_known", min_count), build)

    def known_long_words(self, min_len: int = 8) -> frozenset:
        """Long words the person's own materials use. They are the person's vocabulary, so a long
        one is not evidence that the text is hard to read."""
        return self._memo(
            ("long_known", min_len),
            lambda: frozenset(t[0] for t in tokens(self.known_text) if len(t[0]) >= min_len),
        )

    def cv_stem_counts(self, fallback_text: str = "") -> dict:
        def build():
            counts: dict = {}
            for t in content_tokens(self.cv_text or fallback_text):
                counts[stem(t[0])] = counts.get(stem(t[0]), 0) + 1
            return counts
        return self._memo(("cv_counts", bool(self.cv_text)), build)


def _ngrams(text: str, n: int) -> set:
    words = [t[0] for t in tokens(text)]
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


def build_context(section: str, jd_text: str = "", cv_text: str = "") -> JudgeContext:
    """Assemble a context from the library on disk (private sections already stripped)."""
    from pipeline.config import META_PATH, SKILLS_PATH
    from pipeline.tailorer import (
        load_experience_files, load_persona_files, load_personal_additions, load_text,
    )
    library = "\n\n".join([
        load_text(META_PATH), load_persona_files(), load_experience_files(), load_text(SKILLS_PATH),
    ])
    from pipeline import weights
    return JudgeContext(
        section=section, jd_text=jd_text or "", library_text=library,
        personal_additions=load_personal_additions(), cv_text=cv_text or "",
        role_weights=weights.library_weights(),
    )


def context_for(app: dict, section: str, replace: tuple | None = None) -> JudgeContext:
    """
    Context for judging one section of an application's CV.

    `replace` is (old_text, new_text) for a section that is about to replace its previous
    version, so cross-section counts reflect the CV as it will read rather than counting the
    old and new text twice.
    """
    cv_text = (app.get("cv_markdown") or "")
    if replace:
        old, new = replace
        cv_text = cv_text.replace(old, new) if old and old in cv_text else f"{cv_text}\n\n{new}"
    return build_context(section, jd_text=app.get("jd_text") or "", cv_text=cv_text)
