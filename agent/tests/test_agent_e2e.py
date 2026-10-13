"""Drive the real Agent loop against a stateful fake API that applies JSON Patches."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import jsonpatch
from fixtures import NOW, deployment, pod, replicaset
from kubernetes import client as k
from kubernetes.client import ApiClient

from healer import state
from healer.actions import Executor
from healer.agent import Agent
from healer.config import ANN_APPROVE, ActionPolicy, Policy
from healer.diagnoser import Diagnosis

API = ApiClient()


class FakeApps:
    def __init__(self, dep, rss):
        self.dep = API.sanitize_for_serialization(dep)
        self.rss = rss
        self.patches = []

    def _obj(self):
        return API._ApiClient__deserialize(self.dep, "V1Deployment")

    def read_namespaced_deployment(self, name, ns):
        return self._obj()

    def list_namespaced_deployment(self, ns):
        return k.V1DeploymentList(items=[self._obj()])

    def patch_namespaced_deployment(self, name, ns, ops):
        self.patches.append(ops)
        self.dep = jsonpatch.apply_patch(self.dep, ops)
        if any(o["path"].startswith("/spec") and o["op"] != "test" for o in ops):
            self.dep["metadata"]["generation"] += 1

    def list_namespaced_replica_set(self, ns, label_selector=None):
        return k.V1ReplicaSetList(items=self.rss)

    def read_namespaced_replica_set(self, name, ns):
        return next(r for r in self.rss if r.metadata.name == name)


def make_agent(sig_pod, diagnosis, action_modes):
    apps = FakeApps(deployment(image="nginx:2"), [replicaset("web-h1", 1, "nginx:1"),
                                                  replicaset("web-h2", 2, "nginx:2")])
    core = MagicMock()
    core.list_namespaced_pod.return_value = k.V1PodList(items=[sig_pod])
    core.read_namespaced_pod.return_value = sig_pod
    core.read_namespaced_pod_log.return_value = "FATAL: something broke\n"
    core.list_namespaced_event.return_value = k.CoreV1EventList(items=[])
    diagnoser = MagicMock()
    diagnoser.diagnose.return_value = diagnosis
    pol = Policy(actions={a: ActionPolicy(mode=m) for a, m in action_modes.items()})
    notifier = MagicMock()
    agent = Agent(SimpleNamespace(), pol, core, apps, API, diagnoser, Executor(core, apps, API), notifier)
    return agent, apps, core, diagnoser, notifier


def test_bad_rollout_rolled_back_automatically():
    d = Diagnosis("image tag does not exist", "rollback_deployment", 0.95, "rolled back")
    agent, apps, _, diagnoser, notifier = make_agent(pod(waiting="ImagePullBackOff"), d,
                                                     {"rollback_deployment": "auto"})
    agent.tick(NOW)
    assert apps.dep["spec"]["template"]["spec"]["containers"][0]["image"] == "nginx:1"
    hist = state.history(apps._obj())
    assert hist[0]["action"] == "rollback_deployment" and hist[0]["params"]["to_revision"] == 1
    assert notifier.send.call_args.args[0] == "execute"

    # Next loop inside the cooldown: no second LLM call.
    agent.tick(NOW + timedelta(minutes=1))
    assert diagnoser.diagnose.call_count == 1


def test_oom_proposal_then_human_approval():
    d = Diagnosis("working set exceeds limit", "increase_memory_limit", 0.9, "raise it", container="web")
    agent, apps, core, diagnoser, notifier = make_agent(
        pod(waiting="CrashLoopBackOff", last_reason="OOMKilled"), d, {"increase_memory_limit": "propose"})

    agent.tick(NOW)
    prop = state.proposal(apps._obj())
    assert prop["action"] == "increase_memory_limit" and prop["params"]["to"] == "256Mi"
    limits = apps.dep["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"]
    assert limits["memory"] == "128Mi"  # nothing changed yet

    # Waiting on a human: no re-diagnosis even after the cooldown.
    agent.tick(NOW + timedelta(minutes=10))
    assert diagnoser.diagnose.call_count == 1

    # Human approves with the wrong id: ignored.
    apps.dep["metadata"]["annotations"][ANN_APPROVE] = "nope"
    agent.tick(NOW + timedelta(minutes=11))
    assert apps.dep["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"]["memory"] == "128Mi"

    # Correct approval executes and clears the proposal.
    apps.dep["metadata"]["annotations"][ANN_APPROVE] = prop["id"]
    agent.tick(NOW + timedelta(minutes=12))
    res = apps.dep["spec"]["template"]["spec"]["containers"][0]["resources"]
    assert res["limits"]["memory"] == "256Mi" and res["requests"]["memory"] == "256Mi"
    obj = apps._obj()
    assert state.proposal(obj) is None and state.approved_id(obj) is None
    assert state.history(obj)[-1]["approved_by_proposal"] == prop["id"]


def test_missing_config_escalates_without_touching_workload():
    d = Diagnosis("DATABASE_URL not set", "escalate", 0.9, "needs a config change")
    agent, apps, _, _, notifier = make_agent(pod(waiting="CrashLoopBackOff", last_reason="Error"), d,
                                             {"restart_pod": "auto", "rollback_deployment": "auto"})
    before = dict(apps.dep["spec"])
    agent.tick(NOW)
    assert apps.dep["spec"] == before
    assert notifier.send.call_args.args[0] == "escalate"
    # Escalations get the long cooldown.
    agent.tick(NOW + timedelta(minutes=30))
    assert agent.diagnoser.diagnose.call_count == 1


def test_concurrent_edit_aborts_action():
    d = Diagnosis("bad tag", "rollback_deployment", 0.95, "")
    agent, apps, _, _, notifier = make_agent(pod(waiting="ImagePullBackOff"), d, {"rollback_deployment": "auto"})
    real_collect_dep = apps.read_namespaced_deployment

    calls = {"n": 0}

    def read(name, ns):
        calls["n"] += 1
        obj = real_collect_dep(name, ns)
        if calls["n"] == 2:  # the collect() read; then a human edits the spec
            apps.dep["metadata"]["generation"] += 1
        return obj

    apps.read_namespaced_deployment = read

    def patch(name, ns, ops):
        try:
            FakeApps.patch_namespaced_deployment(apps, name, ns, ops)
        except jsonpatch.JsonPatchTestFailed:
            raise k.exceptions.ApiException(status=422, reason="Unprocessable Entity") from None

    apps.patch_namespaced_deployment = patch
    agent.tick(NOW)
    assert apps.dep["spec"]["template"]["spec"]["containers"][0]["image"] == "nginx:2"
    decision = notifier.send.call_args.args[3]
    assert decision.outcome == "escalate" and "changed" in decision.reason
