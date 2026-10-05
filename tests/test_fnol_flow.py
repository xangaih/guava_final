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
    assert call.get_variable("call_outcome") == "claim_created"


def test_s2_wrong_dob_twice_transfers_without_leaking_which_field(monkeypatch):
    call = _call()
    call.set_field("fnol_policy_number_digits", "100245")
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
    assert call.get_variable("call_outcome") == "auth_failed"


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
    assert call.get_variable("call_outcome") == "error"


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
    validator = fnol_flow._agent._on_validate_handlers["fnol_policy_number_digits"]
    for _ in range(3):
        result = validator(call, "garbage")
    assert result is True  # gives up after 3, lets the task "complete"...
    assert call.get_variable("handoff_reason") == "validation_exhausted"


def test_identity_handles_the_real_captured_dob_payload_shape(monkeypatch):
    # Regression test for a live bug (2026-10-05): a "date" field's
    # payload is {'day', 'month', 'year'}, not an ISO string. This
    # crashed _on_identity_complete uncaught on a real call.
    call = _call()
    call.set_field("fnol_policy_number_digits", "100245")
    call.set_field("fnol_dob", {"day": 12, "month": 4, "year": 1988})
    monkeypatch.setattr(
        fnol_flow.client, "verify_policy", lambda *a, **k: Ok(PolicyVerification(verified=True, holder_name="Jordan Alvarez", status="active"))
    )
    fnol_flow._on_identity_complete(call)
    assert not _transfers(call)
    assert _tasks(call)[-1].task_id == "fnol_loss_basics"


def test_identity_fails_cleanly_instead_of_crashing_on_unparseable_dob(monkeypatch):
    # Belt-and-suspenders: even if some future payload shape isn't one
    # as_date handles, this must transfer honestly, not crash the
    # handler and leave the call stalling (what actually happened live
    # before this fix).
    call = _call()
    call.set_field("fnol_policy_number_digits", "100245")
    call.set_field("fnol_dob", {"unexpected": "shape"})
    fnol_flow._on_identity_complete(call)
    transfers = _transfers(call)
    assert len(transfers) == 1
    assert call.get_variable("call_outcome") == "error"


def test_lapsed_policy_is_not_rejected_by_the_agent(monkeypatch):
    # C6: the agent never sees or acts on policy status -- verify_policy
    # only returns verified/holder_name/status, and a lapsed policy still
    # returns verified=True. The flow must not hang up or mention coverage.
    call = _call()
    call.set_field("fnol_policy_number_digits", "100512")
    call.set_field("fnol_dob", "1990-06-30")
    monkeypatch.setattr(
        fnol_flow.client, "verify_policy", lambda *a, **k: Ok(PolicyVerification(verified=True, holder_name="Evan Brooks", status="lapsed"))
    )
    fnol_flow._on_identity_complete(call)
    assert not _transfers(call)
    assert _tasks(call)[-1].task_id == "fnol_loss_basics"
    assert "coverage" not in " ".join(c.objective for c in _tasks(call)).lower()


def test_handle_question_deflects_actual_coverage_fault_legal_questions():
    call = _call()
    for question in ["Is this covered by my policy?", "Whose fault was this?", "I'm going to call my attorney."]:
        assert fnol_flow.handle_question(call, question) == fnol_flow.copy.COVERAGE_DEFLECTION


def test_handle_question_acknowledges_benign_process_questions_instead_of_the_coverage_non_sequitur():
    # Regression test for a live bug (2026-10-05): every question,
    # including this one, got the coverage-deflection non-sequitur.
    call = _call()
    for question in ["What else do you need?", "What else did you ask for?", "Why do you need my license plate?"]:
        answer = fnol_flow.handle_question(call, question)
        assert answer == fnol_flow.copy.INTAKE_QUESTION_ACKNOWLEDGMENT
        assert answer != fnol_flow.copy.COVERAGE_DEFLECTION


class _FakeGuavaClient:
    def __init__(self, raise_on_send: bool = False):
        self.raise_on_send = raise_on_send
        self.sent: list[tuple[str, str, str]] = []

    def send_sms(self, from_number: str, to_number: str, message: str) -> None:
        if self.raise_on_send:
            raise RuntimeError("sms failed")
        self.sent.append((from_number, to_number, message))


def test_sms_not_sent_when_flag_is_off(monkeypatch):
    monkeypatch.setattr(fnol_flow.config, "SMS_CONFIRMATION_ENABLED", False)
    fake = _FakeGuavaClient()
    monkeypatch.setattr(fnol_flow.guava, "Client", lambda: fake)
    call = _call()
    assert fnol_flow._maybe_send_sms_confirmation(call, "CLM-0001000") is False
    assert fake.sent == []


def test_sms_not_sent_for_a_non_pstn_call(monkeypatch):
    from guava.types.call_info import WebRTCCallInfo

    monkeypatch.setattr(fnol_flow.config, "SMS_CONFIRMATION_ENABLED", True)
    fake = _FakeGuavaClient()
    monkeypatch.setattr(fnol_flow.guava, "Client", lambda: fake)
    call = MockCall(call_info=WebRTCCallInfo(webrtc_code="grtc-test"))
    assert fnol_flow._maybe_send_sms_confirmation(call, "CLM-0001000") is False
    assert fake.sent == []


def test_sms_not_sent_when_caller_has_no_from_number(monkeypatch):
    monkeypatch.setattr(fnol_flow.config, "SMS_CONFIRMATION_ENABLED", True)
    fake = _FakeGuavaClient()
    monkeypatch.setattr(fnol_flow.guava, "Client", lambda: fake)
    call = MockCall(call_info=PSTNCallInfo(from_number=None, to_number="+14843981776"))
    assert fnol_flow._maybe_send_sms_confirmation(call, "CLM-0001000") is False
    assert fake.sent == []


def test_sms_sent_when_enabled_on_a_real_pstn_call(monkeypatch):
    monkeypatch.setattr(fnol_flow.config, "SMS_CONFIRMATION_ENABLED", True)
    fake = _FakeGuavaClient()
    monkeypatch.setattr(fnol_flow.guava, "Client", lambda: fake)
    call = _call()  # default call_info is PSTN with a from_number
    assert fnol_flow._maybe_send_sms_confirmation(call, "CLM-0001000") is True
    assert len(fake.sent) == 1
    assert "CLM-0001000" in fake.sent[0][2]
    assert "demo" in fake.sent[0][2].lower()


def test_sms_send_failure_is_never_fatal_and_says_nothing_about_a_text(monkeypatch):
    # Say-do: the API accepting the request isn't proof of delivery, so
    # only a successful (non-raising) send may claim a text was sent.
    monkeypatch.setattr(fnol_flow.config, "SMS_CONFIRMATION_ENABLED", True)
    fake = _FakeGuavaClient(raise_on_send=True)
    monkeypatch.setattr(fnol_flow.guava, "Client", lambda: fake)
    call = _call()
    assert fnol_flow._maybe_send_sms_confirmation(call, "CLM-0001000") is False
    assert fnol_flow._sms_confirmation_note(call, "CLM-0001000") == ""


def test_finish_claim_mentions_a_text_only_when_sms_actually_sent(monkeypatch):
    monkeypatch.setattr(fnol_flow.config, "SMS_CONFIRMATION_ENABLED", True)
    fake = _FakeGuavaClient()
    monkeypatch.setattr(fnol_flow.guava, "Client", lambda: fake)
    call = _call()
    call.set_variable("fnol_policyholder_name", "Jordan Alvarez")
    fnol_flow._finish_claim(call, "CLM-0001000")
    instructions = _instructions(call)
    assert any("text message" in text.lower() for text in instructions)


def test_finish_claim_says_nothing_about_a_text_when_flag_is_off(monkeypatch):
    monkeypatch.setattr(fnol_flow.config, "SMS_CONFIRMATION_ENABLED", False)
    call = _call()
    call.set_variable("fnol_policyholder_name", "Jordan Alvarez")
    fnol_flow._finish_claim(call, "CLM-0001000")
    instructions = _instructions(call)
    assert not any("text message" in text.lower() for text in instructions)


def _complete_identity(call: MockCall, monkeypatch) -> None:
    call.set_field("fnol_policy_number_digits", "100245")
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
