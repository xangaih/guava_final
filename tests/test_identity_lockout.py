"""Stronger identity: the caller-phone signal (flag only) and the cross-call
lockout (backend-decided, HTTP 423 -> Locked -> a person, never a retry)."""

import httpx
from fastapi.testclient import TestClient
from guava.commands import TransferCommand
from guava.events import BotSessionEnded
from guava.testing import MockCall
from guava.types.call_info import PSTNCallInfo, WebRTCCallInfo

from mercury_claims import agent as agent_module
from mercury_claims import fnol_flow, status_flow
from mercury_claims.client import Client
from mercury_claims.models import ClaimStatus, Locked, NotFound, Ok, PolicyVerification
from mock_backend import db


def _call(from_number="+15555551234") -> MockCall:
    return MockCall(call_info=PSTNCallInfo(from_number=from_number, to_number="+15555555555"))


def _transfers(call: MockCall) -> list[TransferCommand]:
    return [c for c in call._command_queue if isinstance(c, TransferCommand)]


# --- the client: 423 is its own outcome ---

def _client_with(status_code: int) -> Client:
    c = Client()
    c._http = httpx.Client(
        base_url="http://test",
        transport=httpx.MockTransport(lambda req: httpx.Response(status_code, json={"detail": "x"})),
    )
    return c


def test_client_maps_423_to_locked_not_malformed():
    from datetime import date

    assert isinstance(_client_with(423).verify_policy("MCY-1", date(1988, 4, 12)), Locked)
    assert isinstance(_client_with(404).verify_policy("MCY-1", date(1988, 4, 12)), NotFound)  # unchanged


# --- the desks: locked means a person, for their security ---

def test_fnol_locked_transfers_for_security_without_explaining(monkeypatch):
    call = _call()
    fnol_flow.start(call)
    call.set_field("fnol_policy_number_digits", "100245")
    call.set_field("fnol_dob", "1988-04-12")
    monkeypatch.setattr(fnol_flow.client, "verify_policy", lambda *a, **k: Locked())
    fnol_flow._on_identity_complete(call)

    transfers = _transfers(call)
    assert len(transfers) == 1
    message = transfers[0].transfer_message.lower()
    assert "for their security" in message
    assert "unavailable" not in message and "lock" not in message.replace("do not mention a lockout", "")
    assert call.get_variable("call_outcome") == "transferred"
    assert call.get_variable("call_transfer_reason") == "identity_locked"


def test_status_locked_transfers_for_security_without_explaining(monkeypatch):
    call = _call()
    status_flow.start(call)
    call.set_field("status_claim_number_digits", "0000001")
    call.set_field("status_dob", "1988-04-12")
    call.set_field("status_zip", "90001")
    monkeypatch.setattr(status_flow.client, "lookup_claim", lambda *a, **k: Locked())
    status_flow._on_identity_complete(call)

    transfers = _transfers(call)
    assert len(transfers) == 1
    assert "for their security" in transfers[0].transfer_message.lower()
    assert call.get_variable("call_transfer_reason") == "identity_locked"


# --- the caller-phone signal ---

def test_caller_phone_is_sent_and_phone_match_is_stored(monkeypatch, recorded_events):
    seen = {}

    def fake_verify(policy_number, dob, caller_phone=None):
        seen["caller_phone"] = caller_phone
        return Ok(PolicyVerification(verified=True, holder_name="Jordan Alvarez", status="active", phone_match=True))

    monkeypatch.setattr(fnol_flow.client, "verify_policy", fake_verify)
    call = _call(from_number="+15555551234")
    fnol_flow.start(call)
    call.set_field("fnol_policy_number_digits", "100245")
    call.set_field("fnol_dob", "1988-04-12")
    fnol_flow._on_identity_complete(call)

    assert seen["caller_phone"] == "+15555551234"
    assert call.get_variable("call_phone_match") is True
    verified = [e for e in recorded_events if e[1] == "verified"][0]
    assert verified[2]["phone_match"] is True


def test_hidden_or_web_caller_has_no_phone_and_nothing_breaks(monkeypatch):
    seen = {}

    def fake_lookup(claim_number, dob, zip_code, caller_phone=None):
        seen["caller_phone"] = caller_phone
        return Ok(ClaimStatus(status="received"))  # phone_match defaults to None

    monkeypatch.setattr(status_flow.client, "lookup_claim", fake_lookup)
    call = MockCall(call_info=WebRTCCallInfo(webrtc_code="grtc-test"))
    assert agent_module.caller_phone(call) is None
    status_flow.start(call)
    call.set_field("status_claim_number_digits", "0000001")
    call.set_field("status_dob", "1988-04-12")
    call.set_field("status_zip", "90001")
    status_flow._on_identity_complete(call)

    assert seen["caller_phone"] is None
    assert call.get_variable("call_phone_match") is None
    assert not _transfers(call)


def test_phone_match_reaches_the_audit_row_and_the_handoff_context(monkeypatch):
    recorded = {}
    monkeypatch.setattr(agent_module.client, "record_call_session", lambda **k: recorded.update(k))
    call = _call()
    call.set_variable("call_phone_match", False)
    agent_module.on_session_end(call, BotSessionEnded(termination_reason="user-hangup"))
    assert recorded["phone_match"] is False
    assert agent_module.build_handoff_context(call)["phone_match"] is False


# --- backend helpers (pure) ---

def test_phone_match_compares_the_last_ten_digits_and_is_none_when_unknown():
    assert db.phone_match("+1 (628) 800-4139", "6288004139") is True
    assert db.phone_match("+16288004139", "+15550101") is False
    assert db.phone_match(None, "+16288004139") is None
    assert db.phone_match("+16288004139", None) is None
    assert db.phone_match("+16288004139", "") is None


def test_phone_hash_is_stable_and_never_the_number():
    h = db.phone_hash("+1 (628) 800-4139")
    assert h == db.phone_hash("16288004139")
    assert "4139" not in h
    assert db.phone_hash(None) is None and db.phone_hash("") is None


# --- backend endpoints ---

def test_verify_endpoint_passes_the_caller_phone_and_reset_requires_the_admin_token(monkeypatch):
    from mock_backend import main

    seen = {}
    monkeypatch.setattr(
        db, "verify_policy",
        lambda policy_number, dob, caller_phone=None: seen.update(caller_phone=caller_phone) or main.PolicyVerifyResponse(verified=False),
    )
    monkeypatch.setattr(db, "reset_lockouts", lambda: 3)
    api = TestClient(main.app)
    key = {"X-API-Key": main.config.BACKEND_API_KEY}

    r = api.post("/policies/verify", json={"policy_number": "MCY-100245", "dob": "1988-04-12", "caller_phone": "+15555551234"}, headers=key)
    assert r.status_code == 200 and seen["caller_phone"] == "+15555551234"

    assert api.post("/admin/reset-lockouts").status_code == 401
    assert api.post("/admin/reset-lockouts", headers={"X-Admin-Token": main.config.ADMIN_TOKEN}).json() == {"cleared": 3}
