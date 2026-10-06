"""Secret redaction for receipts, outbox rows, and transport transcripts.

Redactors already exist in studio_collector / session_supervisor. This module is
the shared write-path gate so Cursor / OpenAI / say-outbox bodies cannot land
unredacted secrets on disk.
"""

from __future__ import annotations

import os
import re
from typing import Any

# Keep the surrounding label so operators can still see *what* was redacted.
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"(?i)(\b(?:authorization|bearer)\s*[: ]\s*)([^\s,;]{8,})"),
        r"\1[redacted]",
    ),
    (
        re.compile(
            r"(?i)(\b(?:api[_ -]?key|token|secret|password|passwd)\s*[=:]\s*)([^\s,;\"']{6,})"
        ),
        r"\1[redacted]",
    ),
    (
        re.compile(
            r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{16,}|"
            r"github_pat_[A-Za-z0-9_]{16,}|AKIA[0-9A-Z]{16}|"
            r"xox[baprs]-[A-Za-z0-9-]{12,}|key_[A-Za-z0-9_-]{16,})\b"
        ),
        "[redacted credential]",
    ),
)

# Detect only live credential shapes — not the placeholder text we write back.
_DETECT = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{16,}|"
    r"github_pat_[A-Za-z0-9_]{16,}|AKIA[0-9A-Z]{16}|"
    r"xox[baprs]-[A-Za-z0-9-]{12,}|key_[A-Za-z0-9_-]{16,})\b"
)


class RedactionGateError(ValueError):
    """Raised when strict mode sees a secret that would have been written raw."""


def contains_secret(text: str) -> bool:
    return bool(text) and _DETECT.search(text) is not None


def redact_secrets(text: str) -> str:
    """Return text with common credential shapes replaced."""
    if not text:
        return text
    out = text
    for pattern, replacement in _PATTERNS:
        out = pattern.sub(replacement, out)
    return out


def gate_text(text: str, *, strict: bool | None = None) -> str:
    """Redact before disk. Strict mode (CI) fails if the input still carried secrets.

    Default: redact and continue. ``ALICIA_REDACTION_STRICT=1`` or an explicit
    ``strict=True`` raises after redacting so fixtures prove the gate bites.
    """
    if text is None:
        return ""
    raw = str(text)
    if strict is None:
        strict = os.environ.get("ALICIA_REDACTION_STRICT", "").strip() == "1"
    cleaned = redact_secrets(raw)
    if strict and contains_secret(raw):
        raise RedactionGateError("refusing to persist unredacted secret material")
    return cleaned


def gate_value(value: Any, *, strict: bool | None = None) -> Any:
    """Recursively redact string leaves in dict/list structures."""
    if isinstance(value, str):
        return gate_text(value, strict=strict)
    if isinstance(value, dict):
        return {k: gate_value(v, strict=strict) for k, v in value.items()}
    if isinstance(value, list):
        return [gate_value(v, strict=strict) for v in value]
    if isinstance(value, tuple):
        return tuple(gate_value(v, strict=strict) for v in value)
    return value
