from datetime import timedelta
from unittest.mock import MagicMock

from fixtures import NOW, pod
from kubernetes import client as k

from healer.config import Policy
from healer.detector import detect, pod_signature


def sig(p):
    return pod_signature(p, NOW, pending_threshold=300, min_age=45)


def test_oom_beats_crashloop():
    assert sig(pod(waiting="CrashLoopBackOff", last_reason="OOMKilled")) == ("OOMKilled", ["web"])


def test_old_oom_on_healthy_pod_ignored():
    assert sig(pod(last_reason="OOMKilled", ready=True, finished_ago_s=3600)) is None


def test_image_pull_normalized():
    assert sig(pod(waiting="ErrImagePull")) == ("ImagePullBackOff", ["web"])


def test_crashloop():
    assert sig(pod(waiting="CrashLoopBackOff", last_reason="Error")) == ("CrashLoopBackOff", ["web"])


def test_young_pod_ignored():
    assert sig(pod(waiting="CrashLoopBackOff", age_s=10)) is None


def test_unschedulable_after_threshold():
    cond = [k.V1PodCondition(type="PodScheduled", status="False", reason="Unschedulable")]
    p = pod(phase="Pending", conditions=cond, age_s=600)
    p.status.container_statuses = None
    assert sig(p) == ("Unschedulable", [])


def test_detect_groups_by_deployment():
    core, apps = MagicMock(), MagicMock()
    core.list_namespaced_pod.return_value = k.V1PodList(items=[
        pod("a", waiting="CrashLoopBackOff"), pod("b", waiting="CrashLoopBackOff"), pod("c", ready=True)])
    apps.read_namespaced_replica_set.return_value = k.V1ReplicaSet(metadata=k.V1ObjectMeta(
        name="web-h2", owner_references=[k.V1OwnerReference(api_version="apps/v1", kind="Deployment",
                                                            name="web", uid="u", controller=True)]))
    incidents = detect(core, apps, "healer-demo", Policy(), NOW + timedelta(seconds=0))
    assert len(incidents) == 1
    assert incidents[0].key == "healer-demo/web/CrashLoopBackOff"
    assert incidents[0].pods == ["a", "b"]
    apps.read_namespaced_replica_set.assert_called_once()  # cached per loop
