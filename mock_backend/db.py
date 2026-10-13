import hashlib
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException
from postgrest.exceptions import APIError
from supabase import Client, create_client

from . import config
from .schemas import (
    CallEventCreate,
    CallSessionCreate,
    ClaimCreateRequest,
    ClaimCreateResponse,
    ClaimLookupRequest,
    ClaimLookupResponse,
    PolicyVerifyResponse,
    TowRequestCreate,
    TowRequestResponse,
)

_client: Client | None = None


def get_client() -> Client:
    global _client
    if _client is None:
        _client = create_client(config.SUPABASE_URL, config.SUPABASE_SECRET_KEY)
    return _client


def mask_dob(dob: str) -> str:
    return "****-**-**"


def truncate_policy_number(policy_number: str) -> str:
    return policy_number[:4] + "***"


def _compute_coverage_review(policy: dict, loss_at: datetime) -> tuple[bool, str | None]:
    if policy["status"] != "active":
        return True, f"Policy status is '{policy['status']}' as of the loss date."
    effective = date.fromisoformat(policy["effective_date"])
    expiration = date.fromisoformat(policy["expiration_date"])
    if not (effective <= loss_at.date() <= expiration):
        return True, "Loss date falls outside the policy's effective period."
    return False, None


# --- Identity: lockout across calls, and the caller-phone signal ---
#
# The agent only sees one call; the backend sees all of them, so this is
# where "5 failures in 30 minutes → locked for 30 minutes" lives. A lock
# means HTTP 423, which the agent turns into "for your security, let me
# connect you with a representative" -- never "you're locked out", and
# never a hard block, because an attacker could otherwise lock a real
# customer out on purpose. The caller's number is stored only as a hash.

LOCKOUT_FAILURES = 5
LOCKOUT_WINDOW = timedelta(minutes=30)


def _digits(phone: str | None) -> str:
    return "".join(ch for ch in (phone or "") if ch.isdigit())


def phone_hash(phone: str | None) -> str | None:
    digits = _digits(phone)
    return hashlib.sha256(digits.encode()).hexdigest() if digits else None


def phone_match(on_file: str | None, caller: str | None) -> bool | None:
    """None when either side is unknown; otherwise compare the last 10
    digits so +1 prefixes and formatting don't matter."""
    a, b = _digits(on_file)[-10:], _digits(caller)[-10:]
    if not a or not b:
        return None
    return a == b


def _recent_failures(column: str, value: str) -> int:
    since = (datetime.now(timezone.utc) - LOCKOUT_WINDOW).isoformat()
    rows = (
        get_client()
        .table("auth_attempts")
        .select("success")
        .eq(column, value)
        .gte("created_at", since)
        .order("created_at", desc=True)
        .execute()
        .data
    )
    failures = 0
    for row in rows:  # newest first: a success resets the count
        if row["success"]:
            break
        failures += 1
    return failures


def _is_locked(identifier: str, caller_hash: str | None) -> bool:
    if _recent_failures("identifier", identifier) >= LOCKOUT_FAILURES:
        return True
    return bool(caller_hash) and _recent_failures("caller_phone_hash", caller_hash) >= LOCKOUT_FAILURES


def _record_attempt(identifier: str, caller_hash: str | None, success: bool) -> None:
    get_client().table("auth_attempts").insert(
        {"identifier": identifier, "caller_phone_hash": caller_hash, "success": success}
    ).execute()


def reset_lockouts() -> int:
    # Demo convenience: practising the wrong-birthday path locks you out.
    res = get_client().table("auth_attempts").delete().neq("identifier", "").execute()
    return len(res.data or [])


def verify_policy(policy_number: str, dob: date, caller_phone: str | None = None) -> PolicyVerifyResponse:
    client = get_client()
    caller_hash = phone_hash(caller_phone)
    if _is_locked(policy_number, caller_hash):
        raise HTTPException(status_code=423, detail="too many failed attempts")

    res = client.table("policies").select("*").eq("policy_number", policy_number).limit(1).execute()
    success = bool(res.data) and res.data[0]["dob"] == dob.isoformat()
    _record_attempt(policy_number, caller_hash, success)
    if not success:
        return PolicyVerifyResponse(verified=False)
    policy = res.data[0]
    return PolicyVerifyResponse(
        verified=True,
        holder_name=policy["holder_name"],
        status=policy["status"],
        phone_match=phone_match(policy.get("phone"), caller_phone),
    )


def create_claim(payload: ClaimCreateRequest) -> ClaimCreateResponse:
    client = get_client()
    policy_res = client.table("policies").select("*").eq("policy_number", payload.policy_number).limit(1).execute()
    if not policy_res.data:
        raise HTTPException(status_code=404, detail="policy not found")
    policy = policy_res.data[0]

    coverage_review_flag, coverage_review_reason = _compute_coverage_review(policy, payload.loss_at)
    # Injuries always route the claim to a human (H2); the agent never
    # decides this, the backend does, so it can't be talked around.
    status = "needs_human" if payload.injuries else "received"

    row = {
        "idempotency_key": payload.idempotency_key,
        "policy_number": payload.policy_number,
        "status": status,
        "intake_complete": payload.intake_complete,
        "loss_type": payload.loss_type,
        "loss_at": payload.loss_at.isoformat(),
        "loss_location": payload.loss_location,
        "description": payload.description,
        "injuries": payload.injuries,
        "drivable": payload.drivable,
        "police_report_filed": payload.police_report_filed,
        "police_report_number": payload.police_report_number,
        "police_department": payload.police_department,
        "details": payload.details,
        "coverage_review_flag": coverage_review_flag,
        "coverage_review_reason": coverage_review_reason,
    }

    try:
        insert_res = client.table("claims").insert(row).execute()
        claim_number = insert_res.data[0]["claim_number"]
    except APIError as exc:
        if exc.code != "23505":  # not a unique_violation on idempotency_key
            raise
        # A retried request with the same idempotency key: the claim
        # already exists, so hand back its number instead of erroring or
        # creating a duplicate.
        existing = (
            client.table("claims")
            .select("claim_number")
            .eq("idempotency_key", payload.idempotency_key)
            .limit(1)
            .execute()
        )
        if not existing.data:
            raise
        claim_number = existing.data[0]["claim_number"]

    return ClaimCreateResponse(claim_number=claim_number)


def lookup_claim(payload: ClaimLookupRequest) -> ClaimLookupResponse:
    client = get_client()
    caller_hash = phone_hash(payload.caller_phone)
    if _is_locked(payload.claim_number, caller_hash):
        raise HTTPException(status_code=423, detail="too many failed attempts")

    res = (
        client.table("claims")
        .select("*, policies(dob, zip, phone)")
        .eq("claim_number", payload.claim_number)
        .limit(1)
        .execute()
    )
    # A missing claim and a right-claim-wrong-DOB-or-ZIP look identical to
    # the caller: both come back as "not found" so nothing is leaked.
    claim = res.data[0] if res.data else None
    policy = (claim or {}).get("policies") or {}
    success = claim is not None and policy.get("dob") == payload.dob.isoformat() and policy.get("zip") == payload.zip
    _record_attempt(payload.claim_number, caller_hash, success)
    if not success:
        raise HTTPException(status_code=404, detail="claim not found")
    return ClaimLookupResponse(
        status=claim["status"],
        adjuster_name=claim.get("adjuster_name"),
        adjuster_phone=claim.get("adjuster_phone"),
        phone_match=phone_match(policy.get("phone"), payload.caller_phone),
    )


def create_tow_request(payload: TowRequestCreate) -> TowRequestResponse:
    client = get_client()
    claim_res = (
        client.table("claims")
        .select("id, policies(zip)")
        .eq("claim_number", payload.claim_number)
        .limit(1)
        .execute()
    )
    if not claim_res.data:
        raise HTTPException(status_code=404, detail="claim not found")
    claim = claim_res.data[0]
    zip_prefix = ((claim.get("policies") or {}).get("zip") or "")[:2]

    providers_res = client.table("tow_providers").select("*").eq("active", True).execute()
    providers = providers_res.data
    provider = next((p for p in providers if zip_prefix in p["zip_prefixes"]), None)
    if provider is None and providers:
        # No provider covers this ZIP; fall back to any active provider
        # rather than failing the demo outright.
        provider = providers[0]
    if provider is None:
        raise HTTPException(status_code=503, detail="no tow provider available")

    row = {
        "claim_id": claim["id"],
        "provider_id": provider["id"],
        "pickup_address": payload.pickup_address,
        "destination_type": payload.destination_type,
        "destination_address": payload.destination_address,
        "status": "requested",
        "eta_minutes": provider["avg_eta_minutes"],
    }
    insert_res = client.table("tow_requests").insert(row).execute()
    request_id = insert_res.data[0]["id"]
    return TowRequestResponse(
        provider_name=provider["name"],
        eta_minutes=provider["avg_eta_minutes"],
        request_id=str(request_id),
    )


def create_call_session(payload: CallSessionCreate) -> dict:
    client = get_client()
    insert_res = client.table("call_sessions").insert(payload.model_dump()).execute()
    return {"recorded": True, "id": insert_res.data[0]["id"]}


def create_call_event(payload: CallEventCreate) -> dict:
    client = get_client()
    insert_res = client.table("call_events").insert(payload.model_dump()).execute()
    return {"recorded": True, "id": insert_res.data[0]["id"]}


def list_call_events(limit: int = 200) -> list[dict]:
    client = get_client()
    res = (
        client.table("call_events")
        .select("call_id, event_type, details, created_at")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return res.data
