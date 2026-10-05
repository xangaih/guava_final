"""The one shared Agent for the Mercury Claims line.

Single-slot callbacks (on_call_received, on_call_start, on_question,
on_action_request, on_session_end) live ONLY here, never in a flow
module: the SDK stores one function per slot, and a second registration
silently overwrites the first (F13). Flow modules register themselves
via register_flow() and this module dispatches to whichever flow is
active, by call.get_variable("flow").

Keyed callbacks (on_task_complete("<task>"), on_validate("<field>"),
on_action("<key>")) are also agent-global, so flow modules prefix their
task/field names (fnol_, status_, tow_) to avoid collisions -- except
the route task and the shared "transfer_to_human" action defined here,
which are intentionally common to every flow (H3/H4 both just mean
"transfer", regardless of which flow the caller was in).
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Optional

import guava
from guava.events import BotSessionEnded

from . import config, copy
from .client import client

logger = logging.getLogger("mercury_claims.agent")

agent = guava.Agent(
    name="Avery",
    organization="Mercury Insurance — Auto Claims",
    purpose=(
        "handle inbound calls to Mercury Insurance's auto claims line: "
        "report a new claim, check an existing claim's status, or answer "
        "towing and roadside assistance questions"
    ),
)


@dataclass
class FlowHandlers:
    """What a flow module registers with the shared agent.

    `start` is required; everything else is optional, and the shared
    single-slot callbacks fall back sensibly when the active flow hasn't
    registered a given hook.
    """

    start: Callable[[guava.Call], None]
    handle_question: Optional[Callable[[guava.Call, str], str]] = None
    classify_intent: Optional[Callable[[str], object]] = None
    build_session_summary: Optional[Callable[[guava.Call, BotSessionEnded], dict]] = None


_flows: dict[str, FlowHandlers] = {}

ROUTE_TASK = "route"
FLOW_BY_PURPOSE = {
    "report_new_claim": "fnol",
    "check_claim_status": "status",
    "towing_or_roadside_question": "tow",
}


def register_flow(name: str, handlers: FlowHandlers) -> None:
    """Called by each flow module at import time (mercury_claims/__main__.py
    imports every flow module before starting the agent)."""
    _flows[name] = handlers


def _current_flow(call: guava.Call) -> Optional[FlowHandlers]:
    return _flows.get(call.get_variable("flow"))


def transfer_to_human(call: guava.Call, instructions: str = copy.TRANSFER_GENERIC) -> None:
    call.transfer(destination=config.HUMAN_LINE_NUMBER, instructions=instructions)


# --- Shared handoff infrastructure (section 8.5: handoff rules in code) ---
#
# H7 (a validator fails 3 times on one field -> transfer) applies to every
# flow's fields, so it's implemented once here rather than per flow.
# on_validate's automatic retry (the SDK calls call.retry_task() itself
# whenever a validator returns non-True) has no built-in attempt limit --
# left alone, a caller who keeps giving an invalid value loops forever,
# which is exactly the "no loops" failure the brief calls out. Wrapping a
# validator with bounded_validate() caps that at MAX_VALIDATION_ATTEMPTS:
# past the limit it gives up (returns True so the task completes) and
# flags the call via a shared variable; every flow's on_task_complete
# handler checks needs_handoff(call) first and transfers if it's set,
# before doing anything else.

MAX_VALIDATION_ATTEMPTS = 3


def bounded_validate(field_key: str, check: Callable[[object], tuple[bool, str]]):
    attempts_key = f"_{field_key}_validation_attempts"

    def validator(call: guava.Call, value) -> bool | tuple[bool, str]:
        ok, message = check(value)
        if ok:
            return True
        attempts = call.get_variable(attempts_key, 0) + 1
        call.set_variable(attempts_key, attempts)
        if attempts >= MAX_VALIDATION_ATTEMPTS:
            call.set_variable("handoff_reason", "validation_exhausted")
            return True
        return (False, message)

    return validator


def needs_handoff(call: guava.Call) -> bool:
    return call.get_variable("handoff_reason") is not None


# --- call_sessions audit (C14) ---
#
# "claim_created" | "status_delivered" | "transferred" | "auth_failed" |
# "abandoned" | "error" | "completed". A flow calls set_outcome() at each
# terminal branch (identity failure, backend-down, successful delivery,
# transfer, etc.); on_session_end reads it back and writes the audit row.
# If a call ends without ever reaching a terminal branch (the caller just
# hangs up mid-task), outcome stays unset and on_session_end reports it
# as "abandoned" -- that's what the value is for.
#
# Confirmed live (2026-10-05): a claim was successfully filed, then,
# while the call was winding down, a benign follow-up question ("do you
# need my insurance carrier?") was misclassified by the mid-call intent
# recognizer as "wants a human" and triggered a real transfer. The audit
# row ended up saying outcome=transferred with no sign a claim had ever
# been created -- the later call silently overwrote the earlier one.
# A genuine success must not be erased by a transfer that happens after
# the caller's actual purpose was already fulfilled, so once a terminal
# success is recorded, a later non-success call only annotates the
# transfer reason instead of overwriting the outcome.

_TERMINAL_SUCCESS_OUTCOMES = {"claim_created", "status_delivered", "completed"}


def set_outcome(call: guava.Call, outcome: str, *, transfer_reason: str | None = None) -> None:
    existing = call.get_variable("call_outcome")
    if existing in _TERMINAL_SUCCESS_OUTCOMES and outcome not in _TERMINAL_SUCCESS_OUTCOMES:
        if transfer_reason is not None:
            call.set_variable("call_transfer_reason", f"post_completion:{transfer_reason}")
        return
    call.set_variable("call_outcome", outcome)
    if transfer_reason is not None:
        call.set_variable("call_transfer_reason", transfer_reason)


_REDACTED_NAME_KEYS = ("policyholder_name", "name")
_REDACTED_POLICY_KEYS = ("policy_number",)
_REDACTED_DOB_KEYS = ("dob",)


def _redact_summary(summary: dict) -> dict:
    # Local application logs should be PII-free (C14), same principle as
    # the backend's own masked logging -- this is a different sink than
    # the call_sessions table (which never receives these fields at all).
    redacted = {}
    for key, value in summary.items():
        if value is None:
            redacted[key] = value
        elif any(k in key for k in _REDACTED_DOB_KEYS):
            redacted[key] = "****-**-**"
        elif any(k in key for k in _REDACTED_POLICY_KEYS):
            redacted[key] = f"{str(value)[:4]}***"
        elif any(k in key for k in _REDACTED_NAME_KEYS):
            redacted[key] = "<redacted>"
        else:
            redacted[key] = value
    return redacted


# --- Typed-field coercion ---
#
# The SDK's ActionItemCompletedEvent carries `payload: Any` with no local
# type coercion (verified against the installed source). Confirmed live
# (2026-10-05, real phone call): a "date" field's payload is a dict,
# {'day': 12, 'month': 4, 'year': 1988} -- not an ISO string, not a
# datetime.date. str(value).fromisoformat() on that dict crashed
# on_task_complete uncaught, which didn't fail cleanly: the SDK caught
# the exception and sent an ExpertErrorCommand, but the model just
# stalled ("one moment... still looking into this...") for over a
# minute before giving up confused. A "datetime" field's shape wasn't
# reached in that call (it crashed on the DOB field first); the
# hour/minute/second handling below is inferred from the same pattern,
# not independently confirmed -- flagging that.

def as_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, dict):
        return date(int(value["year"]), int(value["month"]), int(value["day"]))
    return date.fromisoformat(str(value))


def as_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, dict):
        return datetime(
            int(value["year"]),
            int(value["month"]),
            int(value["day"]),
            int(value.get("hour", 0)),
            int(value.get("minute", 0)),
            int(value.get("second", 0)),
        )
    text = str(value)
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return datetime.combine(date.fromisoformat(text), datetime.min.time())


@agent.on_call_received
def on_call_received(call_info: guava.CallInfo) -> guava.IncomingCallAction:
    # Mercury's claims line runs 24/7 (C7): always accept, no business hours.
    return guava.AcceptCall()


@agent.on_call_start
def on_call_start(call: guava.Call) -> None:
    call.read_script(copy.OPENING_DISCLOSURE)
    call.set_task(
        ROUTE_TASK,
        objective="Find out why the caller is contacting Mercury Insurance's auto claims line.",
        checklist=[
            guava.Field(
                key="call_purpose",
                description="Why the caller is contacting Mercury Insurance Auto Claims",
                field_type="multiple_choice",
                choices=[
                    "report_new_claim",
                    "check_claim_status",
                    "towing_or_roadside_question",
                    "something_else",
                ],
                required=True,
            ),
        ],
    )


@agent.on_task_complete(ROUTE_TASK)
def on_route_complete(call: guava.Call) -> None:
    purpose = call.get_field("call_purpose")
    flow_name = FLOW_BY_PURPOSE.get(purpose)

    if flow_name is None:
        # "something_else", or anything unrecognized.
        set_outcome(call, "transferred", transfer_reason="something_else")
        transfer_to_human(call)
        return

    handlers = _flows.get(flow_name)
    if handlers is None:
        # Listed as a call type in scope (section 1) but not built yet
        # in this codebase.
        logger.warning("no flow registered for %r yet", flow_name)
        set_outcome(call, "transferred", transfer_reason="flow_not_built")
        transfer_to_human(call)
        return

    switch_flow(call, flow_name)


@agent.on_question
def on_question(call: guava.Call, question: str) -> str:
    handlers = _current_flow(call)
    if handlers and handlers.handle_question:
        return handlers.handle_question(call, question)
    return "Let me get someone who can help with that."


@agent.on_action_request
def on_action_request(call: guava.Call, intent_summary: str):
    handlers = _current_flow(call)
    if handlers and handlers.classify_intent:
        return handlers.classify_intent(intent_summary)
    return None


@agent.on_action("transfer_to_human")
def on_transfer_to_human(call: guava.Call) -> None:
    # H3 (asks for a person/supervisor) and H4 (disputes fault, mentions
    # attorney/lawsuit/complaint) both just mean "transfer" -- one shared
    # action key avoids every flow registering its own near-duplicate.
    set_outcome(call, "transferred", transfer_reason="mid_call_intent")
    transfer_to_human(call)


# --- Mid-call flow switching ---
#
# Confirmed live (2026-10-05): a caller mid-call in tow_flow said "I
# wanna file a claim" -- there was no mechanism anywhere to recognize or
# act on wanting a *different* top-level purpose (only "wants a human"
# was ever wired up), so the model had nothing it could do and stalled
# with filler until the caller hung up. A SuggestedAction is just
# {key, description} with no parameter slot, so the target flow has to
# be encoded in the action key itself -- one shared action per
# destination, same pattern as transfer_to_human.

def switch_flow(call: guava.Call, flow_name: str) -> None:
    # Clear any stale handoff flag from the old flow -- otherwise the
    # new flow's very first task-completion would see needs_handoff()
    # still True (from 3 failed validator attempts, say) and immediately
    # transfer for no reason the caller would understand.
    call.set_variable("handoff_reason", None)
    call.set_variable("flow", flow_name)
    _flows[flow_name].start(call)


@agent.on_action("switch_to_fnol")
def on_switch_to_fnol(call: guava.Call) -> None:
    switch_flow(call, "fnol")


@agent.on_action("switch_to_status")
def on_switch_to_status(call: guava.Call) -> None:
    switch_flow(call, "status")


@agent.on_action("switch_to_tow")
def on_switch_to_tow(call: guava.Call) -> None:
    switch_flow(call, "tow")


@agent.on_session_end
def on_session_end(call: guava.Call, event: BotSessionEnded) -> None:
    handlers = _current_flow(call)
    summary = handlers.build_session_summary(call, event) if handlers and handlers.build_session_summary else {}
    outcome = call.get_variable("call_outcome") or "abandoned"

    # Audit write; failure here must never be fatal to the call -- it
    # already isn't, since record_call_session only ever logs on failure.
    client.record_call_session(
        call_id=call.id,
        purpose=call.get_variable("flow"),
        outcome=outcome,
        transfer_reason=call.get_variable("call_transfer_reason"),
        claim_number=summary.get("claim_number"),
    )

    logger.info(
        "call ended: flow=%s outcome=%s termination_reason=%s dnc=%s %s",
        call.get_variable("flow"),
        outcome,
        event.termination_reason,
        event.dnc,
        _redact_summary(summary),
    )
