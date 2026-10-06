"""Offline tests for tow_flow.py, via guava.testing.MockCall.

faq.ask is monkeypatched so these never touch the network or construct a
real DocumentQA (which would otherwise upload documents to the Guava
server on first use).
"""

from guava.commands import SetTaskCommand, TransferCommand
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
    assert call.get_variable("call_outcome") == "transferred"


def test_no_dispatch_needed_offers_anything_else_before_ending():
    call = _call()
    call.set_field("tow_needs_dispatch", "no")
    tow_flow._on_tow_intent_complete(call)
    transfers = [c for c in call._command_queue if isinstance(c, TransferCommand)]
    assert not transfers
    tasks = [c for c in call._command_queue if isinstance(c, SetTaskCommand)]
    assert tasks[-1].task_id == "wrap_up"
    assert "anything else" in tasks[-1].objective.lower()
    assert call.get_variable("call_outcome") == "completed"


def test_general_pricing_question_reaches_the_faq_not_the_deflection(monkeypatch):
    # A general "what's the price" question is exactly what the FAQ's
    # tier/dollar content exists to answer -- it must NOT be caught by the
    # caller-specific guard (which would short-circuit the FAQ entirely).
    call = _call()
    monkeypatch.setattr(tow_flow.faq, "ask", lambda q: "Three tiers: up to $75, $500, or $1,000.")
    answer = tow_flow.handle_question(call, "What's the price for a 100 mile tow?")
    assert "$500" in answer


def test_personal_cost_question_is_still_deflected(monkeypatch):
    call = _call()
    monkeypatch.setattr(tow_flow.faq, "ask", lambda q: "should not be reached")
    for question in ["Do I have to pay for the tow?", "Will I be charged for this?", "How much will I owe?"]:
        assert tow_flow.handle_question(call, question) == tow_flow.copy.COVERAGE_DEFLECTION


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
