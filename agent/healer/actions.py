"""Executors. Every mutation is a JSON Patch guarded by `test` operations so a
concurrent change by a human or a GitOps controller makes the patch fail
instead of being silently overwritten."""

from __future__ import annotations

from .policy import REVISION_HASH_LABEL


class Executor:
    def __init__(self, core, apps, api_client):
        self.core = core
        self.apps = apps
        self.api_client = api_client

    def run(self, decision, incident, ctx) -> dict:
        fn = getattr(self, f"_{decision.action}")
        return fn(decision.params, incident, ctx)

    def _guard(self, ctx) -> list[dict]:
        # metadata.generation only changes on spec edits, so status updates by
        # the Deployment controller during the diagnosis don't trip this.
        return [{"op": "test", "path": "/metadata/generation", "value": ctx.generation}]

    def _restart_pod(self, params, incident, ctx) -> dict:
        deleted = []
        for name in params["pods"]:
            self.core.delete_namespaced_pod(name, incident.namespace)
            deleted.append(name)
        return {"deleted_pods": deleted}

    def _rollback_deployment(self, params, incident, ctx) -> dict:
        target = dict(ctx.revisions)[params["to_revision"]]
        template = self.api_client.sanitize_for_serialization(target.spec.template)
        template.get("metadata", {}).get("labels", {}).pop(REVISION_HASH_LABEL, None)
        ops = self._guard(ctx) + [{"op": "replace", "path": "/spec/template", "value": template}]
        self.apps.patch_namespaced_deployment(incident.deployment, incident.namespace, ops)
        return {"rolled_back_to_revision": params["to_revision"], "images": params["to_images"]}

    def _increase_memory_limit(self, params, incident, ctx) -> dict:
        containers = ctx.deployment.spec.template.spec.containers
        idx = next(i for i, c in enumerate(containers) if c.name == params["container"])
        res = self.api_client.sanitize_for_serialization(containers[idx].resources) or {}
        res.setdefault("limits", {})["memory"] = params["to"]
        # Requests follow the limit. GKE Autopilot requires requests == limits,
        # and on RKE2 it keeps the pod's QoS class unchanged.
        res.setdefault("requests", {})["memory"] = params["to"]
        base = f"/spec/template/spec/containers/{idx}"
        ops = self._guard(ctx) + [
            {"op": "test", "path": f"{base}/name", "value": params["container"]},
            {"op": "add", "path": f"{base}/resources", "value": res},
        ]
        self.apps.patch_namespaced_deployment(incident.deployment, incident.namespace, ops)
        return {"container": params["container"], "memory": f"{params['from']} -> {params['to']}"}
