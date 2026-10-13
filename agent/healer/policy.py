"""The guardrails. Deterministic code decides whether, what, and how much.

The model recommends an action; this module decides whether it is allowed,
computes every parameter (targets, revisions, memory values), and picks
auto-execute, propose-for-approval, or escalate.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import ALLOWED_BY_SIGNATURE
from .quantity import format_mi, parse_bytes

REVISION_HASH_LABEL = "pod-template-hash"


@dataclass
class Decision:
    outcome: str  # execute | propose | escalate
    action: str
    reason: str
    params: dict = field(default_factory=dict)


def _escalate(reason: str, action: str = "escalate") -> Decision:
    return Decision("escalate", action, reason)


def _template_fingerprint(api_client, rs) -> str:
    import json

    tmpl = api_client.sanitize_for_serialization(rs.spec.template)
    tmpl.get("metadata", {}).get("labels", {}).pop(REVISION_HASH_LABEL, None)
    return json.dumps(tmpl, sort_keys=True)


def params_restart_pod(policy, incident, diagnosis, ctx, api_client):
    owned = []
    for pod in ctx.pods:
        if any(r.controller and r.kind == "ReplicaSet" for r in pod.metadata.owner_references or []):
            owned.append(pod.metadata.name)
    # ctx.pods holds at most a couple of representatives; include the rest by name.
    for name in incident.pods:
        if name not in owned and len(owned) < policy.action("restart_pod").max_pods:
            owned.append(name)
    if not owned:
        return None, "no controller-owned pods to restart"
    return {"pods": owned[: policy.action("restart_pod").max_pods]}, None


def params_rollback(policy, incident, diagnosis, ctx, api_client):
    if len(ctx.revisions) < 2:
        return None, "no earlier revision exists to roll back to"
    (cur_rev, cur_rs), (prev_rev, prev_rs) = ctx.revisions[-1], ctx.revisions[-2]
    if _template_fingerprint(api_client, cur_rs) == _template_fingerprint(api_client, prev_rs):
        return None, f"revision {prev_rev} has the same pod template as {cur_rev}"
    return {
        "from_revision": cur_rev,
        "to_revision": prev_rev,
        "to_images": {c.name: c.image for c in prev_rs.spec.template.spec.containers},
    }, None


def params_memory(policy, incident, diagnosis, ctx, api_client):
    ap = policy.action("increase_memory_limit")
    name = diagnosis.container or (incident.containers[0] if len(incident.containers) == 1 else None)
    if not name:
        return None, "model did not name a container and several were OOM-killed"
    if name not in incident.containers:
        return None, f"container {name!r} was not OOM-killed"
    containers = ctx.deployment.spec.template.spec.containers
    match = [c for c in containers if c.name == name]
    if not match:
        return None, f"container {name!r} not found in the pod template"
    res = match[0].resources
    current = (res.limits or {}).get("memory") if res else None
    if not current:
        return None, f"container {name!r} has no memory limit; OOM is node pressure, not a limit"
    cur_b, ceiling_b = parse_bytes(current), parse_bytes(ap.ceiling)
    new_b = min(int(cur_b * ap.max_factor), ceiling_b)
    if new_b <= cur_b:
        return None, f"memory limit {current} is already at the policy ceiling {ap.ceiling}"
    return {"container": name, "from": current, "to": format_mi(new_b)}, None


PARAMS = {
    "restart_pod": params_restart_pod,
    "rollback_deployment": params_rollback,
    "increase_memory_limit": params_memory,
}


def evaluate(policy, incident, diagnosis, ctx, recent: list[dict], api_client) -> Decision:
    action = diagnosis.action
    if action == "escalate":
        if getattr(diagnosis, "fallback", False):
            return _escalate("LLM diagnosis unavailable; no automated action taken")
        return _escalate("model recommended escalation")

    ap = policy.action(action)
    if ap is None or ap.mode == "disabled":
        return _escalate(f"{action} is not enabled by policy", action)

    if action not in ALLOWED_BY_SIGNATURE.get(incident.signature, ()):
        return _escalate(f"{action} is not a valid remediation for {incident.signature}", action)

    if len(recent) >= policy.max_actions_per_workload:
        return _escalate(
            f"circuit breaker open: {len(recent)} actions in the last {policy.rate_window_minutes} min", action
        )

    repeat = [h for h in recent if h.get("signature") == incident.signature]
    if repeat:
        last = repeat[-1]
        return _escalate(
            f"{last.get('action')} at {last.get('ts')} did not resolve {incident.signature}; not retrying", action
        )

    params, err = PARAMS[action](policy, incident, diagnosis, ctx, api_client)
    if err:
        return _escalate(err, action)

    if diagnosis.confidence < policy.min_confidence:
        return Decision("propose", action, f"confidence {diagnosis.confidence:.2f} below {policy.min_confidence}", params)
    if policy.dry_run:
        return Decision("propose", action, "dry-run mode", params)
    if ap.mode == "auto":
        return Decision("execute", action, "allowed by policy (auto)", params)
    return Decision("propose", action, "policy requires human approval", params)


def revalidate(policy, incident, diagnosis, ctx, api_client, approved_params: dict) -> tuple[dict | None, str | None]:
    """Recompute parameters at approval time and refuse if the world changed."""
    params, err = PARAMS[diagnosis.action](policy, incident, diagnosis, ctx, api_client)
    if err:
        return None, err
    keys = {"rollback_deployment": ("to_revision",), "increase_memory_limit": ("container", "from", "to")}
    for k in keys.get(diagnosis.action, ()):
        if params.get(k) != approved_params.get(k):
            return None, f"stale proposal: {k} is now {params.get(k)!r}, approved {approved_params.get(k)!r}"
    return params, None
