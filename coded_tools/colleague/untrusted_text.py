"""Redaction for untrusted GitHub/Slack text entering agent prompts."""

from __future__ import annotations

import re


_PATTERNS = (
    (re.compile(r"-----BEGIN [A-Z0-9 ]+-----.*?-----END [A-Z0-9 ]+-----", re.I | re.S), "<redacted-secret>"),
    (re.compile(r"(?i)\b(?:ghp|gho|ghs|ghu|github_pat|sk|xoxb|xoxp)-[A-Za-z0-9_\-]{12,}"), "<redacted-secret>"),
    (re.compile(r"(?i)(\b(?:api[_ -]?key|token|secret|password|authorization)\s*[:=]\s*)([^\s,;]+)"), r"\1<redacted-secret>"),
    (re.compile(r'(?<![A-Za-z0-9])(?:/Users|/home|/private/var|/tmp)/[^\s`"\']+'), "<redacted-local-path>"),
    (re.compile(r'(?<![A-Za-z0-9])[A-Za-z]:\\[^\s`"\']+'), "<redacted-local-path>"),
)


def sanitize_untrusted_text(value: str, limit: int) -> str:
    """Bound, redact machine-local paths/secrets, and preserve useful issue text."""
    text = str(value or "")[: max(0, limit)]
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text
