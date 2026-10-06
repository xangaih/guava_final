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
    end_call_with_wrapup,
    is_claim_history_question,
    needs_handoff,
    register_flow,
    set_outcome,
    transfer_to_human,
)
from .agent import agent as _agent
from .client import client
from .models import Malformed, NotFound, Ok, Unavailable

# Named (not inline) so a test can check every desk offers the moves it
# needs. Confirmed live (2026-10-06): "check ANOTHER claim" had no move on
# this card -- switch_to_status was missing because "you're already here"
# -- so the recognizer fell back to transfer_to_human.
_INTENTS = {
    "transfer_to_human": (
        "The caller explicitly asks to speak to a human, a representative, or a supervisor, "
        "or wants to enroll in a new policy, become a new customer, or get an insurance quote "
        "-- this line only handles existing policies and claims. This does NOT include the "
        "caller asking about their own claim details or the status lookup process -- those "
        "are normal parts of checking a claim, not a request for a human. (Confirmed live: "
        "fnol_flow's broader version of this intent over-triggered on a benign claim question; "
        "keeping this one narrow defensively.)"
    ),
    "transfer_for_claim_history": (
        "The caller asks how many claims they have, for a list of their claims, or about their "
        "claim history. This line can only look up one claim at a time by its number."
    ),
    "switch_to_status": (
        "The caller wants to check the status of ANOTHER claim -- a different claim number than "
        "the one just looked up. This does NOT include questions about the claim just checked."
    ),
    "switch_to_fnol": (
        "The caller explicitly states they were just in an accident, their car was just hit, "
        "or they need to report a brand new incident right now, separate from the claim they "
        "called to check on. This does NOT include asking for more general information, "
        "details, or explanations about the claim being checked -- that's a normal part of "
        "the status check."
    ),
    "switch_to_tow": (
        "The caller explicitly states they want general towing or roadside assistance "
        "information and have stopped wanting to continue checking this claim. This does NOT "
        "include asking for more general information, details, or explanations about the "
        "claim being checked -- that's a normal part of the status check."
    ),
}
_intent = IntentRecognizer(_INTENTS)

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
    # Same reason as fnol_flow.start(): a second status check in the same
    # call (via "anything else?") must get a fresh identity-attempt budget.
    call.set_variable("status_identity_attempts", 0)  # 0, not None -- see fnol_flow.start
    _set_identity_task(call)


# The identity page changes shape depending on what this call already
# established -- a competent human wouldn't re-ask a date of birth they
# verified two minutes ago, and would offer the claim they just filed:
#   - call_verified_dob set   -> no DOB blank; the stored value is reused
#   - call_claim_number set   -> the just-filed claim is offered first
# Every lookup is still exact-matched at the backend with all three
# values; only the *asking* is skipped, and only within one call.
def _set_identity_task(call: guava.Call, *, retry_note: str | None = None) -> None:
    verified_dob = call.get_variable("call_verified_dob")
    just_filed = call.get_variable("call_claim_number")

    if verified_dob:
        objective = (
            "The caller's identity (date of birth) was already verified earlier in this call. "
            "Collect only the claim number and the ZIP code on file -- do not ask for the date "
            "of birth again. Do not share any status, payment, or representative information "
            "until the lookup succeeds."
        )
    else:
        objective = (
            "Verify the caller's identity before sharing any claim details. Collect their claim "
            "number, date of birth, and ZIP code on file. The claim number is on any confirmation "
            "the caller received when the claim was filed; if they truly can't find it, offer to "
            "connect them with a representative who can look it up a different way, rather than "
            "guessing or skipping it. Do not share any status, payment, or representative "
            "information until identity is confirmed."
        )
    if just_filed:
        objective += (
            f" A claim, {just_filed}, was filed earlier in this call; if the caller wants to check "
            f"that one, use its digits as the claim number."
        )
    if retry_note:
        objective = f"{retry_note} {objective}"

    checklist: list = []
    if just_filed:
        checklist.append(
            guava.Say(
                f"Would you like to check the claim we just filed, {just_filed}, or a different "
                f"one? For a different one, you can say the digits after CLM-dash or enter them "
                f"on your phone's keypad."
            )
        )
    else:
        checklist.append(
            guava.Say(
                "I can help you check on another claim. I'll just need the claim number and ZIP "
                "code."
                if verified_dob
                else "I can help you check on an existing claim. First, I need to verify your "
                "identity."
            )
        )
        checklist.append(
            guava.Say(
                "Could you give me your claim number? You can say the digits after CLM-dash, or "
                "enter them on your phone's keypad -- whichever's easier."
            )
        )
    checklist.append(
        guava.Field(
            key="status_claim_number_digits",
            description=(
                "The 7 digits after 'CLM-' in the caller's claim number. The prefix is "
                "always CLM- and is not collected here. The caller may say the digits or "
                "enter them on their phone's keypad."
            ),
            field_type="digit_sequence",
            required=True,
        )
    )
    if not verified_dob:
        checklist.append(
            guava.Field(key="status_dob", description="The claimant's date of birth, for identity verification", field_type="date", required=True)
        )
    checklist.append(
        guava.Field(
            key="status_zip",
            description="The ZIP code on file for the policy. The caller may say the digits or enter them on their phone's keypad.",
            field_type="digit_sequence",
            required=True,
        )
    )
    call.set_task("status_identity", objective=objective, checklist=checklist)


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
    carried_dob = call.get_variable("call_verified_dob")
    try:
        dob = as_date(carried_dob if carried_dob else call.get_field("status_dob"))
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
            call.set_variable("call_verified_dob", dob.isoformat())
            _deliver_status(call, value.status, value.adjuster_name, value.adjuster_phone)
        case NotFound():
            # Same shape whether the claim doesn't exist or the DOB/ZIP
            # didn't match -- nothing is leaked about which was wrong.
            if attempts < 2 and carried_dob:
                # The carried DOB may simply not belong to this claim (a
                # spouse's claim, say). The current page has no DOB blank,
                # so a retry couldn't fix that -- drop the carry-over and
                # ask for the full set instead. Attempt budget still counts.
                call.set_variable("call_verified_dob", None)
                _set_identity_task(
                    call,
                    retry_note=(
                        "The claim number or ZIP code did not match our records. This time, "
                        "collect all three: the claim number, the date of birth, and the ZIP code."
                    ),
                )
            elif attempts < 2:
                # Same live finding as fnol_flow: "repeat all three" let the
                # caller re-say one field and the model stall on the rest.
                call.retry_task(
                    reason=(
                        "The claim number, date of birth, or ZIP code did not match our records. "
                        "Ask the caller to say or enter ALL THREE again in full -- repeating only "
                        "one or two of them is not enough for another lookup attempt."
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
    end_call_with_wrapup(call, builder(adjuster_name, adjuster_phone))


def handle_question(call: guava.Call, question: str) -> str:
    if is_claim_history_question(question):
        return copy.CLAIM_HISTORY_DEFLECTION
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
