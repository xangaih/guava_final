from datetime import date, datetime

from fastapi import HTTPException
from postgrest.exceptions import APIError
from supabase import Client, create_client

from . import config
from .schemas import (
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


def verify_policy(policy_number: str, dob: date) -> PolicyVerifyResponse:
    client = get_client()
    res = client.table("policies").select("*").eq("policy_number", policy_number).limit(1).execute()
    if not res.data or res.data[0]["dob"] != dob.isoformat():
        return PolicyVerifyResponse(verified=False)
    policy = res.data[0]
    return PolicyVerifyResponse(verified=True, holder_name=policy["holder_name"], status=policy["status"])


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
    res = (
        client.table("claims")
        .select("*, policies(dob, zip)")
        .eq("claim_number", payload.claim_number)
        .limit(1)
        .execute()
    )
    # A missing claim and a right-claim-wrong-DOB-or-ZIP look identical to
    # the caller: both come back as "not found" so nothing is leaked.
    if not res.data:
        raise HTTPException(status_code=404, detail="claim not found")
    claim = res.data[0]
    policy = claim.get("policies") or {}
    if policy.get("dob") != payload.dob.isoformat() or policy.get("zip") != payload.zip:
        raise HTTPException(status_code=404, detail="claim not found")
    return ClaimLookupResponse(
        status=claim["status"],
        adjuster_name=claim.get("adjuster_name"),
        adjuster_phone=claim.get("adjuster_phone"),
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
