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

import guava

from . import config, copy, faq
from .agent import FlowHandlers, needs_handoff, register_flow, transfer_to_human
from .agent import agent as _agent

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
                "I can help with general questions about towing and roadside assistance. If "
                "you've been in an accident, let me know and I'll get that started as a claim "
                "instead."
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
        transfer_to_human(call)
        return

    if call.get_field("tow_needs_dispatch") == "yes":
        # H11: real dispatch is out of scope here -- hand off to the
        # roadside line.
        call.transfer(
            destination=config.ROADSIDE_LINE_NUMBER,
            instructions=(
                "Let the caller know you're connecting them with roadside dispatch now to get a "
                "tow sent out."
            ),
        )
    else:
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


register_flow(
    "tow",
    FlowHandlers(
        start=start,
        handle_question=handle_question,
        build_session_summary=build_session_summary,
    ),
)
