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
from typing import Callable, Optional

import guava
from guava.events import BotSessionEnded

from . import config, copy

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


def _transfer_to_human(call: guava.Call, instructions: str = copy.TRANSFER_GENERIC) -> None:
    call.transfer(destination=config.HUMAN_LINE_NUMBER, instructions=instructions)


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
        _transfer_to_human(call)
        return

    handlers = _flows.get(flow_name)
    if handlers is None:
        # Listed as a call type in scope (section 1) but not built yet
        # in this codebase.
        logger.warning("no flow registered for %r yet", flow_name)
        _transfer_to_human(call)
        return

    call.set_variable("flow", flow_name)
    handlers.start(call)


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
    _transfer_to_human(call)


@agent.on_session_end
def on_session_end(call: guava.Call, event: BotSessionEnded) -> None:
    handlers = _current_flow(call)
    summary = handlers.build_session_summary(call, event) if handlers and handlers.build_session_summary else {}
    logger.info(
        "call ended: flow=%s termination_reason=%s dnc=%s %s",
        call.get_variable("flow"),
        event.termination_reason,
        event.dnc,
        summary,
    )
