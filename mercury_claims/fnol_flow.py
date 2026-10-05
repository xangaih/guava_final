"""Report a new auto claim. Descends from originals/fnol/__main__.py.

Same task shape as the original (identity -> loss basics -> vehicle ->
police report -> other party -> submit, with a tow offer when the car
isn't drivable), but:
  - every collected field reaches the claim record (C2, fixes F1)
  - no promise is spoken unless the server confirmed it (C4, fixes F2)
  - DOB is a typed date field, 2 attempts then transfer, no enumeration
    leak (C5, fixes F4/F5)
  - a lapsed/cancelled policy does not end the call -- intake continues
    and the backend flags it for review on its own (C6, fixes F6)
  - handoff rules H1 (disclosure, already in agent.py's opening script),
    H2, H5, H6, H7 are in code, not left to the model (C10)
"""

import logging
import uuid
from datetime import datetime

import guava
from guava.helpers.llm import IntentRecognizer

from . import config, copy
from .agent import (
    FlowHandlers,
    as_date,
    as_datetime,
    bounded_validate,
    needs_handoff,
    register_flow,
    set_outcome,
    transfer_to_human,
)
from .agent import agent as _agent
from .client import client
from .models import Malformed, NotFound, Ok, Unavailable

logger = logging.getLogger("mercury_claims.fnol_flow")

_intent = IntentRecognizer(
    {
        "transfer_to_human": (
            "The caller explicitly asks to speak to a human, a representative, or a supervisor; "
            "disputes who was at fault; mentions a lawyer, attorney, lawsuit, or formal complaint; "
            "or states that they are not the policyholder and are calling on someone else's behalf. "
            "This does NOT include the caller asking what information is still needed, asking "
            "about their own claim or vehicle details, or asking general questions about the "
            "process -- those are normal parts of filing a claim, not a request for a human."
        ),
    }
)

# H9's deflection guard is deterministic (code), not another model call
# stacked on top of the intent recognizer -- same reasoning as tow_flow's
# guard. Confirmed live (2026-10-05): the single canned deflection was
# returned for EVERY question, including benign ones like "what else do
# you need?", which reads as a non-sequitur when the question has
# nothing to do with coverage, fault, or legal liability.
_COVERAGE_FAULT_LEGAL_KEYWORDS = (
    "covered",
    "coverage",
    "my policy",
    "deductible",
    "premium",
    "whose fault",
    "my fault",
    "at fault",
    "liable",
    "liability",
    "lawsuit",
    "attorney",
    "lawyer",
    "sue",
    "complaint",
    "how much will i",
    "payout",
    "pay out",
    "settlement",
)


def _is_coverage_fault_or_legal(question: str) -> bool:
    q = question.lower()
    return any(kw in q for kw in _COVERAGE_FAULT_LEGAL_KEYWORDS)


def _get_or_create_idempotency_key(call: guava.Call) -> str:
    key = call.get_variable("fnol_idempotency_key")
    if not key:
        key = str(uuid.uuid4())
        call.set_variable("fnol_idempotency_key", key)
    return key


def _check_policy_number_digits(value: object) -> tuple[bool, str]:
    text = str(value)
    if text.isdigit() and len(text) == 6:
        return True, ""
    return (
        False,
        "I need exactly the 6 digits after MCY-dash, nothing else. Could you say or enter just "
        "those 6 digits?",
    )


def _check_loss_at_not_future(value: object) -> tuple[bool, str]:
    try:
        when = as_datetime(value)
    except (ValueError, KeyError, TypeError):
        return False, "I couldn't understand that date and time. Could you repeat it?"
    if when > datetime.now():
        return False, "That's in the future -- please confirm when the incident actually happened."
    return True, ""


def start(call: guava.Call) -> None:
    call.set_task(
        "fnol_identity",
        objective=(
            "Verify the caller's identity before collecting any claim information. Collect their "
            "policy number and date of birth. Do not discuss claim details, coverage, or policy "
            "status until identity is verified."
        ),
        checklist=[
            guava.Say(
                "I'm sorry to hear you may be dealing with a loss. I'll help you get a claim "
                "started. First, I need to verify your identity."
            ),
            guava.Field(
                key="fnol_policy_number_digits",
                description=(
                    "The 6 digits after 'MCY-' in the caller's policy number. The prefix is "
                    "always MCY- and is not collected here. The caller may say the digits or "
                    "enter them on their phone's keypad."
                ),
                field_type="digit_sequence",
                required=True,
            ),
            guava.Field(
                key="fnol_dob",
                description="The caller's date of birth, for identity verification",
                field_type="date",
                required=True,
            ),
        ],
    )


_agent.on_validate("fnol_policy_number_digits")(
    bounded_validate("fnol_policy_number_digits", _check_policy_number_digits)
)
_agent.on_validate("fnol_loss_at")(bounded_validate("fnol_loss_at", _check_loss_at_not_future))


@_agent.on_task_complete("fnol_identity")
def _on_identity_complete(call: guava.Call) -> None:
    if needs_handoff(call):
        set_outcome(call, "transferred", transfer_reason="validation_exhausted")
        transfer_to_human(call)
        return

    policy_number = f"MCY-{call.get_field('fnol_policy_number_digits')}"
    try:
        dob = as_date(call.get_field("fnol_dob"))
    except (ValueError, KeyError, TypeError):
        # Belt-and-suspenders: if the field payload is ever a shape
        # as_date doesn't handle, fail cleanly and immediately instead of
        # crashing on_task_complete uncaught -- that left a real call
        # silently stalling for over a minute before giving up confused.
        set_outcome(call, "error", transfer_reason="dob_unparseable")
        transfer_to_human(call, instructions=copy.TRANSFER_BACKEND_DOWN)
        return
    result = client.verify_policy(policy_number, dob)

    attempts = call.get_variable("fnol_identity_attempts", 0) + 1
    call.set_variable("fnol_identity_attempts", attempts)

    match result:
        case Ok(value) if value.verified:
            call.set_variable("fnol_policy_number", policy_number)
            call.set_variable("fnol_policyholder_name", value.holder_name)
            call.set_task(
                "fnol_loss_basics",
                objective=(
                    f"Identity verified -- the caller is {value.holder_name}. Find out what kind of "
                    f"auto loss they're reporting, when and where it happened, and whether anyone was "
                    f"hurt. Be empathetic and patient."
                ),
                checklist=[
                    guava.Say(
                        f"Thank you, {value.holder_name}. Your identity has been verified. Now let's "
                        f"get your claim started. Can you tell me generally what happened?"
                    ),
                    guava.Field(
                        key="fnol_loss_type",
                        description="The category of auto loss being reported",
                        field_type="multiple_choice",
                        choices=["collision", "theft", "vandalism", "weather", "other"],
                        required=True,
                    ),
                    guava.Field(
                        key="fnol_loss_at",
                        description="The date and time the incident occurred",
                        field_type="datetime",
                        required=True,
                    ),
                    guava.Field(
                        key="fnol_loss_location",
                        description="The address or location where the incident occurred",
                        field_type="text",
                        required=True,
                    ),
                    guava.Field(
                        key="fnol_loss_description",
                        description="A description of what happened",
                        field_type="text",
                        required=True,
                    ),
                    guava.Field(
                        key="fnol_injuries",
                        description="Whether any injuries to any person were involved in this incident",
                        field_type="multiple_choice",
                        choices=["yes", "no"],
                        required=True,
                    ),
                ],
            )
        case Ok() | NotFound():
            # Same failure shape either way: wrong policy number or wrong
            # DOB look identical, so the caller can't learn which was
            # wrong (F4/F5). Up to 2 attempts, then transfer (H5).
            if attempts < 2:
                call.retry_task(
                    reason=(
                        "The policy number or date of birth did not match our records. Ask the "
                        "caller to carefully repeat both."
                    )
                )
            else:
                set_outcome(call, "auth_failed", transfer_reason="identity_mismatch")
                transfer_to_human(
                    call,
                    instructions=(
                        "Let the caller know the information provided doesn't match our records and, "
                        "for security, you can't continue without verifying identity. Connect them "
                        "with a representative who can help look up their policy."
                    ),
                )
        case Unavailable() | Malformed():
            # H6: backend unavailable after the client's retry -- say so
            # honestly, never pretend identity was checked.
            set_outcome(call, "error", transfer_reason="backend_unavailable")
            transfer_to_human(
                call,
                instructions=(
                    "Let the caller know our systems are temporarily unavailable and you can't "
                    "verify their identity right now. Apologize, and connect them with a "
                    "representative."
                ),
            )


@_agent.on_task_complete("fnol_loss_basics")
def _on_loss_basics_complete(call: guava.Call) -> None:
    if needs_handoff(call):
        set_outcome(call, "transferred", transfer_reason="validation_exhausted")
        transfer_to_human(call)
        return

    if call.get_field("fnol_injuries") == "yes":
        _submit_partial_claim_for_injury(call)
        return

    call.set_task(
        "fnol_vehicle",
        objective=(
            "Collect details about the insured vehicle: year, make, model, color, plate, how many "
            "passengers (if known), a description of the damage, where the vehicle is now, and "
            "whether it's still drivable."
        ),
        checklist=[
            guava.Field(key="fnol_vehicle_year", description="The insured vehicle's model year", field_type="integer", required=True),
            guava.Field(key="fnol_vehicle_make", description="The insured vehicle's make", field_type="text", required=True),
            guava.Field(key="fnol_vehicle_model", description="The insured vehicle's model", field_type="text", required=True),
            guava.Field(key="fnol_vehicle_color", description="The insured vehicle's color", field_type="text", required=True),
            guava.Field(key="fnol_vehicle_plate", description="The insured vehicle's license plate", field_type="text", required=True),
            guava.Field(key="fnol_passenger_count", description="Number of passengers in the insured vehicle, if known", field_type="integer", required=False),
            guava.Field(key="fnol_damage_description", description="A description of the damage to the insured vehicle", field_type="text", required=True),
            guava.Field(key="fnol_vehicle_location", description="Where the insured vehicle currently is", field_type="text", required=True),
            guava.Field(
                key="fnol_drivable",
                description="Whether the insured vehicle is still drivable",
                field_type="multiple_choice",
                choices=["yes", "no"],
                required=True,
            ),
        ],
    )


def _submit_partial_claim_for_injury(call: guava.Call) -> None:
    result = client.create_claim(
        idempotency_key=_get_or_create_idempotency_key(call),
        policy_number=call.get_variable("fnol_policy_number"),
        loss_type=call.get_field("fnol_loss_type"),
        loss_at=as_datetime(call.get_field("fnol_loss_at")),
        loss_location=call.get_field("fnol_loss_location"),
        description=call.get_field("fnol_loss_description"),
        injuries=True,
        intake_complete=False,
    )
    match result:
        case Ok(value):
            # H2: injuries reported -> save a partial record, then
            # transfer. Only say "saved" because the write was confirmed.
            set_outcome(call, "claim_created", transfer_reason="injury_reported")
            transfer_to_human(
                call,
                instructions=(
                    f"The caller has reported injuries. Let them know that because injuries are "
                    f"involved, you need to connect them with a representative who can help right "
                    f"away. Their information so far has been saved under claim number "
                    f"{value.claim_number}. Do not discuss coverage or claim outcome."
                ),
            )
        case _:
            set_outcome(call, "error", transfer_reason="injury_reported_save_failed")
            transfer_to_human(
                call,
                instructions=(
                    "The caller has reported injuries. Let them know you're connecting them with a "
                    "representative right away. Do NOT say their information has been saved -- the "
                    "save could not be confirmed due to a system issue. Do not discuss coverage or "
                    "claim outcome."
                ),
            )


@_agent.on_task_complete("fnol_vehicle")
def _on_vehicle_complete(call: guava.Call) -> None:
    if needs_handoff(call):
        set_outcome(call, "transferred", transfer_reason="validation_exhausted")
        transfer_to_human(call)
        return
    call.set_task(
        "fnol_police_report",
        objective="Find out whether a police report was filed for this incident.",
        checklist=[
            guava.Field(
                key="fnol_police_report_filed",
                description="Whether a police report was filed for this incident",
                field_type="multiple_choice",
                choices=["yes", "no", "not sure"],
                required=True,
            ),
            guava.Field(key="fnol_police_report_number", description="The police report number, if known", field_type="text", required=False),
            guava.Field(key="fnol_police_department", description="The police department that responded, if known", field_type="text", required=False),
        ],
    )


@_agent.on_task_complete("fnol_police_report")
def _on_police_report_complete(call: guava.Call) -> None:
    if needs_handoff(call):
        set_outcome(call, "transferred", transfer_reason="validation_exhausted")
        transfer_to_human(call)
        return
    call.set_task(
        "fnol_other_party",
        objective=(
            "Ask if another party was involved and, if so, collect what the caller knows about "
            "them. All of this is optional -- don't push if they don't know."
        ),
        checklist=[
            guava.Field(key="fnol_other_party_name", description="The other party's name, if known", field_type="text", required=False),
            guava.Field(key="fnol_other_party_plate", description="The other party's license plate, if known", field_type="text", required=False),
            guava.Field(key="fnol_other_party_insurer", description="The other party's insurance company, if known", field_type="text", required=False),
        ],
    )


@_agent.on_task_complete("fnol_other_party")
def _on_other_party_complete(call: guava.Call) -> None:
    if needs_handoff(call):
        set_outcome(call, "transferred", transfer_reason="validation_exhausted")
        transfer_to_human(call)
        return
    _submit_claim(call)


def _submit_claim(call: guava.Call) -> None:
    details = {
        "vehicle": {
            "year": call.get_field("fnol_vehicle_year"),
            "make": call.get_field("fnol_vehicle_make"),
            "model": call.get_field("fnol_vehicle_model"),
            "color": call.get_field("fnol_vehicle_color"),
            "plate": call.get_field("fnol_vehicle_plate"),
        },
        "passenger_count": call.get_field("fnol_passenger_count"),
        "damage_description": call.get_field("fnol_damage_description"),
        "vehicle_location": call.get_field("fnol_vehicle_location"),
        "other_party": {
            "name": call.get_field("fnol_other_party_name"),
            "plate": call.get_field("fnol_other_party_plate"),
            "insurer": call.get_field("fnol_other_party_insurer"),
        },
    }
    drivable = call.get_field("fnol_drivable") == "yes"
    result = client.create_claim(
        idempotency_key=_get_or_create_idempotency_key(call),
        policy_number=call.get_variable("fnol_policy_number"),
        loss_type=call.get_field("fnol_loss_type"),
        loss_at=as_datetime(call.get_field("fnol_loss_at")),
        loss_location=call.get_field("fnol_loss_location"),
        description=call.get_field("fnol_loss_description"),
        injuries=False,
        drivable=drivable,
        police_report_filed=call.get_field("fnol_police_report_filed"),
        police_report_number=call.get_field("fnol_police_report_number"),
        police_department=call.get_field("fnol_police_department"),
        details=details,
    )

    match result:
        case Ok(value):
            call.set_variable("fnol_claim_number", value.claim_number)
            if not drivable:
                call.set_task(
                    "fnol_tow_offer",
                    objective=(
                        "The vehicle isn't drivable. Offer to arrange a tow. If they want one, find "
                        "out where it should go; if not, that's fine."
                    ),
                    checklist=[
                        guava.Field(
                            key="fnol_tow_destination",
                            description=(
                                "Where the caller wants the vehicle towed, or that they don't want a "
                                "tow arranged right now"
                            ),
                            field_type="multiple_choice",
                            choices=["preferred_shop", "home", "other", "decline"],
                            required=True,
                        ),
                        guava.Field(
                            key="fnol_tow_destination_address",
                            description="The destination address for the tow, if they want one",
                            field_type="text",
                            required=False,
                        ),
                    ],
                )
            else:
                _finish_claim(call, value.claim_number)
        case _:
            # H6: backend unavailable (or a malformed response) even after
            # the client's retry. Never say "filed."
            set_outcome(call, "error", transfer_reason="claim_submit_failed")
            transfer_to_human(
                call,
                instructions=(
                    "Let the caller know the claim could not be submitted right now due to a system "
                    "issue. Apologize, and connect them with a representative who can help. Do not "
                    "say the claim was filed or saved."
                ),
            )


# --- C15 (stretch): SMS confirmation of the claim number ---
#
# Off by default (SMS_CONFIRMATION_ENABLED). Only say "you've been
# texted" if send_sms actually returned without raising -- the API
# accepting the request is not proof the handset received it, so this
# needs a real test on the demo phone before the flag is ever turned on
# (see README). Scoped to the final successful-submission paths only
# (drivable=yes finish, and both tow-offer outcomes) -- not the
# injury-partial-claim path, which ends in a transfer, not a goodbye.

def _maybe_send_sms_confirmation(call: guava.Call, claim_number: str) -> bool:
    if not config.SMS_CONFIRMATION_ENABLED:
        return False
    info = call.call_info
    if getattr(info, "call_type", None) != "pstn":
        return False
    from_number = getattr(info, "from_number", None)
    if not from_number:
        return False
    try:
        guava.Client().send_sms(
            from_number=config.GUAVA_AGENT_NUMBER,
            to_number=from_number,
            message=f"Mercury Insurance (demo): your claim number is {claim_number}. This is a demo text, not a real claim.",
        )
        return True
    except Exception:
        logger.exception("send_sms failed for claim %s", claim_number)
        return False


def _sms_confirmation_note(call: guava.Call, claim_number: str) -> str:
    if _maybe_send_sms_confirmation(call, claim_number):
        return " You've also been sent a text message confirming the claim number."
    return ""


def _finish_claim(call: guava.Call, claim_number: str) -> None:
    set_outcome(call, "claim_created")
    policyholder = call.get_variable("fnol_policyholder_name")
    sms_note = _sms_confirmation_note(call, claim_number)
    call.hangup(
        final_instructions=(
            f"Let {policyholder} know their claim has been filed successfully. Their claim number "
            f"is {claim_number}.{sms_note} A representative will follow up -- do not give a "
            f"specific timeline or promise a callback window. Thank them for calling, and politely "
            f"say goodbye."
        )
    )


@_agent.on_task_complete("fnol_tow_offer")
def _on_tow_offer_complete(call: guava.Call) -> None:
    if needs_handoff(call):
        set_outcome(call, "transferred", transfer_reason="validation_exhausted")
        transfer_to_human(call)
        return

    claim_number = call.get_variable("fnol_claim_number")
    destination = call.get_field("fnol_tow_destination")
    policyholder = call.get_variable("fnol_policyholder_name")

    if destination == "decline":
        _finish_claim(call, claim_number)
        return

    result = client.request_tow(
        claim_number=claim_number,
        pickup_address=call.get_field("fnol_vehicle_location"),
        destination_type=destination,
        destination_address=call.get_field("fnol_tow_destination_address"),
    )
    match result:
        case Ok(value):
            set_outcome(call, "claim_created")
            sms_note = _sms_confirmation_note(call, claim_number)
            call.hangup(
                final_instructions=(
                    f"Let {policyholder} know their claim number is {claim_number}, and that you've "
                    f"requested a tow -- {value.provider_name} is expected in approximately "
                    f"{value.eta_minutes} minutes. Make clear the ETA is approximate.{sms_note} A "
                    f"representative will follow up on the claim. Thank them and politely say "
                    f"goodbye."
                )
            )
        case _:
            set_outcome(call, "claim_created", transfer_reason="tow_request_failed")
            sms_note = _sms_confirmation_note(call, claim_number)
            call.hangup(
                final_instructions=(
                    f"Let {policyholder} know their claim number is {claim_number}. Let them know the "
                    f"tow could not be requested right now due to a system issue, and that a "
                    f"representative will follow up and can help arrange one. Do NOT say a tow has "
                    f"been requested.{sms_note} Thank them and politely say goodbye."
                )
            )


def handle_question(call: guava.Call, question: str) -> str:
    if _is_coverage_fault_or_legal(question):
        # H9: coverage/fault/legal questions are deflected to a human;
        # the agent does intake only, never a determination.
        return copy.COVERAGE_DEFLECTION
    # Anything else (process/meta questions like "what else do you
    # need?"): acknowledge honestly instead of giving the coverage
    # non-sequitur, and get back to the intake task.
    return copy.INTAKE_QUESTION_ACKNOWLEDGMENT


def classify_intent(intent_summary: str):
    return _intent.classify(intent_summary)


def build_session_summary(call: guava.Call, event) -> dict:
    return {
        "policyholder_name": call.get_variable("fnol_policyholder_name"),
        "policy_number": call.get_variable("fnol_policy_number"),
        "claim_number": call.get_variable("fnol_claim_number"),
        "identity_attempts": call.get_variable("fnol_identity_attempts"),
    }


register_flow(
    "fnol",
    FlowHandlers(
        start=start,
        handle_question=handle_question,
        classify_intent=classify_intent,
        build_session_summary=build_session_summary,
    ),
)
