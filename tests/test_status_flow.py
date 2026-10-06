"""Offline tests for status_flow.py, via guava.testing.MockCall.

client.lookup_claim is monkeypatched to canned typed Results -- no
server, no network.
"""

from datetime import date

from guava.commands import SetTaskCommand, TransferCommand
from guava.testing import MockCall
from guava.types.call_info import PSTNCallInfo

from mercury_claims import status_flow
from mercury_claims.models import ClaimStatus, NotFound, Ok, Unavailable


def _call() -> MockCall:
    return MockCall(call_info=PSTNCallInfo(from_number="+15555555555", to_number="+15555555555"))


def _last_task(call: MockCall) -> SetTaskCommand:
    return [c for c in call._command_queue if isinstance(c, SetTaskCommand)][-1]


def _field_keys(call: MockCall) -> list[str]:
    # Say items get an auto-generated key; the desk's own blanks are prefixed.
    return [item.key for item in _last_task(call).action_items if item.key.startswith("status_")]


def _last_task_objective(call: MockCall) -> str:
    # A delivered status is the objective of the shared wrap_up task
    # (agent.end_call_with_wrapup), not a hangup() instruction any more.
    tasks = [c for c in call._command_queue if isinstance(c, SetTaskCommand)]
    assert tasks[-1].task_id == "wrap_up"
    return tasks[-1].objective


def _transfers(call: MockCall) -> list[TransferCommand]:
    return [c for c in call._command_queue if isinstance(c, TransferCommand)]


def _set_identity_fields(call: MockCall) -> None:
    call.set_field("status_claim_number_digits", "0000001")
    call.set_field("status_dob", "1988-04-12")
    call.set_field("status_zip", "90001")


def test_s11_received_no_adjuster_assigned_invents_nothing(monkeypatch):
    call = _call()
    _set_identity_fields(call)
    monkeypatch.setattr(status_flow.client, "lookup_claim", lambda *a, **k: Ok(ClaimStatus(status="received", adjuster_name=None, adjuster_phone=None)))

    status_flow._on_identity_complete(call)

    assert not _transfers(call)
    text = _last_task_objective(call).lower()
    assert "received" in text
    assert "no representative has been assigned" in text
    # Never invents a name.
    assert "jordan" not in text and "morgan" not in text
    assert "anything else" in text
    assert "goodbye" not in text  # said only after the caller answers "no"
    assert call.get_variable("call_outcome") == "status_delivered"


def test_start_resets_the_identity_attempt_budget():
    call = _call()
    call.set_variable("status_identity_attempts", 1)
    status_flow.start(call)
    assert call.get_variable("status_identity_attempts") == 0


def test_identity_retry_still_works_after_start_reset(monkeypatch):
    # Same live crash class as fnol_flow (2026-10-06): a None reset
    # reached the handler's "+ 1".
    call = _call()
    status_flow.start(call)
    _set_identity_fields(call)
    monkeypatch.setattr(status_flow.client, "lookup_claim", lambda *a, **k: NotFound())
    status_flow._on_identity_complete(call)
    assert call.get_variable("status_identity_attempts") == 1
    assert not _transfers(call)


# --- DOB carry-over within one call ---

def test_fresh_call_asks_for_all_three_identity_fields():
    call = _call()
    status_flow.start(call)
    assert _field_keys(call) == ["status_claim_number_digits", "status_dob", "status_zip"]


def test_verified_dob_is_not_asked_again_at_this_desk():
    # A competent human wouldn't re-ask a DOB they verified two minutes ago.
    call = _call()
    call.set_variable("call_verified_dob", "1988-04-12")
    status_flow.start(call)
    assert _field_keys(call) == ["status_claim_number_digits", "status_zip"]
    assert "do not ask for the date of birth again" in _last_task(call).objective


def test_just_filed_claim_is_offered_first():
    call = _call()
    call.set_variable("call_verified_dob", "1988-04-12")
    call.set_variable("call_claim_number", "CLM-0001008")
    status_flow.start(call)
    assert "CLM-0001008" in _last_task(call).objective


def test_lookup_uses_the_carried_dob_and_a_success_stores_it(monkeypatch):
    call = _call()
    call.set_variable("call_verified_dob", "1988-04-12")
    status_flow.start(call)
    call.set_field("status_claim_number_digits", "0000001")
    call.set_field("status_zip", "90001")  # no status_dob field was collected
    seen = {}

    def fake_lookup(claim_number, dob, zip_code):
        seen.update(claim_number=claim_number, dob=dob, zip_code=zip_code)
        return Ok(ClaimStatus(status="received"))

    monkeypatch.setattr(status_flow.client, "lookup_claim", fake_lookup)
    status_flow._on_identity_complete(call)
    assert seen["dob"] == date(1988, 4, 12)
    assert call.get_variable("call_verified_dob") == "1988-04-12"
    assert call.get_variable("call_outcome") == "status_delivered"


def test_fresh_verification_stores_the_dob_for_later_desks(monkeypatch):
    call = _call()
    status_flow.start(call)
    _set_identity_fields(call)
    monkeypatch.setattr(status_flow.client, "lookup_claim", lambda *a, **k: Ok(ClaimStatus(status="received")))
    status_flow._on_identity_complete(call)
    assert call.get_variable("call_verified_dob") == "1988-04-12"


def test_mismatch_with_a_carried_dob_drops_it_and_asks_for_everything(monkeypatch):
    # The carried DOB might not belong to this claim (a spouse's claim).
    # The page had no DOB blank, so a plain retry couldn't fix that.
    call = _call()
    call.set_variable("call_verified_dob", "1988-04-12")
    status_flow.start(call)
    call.set_field("status_claim_number_digits", "0000009")
    call.set_field("status_zip", "90001")
    monkeypatch.setattr(status_flow.client, "lookup_claim", lambda *a, **k: NotFound())
    status_flow._on_identity_complete(call)

    assert not _transfers(call)
    assert call.get_variable("call_verified_dob") is None
    assert _last_task(call).task_id == "status_identity"
    assert "status_dob" in _field_keys(call)
    assert call.get_variable("status_identity_attempts") == 1  # the miss still counted


def test_handle_question_about_claim_history_states_the_limitation():
    call = _call()
    for q in ["How many claims do I have?", "Can you list my claims?"]:
        assert status_flow.handle_question(call, q) == status_flow.copy.CLAIM_HISTORY_DEFLECTION


def test_received_with_adjuster_assigned(monkeypatch):
    call = _call()
    _set_identity_fields(call)
    monkeypatch.setattr(
        status_flow.client, "lookup_claim", lambda *a, **k: Ok(ClaimStatus(status="received", adjuster_name="Morgan Price", adjuster_phone="+15550111"))
    )
    status_flow._on_identity_complete(call)
    text = _last_task_objective(call).lower()
    assert "morgan price" in text


def test_s9_denied_transfers_without_speaking_reason(monkeypatch):
    call = _call()
    _set_identity_fields(call)
    monkeypatch.setattr(
        status_flow.client, "lookup_claim", lambda *a, **k: Ok(ClaimStatus(status="denied", adjuster_name="Appeals Department", adjuster_phone="+15550114"))
    )
    status_flow._on_identity_complete(call)

    transfers = _transfers(call)
    assert len(transfers) == 1
    message = transfers[0].transfer_message.lower()
    assert "do not mention, speculate about, or confirm any reason" in message
    assert "dollar" not in message or "do not state any dollar amount" in message
    assert call.get_variable("call_outcome") == "transferred"


def test_s12_needs_human_transfers_without_reason(monkeypatch):
    call = _call()
    _set_identity_fields(call)
    monkeypatch.setattr(status_flow.client, "lookup_claim", lambda *a, **k: Ok(ClaimStatus(status="needs_human")))
    status_flow._on_identity_complete(call)
    transfers = _transfers(call)
    assert len(transfers) == 1
    assert "reason" in transfers[0].transfer_message.lower()


def test_s12_approved_never_states_amount(monkeypatch):
    call = _call()
    _set_identity_fields(call)
    monkeypatch.setattr(
        status_flow.client, "lookup_claim", lambda *a, **k: Ok(ClaimStatus(status="approved", adjuster_name="Taylor Brooks", adjuster_phone="+15550113"))
    )
    status_flow._on_identity_complete(call)
    text = _last_task_objective(call).lower()
    assert "approved" in text
    assert "do not state or estimate any dollar amount" in text


def test_s12_awaiting_documents_does_not_guess_which(monkeypatch):
    call = _call()
    _set_identity_fields(call)
    monkeypatch.setattr(status_flow.client, "lookup_claim", lambda *a, **k: Ok(ClaimStatus(status="awaiting_documents", adjuster_name="Riley Chen", adjuster_phone="+15550112")))
    status_flow._on_identity_complete(call)
    text = _last_task_objective(call).lower()
    assert "do not guess which documents" in text


def test_wrong_identity_twice_transfers_without_leaking_which_field(monkeypatch):
    call = _call()
    _set_identity_fields(call)
    monkeypatch.setattr(status_flow.client, "lookup_claim", lambda *a, **k: NotFound())

    status_flow._on_identity_complete(call)
    assert not _transfers(call)  # first miss: retry

    status_flow._on_identity_complete(call)
    transfers = _transfers(call)
    assert len(transfers) == 1
    message = transfers[0].transfer_message.lower()
    assert "doesn't match our records" in message
    assert "date of birth" not in message
    assert "zip" not in message


def test_backend_unavailable_transfers_honestly(monkeypatch):
    call = _call()
    _set_identity_fields(call)
    monkeypatch.setattr(status_flow.client, "lookup_claim", lambda *a, **k: Unavailable(reason="http_500"))
    status_flow._on_identity_complete(call)
    transfers = _transfers(call)
    assert len(transfers) == 1
    assert "temporarily unavailable" in transfers[0].transfer_message.lower()


def test_unknown_status_counts_as_unavailable(monkeypatch):
    call = _call()
    _set_identity_fields(call)
    monkeypatch.setattr(status_flow.client, "lookup_claim", lambda *a, **k: Ok(ClaimStatus(status="some_future_status")))
    status_flow._on_identity_complete(call)
    transfers = _transfers(call)
    assert len(transfers) == 1


def test_identity_handles_the_real_captured_dob_payload_shape(monkeypatch):
    # Regression test for the live bug found in fnol_flow (same as_date
    # helper is shared by status_flow).
    call = _call()
    call.set_field("status_claim_number_digits", "0000001")
    call.set_field("status_dob", {"day": 12, "month": 4, "year": 1988})
    call.set_field("status_zip", "90001")
    monkeypatch.setattr(status_flow.client, "lookup_claim", lambda *a, **k: Ok(ClaimStatus(status="received")))
    status_flow._on_identity_complete(call)
    assert not _transfers(call)


def test_identity_fails_cleanly_instead_of_crashing_on_unparseable_dob():
    call = _call()
    call.set_field("status_claim_number_digits", "0000001")
    call.set_field("status_dob", {"unexpected": "shape"})
    call.set_field("status_zip", "90001")
    status_flow._on_identity_complete(call)
    transfers = _transfers(call)
    assert len(transfers) == 1
    assert call.get_variable("call_outcome") == "error"


def test_handle_question_about_the_claim_gets_the_f11_safe_deflection():
    call = _call()
    for question in ["Why was my claim denied?", "How much is my settlement?", "What's happening with my claim?"]:
        assert status_flow.handle_question(call, question) == status_flow.copy.STATUS_CLAIM_DETAIL_DEFLECTION


def test_handle_question_unrelated_to_claim_gets_a_different_honest_response():
    # Regression test for a live bug (2026-10-05): "Can you give me
    # information on the towing requests?" got the claim-detail
    # deflection, which was a non-sequitur.
    call = _call()
    for question in ["Can you give me information on the towing requests?", "I'd like to enroll in a new policy."]:
        answer = status_flow.handle_question(call, question)
        assert answer == status_flow.copy.STATUS_UNRELATED_QUESTION_ACKNOWLEDGMENT
        assert answer != status_flow.copy.STATUS_CLAIM_DETAIL_DEFLECTION
