"""Minimal Kubernetes memory quantity parsing (bytes in, Mi out)."""

from __future__ import annotations

import math
import re

_UNITS = {
    "": 1,
    "k": 10**3, "M": 10**6, "G": 10**9, "T": 10**12,
    "Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40,
}
_RE = re.compile(r"^([0-9]+(?:\.[0-9]+)?(?:[eE][0-9]+)?)(Ki|Mi|Gi|Ti|k|M|G|T)?$")


def parse_bytes(q: str | int | float) -> int:
    if isinstance(q, (int, float)):
        return int(q)
    m = _RE.match(str(q).strip())
    if not m:
        raise ValueError(f"unsupported memory quantity: {q!r}")
    return int(float(m.group(1)) * _UNITS[m.group(2) or ""])


def format_mi(n_bytes: int) -> str:
    """Round up to whole MiB, which every Kubernetes distro accepts."""
    return f"{math.ceil(n_bytes / 2**20)}Mi"
