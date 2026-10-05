"""Offline tests for fnol_flow.py, via guava.testing.MockCall.

client.verify_policy / create_claim / request_tow are monkeypatched to
return canned typed Results, so these run with no server and no network
-- they test the flow's branching, not the HTTP client (that's C9's job,
already covered separately).
"""

from datetime import datetime

import pytest
from guava.commands import SendInstructionCommand, SetTaskCommand, TransferCommand
from guava.testing import MockCall
from guava.types.call_info import PSTNCallInfo

from mercury_claims import fnol_flow
from mercury_claims.models import (
    ClaimCreated,
    Malformed,
    NotFound,
    Ok,
    PolicyVerification,
    TowDispatched,
    Unavailable,
)


def _call() -> MockCall:
    return MockCall(call_info=PSTNCallInfo(from_number="+15555555555", to_number="+15555555555"))


def _instructions(call: MockCall) -> list[str]:
    return [c.instruction for c in call._command_queue if isinstance(c, SendInstructionCommand)]


def _transfers(call: MockCall) -> list[TransferCommand]:
    return [c for c in call._command_queue if isinstance(c, TransferCommand)]


def _tasks(call: MockCall) -> list[SetTaskCommand]:
    return [c for c in call._command_queue if isinstance(c, SetTaskCommand)]


def test_s1_happy_path_drivable(monkeypatch):
    call = _call()
    _complete_identity_and_basics(call, monkeypatch, injuries="no")
    assert _tasks(call)[-1].task_id == "fnol_vehicle"
    assert call.get_variable("fnol_policyholder_name") == "Jordan Alvarez"

    call.set_field("fnol_drivable", "yes")
    fnol_flow._on_vehicle_complete(call)
    assert _tasks(call)[-1].task_id == "fnol_police_report"

    call.set_field("fnol_police_report_filed", "no")
    fnol_flow._on_police_report_complete(call)
    assert _tasks(call)[-1].task_id == "fnol_other_party"

    monkeypatch.setattr(fnol_flow.client, "create_claim", lambda **k: Ok(ClaimCreated(claim_number="CLM-0001000")))
    fnol_flow._on_other_party_complete(call)

    assert not _transfers(call)
    assert any("CLM-0001000" in text for text in _instructions(call))
    assert call.get_variable("fnol_claim_number") == "CLM-0001000"


def test_s2_wrong_dob_twice_transfers_without_leaking_which_field(monkeypatch):
    call = _call()
    call.set_field("fnol_policy_number", "MCY-100245")
    call.set_field("fnol_dob", "1999-01-01")
    monkeypatch.setattr(fnol_flow.client, "verify_policy", lambda *a, **k: Ok(PolicyVerification(verified=False)))

    fnol_flow._on_identity_complete(call)
    assert not _transfers(call)  # first miss: retry, not transfer

    fnol_flow._on_identity_complete(call)
    transfers = _transfers(call)
    assert len(transfers) == 1
    message = transfers[0].transfer_message.lower()
    assert "doesn't match our records" in message
    # Never names which specific field was wrong.
    assert "date of birth" not in message
    assert "policy number" not in message


def test_s3_backend_500_on_submit_never_says_filed(monkeypatch):
    call = _call()
    _complete_identity_and_basics(call, monkeypatch, injuries="no")
    call.set_field("fnol_drivable", "yes")
    fnol_flow._on_vehicle_complete(call)
    call.set_field("fnol_police_report_filed", "no")
    fnol_flow._on_police_report_complete(call)

    monkeypatch.setattr(fnol_flow.client, "create_claim", lambda **k: Unavailable(reason="http_500"))
    fnol_flow._on_other_party_complete(call)

    transfers = _transfers(call)
    assert len(transfers) == 1
    message = transfers[0].transfer_message.lower()
    assert "could not be submitted" in message
    assert "not say the claim was filed" in message


def test_s5_malformed_response_treated_as_unavailable(monkeypatch):
    call = _call()
    _complete_identity_and_basics(call, monkeypatch, injuries="no")
    call.set_field("fnol_drivable", "yes")
    fnol_flow._on_vehicle_complete(call)
    call.set_field("fnol_police_report_filed", "no")
    fnol_flow._on_police_report_complete(call)

    monkeypatch.setattr(fnol_flow.client, "create_claim", lambda **k: Malformed(reason="schema_mismatch"))
    fnol_flow._on_other_party_complete(call)

    transfers = _transfers(call)
    assert len(transfers) == 1
    assert "could not be submitted" in transfers[0].transfer_message.lower()


def test_s6_injury_reported_saves_partial_then_transfers(monkeypatch):
    call = _call()
    _complete_identity(call, monkeypatch)

    call.set_field("fnol_loss_type", "collision")
    call.set_field("fnol_loss_at", datetime(2026, 10, 1, 12, 0, 0).isoformat())
    call.set_field("fnol_loss_location", "Main St")
    call.set_field("fnol_loss_description", "crash")
    call.set_field("fnol_injuries", "yes")

    monkeypatch.setattr(fnol_flow.client, "create_claim", lambda **k: Ok(ClaimCreated(claim_number="CLM-0001000")))
    fnol_flow._on_loss_basics_complete(call)

    transfers = _transfers(call)
    assert len(transfers) == 1
    assert "saved" in transfers[0].transfer_message.lower()
    assert "CLM-0001000" in transfers[0].transfer_message


def test_s6_injury_partial_save_fails_does_not_claim_saved(monkeypatch):
    call = _call()
    _complete_identity(call, monkeypatch)

    call.set_field("fnol_loss_type", "collision")
    call.set_field("fnol_loss_at", datetime(2026, 10, 1, 12, 0, 0).isoformat())
    call.set_field("fnol_loss_location", "Main St")
    call.set_field("fnol_loss_description", "crash")
    call.set_field("fnol_injuries", "yes")

    monkeypatch.setattr(fnol_flow.client, "create_claim", lambda **k: Unavailable(reason="timeout"))
    fnol_flow._on_loss_basics_complete(call)

    transfers = _transfers(call)
    assert len(transfers) == 1
    assert "not say their information has been saved" in transfers[0].transfer_message.lower()


def test_s7_not_drivable_tow_requested_says_requested_not_arriving(monkeypatch):
    call = _call()
    _complete_identity_and_basics(call, monkeypatch, injuries="no")
    call.set_field("fnol_drivable", "no")
    fnol_flow._on_vehicle_complete(call)
    call.set_field("fnol_police_report_filed", "no")
    fnol_flow._on_police_report_complete(call)

    monkeypatch.setattr(fnol_flow.client, "create_claim", lambda **k: Ok(ClaimCreated(claim_number="CLM-0001000")))
    fnol_flow._on_other_party_complete(call)
    assert _tasks(call)[-1].task_id == "fnol_tow_offer"

    call.set_field("fnol_tow_destination", "home")
    monkeypatch.setattr(
        fnol_flow.client, "request_tow", lambda **k: Ok(TowDispatched(provider_name="Golden State Towing", eta_minutes=25, request_id="r1"))
    )
    fnol_flow._on_tow_offer_complete(call)

    text = " ".join(_instructions(call)).lower()
    assert "requested" in text
    assert "arriving" not in text


def test_tow_decline_skips_tow_request(monkeypatch):
    call = _call()
    _complete_identity_and_basics(call, monkeypatch, injuries="no")
    call.set_field("fnol_drivable", "no")
    fnol_flow._on_vehicle_complete(call)
    call.set_field("fnol_police_report_filed", "no")
    fnol_flow._on_police_report_complete(call)
    monkeypatch.setattr(fnol_flow.client, "create_claim", lambda **k: Ok(ClaimCreated(claim_number="CLM-0001000")))
    fnol_flow._on_other_party_complete(call)

    call.set_field("fnol_tow_destination", "decline")
    called = {"request_tow": False}
    monkeypatch.setattr(fnol_flow.client, "request_tow", lambda **k: called.update(request_tow=True))
    fnol_flow._on_tow_offer_complete(call)
    assert called["request_tow"] is False


def test_h7_validator_exhaustion_transfers_instead_of_looping():
    call = _call()
    validator = fnol_flow._agent._on_validate_handlers["fnol_policy_number"]
    for _ in range(3):
        result = validator(call, "garbage")
    assert result is True  # gives up after 3, lets the task "complete"...
    assert call.get_variable("handoff_reason") == "validation_exhausted"


def test_lapsed_policy_is_not_rejected_by_the_agent(monkeypatch):
    # C6: the agent never sees or acts on policy status -- verify_policy
    # only returns verified/holder_name/status, and a lapsed policy still
    # returns verified=True. The flow must not hang up or mention coverage.
    call = _call()
    call.set_field("fnol_policy_number", "MCY-100512")
    call.set_field("fnol_dob", "1990-06-30")
    monkeypatch.setattr(
        fnol_flow.client, "verify_policy", lambda *a, **k: Ok(PolicyVerification(verified=True, holder_name="Evan Brooks", status="lapsed"))
    )
    fnol_flow._on_identity_complete(call)
    assert not _transfers(call)
    assert _tasks(call)[-1].task_id == "fnol_loss_basics"
    assert "coverage" not in " ".join(c.objective for c in _tasks(call)).lower()


def _complete_identity(call: MockCall, monkeypatch) -> None:
    call.set_field("fnol_policy_number", "MCY-100245")
    call.set_field("fnol_dob", "1988-04-12")
    monkeypatch.setattr(
        fnol_flow.client, "verify_policy", lambda *a, **k: Ok(PolicyVerification(verified=True, holder_name="Jordan Alvarez", status="active"))
    )
    fnol_flow._on_identity_complete(call)


def _complete_identity_and_basics(call: MockCall, monkeypatch, *, injuries: str) -> None:
    _complete_identity(call, monkeypatch)
    call.set_field("fnol_loss_type", "collision")
    call.set_field("fnol_loss_at", datetime(2026, 10, 1, 12, 0, 0).isoformat())
    call.set_field("fnol_loss_location", "Main St")
    call.set_field("fnol_loss_description", "crash")
    call.set_field("fnol_injuries", injuries)
    fnol_flow._on_loss_basics_complete(call)
