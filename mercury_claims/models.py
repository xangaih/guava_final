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


Result = Ok[T] | NotFound | Unavailable | Malformed


class PolicyVerification(BaseModel):
    verified: bool
    holder_name: str | None = None
    status: str | None = None


class ClaimCreated(BaseModel):
    claim_number: str


class ClaimStatus(BaseModel):
    status: str
    adjuster_name: str | None = None
    adjuster_phone: str | None = None


class TowDispatched(BaseModel):
    provider_name: str
    eta_minutes: int
    request_id: str
