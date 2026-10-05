"""Towing and roadside assistance questions (C11). New flow -- no
original to descend from.

Two different things, kept separate per Mercury's own public pages
(section 8.4): after an accident, the claims team arranges a tow (that's
fnol_flow's job, not this one); Roadside Assistance is optional coverage
for non-accident breakdowns. This flow answers general questions from a
short FAQ (faq.py, via DocumentQA) and, if the caller actually needs a
tow dispatched right now for a non-accident breakdown, transfers to the
roadside line (H11) -- real dispatch is out of scope.
"""

from guava.helpers.llm import IntentRecognizer

import guava

from . import config, copy, faq
from .agent import FlowHandlers, needs_handoff, register_flow, set_outcome, transfer_to_human
from .agent import agent as _agent

# Confirmed live (2026-10-05): this flow never registered a
# classify_intent, so "can I talk to a live representative?" had no
# path to the shared transfer_to_human action at all -- the model had
# no actual transfer capability available to it and falsely claimed no
# representatives were available. fnol_flow and status_flow both had
# this wired up from the start; this flow was simply missed.
# Confirmed live (2026-10-05, same call): a caller who said "I wanna
# file a claim" mid-call also had no path anywhere -- switch_to_fnol/
# switch_to_status (agent.py) are the fix.
#
# Confirmed live (2026-10-05, later test): switch_to_fnol initially
# misfired on an elaborate towing/plan-tier FAQ follow-up question --
# its own description restated "towing/roadside question" right next
# to a caller utterance that was itself heavily about towing, which may
# have invited keyword-overlap confusion. Reworded below to lead with a
# sharp, vivid positive trigger (an actual new incident) and phrase the
# exclusion abstractly ("more general information") instead of
# repeating topic keywords.
_intent = IntentRecognizer(
    {
        "transfer_to_human": (
            "The caller explicitly asks to speak to a human, a representative, or a supervisor. "
            "This does NOT include the caller asking general towing/roadside questions, or "
            "asking about towing distances, costs, or what's covered -- those are normal parts "
            "of this call, not a request for a human."
        ),
        "switch_to_fnol": (
            "The caller explicitly states they were just in an accident, their car was just hit, "
            "or they need to report a brand new incident right now. This does NOT include asking "
            "for more general information, details, or explanations about anything already being "
            "discussed -- that's a normal follow-up question, not a new incident."
        ),
        "switch_to_status": (
            "The caller explicitly states they want to check on an existing claim they already "
            "filed. This does NOT include asking for more general information, details, or "
            "explanations about anything already being discussed -- that's a normal follow-up "
            "question, not a request to check a claim."
        ),
    }
)

_PERSONAL_COVERAGE_KEYWORDS = (
    "my policy",
    "my coverage",
    "my plan",
    "am i covered",
    "do i have",
    "is this covered",
    "will i be charged",
    "how much will i",
    "my claim",
    "my deductible",
    "my rate",
    "my premium",
)


def _is_caller_specific(question: str) -> bool:
    # H9's deflection guard is deterministic (code), not another model
    # call stacked on top of DocumentQA -- see section 8.6's code-vs-model
    # table. A keyword check is a known-approximate heuristic: it can
    # miss other phrasings of the same ask, or over-trigger on an
    # unrelated question that happens to say "my car." Flagging this as
    # an honest limitation, not a guarantee.
    q = question.lower()
    return any(kw in q for kw in _PERSONAL_COVERAGE_KEYWORDS)


def start(call: guava.Call) -> None:
    call.set_task(
        "tow_intent",
        objective=(
            "The caller has questions about towing or roadside assistance, or may need a tow "
            "dispatched right now for a current, non-accident breakdown. Answer any general "
            "questions as they come up. Find out whether they need a tow or roadside dispatch "
            "sent right now."
        ),
        checklist=[
            guava.Say(
                "Sure -- what's your question about towing or roadside assistance? If you've "
                "been in an accident, let me know and I'll get that started as a claim instead."
            ),
            guava.Field(
                key="tow_needs_dispatch",
                description=(
                    "Whether the caller needs a tow or roadside dispatch sent right now for a "
                    "current, non-accident breakdown (not an accident -- that's a new claim)"
                ),
                field_type="multiple_choice",
                choices=["yes", "no"],
                required=True,
            ),
        ],
    )


@_agent.on_task_complete("tow_intent")
def _on_tow_intent_complete(call: guava.Call) -> None:
    if needs_handoff(call):
        set_outcome(call, "transferred", transfer_reason="validation_exhausted")
        transfer_to_human(call)
        return

    if call.get_field("tow_needs_dispatch") == "yes":
        # H11: real dispatch is out of scope here -- hand off to the
        # roadside line.
        set_outcome(call, "transferred", transfer_reason="roadside_dispatch_needed")
        call.transfer(
            destination=config.ROADSIDE_LINE_NUMBER,
            instructions=(
                "Let the caller know you're connecting them with roadside dispatch now to get a "
                "tow sent out."
            ),
        )
    else:
        # No enum value fits "FAQ answered, call ends normally" better
        # than "completed" (0002 migration) -- it's genuinely not a
        # claim, a status delivery, or a transfer.
        set_outcome(call, "completed")
        call.hangup(final_instructions="Thank the caller for calling, and politely say goodbye.")


def handle_question(call: guava.Call, question: str) -> str:
    if _is_caller_specific(question):
        return copy.COVERAGE_DEFLECTION

    answer = faq.ask(question)
    if answer is None:
        return copy.FAQ_DEFLECTION
    return answer


def build_session_summary(call: guava.Call, event) -> dict:
    return {"needed_dispatch": call.get_field("tow_needs_dispatch")}


def classify_intent(intent_summary: str):
    return _intent.classify(intent_summary)


register_flow(
    "tow",
    FlowHandlers(
        start=start,
        handle_question=handle_question,
        classify_intent=classify_intent,
        build_session_summary=build_session_summary,
    ),
)
