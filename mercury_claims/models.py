"""Typed results for the backend client.

Every Client call returns one of these instead of raising, so flow code
branches on an explicit value (Ok | NotFound | Unavailable | Malformed)
rather than on a raw exception. See client.py.
"""

from dataclasses import dataclass
from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


@dataclass(frozen=True)
class Ok(Generic[T]):
    value: T


@dataclass(frozen=True)
class NotFound:
    """The resource doesn't exist, or identity didn't match. Deliberately
    the same shape either way, so the agent can't tell a caller which."""


@dataclass(frozen=True)
class Unavailable:
    """Timeout, connection error, or 5xx after the one retry."""

    reason: str


@dataclass(frozen=True)
class Malformed:
    """A response that doesn't parse, or doesn't match the expected shape."""

    reason: str


@dataclass(frozen=True)
class Locked:
    """Too many failed identity attempts for this policy/claim or from this
    phone (HTTP 423). Its own type so a flow can't mistake it for "systems
    are down": the honest response is a transfer, not an apology."""


Result = Ok[T] | NotFound | Unavailable | Malformed | Locked


class PolicyVerification(BaseModel):
    verified: bool
    holder_name: str | None = None
    status: str | None = None
    # True/False: caller's number vs the one on the policy. None: unknown
    # (hidden number, web call). A signal for the rep and the audit row,
    # not a gate -- people call from work phones and family phones.
    phone_match: bool | None = None


class ClaimCreated(BaseModel):
    claim_number: str


class ClaimStatus(BaseModel):
    status: str
    adjuster_name: str | None = None
    adjuster_phone: str | None = None
    phone_match: bool | None = None


class TowDispatched(BaseModel):
    provider_name: str
    eta_minutes: int
    request_id: str
