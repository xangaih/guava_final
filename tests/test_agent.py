from datetime import date, datetime

from guava.commands import ReadScriptCommand, SetTaskCommand, TransferCommand
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


def test_route_dispatches_to_registered_flow():
    started = {}

    def fake_start(call):
        started["called"] = True

    agent_module.register_flow("fnol", agent_module.FlowHandlers(start=fake_start))
    try:
        call = _call()
        call.set_field("call_purpose", "report_new_claim")
        agent_module.on_route_complete(call)
        assert started.get("called") is True
        assert call.get_variable("flow") == "fnol"
    finally:
        agent_module._flows.pop("fnol", None)


def test_on_question_falls_back_when_no_flow_registered():
    call = _call()
    result = agent_module.on_question(call, "Is this covered?")
    assert isinstance(result, str) and len(result) > 0


def test_on_question_dispatches_to_registered_flow():
    agent_module.register_flow(
        "fnol",
        agent_module.FlowHandlers(start=lambda call: None, handle_question=lambda call, q: f"echo: {q}"),
    )
    try:
        call = _call()
        call.set_variable("flow", "fnol")
        assert agent_module.on_question(call, "huh?") == "echo: huh?"
    finally:
        agent_module._flows.pop("fnol", None)


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
