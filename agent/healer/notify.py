"""Structured audit log (stdout -> Cloud Logging / any log pipeline) plus optional Slack."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

import httpx

log = logging.getLogger("healer")


def setup_logging():
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])


def emit(event: str, severity: str = "INFO", **fields):
    record = {"severity": severity, "time": datetime.now(UTC).isoformat(), "event": event, **fields}
    log.info(json.dumps(record, default=str))


class Notifier:
    ICONS = {"execute": ":white_check_mark:", "propose": ":raised_hand:", "escalate": ":rotating_light:"}

    def __init__(self, webhook: str | None, cluster: str):
        self.webhook = webhook
        self.cluster = cluster

    def send(self, outcome: str, incident, diagnosis, decision, result: dict | None = None, approve_cmd: str | None = None):
        if not self.webhook:
            return
        lines = [
            f"{self.ICONS.get(outcome, '')} *{outcome.upper()}* `{incident.key}` on `{self.cluster}`",
            f"*Root cause:* {diagnosis.root_cause}",
            f"*Action:* `{decision.action}` ({decision.reason}), confidence {diagnosis.confidence:.2f}",
        ]
        if decision.params:
            lines.append(f"*Params:* `{json.dumps(decision.params)}`")
        if result:
            lines.append(f"*Result:* `{json.dumps(result)}`")
        if approve_cmd:
            lines.append(f"*Approve with:* `{approve_cmd}`")
        lines.append(diagnosis.summary)
        try:
            httpx.post(self.webhook, json={"text": "\n".join(lines)}, timeout=10)
        except httpx.HTTPError as exc:
            emit("notify_failed", "WARNING", error=str(exc))
