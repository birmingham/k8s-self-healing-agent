from types import SimpleNamespace
from unittest.mock import MagicMock

from healer.detector import Incident
from healer.diagnoser import TOOL, Diagnoser, Diagnosis

INC = Incident("ns", "web", "CrashLoopBackOff", ["p"], ["web"])


def client_returning(blocks):
    c = MagicMock()
    c.messages.create.return_value = SimpleNamespace(content=blocks)
    return c


def test_forces_tool_and_parses():
    block = SimpleNamespace(type="tool_use", name="propose_remediation", input={
        "root_cause": "bad tag", "evidence": ["ErrImagePull"], "action": "rollback_deployment",
        "confidence": 0.93, "summary": "roll back"})
    c = client_returning([block])
    d = Diagnoser("claude-sonnet-5-5", client=c).diagnose(INC, {"k": "v"})
    assert d.action == "rollback_deployment" and d.confidence == 0.93
    kwargs = c.messages.create.call_args.kwargs
    assert kwargs["tool_choice"] == {"type": "tool", "name": "propose_remediation"}
    assert "<telemetry>" in kwargs["messages"][0]["content"]


def test_schema_enum_matches_actions():
    assert TOOL["input_schema"]["properties"]["action"]["enum"] == [
        "restart_pod", "rollback_deployment", "increase_memory_limit", "escalate"]


def test_unknown_action_becomes_escalate():
    d = Diagnosis.from_tool_input({"action": "kubectl delete ns prod", "confidence": 5})
    assert d.action == "escalate" and d.confidence == 1.0


def test_api_failure_escalates():
    c = MagicMock()
    c.messages.create.side_effect = TimeoutError("boom")
    d = Diagnoser("m", client=c).diagnose(INC, {})
    assert d.action == "escalate" and d.fallback
