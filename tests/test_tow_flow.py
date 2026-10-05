"""Offline tests for tow_flow.py, via guava.testing.MockCall.

faq.ask is monkeypatched so these never touch the network or construct a
real DocumentQA (which would otherwise upload documents to the Guava
server on first use).
"""

from guava.commands import SendInstructionCommand, TransferCommand
from guava.testing import MockCall
from guava.types.call_info import PSTNCallInfo

from mercury_claims import tow_flow


def _call() -> MockCall:
    return MockCall(call_info=PSTNCallInfo(from_number="+15555555555", to_number="+15555555555"))


def test_needs_dispatch_transfers_to_roadside_line(monkeypatch):
    call = _call()
    call.set_field("tow_needs_dispatch", "yes")
    tow_flow._on_tow_intent_complete(call)
    transfers = [c for c in call._command_queue if isinstance(c, TransferCommand)]
    assert len(transfers) == 1
    assert transfers[0].to_number == tow_flow.config.ROADSIDE_LINE_NUMBER


def test_no_dispatch_needed_just_hangs_up():
    call = _call()
    call.set_field("tow_needs_dispatch", "no")
    tow_flow._on_tow_intent_complete(call)
    transfers = [c for c in call._command_queue if isinstance(c, TransferCommand)]
    assert not transfers
    assert any(isinstance(c, SendInstructionCommand) for c in call._command_queue)


def test_general_question_answered_from_faq(monkeypatch):
    call = _call()
    monkeypatch.setattr(tow_flow.faq, "ask", lambda q: "Roadside Assistance covers lockouts, jump starts, flat tires, and fuel delivery.")
    answer = tow_flow.handle_question(call, "What does roadside assistance cover?")
    assert "lockouts" in answer


def test_caller_specific_question_is_deflected_not_answered(monkeypatch):
    call = _call()
    called = {"faq_asked": False}
    monkeypatch.setattr(tow_flow.faq, "ask", lambda q: called.update(faq_asked=True))
    answer = tow_flow.handle_question(call, "Is this covered under my policy?")
    assert answer == tow_flow.copy.COVERAGE_DEFLECTION
    assert called["faq_asked"] is False  # never even asks the FAQ


def test_faq_unavailable_deflects_instead_of_inventing_an_answer(monkeypatch):
    call = _call()
    monkeypatch.setattr(tow_flow.faq, "ask", lambda q: None)
    answer = tow_flow.handle_question(call, "What towing distance is covered?")
    assert answer == tow_flow.copy.FAQ_DEFLECTION


def test_h7_validator_exhaustion_on_dispatch_field_transfers():
    call = _call()
    call.set_variable("handoff_reason", "validation_exhausted")
    tow_flow._on_tow_intent_complete(call)
    transfers = [c for c in call._command_queue if isinstance(c, TransferCommand)]
    assert len(transfers) == 1
    assert transfers[0].to_number == tow_flow.config.HUMAN_LINE_NUMBER
