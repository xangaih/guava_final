"""Rep dashboard: hand-off context on transfer, follow-ups at call end, and
the rule that none of it may ever affect the call itself."""

import threading

from fastapi.testclient import TestClient
from guava.commands import TransferCommand
from guava.events import BotSessionEnded
from guava.testing import MockCall
from guava.types.call_info import PSTNCallInfo

from mercury_claims import agent as agent_module
from mercury_claims import tow_flow
from mercury_claims.client import Client


def _call(from_number="+15555551234") -> MockCall:
    return MockCall(call_info=PSTNCallInfo(from_number=from_number, to_number="+15555555555"))


def _transfers(call: MockCall) -> list[TransferCommand]:
    return [c for c in call._command_queue if isinstance(c, TransferCommand)]


# --- hand-off context ---

def test_handoff_context_has_what_a_rep_needs_and_nothing_it_must_not():
    call = _call()
    call.set_variable("flow", "fnol")
    call.set_variable("call_verified_dob", "1988-04-12")
    call.set_variable("fnol_policyholder_name", "Jordan Alvarez")
    call.set_variable("fnol_policy_number", "MCY-100245")
    call.set_variable("call_claim_number", "CLM-0001000")
    call.set_variable("call_last_request", "how many claims do I have")
    call.set_field("fnol_loss_type", "collision")
    call.set_field("fnol_loss_location", "Main St")
    call.set_field("fnol_injuries", "yes")
    agent_module.set_outcome(call, "claim_created", transfer_reason="injury_reported")

    ctx = agent_module.build_handoff_context(call)

    assert ctx["desk"] == "fnol"
    assert ctx["reason"] == "injury_reported"
    assert ctx["verified"] is True
    assert ctx["name"] == "Jordan Alvarez"
    assert ctx["policy_number"] == "MCY-100245"
    assert ctx["claim_number"] == "CLM-0001000"
    assert ctx["caller_last4"] == "1234"
    assert ctx["last_request"] == "how many claims do I have"
    assert ctx["collected"] == {"loss_type": "collision", "loss_location": "Main St", "injuries": "yes"}
    # Never: the date of birth, or the full caller number.
    flat = str(ctx)
    assert "1988" not in flat
    assert "5555551234" not in flat


def test_handoff_context_on_an_unverified_caller_is_honest_and_minimal():
    call = _call(from_number=None)
    call.set_variable("flow", "status")
    ctx = agent_module.build_handoff_context(call)
    assert ctx["verified"] is False
    assert "name" not in ctx and "collected" not in ctx and "caller_last4" not in ctx


# --- follow-ups ---

def test_follow_up_rules():
    def with_(outcome, reason=None, claim="CLM-0001000", verified=True):
        call = _call()
        if claim:
            call.set_variable("call_claim_number", claim)
        if verified:
            call.set_variable("call_verified_dob", "1988-04-12")
        if reason:
            call.set_variable("call_transfer_reason", reason)
        return agent_module.follow_up_for(call, outcome)

    assert "Injury flagged" in with_("claim_created", "injury_reported")
    assert "Arrange a tow" in with_("claim_created", "tow_request_failed")
    assert "Assign an adjuster" in with_("claim_created")
    assert "NOT filed" in with_("error", "claim_submit_failed", claim=None)
    assert "consider a callback" in with_("abandoned", claim=None)
    assert with_("abandoned", claim=None, verified=False) is None
    assert with_("status_delivered", claim=None) is None
    assert with_("completed", claim=None) is None


# --- the dashboard can never affect the call ---

def test_transfer_still_happens_when_event_recording_blows_up(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("dashboard down")

    monkeypatch.setattr(agent_module.client, "record_call_event", boom)
    call = _call()
    agent_module.transfer_to_human(call)
    assert len(_transfers(call)) == 1
    assert _transfers(call)[0].to_number == agent_module.config.HUMAN_LINE_NUMBER


def test_transfer_to_human_sends_the_context_first(recorded_events):
    call = _call()
    call.set_variable("flow", "status")
    agent_module.set_outcome(call, "transferred", transfer_reason="status_denied")
    agent_module.transfer_to_human(call, instructions="x")
    assert [e[1] for e in recorded_events] == ["transfer"]
    assert recorded_events[0][2]["reason"] == "status_denied"
    assert recorded_events[0][2]["desk"] == "status"
    assert len(_transfers(call)) == 1


def test_roadside_transfer_uses_the_roadside_line_and_records_context(recorded_events):
    call = _call()
    call.set_field("tow_needs_dispatch", "yes")
    tow_flow._on_tow_intent_complete(call)
    assert _transfers(call)[0].to_number == tow_flow.config.ROADSIDE_LINE_NUMBER
    assert recorded_events[-1][1] == "transfer"
    assert recorded_events[-1][2]["reason"] == "roadside_dispatch_needed"


def test_call_start_and_desk_switch_record_events(recorded_events):
    call = _call()
    agent_module.on_call_start(call)
    agent_module.register_flow("_test_desk", agent_module.FlowHandlers(start=lambda c: None))
    try:
        agent_module.switch_flow(call, "_test_desk")
    finally:
        agent_module._flows.pop("_test_desk", None)
    assert recorded_events[0][1:] == ("call_started", {"caller_last4": "1234"})
    assert recorded_events[1][1:] == ("desk", {"desk": "_test_desk"})


def test_last_request_is_remembered_without_changing_the_answer():
    agent_module.register_flow(
        "_test_q",
        agent_module.FlowHandlers(
            start=lambda c: None,
            handle_question=lambda c, q: "answer",
            classify_intent=lambda s: None,
        ),
    )
    try:
        call = _call()
        call.set_variable("flow", "_test_q")
        assert agent_module.on_question(call, "how far will you tow?") == "answer"
        assert call.get_variable("call_last_request") == "how far will you tow?"
        assert agent_module.on_action_request(call, "wants a person") is None
        assert call.get_variable("call_last_request") == "wants a person"
    finally:
        agent_module._flows.pop("_test_q", None)


def test_session_end_records_outcome_and_follow_up(monkeypatch, recorded_events):
    monkeypatch.setattr(agent_module.client, "record_call_session", lambda **k: None)
    call = _call()
    call.set_variable("flow", "fnol")
    call.set_variable("call_claim_number", "CLM-0001000")
    agent_module.set_outcome(call, "claim_created")
    agent_module.on_session_end(call, BotSessionEnded(termination_reason="bot-hangup"))
    ended = [e for e in recorded_events if e[1] == "call_ended"]
    assert len(ended) == 1
    assert ended[0][2]["outcome"] == "claim_created"
    assert "Assign an adjuster to new claim CLM-0001000" == ended[0][2]["follow_up"]


# --- the client method itself ---

def test_record_call_event_never_raises_when_the_backend_is_unreachable(monkeypatch):
    monkeypatch.setattr(agent_module.config, "BACKEND_BASE_URL", "http://127.0.0.1:9")  # nothing listens
    c = Client()
    thread = c.record_call_event("call-1", "call_started", {"caller_last4": "1234"})
    assert isinstance(thread, threading.Thread)
    thread.join(timeout=5)
    assert not thread.is_alive()
    c.close()


# --- the backend endpoints ---

def test_backend_call_events_endpoints(monkeypatch):
    from mock_backend import db, main

    stored = []
    monkeypatch.setattr(db, "create_call_event", lambda p: stored.append(p.model_dump()) or {"recorded": True, "id": "x"})
    monkeypatch.setattr(db, "list_call_events", lambda limit=200: list(reversed(stored))[:limit])
    api = TestClient(main.app)
    key = {"X-API-Key": main.config.BACKEND_API_KEY}

    assert api.post("/call-events", json={"call_id": "c1", "event_type": "call_started", "details": {"caller_last4": "1234"}}, headers=key).status_code == 200
    assert api.post("/call-events", json={"call_id": "c1", "event_type": "nope"}).status_code == 401  # no key
    assert api.post("/call-events", json={"call_id": "c1", "event_type": "nope"}, headers=key).status_code == 422  # bad type

    events = api.get("/rep/events").json()
    assert events == [{"call_id": "c1", "event_type": "call_started", "details": {"caller_last4": "1234"}}]

    page = api.get("/rep")
    assert page.status_code == 200
    assert "Rep Desk" in page.text
