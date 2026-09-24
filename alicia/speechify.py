"""Turn screen text into something a person would actually say out loud.

The law, from shine's voice reference: *a voice layer is a summarizer with
manners, never a screen reader.* This module is the deterministic half of that —
no model sits between the page and the mouth. Everything here is a pure function
with a test, because the failures are specific and repeatable:

- "Live." was read as /lɪv/ in a status report.
- "Jul" is read as *Jewel*.
- A brief once read every tag off the bottom of a ticket, aloud.
- Reading "REV-418" letter by letter sounds like a serial number, not a ticket.

The rule for adding to this file: if a human wouldn't say it across a desk, it
doesn't reach the speaker.
"""

from __future__ import annotations

import re

# Ceiling on any single utterance. The model is also capped (~220 tokens), but
# that cap can lie — a truncation here is what actually bounds playback time,
# which counts against every hook and harness timeout downstream.
MAX_SPOKEN_CHARS = 500

# Hyphenated UUID, the thing TTS spelled as "fee needs you".
_UUID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.I,
)
_COMPACT_ID = re.compile(r"^[0-9a-f]{16,40}$", re.I)

_MONTHS = {
    "jan": "January", "feb": "February", "mar": "March", "apr": "April",
    "jun": "June", "jul": "July", "aug": "August", "sep": "September",
    "sept": "September", "oct": "October", "nov": "November", "dec": "December",
}

# Words whose written form is ambiguous out loud. Read as the wrong sense they
# derail a whole sentence, and TTS has no context to disambiguate.
_HOMOGRAPHS = [
    (r"\blive\b", "deployed and running"),
    (r"\bread\b(?=\s+(?:the\s+)?(?:docs?|file|log|code))", "look at"),
    (r"\blead\b(?=\s+time)", "leed"),
]

_UNITS = {
    "s": "seconds", "sec": "seconds", "secs": "seconds", "ms": "milliseconds",
    "m": "minutes", "min": "minutes", "mins": "minutes", "h": "hours",
    "hr": "hours", "hrs": "hours", "d": "days", "kb": "kilobytes",
    "mb": "megabytes", "gb": "gigabytes",
}

_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
         "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
         "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def _say_under_100(n: int) -> str:
    if n < 20:
        return _ONES[n]
    tens, ones = divmod(n, 10)
    return _TENS[tens] + (f" {_ONES[ones]}" if ones else "")


def _say_ticket_number(digits: str) -> str:
    """419 -> "four nineteen"; 4 -> "four"; 1234 -> "twelve thirty four".

    Ticket ids are read the way people say them, in pairs, not digit by digit
    and not as a cardinal ("four hundred and nineteen" sounds like money).
    """
    n = int(digits)
    if n < 100:
        return _say_under_100(n)
    if n < 1000:
        hundreds, rest = divmod(n, 100)
        if rest == 0:
            return f"{_ONES[hundreds]} hundred"
        return f"{_ONES[hundreds]} {_say_under_100(rest)}" if rest >= 10 else f"{_ONES[hundreds]} oh {_ONES[rest]}"
    first, second = divmod(n, 100)
    return f"{_say_under_100(first)} {_say_under_100(second) if second else 'hundred'}"


def _say_decimal(raw: str) -> str:
    """Four significant digits, max. "12.49:1" out loud is noise."""
    try:
        value = float(raw.replace(",", ""))
    except ValueError:
        return raw
    if value == int(value) and abs(value) < 10000:
        return str(int(value))
    if abs(value) >= 1000:
        return f"{round(value):,}".replace(",", " thousand ", 1).split(" thousand ")[0] + (
            " thousand" if abs(value) < 1_000_000 else " million"
        )
    return f"{value:.1f}".rstrip("0").rstrip(".")


def is_machine_id(text: str) -> bool:
    """True for UUIDs and other hex blobs no person would say across a desk."""
    s = (text or "").strip()
    if not s:
        return False
    if _UUID.fullmatch(s):
        return True
    return bool(_COMPACT_ID.fullmatch(s.replace("-", "")))


def speakable_name(ticket: str = "", title: str = "", *, fallback: str = "this") -> str:
    """A name a person would use. Ticket ids like REV-484; never a UUID."""
    ticket = (ticket or "").strip()
    title = re.sub(r"\s+", " ", (title or "").strip())
    if ticket and not is_machine_id(ticket):
        return ticket
    if title and not is_machine_id(title):
        short = re.split(r"\s+[—–-]\s+", title, maxsplit=1)[0].strip()
        if len(short) > 72:
            short = short[:72].rsplit(" ", 1)[0]
        return short or fallback
    return fallback


def speechify(text: str, *, max_chars: int = MAX_SPOKEN_CHARS) -> str:
    """Rewrite `text` for a speaker. Deterministic, no model, no network."""
    if not text:
        return ""
    s = text

    # --- strip eye-chrome ------------------------------------------------
    s = re.sub(r"```.*?```", " ", s, flags=re.S)          # code fences
    s = re.sub(r"`([^`]*)`", r"\1", s)                    # inline code ticks
    s = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", s)      # links/images -> label
    s = re.sub(r"^\s{0,3}#{1,6}\s*", "", s, flags=re.M)   # headings
    s = re.sub(r"^\s*[-*+]\s+", "", s, flags=re.M)        # bullets
    s = re.sub(r"^\s*\d+\.\s+", "", s, flags=re.M)        # numbered list markers
    s = re.sub(r"[*_~]{1,3}", "", s)                      # emphasis marks
    s = re.sub(r"^\s*\|.*\|\s*$", " ", s, flags=re.M)     # table rows
    s = re.sub(r"^\s*[-=]{3,}\s*$", " ", s, flags=re.M)   # rules
    s = re.sub(r"[\U0001F000-\U0001FAFF☀-➿️]", "", s)  # emoji

    # --- things no human says out loud ------------------------------------
    s = re.sub(r"https?://\S+", "a link", s)
    s = _UUID.sub("this", s)
    s = re.sub(r"\b[0-9a-f]{7,40}\b", "a commit", s, flags=re.I)  # sha / hex blob
    s = re.sub(r"(?<![\w/])(?:~|\.{1,2})?/[\w.\-/]+", "a file", s)      # paths
    # Semver only: a `v` prefix, or three dotted components. Matching a bare
    # `\d+\.\d+` here swallowed every ordinary decimal — "contrast 12.49:1"
    # became "contrast a version:1".
    s = re.sub(r"\bv\d+\.\d+(?:\.\d+)?(?:-[\w.]+)?\b", "a version", s)
    s = re.sub(r"\b\d+\.\d+\.\d+(?:-[\w.]+)?\b", "a version", s)
    s = re.sub(r"\b(?:feat|fix|chore|docs|refactor)/[\w.\-/]+", "a branch", s)

    # --- ticket ids: REV-418 -> "REV four eighteen" -----------------------
    # Lowercase the prefix: "REV" in caps is read as a spelled-out abbreviation
    # ("R-E-V" or "revvy"). Justin says "rev", so give the synthesiser a word.
    s = re.sub(
        r"\b([A-Z]{2,5})-(\d{1,4})\b",
        lambda m: f"{m.group(1).lower()} {_say_ticket_number(m.group(2))}",
        s,
    )

    # --- dates and months --------------------------------------------------
    s = re.sub(r"\b(\d{4})-(\d{2})-(\d{2})\b", _iso_date, s)
    s = re.sub(
        r"\b(" + "|".join(_MONTHS) + r")\b\.?",
        lambda m: _MONTHS[m.group(1).lower()],
        s,
        flags=re.I,
    )

    # --- homographs --------------------------------------------------------
    for pattern, replacement in _HOMOGRAPHS:
        s = re.sub(pattern, replacement, s, flags=re.I)

    # --- numbers -----------------------------------------------------------
    s = re.sub(r"\b(\d+(?:\.\d+)?)\s*:\s*1\b", lambda m: f"{_say_decimal(m.group(1))} to one", s)
    s = re.sub(r"\$(\d+(?:\.\d+)?)([KMB])\b", _money, s)
    s = re.sub(r"\b\d+\.\d{2,}\b", lambda m: _say_decimal(m.group(0)), s)
    s = re.sub(
        r"\b(\d+)\s*(" + "|".join(_UNITS) + r")\b",
        lambda m: f"{m.group(1)} {_UNITS[m.group(2).lower()]}",
        s,
    )

    # --- shape -------------------------------------------------------------
    s = re.sub(r"\s*\n\s*", " ", s)     # speech has no line breaks
    s = re.sub(r"\s{2,}", " ", s).strip()
    s = re.sub(r"\s+([,.;:!?])", r"\1", s)

    if len(s) > max_chars:
        cut = s[:max_chars]
        stop = max(cut.rfind("."), cut.rfind("!"), cut.rfind("?"))
        s = (cut[: stop + 1] if stop > max_chars * 0.5 else cut.rsplit(" ", 1)[0]).strip()
    return s


def _iso_date(m: re.Match[str]) -> str:
    months = ["January", "February", "March", "April", "May", "June", "July",
              "August", "September", "October", "November", "December"]
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not 1 <= month <= 12:
        return m.group(0)
    return f"{months[month - 1]} {day}"


def _money(m: re.Match[str]) -> str:
    suffix = {"K": "thousand", "M": "million", "B": "billion"}[m.group(2)]
    return f"{m.group(1)} {suffix} dollars"


def chunk_for_speech(text: str, *, first: int = 60, rest: int = 120) -> list[str]:
    """Split into speakable chunks, the first one deliberately short.

    Time-to-first-audio is what the listener experiences as latency, so the
    opening chunk flushes at roughly a clause. Later chunks are longer because
    by then audio is already playing and prosody matters more than latency.
    """
    if not text:
        return []
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    chunks: list[str] = []
    buf = ""
    for sentence in sentences:
        limit = first if not chunks else rest
        if not buf:
            buf = sentence
        elif len(buf) + 1 + len(sentence) <= limit:
            buf = f"{buf} {sentence}"
        else:
            chunks.append(buf)
            buf = sentence
        if len(buf) >= limit:
            chunks.append(buf)
            buf = ""
    if buf:
        chunks.append(buf)
    return chunks
