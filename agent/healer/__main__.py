"""Entrypoint: python -m healer"""

from __future__ import annotations

import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from kubernetes import client, config

from . import __version__
from .actions import Executor
from .agent import Agent
from .config import Settings, load_policy
from .diagnoser import Diagnoser
from .notify import Notifier, emit, setup_logging

_last_tick = time.monotonic()
_stop = threading.Event()


def _health_server(port: int, max_age: float):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            healthy = self.path == "/healthz" and time.monotonic() - _last_tick < max_age
            self.send_response(200 if healthy else 503)
            self.end_headers()
            self.wfile.write(b"ok" if healthy else b"stale")

        def log_message(self, *args):
            pass

    server = HTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()


def main():
    global _last_tick
    setup_logging()
    settings = Settings()
    policy = load_policy(settings.policy_path)

    try:
        config.load_incluster_config()
        mode = "in-cluster"
    except config.ConfigException:
        config.load_kube_config()  # local development against the current context
        mode = "kubeconfig"

    api_client = client.ApiClient()
    core, apps = client.CoreV1Api(api_client), client.AppsV1Api(api_client)
    agent = Agent(
        settings, policy, core, apps, api_client,
        diagnoser=Diagnoser(settings.model, settings.max_tokens),
        executor=Executor(core, apps, api_client),
        notifier=Notifier(settings.slack_webhook, settings.cluster_name),
    )

    signal.signal(signal.SIGTERM, lambda *_: _stop.set())
    signal.signal(signal.SIGINT, lambda *_: _stop.set())
    _health_server(settings.health_port, max_age=policy.loop_interval_seconds * 3 + 120)

    emit("agent_started", version=__version__, cluster=settings.cluster_name, auth=mode, model=settings.model,
         namespaces=policy.watch_namespaces, dry_run=policy.dry_run,
         actions={k: v.mode for k, v in policy.actions.items()})

    while not _stop.is_set():
        try:
            agent.tick()
        except Exception as exc:  # never let one bad loop kill the agent
            emit("tick_failed", "ERROR", error=f"{type(exc).__name__}: {exc}")
        _last_tick = time.monotonic()
        _stop.wait(policy.loop_interval_seconds)
    emit("agent_stopped")


if __name__ == "__main__":
    main()
