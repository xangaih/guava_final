import logging
import time

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from postgrest.exceptions import APIError

from . import config, db
from .schemas import (
    CallSessionCreate,
    ClaimCreateRequest,
    ClaimCreateResponse,
    ClaimLookupRequest,
    ClaimLookupResponse,
    FaultRequest,
    PolicyVerifyRequest,
    PolicyVerifyResponse,
    TowRequestCreate,
    TowRequestResponse,
)

logger = logging.getLogger("mock_backend")

app = FastAPI(title="Mercury mock claims backend")

# In-memory fault-injection switch for the demo (section 7's /admin/fault).
# "timeout"/"slow" just delay the response; "500"/"not_found" raise;
# "malformed" returns a body the client can't parse. Deliberately
# in-memory and single-process: this is a demo fixture, not real state.
_fault_state = {"mode": "none", "remaining": 0}


def require_api_key(x_api_key: str = Header(default="")) -> None:
    if x_api_key != config.BACKEND_API_KEY:
        raise HTTPException(status_code=401, detail="invalid API key")


def require_admin_token(x_admin_token: str = Header(default="")) -> None:
    if x_admin_token != config.ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="invalid admin token")


def check_fault() -> Response | None:
    mode = _fault_state["mode"]
    if mode == "none" or _fault_state["remaining"] <= 0:
        return None
    _fault_state["remaining"] -= 1
    if _fault_state["remaining"] <= 0:
        _fault_state["mode"] = "none"

    if mode == "timeout":
        time.sleep(10)
        return None
    if mode == "slow":
        time.sleep(2)
        return None
    if mode == "500":
        raise HTTPException(status_code=500, detail="simulated server error (fault injection)")
    if mode == "not_found":
        raise HTTPException(status_code=404, detail="simulated not found (fault injection)")
    if mode == "malformed":
        return Response(content="{not valid json", media_type="application/json")
    return None


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/policies/verify", response_model=PolicyVerifyResponse, dependencies=[Depends(require_api_key)])
def policies_verify(payload: PolicyVerifyRequest):
    if (fault := check_fault()) is not None:
        return fault
    logger.info(
        "POST /policies/verify policy=%s dob=%s",
        db.truncate_policy_number(payload.policy_number),
        db.mask_dob(str(payload.dob)),
    )
    return db.verify_policy(payload.policy_number, payload.dob)


@app.post("/claims", response_model=ClaimCreateResponse, dependencies=[Depends(require_api_key)])
def claims_create(payload: ClaimCreateRequest):
    if (fault := check_fault()) is not None:
        return fault
    logger.info(
        "POST /claims policy=%s idempotency_key=%s",
        db.truncate_policy_number(payload.policy_number),
        payload.idempotency_key,
    )
    return db.create_claim(payload)


@app.post("/claims/lookup", response_model=ClaimLookupResponse, dependencies=[Depends(require_api_key)])
def claims_lookup(payload: ClaimLookupRequest):
    if (fault := check_fault()) is not None:
        return fault
    logger.info(
        "POST /claims/lookup claim=%s dob=%s zip=%s",
        payload.claim_number,
        db.mask_dob(str(payload.dob)),
        payload.zip,
    )
    return db.lookup_claim(payload)


@app.post("/tow-requests", response_model=TowRequestResponse, dependencies=[Depends(require_api_key)])
def tow_requests_create(payload: TowRequestCreate):
    if (fault := check_fault()) is not None:
        return fault
    logger.info("POST /tow-requests claim=%s", payload.claim_number)
    return db.create_tow_request(payload)


@app.post("/call-sessions", dependencies=[Depends(require_api_key)])
def call_sessions_create(payload: CallSessionCreate):
    logger.info("POST /call-sessions call_id=%s outcome=%s", payload.call_id, payload.outcome)
    try:
        return db.create_call_session(payload)
    except APIError:
        # Audit write failing must never be fatal to the call; the caller
        # (the agent's client) is expected to log this and move on too.
        logger.exception("call-session audit write failed")
        raise HTTPException(status_code=500, detail="audit write failed") from None


@app.post("/admin/fault", dependencies=[Depends(require_admin_token)])
def admin_fault(payload: FaultRequest) -> dict:
    _fault_state["mode"] = payload.mode
    _fault_state["remaining"] = payload.count if payload.mode != "none" else 0
    logger.info("fault injection set: mode=%s count=%s", payload.mode, payload.count)
    return dict(_fault_state)
