"""Scrub secrets and personal paths from text before retro prints or stores it.

Transcripts contain whatever went through the terminal: API keys, tokens, private keys,
email addresses, home directories. Every excerpt retro shows or saves goes through
`redact` first. `find_secrets` is the same rule set used for reporting, and it backs
`retro audit`.

This is a pattern list, so it will miss things. It is a seatbelt, not a guarantee, which
is why real data also never lives inside a checkout (see `retro.store`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Order matters: specific token formats first, then the generic catch-alls.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "private-key-block",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    ),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai-key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}")),
    ("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})")),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    ("bearer-token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_\-.=]{20,}")),
    # 32 bytes of hex is an Ethereum private key, among other things. Addresses (20 bytes)
    # and tx hashes look similar, but a false positive here only costs a redacted hash.
    ("hex-secret", re.compile(r"\b0x[0-9a-fA-F]{64}\b")),
    # KEY=value in env style (uppercase key), or any key with a quoted value. Lowercase
    # unquoted forms like `tokens: list[str]` are code, not secrets.
    (
        "assignment",
        re.compile(
            r"\b[A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|API_KEY|PRIVATE_KEY)[A-Z0-9_]*\s*[=:]\s*"
            r"['\"]?[^\s'\"]{8,}"
            r"|(?i:\b\w*(?:secret|token|password|api_?key|private_?key)\w*['\"]?\s*[=:]\s*"
            r"['\"][^\s'\"]{8,}['\"])"
        ),
    ),
    ("email", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
]
_HOME = re.compile(r"(?<![\w./\-~])(/home|/Users)/[A-Za-z0-9._\-]+")


@dataclass(frozen=True)
class SecretMatch:
    rule: str
    start: int
    end: int


def find_secrets(text: str) -> list[SecretMatch]:
    found: list[SecretMatch] = []
    taken: list[tuple[int, int]] = []
    for rule, pattern in _PATTERNS:
        for m in pattern.finditer(text):
            if any(m.start() < end and start < m.end() for start, end in taken):
                continue
            taken.append((m.start(), m.end()))
            found.append(SecretMatch(rule, m.start(), m.end()))
    return sorted(found, key=lambda s: s.start)


def redact(text: str) -> str:
    """Replace secrets with `[rule]` markers and home directories with `~`."""
    out, last = [], 0
    for match in find_secrets(text):
        out.append(text[last : match.start])
        out.append(f"[{match.rule}]")
        last = match.end
    out.append(text[last:])
    return _HOME.sub("~", "".join(out))


def excerpt(text: str, limit: int = 240) -> str:
    """A redacted, single-line, length-capped snippet for reports."""
    flat = " ".join(redact(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"
