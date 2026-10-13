"""Durable agent state, stored as annotations on the Deployment itself.

Keeping state on the workload (not in agent memory) means the circuit breaker
and pending approvals survive agent restarts, and `kubectl describe` shows
exactly what the agent did and why.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from .config import ANN_APPROVE, ANN_HISTORY, ANN_LAST_DIAGNOSIS, ANN_PROPOSAL  # noqa: F401

MAX_HISTORY = 10


def _escape(key: str) -> str:
    return key.replace("~", "~0").replace("/", "~1")


def _parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def now_iso(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).isoformat(timespec="seconds")


def annotations(dep) -> dict:
    return dict(dep.metadata.annotations or {})


def _load_json(dep, key, default):
    raw = annotations(dep).get(key)
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def history(dep) -> list[dict]:
    items = _load_json(dep, ANN_HISTORY, [])
    return [h for h in items if isinstance(h, dict) and "ts" in h]


def recent_history(dep, now: datetime, window_minutes: int) -> list[dict]:
    out = []
    for h in history(dep):
        try:
            if (now - _parse_ts(h["ts"])).total_seconds() < window_minutes * 60:
                out.append(h)
        except ValueError:
            continue
    return out


def in_cooldown(dep, signature: str, now: datetime, cooldown_minutes: int, escalation_cooldown_minutes: int) -> bool:
    """Skip re-diagnosing an incident the agent looked at recently.

    Escalated incidents get a longer quiet period so a stuck workload doesn't
    trigger an LLM call (and a page) every few minutes.
    """
    last = _load_json(dep, ANN_LAST_DIAGNOSIS, {}).get(signature)
    if not isinstance(last, dict) or "ts" not in last:
        return False
    minutes = escalation_cooldown_minutes if last.get("outcome") == "escalate" else cooldown_minutes
    try:
        return (now - _parse_ts(last["ts"])).total_seconds() < minutes * 60
    except ValueError:
        return False


def proposal(dep) -> dict | None:
    p = _load_json(dep, ANN_PROPOSAL, None)
    return p if isinstance(p, dict) and "id" in p else None


def approved_id(dep) -> str | None:
    return annotations(dep).get(ANN_APPROVE)


def _ops_for(dep, sets: dict[str, str], removes: tuple[str, ...] = ()) -> list[dict]:
    current = annotations(dep)
    ops: list[dict] = []
    if dep.metadata.annotations is None:
        ops.append({"op": "add", "path": "/metadata/annotations", "value": {}})
    for key, value in sets.items():
        ops.append({"op": "add", "path": f"/metadata/annotations/{_escape(key)}", "value": value})
    for key in removes:
        if key in current:
            ops.append({"op": "remove", "path": f"/metadata/annotations/{_escape(key)}"})
    return ops


def diagnosed_value(dep, signature: str, outcome: str, now: datetime) -> str:
    last = _load_json(dep, ANN_LAST_DIAGNOSIS, {})
    last[signature] = {"ts": now_iso(now), "outcome": outcome}
    return json.dumps(last, sort_keys=True)


def history_value(dep, entry: dict) -> str:
    return json.dumps((history(dep) + [entry])[-MAX_HISTORY:])


class StateStore:
    """All state writes are a single JSON Patch on the Deployment's annotations.

    Annotation changes don't touch spec.template, so they never trigger a rollout.
    """

    def __init__(self, apps):
        self.apps = apps

    def write(self, dep, sets: dict[str, str], removes: tuple[str, ...] = ()):
        ops = _ops_for(dep, sets, removes)
        if ops:
            self.apps.patch_namespaced_deployment(dep.metadata.name, dep.metadata.namespace, ops)
