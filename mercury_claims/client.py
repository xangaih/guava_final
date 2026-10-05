"""HTTP client for the mock claims backend (mock_backend/).

Connect/read timeouts, one retry on timeout/connection error/5xx (reusing
the same request body, so an idempotency_key survives the retry), no
retry on 4xx. Every call returns a typed Result; nothing here raises for
a backend problem, so flow code never has to branch on a raw exception.
"""

import logging
from datetime import date, datetime

import httpx
from pydantic import BaseModel, ValidationError

from . import config
from .models import (
    ClaimCreated,
    ClaimStatus,
    Malformed,
    NotFound,
    Ok,
    PolicyVerification,
    Result,
    TowDispatched,
    Unavailable,
)

logger = logging.getLogger("mercury_claims.client")

CONNECT_TIMEOUT = 3.0
READ_TIMEOUT = 5.0


class Client:
    def __init__(self) -> None:
        self._http = httpx.Client(
            base_url=config.BACKEND_BASE_URL,
            timeout=httpx.Timeout(CONNECT_TIMEOUT, read=READ_TIMEOUT),
            headers={"X-API-Key": config.BACKEND_API_KEY},
        )

    def close(self) -> None:
        self._http.close()

    def _request(self, path: str, json: dict, model_cls: type[BaseModel]) -> Result:
        attempt = 0
        while True:
            attempt += 1
            try:
                resp = self._http.post(path, json=json)
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                if attempt < 2:
                    logger.warning("%s: %s on attempt %d, retrying", path, type(exc).__name__, attempt)
                    continue
                logger.error("%s: unavailable after retry (%s)", path, type(exc).__name__)
                return Unavailable(reason=type(exc).__name__)

            if resp.status_code == 404:
                return NotFound()
            if resp.status_code >= 500:
                if attempt < 2:
                    logger.warning("%s: HTTP %d on attempt %d, retrying", path, resp.status_code, attempt)
                    continue
                logger.error("%s: unavailable after retry (HTTP %d)", path, resp.status_code)
                return Unavailable(reason=f"http_{resp.status_code}")
            if resp.status_code >= 400:
                # A 4xx other than 404 means we sent something wrong; not
                # retried, and not treated as "temporarily unavailable."
                logger.error("%s: HTTP %d: %s", path, resp.status_code, resp.text[:200])
                return Malformed(reason=f"http_{resp.status_code}")

            try:
                data = resp.json()
            except ValueError:
                logger.error("%s: response body was not valid JSON", path)
                return Malformed(reason="invalid_json")
            try:
                return Ok(model_cls.model_validate(data))
            except ValidationError:
                logger.error("%s: response body did not match the expected shape", path)
                return Malformed(reason="schema_mismatch")

    def verify_policy(self, policy_number: str, dob: date) -> Result:
        return self._request(
            "/policies/verify",
            {"policy_number": policy_number, "dob": dob.isoformat()},
            PolicyVerification,
        )

    def create_claim(
        self,
        *,
        idempotency_key: str,
        policy_number: str,
        loss_type: str,
        loss_at: datetime,
        loss_location: str,
        description: str,
        injuries: bool,
        drivable: bool | None = None,
        police_report_filed: str | None = None,
        police_report_number: str | None = None,
        police_department: str | None = None,
        details: dict | None = None,
        intake_complete: bool = True,
    ) -> Result:
        payload = {
            "idempotency_key": idempotency_key,
            "policy_number": policy_number,
            "loss_type": loss_type,
            "loss_at": loss_at.isoformat(),
            "loss_location": loss_location,
            "description": description,
            "injuries": injuries,
            "drivable": drivable,
            "police_report_filed": police_report_filed,
            "police_report_number": police_report_number,
            "police_department": police_department,
            "details": details or {},
            "intake_complete": intake_complete,
        }
        return self._request("/claims", payload, ClaimCreated)

    def lookup_claim(self, claim_number: str, dob: date, zip_code: str) -> Result:
        return self._request(
            "/claims/lookup",
            {"claim_number": claim_number, "dob": dob.isoformat(), "zip": zip_code},
            ClaimStatus,
        )

    def request_tow(
        self,
        claim_number: str,
        pickup_address: str,
        destination_type: str,
        destination_address: str | None = None,
    ) -> Result:
        return self._request(
            "/tow-requests",
            {
                "claim_number": claim_number,
                "pickup_address": pickup_address,
                "destination_type": destination_type,
                "destination_address": destination_address,
            },
            TowDispatched,
        )

    def record_call_session(
        self,
        *,
        call_id: str,
        purpose: str | None = None,
        outcome: str | None = None,
        transfer_reason: str | None = None,
        claim_number: str | None = None,
        api_errors: list | None = None,
    ) -> None:
        """Best-effort audit write. Never raises: a failed audit write
        must never be fatal to the call, so this only logs."""
        try:
            resp = self._http.post(
                "/call-sessions",
                json={
                    "call_id": call_id,
                    "purpose": purpose,
                    "outcome": outcome,
                    "transfer_reason": transfer_reason,
                    "claim_number": claim_number,
                    "api_errors": api_errors or [],
                },
            )
            if resp.status_code >= 400:
                logger.error("/call-sessions: audit write failed (HTTP %d)", resp.status_code)
        except httpx.HTTPError as exc:
            logger.error("/call-sessions: audit write failed (%s)", exc)


# One shared instance (one connection pool) for every flow module.
client = Client()
