"""Single source of truth for "what language is this text?".

Two layers on purpose:

- ``cjk_ratio(text)``: pure counting, returns 0.0–1.0. Exposed because
  ``/offers/{id}/primary_lang`` has historically returned the ratio in
  its response, and external callers (and tests) may rely on that.

- ``detect_text_language(text)``: labeled answer. Returns ``"zh-CN"``,
  ``"en"``, or ``None`` (insufficient signal). This is what every
  content-generation service calls to decide whether to override the
  caller's requested language with the KB's actual language.

Why the asymmetric threshold: English KBs commonly contain a handful of
CJK product names ("HeyGen vs 蝉镜") as noise. We only call a text
"Chinese" when CJK meaningfully outweighs ASCII letters — a small
CJK sprinkle stays English.
"""
from __future__ import annotations


def cjk_ratio(text: str | None) -> float:
    """Fraction of non-whitespace chars that are CJK ideographs."""
    if not text:
        return 0.0
    total = 0
    cjk = 0
    for ch in text:
        if ch.isspace():
            continue
        total += 1
        # CJK Unified Ideographs + Extension A
        if "一" <= ch <= "鿿" or "㐀" <= ch <= "䶿":
            cjk += 1
    return (cjk / total) if total else 0.0


# Minimum signal length. Below this we return None rather than guess.
_MIN_SAMPLE_CHARS = 30

# CJK chars must clear this fraction of ASCII letters to be called "zh".
_ZH_THRESHOLD = 0.3


def detect_text_language(text: str | None) -> str | None:
    """Return ``"zh-CN"`` / ``"en"`` / ``None``.

    ``None`` means the sample is too short or has no letters — callers
    should fall back to whatever language the user supplied.
    """
    if not text:
        return None
    import re
    chinese = len(re.findall(r"[一-鿿]", text))
    ascii_letters = len(re.findall(r"[A-Za-z]", text))
    if chinese + ascii_letters < _MIN_SAMPLE_CHARS:
        return None
    if chinese > ascii_letters * _ZH_THRESHOLD:
        return "zh-CN"
    return "en"


def matches(language: str | None, detected: str | None) -> bool:
    """True when ``language`` and ``detected`` refer to the same family
    (both zh-something or both en-something). Used to skip noisy logs
    like "wanted zh-CN, got zh-CN" when the caller's code uses short
    forms like "zh"."""
    if not language or not detected:
        return False
    return language[:2].lower() == detected[:2].lower()


_FALLBACK_LANGUAGE = "zh-CN"


def resolve_output_language(
    requested_language: str | None,
    kb_text: str | None,
    *,
    caller: str = "",
) -> str:
    """The system-wide rule — one sentence:

        **The KB's language is the default. An explicit API-level
        ``language`` value overrides it; anything else follows the KB.**

    So:
    - ``requested_language`` is a real string → honor it verbatim
      (API caller / UI composer explicitly picked).
    - ``requested_language`` is ``None`` / empty → detect the KB's
      language and return that; if the KB is too short to decide,
      fall back to ``zh-CN``.

    Previous iterations carried a separate ``manual_override: bool``
    alongside an always-populated ``language`` — that split ambiguated
    "did the caller actually pick?" and let frontend defaults win over
    KB detection. Unified rule: presence of ``language`` IS the
    override signal.

    ``caller`` is a short label purely for log breadcrumbs.
    """
    if requested_language:
        return requested_language
    detected = detect_text_language(kb_text)
    if detected:
        import logging
        logging.getLogger(__name__).info(
            "%s: no explicit language — following KB (%s)",
            caller or "lang_detect", detected,
        )
        return detected
    return _FALLBACK_LANGUAGE
