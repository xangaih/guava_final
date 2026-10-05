"""Offline tests for the call_sessions audit write and PII-free logging
(C14). client.record_call_session is monkeypatched -- no network.
"""

from guava.events import BotSessionEnded
from guava.testing import MockCall
from guava.types.call_info import PSTNCallInfo

from mercury_claims import agent as agent_module


def _call() -> MockCall:
    return MockCall(call_info=PSTNCallInfo(from_number="+15555555555", to_number="+15555555555"))


def _event() -> BotSessionEnded:
    return BotSessionEnded(termination_reason="bot-hangup")


def test_on_session_end_records_the_set_outcome(monkeypatch):
    recorded = {}
    monkeypatch.setattr(
        agent_module.client,
        "record_call_session",
        lambda **k: recorded.update(k),
    )
    call = _call()
    call.set_variable("flow", "fnol")
    agent_module.set_outcome(call, "claim_created")

    agent_module.on_session_end(call, _event())

    assert recorded["outcome"] == "claim_created"
    assert recorded["purpose"] == "fnol"
    assert recorded["call_id"] == call.id


def test_on_session_end_defaults_to_abandoned_when_outcome_never_set(monkeypatch):
    recorded = {}
    monkeypatch.setattr(agent_module.client, "record_call_session", lambda **k: recorded.update(k))
    call = _call()
    agent_module.on_session_end(call, _event())
    assert recorded["outcome"] == "abandoned"


def test_redact_summary_masks_dob_policy_number_and_name():
    summary = {
        "policyholder_name": "Jordan Alvarez",
        "policy_number": "MCY-100245",
        "dob": "1988-04-12",
        "claim_number": "CLM-0001000",  # not masked -- needed operationally, not sensitive alone
        "identity_attempts": 1,
    }
    redacted = agent_module._redact_summary(summary)
    assert redacted["policyholder_name"] == "<redacted>"
    assert redacted["policy_number"] == "MCY-***"
    assert redacted["dob"] == "****-**-**"
    assert redacted["claim_number"] == "CLM-0001000"
    assert redacted["identity_attempts"] == 1


def test_redact_summary_passes_through_none():
    assert agent_module._redact_summary({"claim_number": None})["claim_number"] is None


def test_set_outcome_does_not_overwrite_a_success_with_a_later_transfer():
    # Regression test for a live bug (2026-10-05): a claim was filed,
    # then a benign follow-up question was misclassified as "wants a
    # human" and the resulting transfer silently overwrote the earlier
    # claim_created outcome in the audit row.
    call = _call()
    agent_module.set_outcome(call, "claim_created")
    agent_module.set_outcome(call, "transferred", transfer_reason="mid_call_intent")
    assert call.get_variable("call_outcome") == "claim_created"
    assert call.get_variable("call_transfer_reason") == "post_completion:mid_call_intent"


def test_set_outcome_allows_overwriting_a_non_success_outcome():
    call = _call()
    agent_module.set_outcome(call, "error", transfer_reason="backend_unavailable")
    agent_module.set_outcome(call, "auth_failed", transfer_reason="identity_mismatch")
    assert call.get_variable("call_outcome") == "auth_failed"
    assert call.get_variable("call_transfer_reason") == "identity_mismatch"


def test_set_outcome_allows_moving_between_two_successes():
    call = _call()
    agent_module.set_outcome(call, "claim_created")
    agent_module.set_outcome(call, "completed")
    assert call.get_variable("call_outcome") == "completed"
