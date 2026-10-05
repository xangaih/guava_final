"""Offline tests for status_flow.py, via guava.testing.MockCall.

client.lookup_claim is monkeypatched to canned typed Results -- no
server, no network.
"""

from guava.commands import SendInstructionCommand, TransferCommand
from guava.testing import MockCall
from guava.types.call_info import PSTNCallInfo

from mercury_claims import status_flow
from mercury_claims.models import ClaimStatus, NotFound, Ok, Unavailable


def _call() -> MockCall:
    return MockCall(call_info=PSTNCallInfo(from_number="+15555555555", to_number="+15555555555"))


def _instructions(call: MockCall) -> list[str]:
    return [c.instruction for c in call._command_queue if isinstance(c, SendInstructionCommand)]


def _transfers(call: MockCall) -> list[TransferCommand]:
    return [c for c in call._command_queue if isinstance(c, TransferCommand)]


def _set_identity_fields(call: MockCall) -> None:
    call.set_field("status_claim_number", "CLM-0000001")
    call.set_field("status_dob", "1988-04-12")
    call.set_field("status_zip", "90001")


def test_s11_received_no_adjuster_assigned_invents_nothing(monkeypatch):
    call = _call()
    _set_identity_fields(call)
    monkeypatch.setattr(status_flow.client, "lookup_claim", lambda *a, **k: Ok(ClaimStatus(status="received", adjuster_name=None, adjuster_phone=None)))

    status_flow._on_identity_complete(call)

    assert not _transfers(call)
    text = " ".join(_instructions(call)).lower()
    assert "received" in text
    assert "no representative has been assigned" in text
    # Never invents a name.
    assert "jordan" not in text and "morgan" not in text
    assert call.get_variable("call_outcome") == "status_delivered"


def test_received_with_adjuster_assigned(monkeypatch):
    call = _call()
    _set_identity_fields(call)
    monkeypatch.setattr(
        status_flow.client, "lookup_claim", lambda *a, **k: Ok(ClaimStatus(status="received", adjuster_name="Morgan Price", adjuster_phone="+15550111"))
    )
    status_flow._on_identity_complete(call)
    text = " ".join(_instructions(call)).lower()
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
    text = " ".join(_instructions(call)).lower()
    assert "approved" in text
    assert "do not state or estimate any dollar amount" in text


def test_s12_awaiting_documents_does_not_guess_which(monkeypatch):
    call = _call()
    _set_identity_fields(call)
    monkeypatch.setattr(status_flow.client, "lookup_claim", lambda *a, **k: Ok(ClaimStatus(status="awaiting_documents", adjuster_name="Riley Chen", adjuster_phone="+15550112")))
    status_flow._on_identity_complete(call)
    text = " ".join(_instructions(call)).lower()
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
    call.set_field("status_claim_number", "CLM-0000001")
    call.set_field("status_dob", {"day": 12, "month": 4, "year": 1988})
    call.set_field("status_zip", "90001")
    monkeypatch.setattr(status_flow.client, "lookup_claim", lambda *a, **k: Ok(ClaimStatus(status="received")))
    status_flow._on_identity_complete(call)
    assert not _transfers(call)


def test_identity_fails_cleanly_instead_of_crashing_on_unparseable_dob():
    call = _call()
    call.set_field("status_claim_number", "CLM-0000001")
    call.set_field("status_dob", {"unexpected": "shape"})
    call.set_field("status_zip", "90001")
    status_flow._on_identity_complete(call)
    transfers = _transfers(call)
    assert len(transfers) == 1
    assert call.get_variable("call_outcome") == "error"
