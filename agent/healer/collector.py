"""Gather the telemetry the model needs: spec, rollout history, status, events, logs."""

from __future__ import annotations

from dataclasses import dataclass, field

from kubernetes.client.exceptions import ApiException

from .redact import redact, truncate

REVISION = "deployment.kubernetes.io/revision"
LOG_TAIL_LINES = 80
LOG_CHAR_LIMIT = 6000
MAX_PODS = 2
MAX_EVENTS = 12


@dataclass
class Context:
    deployment: object  # V1Deployment
    revisions: list = field(default_factory=list)  # [(int revision, V1ReplicaSet)], oldest first
    pods: list = field(default_factory=list)  # V1Pod objects for the incident
    summary: dict = field(default_factory=dict)  # what the model sees

    @property
    def generation(self) -> int:
        return self.deployment.metadata.generation


def _container_summary(c) -> dict:
    res = c.resources
    probes = {}
    for kind in ("liveness_probe", "readiness_probe", "startup_probe"):
        p = getattr(c, kind)
        if p:
            mech = "httpGet" if p.http_get else "tcpSocket" if p.tcp_socket else "exec" if p._exec else "grpc"
            probes[kind] = {"type": mech, "failureThreshold": p.failure_threshold, "periodSeconds": p.period_seconds}
    return {
        "name": c.name,
        "image": c.image,
        "command": redact(" ".join(c.command or [])) or None,
        "args": redact(" ".join(c.args or [])) or None,
        # Env var NAMES and sources only. Values may be secrets and never leave the cluster.
        "env": [
            {"name": e.name, "from": "secret" if e.value_from and e.value_from.secret_key_ref
             else "configMap" if e.value_from and e.value_from.config_map_key_ref
             else "fieldRef" if e.value_from else "literal"}
            for e in (c.env or [])
        ],
        "envFrom": [
            ("secret:" + ef.secret_ref.name) if ef.secret_ref else ("configMap:" + ef.config_map_ref.name)
            for ef in (c.env_from or []) if ef.secret_ref or ef.config_map_ref
        ],
        "resources": {
            "requests": dict(res.requests or {}) if res else {},
            "limits": dict(res.limits or {}) if res else {},
        },
        "probes": probes,
    }


def _status_summary(cs) -> dict:
    out = {"name": cs.name, "ready": cs.ready, "restartCount": cs.restart_count}
    st = cs.state
    if st and st.waiting:
        out["state"] = {"waiting": st.waiting.reason, "message": redact(st.waiting.message or "")[:500]}
    elif st and st.terminated:
        out["state"] = {"terminated": st.terminated.reason, "exitCode": st.terminated.exit_code}
    elif st and st.running:
        out["state"] = {"running": True}
    last = cs.last_state.terminated if cs.last_state else None
    if last:
        out["lastTerminated"] = {
            "reason": last.reason,
            "exitCode": last.exit_code,
            "finishedAt": last.finished_at.isoformat() if last.finished_at else None,
        }
    return out


def _events(core, namespace: str, name: str) -> list[dict]:
    try:
        items = core.list_namespaced_event(namespace, field_selector=f"involvedObject.name={name}").items
    except ApiException:
        return []

    def ts(e):
        t = e.last_timestamp or e.event_time or e.first_timestamp
        return t.isoformat() if t else ""

    items.sort(key=ts)
    return [
        {"type": e.type, "reason": e.reason, "count": e.count, "message": redact(e.message or "")[:400], "at": ts(e)}
        for e in items[-MAX_EVENTS:]
    ]


def _logs(core, namespace: str, pod: str, container: str) -> dict:
    out = {}
    for previous in (True, False):
        try:
            text = core.read_namespaced_pod_log(
                pod, namespace, container=container, tail_lines=LOG_TAIL_LINES, previous=previous
            )
        except ApiException:
            continue
        if text:
            out["previous" if previous else "current"] = truncate(redact(text), LOG_CHAR_LIMIT)
    return out


def collect(core, apps, incident) -> Context:
    ns = incident.namespace
    dep = apps.read_namespaced_deployment(incident.deployment, ns)
    selector = ",".join(f"{k}={v}" for k, v in (dep.spec.selector.match_labels or {}).items())

    revisions = []
    for rs in apps.list_namespaced_replica_set(ns, label_selector=selector).items:
        owned = any(o.uid == dep.metadata.uid for o in rs.metadata.owner_references or [])
        rev = (rs.metadata.annotations or {}).get(REVISION)
        if owned and rev and rev.isdigit():
            revisions.append((int(rev), rs))
    revisions.sort(key=lambda r: r[0])

    pods = []
    for name in incident.pods[:MAX_PODS]:
        try:
            pods.append(core.read_namespaced_pod(name, ns))
        except ApiException:
            pass

    summary = {
        "incident": {"signature": incident.signature, "affectedPods": len(incident.pods),
                     "containers": incident.containers},
        "deployment": {
            "name": dep.metadata.name,
            "namespace": ns,
            "replicas": dep.spec.replicas,
            "readyReplicas": dep.status.ready_replicas or 0,
            "currentRevision": revisions[-1][0] if revisions else None,
            "strategy": dep.spec.strategy.type if dep.spec.strategy else None,
            "containers": [_container_summary(c) for c in dep.spec.template.spec.containers],
            "conditions": [
                {"type": c.type, "status": c.status, "reason": c.reason, "message": (c.message or "")[:300]}
                for c in dep.status.conditions or []
            ],
        },
        "rolloutHistory": [
            {
                "revision": rev,
                "createdAt": rs.metadata.creation_timestamp.isoformat() if rs.metadata.creation_timestamp else None,
                "images": {c.name: c.image for c in rs.spec.template.spec.containers},
                "memoryLimits": {
                    c.name: (c.resources.limits or {}).get("memory") if c.resources else None
                    for c in rs.spec.template.spec.containers
                },
                "readyReplicas": rs.status.ready_replicas or 0,
            }
            for rev, rs in revisions[-4:]
        ],
        "pods": [],
        "deploymentEvents": _events(core, ns, dep.metadata.name),
    }
    for pod in pods:
        statuses = list(pod.status.init_container_statuses or []) + list(pod.status.container_statuses or [])
        summary["pods"].append({
            "name": pod.metadata.name,
            "phase": pod.status.phase,
            "node": pod.spec.node_name,
            "conditions": [
                {"type": c.type, "status": c.status, "reason": c.reason, "message": (c.message or "")[:300]}
                for c in pod.status.conditions or [] if c.status != "True"
            ],
            "containers": [_status_summary(cs) for cs in statuses],
            "events": _events(core, ns, pod.metadata.name),
            "logs": {c: _logs(core, ns, pod.metadata.name, c) for c in incident.containers},
        })
    return Context(deployment=dep, revisions=revisions, pods=pods, summary=summary)
