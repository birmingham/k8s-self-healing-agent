"""Policy and runtime configuration.

The policy file (mounted from a ConfigMap) is the single place that decides
what the agent is allowed to do. The LLM never sees or changes it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import yaml

# Annotation/label prefix. A DNS subdomain the author controls.
PREFIX = "healer.birmingham.github.io"
MANAGED_LABEL = f"{PREFIX}/managed"
ANN_HISTORY = f"{PREFIX}/history"
ANN_PROPOSAL = f"{PREFIX}/proposal"
ANN_APPROVE = f"{PREFIX}/approve"
ANN_LAST_DIAGNOSIS = f"{PREFIX}/last-diagnosis"

# Every action the agent knows how to perform. The LLM must pick one of these.
ACTIONS = ("restart_pod", "rollback_deployment", "increase_memory_limit", "escalate")

# Which remediations make sense for which failure signature. Anything else is
# rejected no matter how confident the model is.
ALLOWED_BY_SIGNATURE: dict[str, tuple[str, ...]] = {
    "CrashLoopBackOff": ("restart_pod", "rollback_deployment"),
    "ImagePullBackOff": ("rollback_deployment",),
    "CreateContainerConfigError": ("rollback_deployment",),
    "CreateContainerError": ("rollback_deployment",),
    "OOMKilled": ("increase_memory_limit", "rollback_deployment"),
    "Unschedulable": ("rollback_deployment",),
}


@dataclass
class ActionPolicy:
    mode: str = "propose"  # auto | propose | disabled
    max_pods: int = 3  # restart_pod
    max_factor: float = 2.0  # increase_memory_limit
    ceiling: str = "1Gi"  # increase_memory_limit


@dataclass
class Policy:
    watch_namespaces: list[str] = field(default_factory=lambda: ["healer-demo"])
    loop_interval_seconds: int = 30
    min_incident_age_seconds: int = 45
    pending_threshold_seconds: int = 300
    cooldown_minutes: int = 5
    escalation_cooldown_minutes: int = 60
    proposal_ttl_minutes: int = 30
    min_confidence: float = 0.7
    dry_run: bool = False
    rate_window_minutes: int = 60
    max_actions_per_workload: int = 3
    actions: dict[str, ActionPolicy] = field(default_factory=dict)

    def action(self, name: str) -> ActionPolicy | None:
        return self.actions.get(name)


def load_policy(path: str | None) -> Policy:
    if not path or not os.path.exists(path):
        return Policy(actions={"rollback_deployment": ActionPolicy(mode="propose")})
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    rate = raw.get("rateLimit", {}) or {}
    actions = {}
    for name, spec in (raw.get("actions") or {}).items():
        if name not in ACTIONS or name == "escalate":
            raise ValueError(f"unknown action in policy: {name}")
        spec = spec or {}
        mode = spec.get("mode", "propose")
        if mode not in ("auto", "propose", "disabled"):
            raise ValueError(f"invalid mode for {name}: {mode}")
        actions[name] = ActionPolicy(
            mode=mode,
            max_pods=int(spec.get("maxPods", 3)),
            max_factor=float(spec.get("maxFactor", 2.0)),
            ceiling=str(spec.get("ceiling", "1Gi")),
        )
    return Policy(
        watch_namespaces=list(raw.get("watchNamespaces", ["healer-demo"])),
        loop_interval_seconds=int(raw.get("loopIntervalSeconds", 30)),
        min_incident_age_seconds=int(raw.get("minIncidentAgeSeconds", 45)),
        pending_threshold_seconds=int(raw.get("pendingThresholdSeconds", 300)),
        cooldown_minutes=int(raw.get("cooldownMinutes", 5)),
        escalation_cooldown_minutes=int(raw.get("escalationCooldownMinutes", 60)),
        proposal_ttl_minutes=int(raw.get("proposalTtlMinutes", 30)),
        min_confidence=float(raw.get("minConfidence", 0.7)),
        dry_run=bool(raw.get("dryRun", False)),
        rate_window_minutes=int(rate.get("windowMinutes", 60)),
        max_actions_per_workload=int(rate.get("maxActionsPerWorkload", 3)),
        actions=actions,
    )


@dataclass
class Settings:
    """Runtime settings from environment variables."""

    policy_path: str = os.environ.get("HEALER_POLICY", "/etc/healer/policy.yaml")
    model: str = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
    max_tokens: int = int(os.environ.get("ANTHROPIC_MAX_TOKENS", "1500"))
    slack_webhook: str | None = os.environ.get("SLACK_WEBHOOK_URL") or None
    cluster_name: str = os.environ.get("CLUSTER_NAME", "unknown")
    health_port: int = int(os.environ.get("HEALTH_PORT", "8080"))
