"""Check the status of an existing claim. Descends from
originals/claims_status/__main__.py, ported from outbound to inbound.

Removed entirely (F10): reach_person, voicemail, on_outbound_failed, DNC
handling, call_phone arguments -- this flow only ever starts when a call
comes in.

Fixed (F11): the backend's /claims/lookup can only ever return a status
plus an adjuster name/phone -- there is no settlement amount or denial
reason anywhere in the data this flow has access to, so it's structurally
impossible for the agent to speak one. Identity now checks claim number
+ DOB + ZIP (three factors, D4), not claim number + DOB alone.
"""

from guava.helpers.llm import IntentRecognizer

import guava

from . import copy
from .agent import (
    FlowHandlers,
    as_date,
    bounded_validate,
    needs_handoff,
    register_flow,
    set_outcome,
    transfer_to_human,
)
from .agent import agent as _agent
from .client import client
from .models import Malformed, NotFound, Ok, Unavailable

_intent = IntentRecognizer(
    {
        "transfer_to_human": (
            "The caller explicitly asks to speak to a human, a representative, or a supervisor, "
            "or wants to enroll in a new policy, become a new customer, or get an insurance quote "
            "-- this line only handles existing policies and claims. This does NOT include the "
            "caller asking about their own claim details or the status lookup process -- those "
            "are normal parts of checking a claim, not a request for a human. (Confirmed live: "
            "fnol_flow's broader version of this intent over-triggered on a benign claim question; "
            "keeping this one narrow defensively.)"
        ),
        "switch_to_fnol": (
            "The caller says they actually want to report a new claim instead of checking the "
            "status of an existing one -- a change of mind about why they're calling."
        ),
        "switch_to_tow": (
            "The caller says they actually want to ask a general towing or roadside assistance "
            "question instead of checking a claim's status -- a change of mind about why they're "
            "calling."
        ),
    }
)

_STATUS_INSTRUCTIONS = {
    "received": copy.status_received,
    "under_review": copy.status_under_review,
    "awaiting_documents": copy.status_awaiting_documents,
    "approved": copy.status_approved,
    "closed": copy.status_closed,
}

# A question during a status check defaults to "assume it's about this
# claim" (the F11-safe deflection, which never leaks anything either
# way) -- the opposite default from fnol_flow's guard, since most
# questions asked here naturally are about the claim just looked up.
# Only a question that's clearly about something else entirely (towing,
# enrollment) gets a different, honest response instead of the
# claim-status non-sequitur.
_UNRELATED_TOPIC_KEYWORDS = (
    "tow",
    "roadside",
    "road side",
    "enroll",
    "sign up",
    "new policy",
    "new customer",
    "get a quote",
    "get insurance",
)


def _is_unrelated_to_this_claim(question: str) -> bool:
    q = question.lower()
    return any(kw in q for kw in _UNRELATED_TOPIC_KEYWORDS)


def _check_claim_number_digits(value: object) -> tuple[bool, str]:
    text = str(value)
    if text.isdigit() and len(text) == 7:
        return True, ""
    return (
        False,
        "I need exactly the 7 digits after CLM-dash, nothing else. Could you say or enter just "
        "those 7 digits?",
    )


def _check_zip(value: object) -> tuple[bool, str]:
    text = str(value)
    if text.isdigit() and len(text) == 5:
        return True, ""
    return False, "That doesn't look like a 5-digit ZIP code. Could you repeat it?"


def start(call: guava.Call) -> None:
    call.set_task(
        "status_identity",
        objective=(
            "Verify the caller's identity before sharing any claim details. Collect their claim "
            "number, date of birth, and ZIP code on file. Do not share any status, payment, or "
            "representative information until identity is confirmed."
        ),
        checklist=[
            guava.Say(
                "I can help you check on an existing claim. First, I need to verify your identity."
            ),
            guava.Say(
                "Could you give me your claim number? You can say the digits after CLM-dash, or "
                "enter them on your phone's keypad -- whichever's easier."
            ),
            guava.Field(
                key="status_claim_number_digits",
                description=(
                    "The 7 digits after 'CLM-' in the caller's claim number. The prefix is "
                    "always CLM- and is not collected here. The caller may say the digits or "
                    "enter them on their phone's keypad."
                ),
                field_type="digit_sequence",
                required=True,
            ),
            guava.Field(key="status_dob", description="The claimant's date of birth, for identity verification", field_type="date", required=True),
            guava.Field(
                key="status_zip",
                description="The ZIP code on file for the policy. The caller may say the digits or enter them on their phone's keypad.",
                field_type="digit_sequence",
                required=True,
            ),
        ],
    )


_agent.on_validate("status_claim_number_digits")(
    bounded_validate("status_claim_number_digits", _check_claim_number_digits)
)
_agent.on_validate("status_zip")(bounded_validate("status_zip", _check_zip))


@_agent.on_task_complete("status_identity")
def _on_identity_complete(call: guava.Call) -> None:
    if needs_handoff(call):
        set_outcome(call, "transferred", transfer_reason="validation_exhausted")
        transfer_to_human(call)
        return

    claim_number = f"CLM-{call.get_field('status_claim_number_digits')}"
    try:
        dob = as_date(call.get_field("status_dob"))
    except (ValueError, KeyError, TypeError):
        # Same belt-and-suspenders as fnol_flow's identity check: fail
        # cleanly and immediately rather than crashing on_task_complete
        # uncaught.
        set_outcome(call, "error", transfer_reason="dob_unparseable")
        transfer_to_human(call, instructions=copy.TRANSFER_BACKEND_DOWN)
        return
    zip_code = call.get_field("status_zip")
    result = client.lookup_claim(claim_number, dob, zip_code)

    attempts = call.get_variable("status_identity_attempts", 0) + 1
    call.set_variable("status_identity_attempts", attempts)

    match result:
        case Ok(value):
            call.set_variable("status_claim_number", claim_number)
            _deliver_status(call, value.status, value.adjuster_name, value.adjuster_phone)
        case NotFound():
            # Same shape whether the claim doesn't exist or the DOB/ZIP
            # didn't match -- nothing is leaked about which was wrong.
            if attempts < 2:
                call.retry_task(
                    reason=(
                        "The claim number, date of birth, or ZIP code did not match our records. "
                        "Ask the caller to carefully repeat all three."
                    )
                )
            else:
                set_outcome(call, "auth_failed", transfer_reason="identity_mismatch")
                transfer_to_human(
                    call,
                    instructions=(
                        "Let the caller know the information provided doesn't match our records and, "
                        "for security, you can't share claim details. Connect them with a "
                        "representative who can help look up their claim."
                    ),
                )
        case Unavailable() | Malformed():
            set_outcome(call, "error", transfer_reason="backend_unavailable")
            transfer_to_human(call, instructions=copy.TRANSFER_BACKEND_DOWN)


def _deliver_status(call: guava.Call, status: str, adjuster_name: str | None, adjuster_phone: str | None) -> None:
    if status in ("denied", "needs_human"):
        # H8: warm transfer, never speak the reason.
        set_outcome(call, "transferred", transfer_reason=f"status_{status}")
        transfer_to_human(call, instructions=copy.STATUS_TRANSFER_NO_REASON)
        return

    builder = _STATUS_INSTRUCTIONS.get(status)
    if builder is None:
        # An unknown or missing status counts as unavailable.
        set_outcome(call, "error", transfer_reason="unrecognized_status")
        transfer_to_human(call, instructions=copy.TRANSFER_BACKEND_DOWN)
        return

    set_outcome(call, "status_delivered")
    call.hangup(final_instructions=builder(adjuster_name, adjuster_phone))


def handle_question(call: guava.Call, question: str) -> str:
    if _is_unrelated_to_this_claim(question):
        # Confirmed live (2026-10-05): a totally unrelated question
        # ("can you give me information on the towing requests?") got
        # the claim-detail deflection below, which read as a
        # non-sequitur -- same bug class as fnol_flow's old blunt
        # handle_question, just not fixed here yet at the time.
        return copy.STATUS_UNRELATED_QUESTION_ACKNOWLEDGMENT
    # F11: never speak settlement amounts or denial reasons; the backend
    # doesn't even expose them, so there's nothing to leak, but the
    # phrasing here keeps the caller from feeling stonewalled.
    return copy.STATUS_CLAIM_DETAIL_DEFLECTION


def classify_intent(intent_summary: str):
    return _intent.classify(intent_summary)


def build_session_summary(call: guava.Call, event) -> dict:
    return {
        "claim_number": call.get_variable("status_claim_number"),
        "identity_attempts": call.get_variable("status_identity_attempts"),
    }


register_flow(
    "status",
    FlowHandlers(
        start=start,
        handle_question=handle_question,
        classify_intent=classify_intent,
        build_session_summary=build_session_summary,
    ),
)
