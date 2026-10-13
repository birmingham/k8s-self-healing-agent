"""Scrub likely secrets from logs and specs before they leave the cluster."""

from __future__ import annotations

import re

_PATTERNS = [
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"), "[REDACTED_ANTHROPIC_KEY]"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED_AWS_KEY]"),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{8,}"), "Bearer [REDACTED]"),
    (re.compile(r"(?i)(://)[^/\s:@]+:[^/\s@]+@"), r"\1[REDACTED]@"),
    (
        re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|authorization)\b(\s*[:=]\s*)\S+"),
        r"\1\2[REDACTED]",
    ),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"), "[REDACTED_PRIVATE_KEY]"),
]


def redact(text: str) -> str:
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    # Keep the tail: the crash is almost always at the end of the log.
    return "...[truncated]...\n" + text[-limit:]
