"""Deterministic failure detection. No LLM involved: cheap, predictable, testable."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .config import MANAGED_LABEL

IMAGE_PULL = {"ImagePullBackOff", "ErrImagePull"}
CONFIG_ERRORS = {"CreateContainerConfigError", "CreateContainerError"}
OOM_RECENT_SECONDS = 600


@dataclass
class Incident:
    namespace: str
    deployment: str
    signature: str
    pods: list[str] = field(default_factory=list)
    containers: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.namespace}/{self.deployment}/{self.signature}"


def pod_signature(pod, now: datetime, pending_threshold: int, min_age: int):
    """Return (signature, [container names]) for an unhealthy pod, else None."""
    created = pod.metadata.creation_timestamp
    age = (now - created).total_seconds() if created else 0
    if age < min_age:
        return None

    status = pod.status
    statuses = list(status.init_container_statuses or []) + list(status.container_statuses or [])
    oom, pull, config, crash = [], [], [], []
    for cs in statuses:
        last = cs.last_state.terminated if cs.last_state else None
        if last and last.reason == "OOMKilled":
            finished = last.finished_at
            recent = finished is not None and (now - finished).total_seconds() < OOM_RECENT_SECONDS
            if not cs.ready or recent:
                oom.append(cs.name)
        waiting = cs.state.waiting if cs.state else None
        reason = waiting.reason if waiting else None
        if reason in IMAGE_PULL:
            pull.append(cs.name)
        elif reason in CONFIG_ERRORS:
            config.append((reason, cs.name))
        elif reason == "CrashLoopBackOff":
            crash.append(cs.name)

    # Priority: the most specific root-cause signal wins. An OOM-killed
    # container also shows CrashLoopBackOff, but OOMKilled is the real story.
    if oom:
        return "OOMKilled", oom
    if pull:
        return "ImagePullBackOff", pull
    if config:
        return config[0][0], [name for _, name in config]
    if crash:
        return "CrashLoopBackOff", crash
    if status.phase == "Pending" and age >= pending_threshold:
        for cond in status.conditions or []:
            if cond.type == "PodScheduled" and cond.status == "False" and cond.reason == "Unschedulable":
                return "Unschedulable", []
    return None


def owning_deployment(pod, apps, rs_cache: dict) -> str | None:
    """Walk Pod -> ReplicaSet -> Deployment via controller owner references."""
    for ref in pod.metadata.owner_references or []:
        if ref.controller and ref.kind == "ReplicaSet":
            if ref.name not in rs_cache:
                rs = apps.read_namespaced_replica_set(ref.name, pod.metadata.namespace)
                dep = None
                for rref in rs.metadata.owner_references or []:
                    if rref.controller and rref.kind == "Deployment":
                        dep = rref.name
                rs_cache[ref.name] = dep
            return rs_cache[ref.name]
    return None


def detect(core, apps, namespace: str, policy, now: datetime) -> list[Incident]:
    pods = core.list_namespaced_pod(namespace, label_selector=f"{MANAGED_LABEL}=true").items
    rs_cache: dict = {}
    incidents: dict[tuple[str, str], Incident] = {}
    for pod in pods:
        if pod.metadata.deletion_timestamp:
            continue  # already going away
        sig = pod_signature(pod, now, policy.pending_threshold_seconds, policy.min_incident_age_seconds)
        if not sig:
            continue
        deployment = owning_deployment(pod, apps, rs_cache)
        if not deployment:
            continue  # only Deployment-owned workloads are in scope
        signature, containers = sig
        inc = incidents.setdefault(
            (deployment, signature), Incident(namespace, deployment, signature)
        )
        inc.pods.append(pod.metadata.name)
        for c in containers:
            if c not in inc.containers:
                inc.containers.append(c)
    return list(incidents.values())
