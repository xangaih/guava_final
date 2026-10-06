from datetime import date, datetime

from guava.commands import ReadScriptCommand, SendInstructionCommand, SetTaskCommand, TransferCommand
from guava.testing import MockCall
from guava.types.call_info import PSTNCallInfo
from guava.types.incoming_call_action import AcceptCall

from mercury_claims import agent as agent_module


def _call() -> MockCall:
    return MockCall(call_info=PSTNCallInfo(from_number="+15555555555", to_number="+15555555555"))


def test_on_call_received_always_accepts():
    # C7: no business-hours decline, regardless of time of day.
    info = PSTNCallInfo(from_number="+15555555555", to_number="+15555555555")
    result = agent_module.on_call_received(info)
    assert isinstance(result, AcceptCall)


def test_on_call_start_reads_disclosure_and_sets_route_task():
    call = _call()
    agent_module.on_call_start(call)
    commands = call._command_queue
    assert any(isinstance(c, ReadScriptCommand) for c in commands)
    task_commands = [c for c in commands if isinstance(c, SetTaskCommand)]
    assert len(task_commands) == 1
    assert task_commands[0].task_id == agent_module.ROUTE_TASK


def test_route_to_unbuilt_flow_transfers_to_human(monkeypatch):
    # Flow modules register themselves process-wide at import time, so
    # whether "fnol"/"tow" are present here depends on which other test
    # files pytest already imported. Force the "not built yet" case
    # directly instead of relying on import order.
    monkeypatch.delitem(agent_module._flows, "fnol", raising=False)
    call = _call()
    call.set_field("call_purpose", "report_new_claim")
    agent_module.on_route_complete(call)
    transfers = [c for c in call._command_queue if isinstance(c, TransferCommand)]
    assert len(transfers) == 1
    assert transfers[0].to_number == agent_module.config.HUMAN_LINE_NUMBER


def test_route_something_else_transfers_to_human():
    call = _call()
    call.set_field("call_purpose", "something_else")
    agent_module.on_route_complete(call)
    transfers = [c for c in call._command_queue if isinstance(c, TransferCommand)]
    assert len(transfers) == 1
    assert call.get_variable("call_transfer_reason") == "something_else"


def test_route_talk_to_representative_transfers_with_its_own_reason():
    # The audit row must be able to tell "asked for a person" apart from
    # "something else" -- the old code hardcoded the reason.
    call = _call()
    call.set_field("call_purpose", "talk_to_representative")
    agent_module.on_route_complete(call)
    transfers = [c for c in call._command_queue if isinstance(c, TransferCommand)]
    assert len(transfers) == 1
    assert transfers[0].to_number == agent_module.config.HUMAN_LINE_NUMBER
    assert call.get_variable("call_transfer_reason") == "talk_to_representative"


def test_route_different_insurance_type_transfers_with_mercury_specific_wording():
    call = _call()
    call.set_field("call_purpose", "different_insurance_type")
    agent_module.on_route_complete(call)
    transfers = [c for c in call._command_queue if isinstance(c, TransferCommand)]
    assert len(transfers) == 1
    message = transfers[0].transfer_message.lower()
    assert "auto claims" in message
    assert "home, property, or mechanical protection" in message
    assert call.get_variable("call_transfer_reason") == "different_insurance_type"


def test_route_field_offers_every_purpose_including_the_new_ones():
    choices = agent_module._ROUTE_PURPOSE_FIELD.choices
    for expected in ("report_new_claim", "check_claim_status", "towing_or_roadside_question",
                     "talk_to_representative", "different_insurance_type", "something_else"):
        assert expected in choices


def _tasks(call: MockCall) -> list[SetTaskCommand]:
    return [c for c in call._command_queue if isinstance(c, SetTaskCommand)]


def test_end_call_with_wrapup_puts_the_closing_in_a_task_and_asks_anything_else():
    call = _call()
    agent_module.end_call_with_wrapup(call, "Let the caller know the thing is done.")
    task = _tasks(call)[-1]
    assert task.task_id == agent_module.WRAP_UP_TASK
    assert task.objective.startswith("Let the caller know the thing is done.")
    assert "anything else" in task.objective.lower()
    keys = [item.key for item in task.action_items if hasattr(item, "key")]
    assert keys == ["wrap_up_wants_more"]
    # Not a hangup: nothing has told the model to end the call yet.
    assert not any(isinstance(c, SendInstructionCommand) for c in call._command_queue)


def test_wrap_up_no_hangs_up_with_a_goodbye():
    call = _call()
    call.set_field("wrap_up_wants_more", "no")
    agent_module._on_wrap_up_complete(call)
    instructions = [c.instruction for c in call._command_queue if isinstance(c, SendInstructionCommand)]
    assert len(instructions) == 1
    assert "goodbye" in instructions[0].lower()
    assert "hang up" in instructions[0].lower()


def test_wrap_up_yes_re_enters_the_route_task_with_every_purpose():
    # "Yes" reuses on_route_complete's existing, tested dispatch rather
    # than any new routing logic.
    call = _call()
    call.set_field("wrap_up_wants_more", "yes")
    agent_module._on_wrap_up_complete(call)
    task = _tasks(call)[-1]
    assert task.task_id == agent_module.ROUTE_TASK
    # A "what else?" Say (auto-keyed by the SDK) followed by the one
    # shared purpose field -- the same field on_call_start uses.
    assert len(task.action_items) == 2
    assert task.action_items[-1].key == "call_purpose"
    assert task.action_items[-1].choices == agent_module._ROUTE_PURPOSE_FIELD.choices
    assert not any(isinstance(c, SendInstructionCommand) for c in call._command_queue)


def test_wrap_up_yes_then_status_dispatches_through_the_normal_route(monkeypatch):
    started = {}
    monkeypatch.setitem(agent_module._flows, "status", agent_module.FlowHandlers(start=lambda c: started.update(called=True)))
    call = _call()
    call.set_variable("flow", "fnol")
    call.set_variable("handoff_reason", "validation_exhausted")  # stale, from the first flow
    call.set_field("wrap_up_wants_more", "yes")
    agent_module._on_wrap_up_complete(call)
    call.set_field("call_purpose", "check_claim_status")
    agent_module.on_route_complete(call)
    assert started.get("called") is True
    assert call.get_variable("flow") == "status"
    assert agent_module.needs_handoff(call) is False


def test_claim_history_question_detection():
    for q in ["How many claims do I have?", "Can you list all my claims?", "What's my claim history?"]:
        assert agent_module.is_claim_history_question(q)
    for q in ["What's happening with my claim?", "Why was my claim denied?", "Is this covered?"]:
        assert not agent_module.is_claim_history_question(q)


def test_transfer_for_claim_history_action_says_why_and_records_why():
    # Confirmed live (2026-10-06): "how many claims do I have?" got a
    # generic transfer after the model said "let me check your claim
    # history" -- an action it can't take.
    call = _call()
    agent_module.on_transfer_for_claim_history(call)
    transfers = [c for c in call._command_queue if isinstance(c, TransferCommand)]
    assert len(transfers) == 1
    assert transfers[0].to_number == agent_module.config.HUMAN_LINE_NUMBER
    assert "one claim at a time" in transfers[0].transfer_message.lower()
    assert call.get_variable("call_transfer_reason") == "claim_history_request"


def test_every_desk_offers_the_moves_it_needs():
    # Confirmed live (2026-10-06): the status desk had no "check ANOTHER
    # claim" move (switch_to_status was missing from its own card), so
    # the recognizer fell back to transfer_to_human.
    from mercury_claims import fnol_flow, status_flow, tow_flow

    for module in (fnol_flow, status_flow, tow_flow):
        assert "transfer_to_human" in module._INTENTS
    assert "switch_to_status" in status_flow._INTENTS
    assert "switch_to_fnol" in fnol_flow._INTENTS
    for module in (fnol_flow, status_flow):
        assert "transfer_for_claim_history" in module._INTENTS
    # Every move a desk offers must have a registered action behind it.
    for module in (fnol_flow, status_flow, tow_flow):
        for key in module._INTENTS:
            assert key in agent_module.agent._on_action_handlers, f"{key} has no on_action handler"


def test_session_end_keeps_a_claim_number_created_earlier_in_the_call(monkeypatch):
    # Claim filed in fnol, then "anything else" -> status. The status
    # flow's summary has no fnol claim number, but the audit row must.
    recorded = {}
    monkeypatch.setattr(agent_module.client, "record_call_session", lambda **k: recorded.update(k))
    agent_module.register_flow(
        "_test_status_like",
        agent_module.FlowHandlers(start=lambda c: None, build_session_summary=lambda c, e: {"claim_number": None}),
    )
    try:
        call = _call()
        call.set_variable("flow", "_test_status_like")
        call.set_variable("call_claim_number", "CLM-0001000")
        call.set_variable("call_outcome", "status_delivered")
        event = type("Event", (), {"termination_reason": "user-hangup", "dnc": False})()
        agent_module.on_session_end(call, event)
        assert recorded["claim_number"] == "CLM-0001000"
    finally:
        agent_module._flows.pop("_test_status_like", None)


def test_route_dispatches_to_registered_flow():
    # Save/restore rather than unconditionally popping "fnol": pytest
    # collects every test file before running any test, so the real
    # fnol_flow module (and its real registration) may already be
    # present here regardless of file run order. An earlier version of
    # this test popped "fnol" unconditionally in its cleanup, which
    # silently deleted the real registration for every test that ran
    # after it in the same session -- exactly the kind of bug that let
    # tow_flow's missing classify_intent go unnoticed.
    started = {}

    def fake_start(call):
        started["called"] = True

    previous = agent_module._flows.get("fnol")
    agent_module.register_flow("fnol", agent_module.FlowHandlers(start=fake_start))
    try:
        call = _call()
        call.set_field("call_purpose", "report_new_claim")
        agent_module.on_route_complete(call)
        assert started.get("called") is True
        assert call.get_variable("flow") == "fnol"
    finally:
        if previous is not None:
            agent_module._flows["fnol"] = previous
        else:
            agent_module._flows.pop("fnol", None)


def test_on_question_falls_back_when_no_flow_registered():
    call = _call()
    result = agent_module.on_question(call, "Is this covered?")
    assert isinstance(result, str) and len(result) > 0


def test_on_question_dispatches_to_registered_flow():
    # Uses a flow name that can never collide with a real one (unlike
    # the old version of this test, which used "fnol" and then popped
    # it unconditionally in cleanup -- silently deleting the real
    # registration for every test that ran after it).
    agent_module.register_flow(
        "_test_fake_flow",
        agent_module.FlowHandlers(start=lambda call: None, handle_question=lambda call, q: f"echo: {q}"),
    )
    try:
        call = _call()
        call.set_variable("flow", "_test_fake_flow")
        assert agent_module.on_question(call, "huh?") == "echo: huh?"
    finally:
        agent_module._flows.pop("_test_fake_flow", None)


def test_on_action_request_falls_back_to_none_when_no_flow_registered():
    call = _call()
    assert agent_module.on_action_request(call, "caller wants a human") is None


def test_transfer_to_human_action():
    call = _call()
    agent_module.on_transfer_to_human(call)
    transfers = [c for c in call._command_queue if isinstance(c, TransferCommand)]
    assert len(transfers) == 1
    assert transfers[0].to_number == agent_module.config.HUMAN_LINE_NUMBER


def test_as_date_handles_the_real_captured_payload_shape():
    # Captured live, 2026-10-05: a "date" field's payload is this dict,
    # not an ISO string and not a datetime.date. This crashed the
    # original as_date() uncaught on a real call.
    payload = {"day": 12, "month": 4, "year": 1988}
    assert agent_module.as_date(payload) == date(1988, 4, 12)


def test_as_date_still_handles_iso_string_and_date_object():
    assert agent_module.as_date("1988-04-12") == date(1988, 4, 12)
    assert agent_module.as_date(date(1988, 4, 12)) == date(1988, 4, 12)
    assert agent_module.as_date(datetime(1988, 4, 12, 9, 30)) == date(1988, 4, 12)


def test_as_datetime_handles_a_dict_payload_with_time_components():
    payload = {"day": 1, "month": 10, "year": 2026, "hour": 12, "minute": 30, "second": 0}
    assert agent_module.as_datetime(payload) == datetime(2026, 10, 1, 12, 30, 0)


def test_as_datetime_dict_payload_defaults_missing_time_components_to_zero():
    payload = {"day": 1, "month": 10, "year": 2026}
    assert agent_module.as_datetime(payload) == datetime(2026, 10, 1, 0, 0, 0)


def test_as_datetime_still_handles_iso_string_and_datetime_object():
    assert agent_module.as_datetime("2026-10-01T12:30:00") == datetime(2026, 10, 1, 12, 30, 0)
    assert agent_module.as_datetime(datetime(2026, 10, 1, 12, 30)) == datetime(2026, 10, 1, 12, 30)


def test_every_flow_registers_a_classify_intent():
    # Regression test for a live bug (2026-10-05): tow_flow never
    # registered classify_intent, so "can I talk to a live
    # representative?" had no path to transfer_to_human at all -- the
    # model had no real transfer capability and falsely claimed none
    # were available. Importing all three here (rather than relying on
    # whichever test file pytest happens to import first) guarantees
    # every registered flow is checked, regardless of collection order.
    from mercury_claims import fnol_flow, status_flow, tow_flow  # noqa: F401

    for name in ("fnol", "status", "tow"):
        handlers = agent_module._flows[name]
        assert handlers.classify_intent is not None, f"{name} has no classify_intent registered"


def test_switch_flow_sets_the_flow_variable_and_calls_start(monkeypatch):
    started = {}
    fake_handlers = agent_module.FlowHandlers(start=lambda call: started.update(called=True))
    monkeypatch.setitem(agent_module._flows, "fnol", fake_handlers)

    call = _call()
    agent_module.switch_flow(call, "fnol")

    assert call.get_variable("flow") == "fnol"
    assert started.get("called") is True


def test_switch_flow_clears_a_stale_handoff_reason():
    # Regression test for a live bug (2026-10-05): without this, a
    # caller who got 3 strikes on a validator in the OLD flow would
    # immediately get transferred again in the NEW flow, for a reason
    # that happened before they even switched.
    call = _call()
    call.set_variable("handoff_reason", "validation_exhausted")
    agent_module.register_flow("_test_switch_target", agent_module.FlowHandlers(start=lambda call: None))
    try:
        agent_module.switch_flow(call, "_test_switch_target")
        assert agent_module.needs_handoff(call) is False
    finally:
        agent_module._flows.pop("_test_switch_target", None)


def test_switch_to_fnol_status_tow_actions_target_the_right_flow(monkeypatch):
    calls = []
    monkeypatch.setattr(agent_module, "switch_flow", lambda call, name: calls.append(name))
    call = _call()

    agent_module.on_switch_to_fnol(call)
    agent_module.on_switch_to_status(call)
    agent_module.on_switch_to_tow(call)

    assert calls == ["fnol", "status", "tow"]
