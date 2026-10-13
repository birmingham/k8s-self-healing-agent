"""The control loop: detect -> collect -> diagnose -> decide -> act / propose / escalate."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

from kubernetes.client.exceptions import ApiException

from . import state
from .collector import collect
from .config import ANN_APPROVE, ANN_PROPOSAL
from .detector import detect
from .diagnoser import Diagnosis
from .notify import emit
from .policy import Decision, evaluate, revalidate


class Agent:
    def __init__(self, settings, policy, core, apps, api_client, diagnoser, executor, notifier):
        self.settings = settings
        self.policy = policy
        self.core = core
        self.apps = apps
        self.api_client = api_client
        self.diagnoser = diagnoser
        self.executor = executor
        self.notifier = notifier
        self.store = state.StateStore(apps)

    # ---- loop ---------------------------------------------------------------

    def tick(self, now: datetime | None = None):
        now = now or datetime.now(UTC)
        for ns in self.policy.watch_namespaces:
            try:
                incidents = detect(self.core, self.apps, ns, self.policy, now)
            except ApiException as exc:
                emit("detect_failed", "ERROR", namespace=ns, status=exc.status, error=exc.reason)
                continue
            open_incidents = {i.key: i for i in incidents}
            try:
                self.process_approvals(ns, open_incidents, now)
            except ApiException as exc:
                emit("approvals_failed", "ERROR", namespace=ns, status=exc.status, error=exc.reason)
            for incident in incidents:
                try:
                    self.handle(incident, now)
                except ApiException as exc:
                    emit("handle_failed", "ERROR", incident=incident.key, status=exc.status, error=exc.reason)

    # ---- new incidents ------------------------------------------------------

    def handle(self, incident, now: datetime):
        dep = self.apps.read_namespaced_deployment(incident.deployment, incident.namespace)
        if state.in_cooldown(dep, incident.signature, now, self.policy.cooldown_minutes,
                             self.policy.escalation_cooldown_minutes):
            return
        pending = state.proposal(dep)
        if pending and pending.get("incident") == incident.key and not self._expired(pending, now):
            return  # waiting on a human

        emit("incident_detected", "WARNING", incident=incident.key, pods=incident.pods,
             containers=incident.containers)
        ctx = collect(self.core, self.apps, incident)
        diagnosis = self.diagnoser.diagnose(incident, ctx.summary)
        emit("diagnosis", incident=incident.key, **diagnosis.to_dict())

        recent = state.recent_history(ctx.deployment, now, self.policy.rate_window_minutes)
        decision = evaluate(self.policy, incident, diagnosis, ctx, recent, self.api_client)
        emit("decision", incident=incident.key, outcome=decision.outcome, action=decision.action,
             reason=decision.reason, params=decision.params)

        sets: dict[str, str] = {}
        removes: tuple[str, ...] = ()
        result = None
        approve_cmd = None

        if decision.outcome == "execute":
            result, error = self._execute(decision, incident, ctx)
            if error:
                decision = Decision("escalate", decision.action, error, decision.params)
            else:
                sets[state.ANN_HISTORY] = state.history_value(ctx.deployment, self._history_entry(
                    incident, decision, diagnosis, now, approved_by=None))
                removes = (ANN_PROPOSAL, ANN_APPROVE)
        elif decision.outcome == "propose":
            prop = {
                "id": uuid.uuid4().hex[:8],
                "incident": incident.key,
                "signature": incident.signature,
                "action": decision.action,
                "params": decision.params,
                "reason": decision.reason,
                "root_cause": diagnosis.root_cause,
                "confidence": round(diagnosis.confidence, 2),
                "container": diagnosis.container,
                "created": state.now_iso(now),
            }
            sets[ANN_PROPOSAL] = json.dumps(prop, sort_keys=True)
            removes = (ANN_APPROVE,)
            approve_cmd = (f"kubectl -n {incident.namespace} annotate deploy/{incident.deployment} "
                           f"{ANN_APPROVE}={prop['id']} --overwrite")
            emit("proposal_created", incident=incident.key, proposal=prop, approve_with=approve_cmd)

        sets[state.ANN_LAST_DIAGNOSIS] = state.diagnosed_value(ctx.deployment, incident.signature,
                                                               decision.outcome, now)
        self._write_state(incident, sets, removes)
        self.notifier.send(decision.outcome, incident, diagnosis, decision, result, approve_cmd)

    # ---- approvals ----------------------------------------------------------

    def process_approvals(self, namespace: str, open_incidents: dict, now: datetime):
        for dep in self.apps.list_namespaced_deployment(namespace).items:
            prop = state.proposal(dep)
            if not prop:
                continue
            key = prop.get("incident")
            if self._expired(prop, now):
                self.store.write(dep, {}, (ANN_PROPOSAL, ANN_APPROVE))
                emit("proposal_expired", incident=key, proposal_id=prop["id"])
                continue
            approved = state.approved_id(dep)
            if not approved:
                continue
            if approved != prop["id"]:
                emit("approval_mismatch", "WARNING", incident=key, approved=approved, proposal_id=prop["id"])
                continue

            incident = open_incidents.get(key)
            if incident is None:
                self.store.write(dep, {}, (ANN_PROPOSAL, ANN_APPROVE))
                emit("proposal_obsolete", incident=key, proposal_id=prop["id"],
                     reason="incident resolved before approval")
                continue

            ctx = collect(self.core, self.apps, incident)
            diagnosis = Diagnosis(root_cause=prop.get("root_cause", ""), action=prop["action"],
                                  confidence=float(prop.get("confidence", 0)), summary="",
                                  container=prop.get("container"))
            params, err = revalidate(self.policy, incident, diagnosis, ctx, self.api_client, prop.get("params", {}))
            if err:
                decision = Decision("escalate", prop["action"], err, prop.get("params", {}))
                self.store.write(ctx.deployment, {}, (ANN_PROPOSAL, ANN_APPROVE))
                emit("approval_rejected", "WARNING", incident=key, proposal_id=prop["id"], reason=err)
                self.notifier.send("escalate", incident, diagnosis, decision)
                continue

            decision = Decision("execute", prop["action"], f"approved (proposal {prop['id']})", params)
            result, error = self._execute(decision, incident, ctx)
            sets = {}
            if error:
                decision = Decision("escalate", decision.action, error, params)
            else:
                sets[state.ANN_HISTORY] = state.history_value(ctx.deployment, self._history_entry(
                    incident, decision, diagnosis, now, approved_by=prop["id"]))
            sets[state.ANN_LAST_DIAGNOSIS] = state.diagnosed_value(ctx.deployment, incident.signature,
                                                                   decision.outcome, now)
            self._write_state(incident, sets, (ANN_PROPOSAL, ANN_APPROVE))
            self.notifier.send(decision.outcome, incident, diagnosis, decision, result)

    # ---- helpers ------------------------------------------------------------

    def _execute(self, decision, incident, ctx):
        try:
            result = self.executor.run(decision, incident, ctx)
        except ApiException as exc:
            reason = ("workload changed while the agent was deciding; refusing to overwrite"
                      if exc.status == 422 else f"action failed: HTTP {exc.status} {exc.reason}")
            emit("action_failed", "ERROR", incident=incident.key, action=decision.action, status=exc.status,
                 reason=reason)
            return None, reason
        emit("action_executed", "NOTICE", incident=incident.key, action=decision.action,
             params=decision.params, result=result)
        return result, None

    def _write_state(self, incident, sets, removes):
        # Re-read: an executed action may have changed the object since collection.
        dep = self.apps.read_namespaced_deployment(incident.deployment, incident.namespace)
        self.store.write(dep, sets, removes)

    def _expired(self, prop: dict, now: datetime) -> bool:
        try:
            created = datetime.fromisoformat(prop["created"])
        except (KeyError, ValueError):
            return True
        return now - created > timedelta(minutes=self.policy.proposal_ttl_minutes)

    @staticmethod
    def _history_entry(incident, decision, diagnosis, now, approved_by):
        return {
            "ts": state.now_iso(now),
            "signature": incident.signature,
            "action": decision.action,
            "params": decision.params,
            "confidence": round(diagnosis.confidence, 2),
            "approved_by_proposal": approved_by,
        }
