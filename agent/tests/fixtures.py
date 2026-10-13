"""Builders for real kubernetes client model objects."""

from datetime import UTC, datetime, timedelta

from kubernetes import client as k

NOW = datetime(2026, 10, 12, 18, 0, 0, tzinfo=UTC)
LABEL = {"app": "web", "healer.birmingham.github.io/managed": "true"}


def container(name="web", image="nginx:1", mem="128Mi"):
    return k.V1Container(
        name=name, image=image,
        resources=k.V1ResourceRequirements(requests={"cpu": "100m", "memory": mem}, limits={"memory": mem}),
        env=[k.V1EnvVar(name="LOG_LEVEL", value="info"),
             k.V1EnvVar(name="DB_PASSWORD", value_from=k.V1EnvVarSource(
                 secret_key_ref=k.V1SecretKeySelector(name="db", key="password")))],
    )


def template(image="nginx:1", mem="128Mi", hash_="abc"):
    return k.V1PodTemplateSpec(
        metadata=k.V1ObjectMeta(labels={**LABEL, "pod-template-hash": hash_}),
        spec=k.V1PodSpec(containers=[container(image=image, mem=mem)]),
    )


def deployment(image="nginx:2", mem="128Mi", annotations=None, generation=3):
    return k.V1Deployment(
        metadata=k.V1ObjectMeta(name="web", namespace="healer-demo", uid="dep-uid", generation=generation,
                                annotations=annotations if annotations is not None
                                else {"deployment.kubernetes.io/revision": "2"}),
        spec=k.V1DeploymentSpec(
            replicas=2, selector=k.V1LabelSelector(match_labels={"app": "web"}),
            strategy=k.V1DeploymentStrategy(type="RollingUpdate"),
            template=k.V1PodTemplateSpec(metadata=k.V1ObjectMeta(labels=dict(LABEL)),
                                         spec=k.V1PodSpec(containers=[container(image=image, mem=mem)])),
        ),
        status=k.V1DeploymentStatus(ready_replicas=2, conditions=[]),
    )


def replicaset(name, revision, image, mem="128Mi"):
    return k.V1ReplicaSet(
        metadata=k.V1ObjectMeta(
            name=name, namespace="healer-demo",
            annotations={"deployment.kubernetes.io/revision": str(revision)},
            owner_references=[k.V1OwnerReference(api_version="apps/v1", kind="Deployment", name="web",
                                                 uid="dep-uid", controller=True)],
            creation_timestamp=NOW - timedelta(minutes=30 - revision),
        ),
        spec=k.V1ReplicaSetSpec(selector=k.V1LabelSelector(match_labels={"app": "web"}),
                                template=template(image=image, mem=mem, hash_=f"h{revision}")),
        status=k.V1ReplicaSetStatus(replicas=2, ready_replicas=0 if revision == 2 else 2),
    )


def pod(name="web-h2-aaaa", waiting=None, last_reason=None, ready=False, age_s=300, phase="Running",
        conditions=None, rs="web-h2", finished_ago_s=30):
    last = None
    if last_reason:
        last = k.V1ContainerState(terminated=k.V1ContainerStateTerminated(
            exit_code=137 if last_reason == "OOMKilled" else 1, reason=last_reason,
            finished_at=NOW - timedelta(seconds=finished_ago_s)))
    state = k.V1ContainerState(waiting=k.V1ContainerStateWaiting(reason=waiting, message="back-off")) if waiting \
        else k.V1ContainerState(running=k.V1ContainerStateRunning())
    return k.V1Pod(
        metadata=k.V1ObjectMeta(
            name=name, namespace="healer-demo", labels=dict(LABEL),
            creation_timestamp=NOW - timedelta(seconds=age_s),
            owner_references=[k.V1OwnerReference(api_version="apps/v1", kind="ReplicaSet", name=rs,
                                                 uid="rs-uid", controller=True)],
        ),
        spec=k.V1PodSpec(containers=[container()], node_name="node-1"),
        status=k.V1PodStatus(
            phase=phase, conditions=conditions or [],
            container_statuses=[k.V1ContainerStatus(
                name="web", image="nginx:2", image_id="", ready=ready, restart_count=4,
                state=state, last_state=last)],
        ),
    )
