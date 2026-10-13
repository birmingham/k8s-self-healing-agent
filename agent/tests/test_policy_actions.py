import json
from datetime import timedelta
from unittest.mock import MagicMock

import jsonpatch
import pytest
from fixtures import NOW, deployment, pod, replicaset
from kubernetes.client import ApiClient

from healer.actions import Executor
from healer.collector import Context
from healer.config import ActionPolicy, Policy
from healer.detector import Incident
from healer.diagnoser import Diagnosis
from healer.policy import evaluate, revalidate

API = ApiClient()


def policy(**over):
    p = Policy(actions={
        "restart_pod": ActionPolicy(mode="auto"),
        "rollback_deployment": ActionPolicy(mode="auto"),
        "increase_memory_limit": ActionPolicy(mode="propose", max_factor=2.0, ceiling="1Gi"),
    })
    for k, v in over.items():
        setattr(p, k, v)
    return p


def ctx(image="nginx:2", mem="128Mi", revisions=None):
    revs = revisions if revisions is not None else [
        (1, replicaset("web-h1", 1, "nginx:1")), (2, replicaset("web-h2", 2, image, mem))]
    return Context(deployment=deployment(image=image, mem=mem), revisions=revs,
                   pods=[pod(waiting="CrashLoopBackOff")])


def diag(action, confidence=0.9, container=None):
    return Diagnosis(root_cause="x", action=action, confidence=confidence, summary="s", container=container)


def inc(sig="CrashLoopBackOff", containers=("web",)):
    return Incident("healer-demo", "web", sig, pods=["web-h2-aaaa"], containers=list(containers))


def test_rollback_auto_executes():
    d = evaluate(policy(), inc("ImagePullBackOff"), diag("rollback_deployment"), ctx("nginx:typo"), [], API)
    assert d.outcome == "execute"
    assert d.params == {"from_revision": 2, "to_revision": 1, "to_images": {"web": "nginx:1"}}


def test_rollback_without_history_escalates():
    c = ctx(revisions=[(1, replicaset("web-h1", 1, "nginx:1"))])
    d = evaluate(policy(), inc(), diag("rollback_deployment"), c, [], API)
    assert d.outcome == "escalate" and "no earlier revision" in d.reason


def test_restart_not_allowed_for_image_pull():
    d = evaluate(policy(), inc("ImagePullBackOff"), diag("restart_pod"), ctx(), [], API)
    assert d.outcome == "escalate" and "not a valid remediation" in d.reason


def test_low_confidence_becomes_proposal():
    d = evaluate(policy(), inc(), diag("restart_pod", confidence=0.4), ctx(), [], API)
    assert d.outcome == "propose" and "confidence" in d.reason


def test_dry_run_never_executes():
    d = evaluate(policy(dry_run=True), inc(), diag("restart_pod"), ctx(), [], API)
    assert d.outcome == "propose"


def test_failed_fix_not_repeated():
    recent = [{"ts": (NOW - timedelta(minutes=8)).isoformat(), "signature": "CrashLoopBackOff",
               "action": "restart_pod"}]
    d = evaluate(policy(), inc(), diag("restart_pod"), ctx(), recent, API)
    assert d.outcome == "escalate" and "did not resolve" in d.reason


def test_circuit_breaker():
    recent = [{"ts": NOW.isoformat(), "signature": s, "action": "a"} for s in ("A", "B", "C")]
    d = evaluate(policy(), inc(), diag("restart_pod"), ctx(), recent, API)
    assert d.outcome == "escalate" and "circuit breaker" in d.reason


def test_disabled_action_escalates():
    p = policy()
    p.actions["restart_pod"].mode = "disabled"
    assert evaluate(p, inc(), diag("restart_pod"), ctx(), [], API).outcome == "escalate"


def test_memory_doubles_and_proposes():
    d = evaluate(policy(), inc("OOMKilled"), diag("increase_memory_limit", container="web"), ctx(), [], API)
    assert d.outcome == "propose"
    assert d.params == {"container": "web", "from": "128Mi", "to": "256Mi"}


def test_memory_capped_at_ceiling():
    d = evaluate(policy(), inc("OOMKilled"), diag("increase_memory_limit"), ctx(mem="768Mi"), [], API)
    assert d.params["to"] == "1024Mi"


def test_memory_at_ceiling_escalates():
    d = evaluate(policy(), inc("OOMKilled"), diag("increase_memory_limit"), ctx(mem="1Gi"), [], API)
    assert d.outcome == "escalate" and "ceiling" in d.reason


def test_memory_wrong_container_escalates():
    d = evaluate(policy(), inc("OOMKilled"), diag("increase_memory_limit", container="sidecar"), ctx(), [], API)
    assert d.outcome == "escalate"


def test_revalidate_detects_stale_proposal():
    approved = {"container": "web", "from": "128Mi", "to": "256Mi"}
    params, err = revalidate(policy(), inc("OOMKilled"), diag("increase_memory_limit"), ctx(mem="256Mi"),
                             API, approved)
    assert params is None and "stale" in err


# ---- executors: apply the generated JSON Patch to a real document ---------

def apply(ops, c):
    doc = API.sanitize_for_serialization(c.deployment)
    return jsonpatch.apply_patch(doc, ops)


def run(action, params, c, sig="CrashLoopBackOff"):
    core, apps = MagicMock(), MagicMock()
    from healer.policy import Decision
    Executor(core, apps, API).run(Decision("execute", action, "", params), inc(sig), c)
    return core, apps


def test_rollback_patch_restores_previous_template():
    c = ctx("nginx:typo")
    _, apps = run("rollback_deployment", {"to_revision": 1, "to_images": {}}, c, "ImagePullBackOff")
    ops = apps.patch_namespaced_deployment.call_args.args[2]
    out = apply(ops, c)
    assert out["spec"]["template"]["spec"]["containers"][0]["image"] == "nginx:1"
    assert "pod-template-hash" not in out["spec"]["template"]["metadata"]["labels"]


def test_rollback_patch_fails_if_spec_changed():
    c = ctx("nginx:typo")
    _, apps = run("rollback_deployment", {"to_revision": 1, "to_images": {}}, c)
    ops = apps.patch_namespaced_deployment.call_args.args[2]
    c.deployment.metadata.generation = 99  # someone edited the deployment meanwhile
    with pytest.raises(jsonpatch.JsonPatchTestFailed):
        apply(ops, c)


def test_memory_patch_sets_requests_and_limits():
    c = ctx()
    _, apps = run("increase_memory_limit", {"container": "web", "from": "128Mi", "to": "256Mi"}, c, "OOMKilled")
    out = apply(apps.patch_namespaced_deployment.call_args.args[2], c)
    res = out["spec"]["template"]["spec"]["containers"][0]["resources"]
    assert res["limits"]["memory"] == "256Mi" and res["requests"]["memory"] == "256Mi"
    assert res["requests"]["cpu"] == "100m"  # untouched


def test_restart_deletes_pods():
    core, _ = run("restart_pod", {"pods": ["p1", "p2"]}, ctx())
    assert [c.args[0] for c in core.delete_namespaced_pod.call_args_list] == ["p1", "p2"]


def test_state_ops_apply_cleanly():
    from healer import state
    dep = deployment(annotations=None)
    ops = state._ops_for(dep, {state.ANN_PROPOSAL: json.dumps({"id": "x"})})
    out = jsonpatch.apply_patch(API.sanitize_for_serialization(dep), ops)
    assert json.loads(out["metadata"]["annotations"]["healer.birmingham.github.io/proposal"]) == {"id": "x"}


def test_fallback_escalation_reason():
    from healer.detector import Incident as I
    fb = Diagnosis.unavailable(I("ns", "web", "CrashLoopBackOff"), RuntimeError("400"))
    d = evaluate(policy(), inc(), fb, ctx(), [], API)
    assert d.outcome == "escalate" and "unavailable" in d.reason
