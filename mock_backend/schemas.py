from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class PolicyVerifyRequest(BaseModel):
    policy_number: str
    dob: date


class PolicyVerifyResponse(BaseModel):
    # One boolean for any failure (wrong policy number or wrong DOB) so a
    # caller can't learn which part they got wrong.
    verified: bool
    holder_name: str | None = None
    status: str | None = None


class ClaimCreateRequest(BaseModel):
    idempotency_key: str
    policy_number: str
    loss_type: str
    loss_at: datetime
    loss_location: str
    description: str
    injuries: bool
    drivable: bool | None = None
    police_report_filed: Literal["yes", "no", "not sure"] | None = None
    police_report_number: str | None = None
    police_department: str | None = None
    # Vehicle, passenger, other-party and vehicle-condition fields that
    # don't get their own column.
    details: dict = Field(default_factory=dict)
    intake_complete: bool = True


class ClaimCreateResponse(BaseModel):
    claim_number: str


class ClaimLookupRequest(BaseModel):
    claim_number: str
    dob: date
    zip: str


class ClaimLookupResponse(BaseModel):
    status: str
    adjuster_name: str | None = None
    adjuster_phone: str | None = None


class TowRequestCreate(BaseModel):
    claim_number: str
    pickup_address: str
    destination_type: Literal["preferred_shop", "home", "other"]
    destination_address: str | None = None


class TowRequestResponse(BaseModel):
    provider_name: str
    eta_minutes: int
    request_id: str


class CallSessionCreate(BaseModel):
    call_id: str
    purpose: str | None = None
    outcome: (
        Literal[
            "claim_created", "status_delivered", "transferred", "auth_failed", "abandoned",
            "error", "completed",
        ]
        | None
    ) = None
    transfer_reason: str | None = None
    claim_number: str | None = None
    api_errors: list = Field(default_factory=list)


class FaultRequest(BaseModel):
    mode: Literal["none", "timeout", "slow", "500", "malformed", "not_found"]
    count: int = 1
