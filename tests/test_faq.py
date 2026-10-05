"""Offline tests for faq.py's fallback behavior. DocumentQA itself is
monkeypatched out entirely -- constructing a real one uploads documents
to the Guava server, which these tests must never do.
"""

from mercury_claims import faq


class _FakeQA:
    def __init__(self, answer=None, raise_on_ask=False):
        self._answer = answer
        self._raise_on_ask = raise_on_ask

    def ask(self, question):
        if self._raise_on_ask:
            raise RuntimeError("boom")
        return self._answer


def _reset():
    faq._qa = None
    faq._init_failed = False


def test_ask_returns_the_answer_on_success(monkeypatch):
    _reset()
    monkeypatch.setattr(faq, "DocumentQA", lambda **k: _FakeQA(answer="towing tiers are 15/100/200 miles"))
    assert faq.ask("What towing distances are covered?") == "towing tiers are 15/100/200 miles"


def test_ask_returns_none_when_init_fails(monkeypatch):
    _reset()

    def _boom(**k):
        raise RuntimeError("no network")

    monkeypatch.setattr(faq, "DocumentQA", _boom)
    assert faq.ask("anything") is None
    # Doesn't retry construction on every call once it's failed once.
    assert faq._init_failed is True


def test_ask_returns_none_when_ask_itself_raises(monkeypatch):
    _reset()
    monkeypatch.setattr(faq, "DocumentQA", lambda **k: _FakeQA(raise_on_ask=True))
    assert faq.ask("anything") is None
