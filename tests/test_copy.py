"""Offline tests for copy.py's status-wording functions.

Regression coverage for a live bug (2026-10-05): a hand-edited claim
had adjuster_name set but adjuster_phone still null, which the old
code would have turned into "reachable at None."
"""

from mercury_claims import copy

_STATUS_FUNCTIONS = [
    copy.status_received,
    copy.status_under_review,
    copy.status_awaiting_documents,
    copy.status_approved,
    copy.status_closed,
]


def test_name_without_phone_never_mentions_a_phone_number():
    for fn in _STATUS_FUNCTIONS:
        text = fn("Ken Mike", None)
        assert "Ken Mike" in text
        assert "None" not in text
        assert "reachable at" not in text


def test_name_and_phone_both_present_mentions_both():
    for fn in _STATUS_FUNCTIONS:
        text = fn("Ken Mike", "+15550111")
        assert "Ken Mike" in text
        assert "+15550111" in text


def test_neither_present_says_no_representative_or_generic_followup():
    for fn in _STATUS_FUNCTIONS:
        text = fn(None, None)
        assert "None" not in text
        assert "Ken Mike" not in text


def test_representative_clause_helper_directly():
    assert copy._representative_clause("Ken Mike", "+15550111") == "Ken Mike, reachable at +15550111"
    assert copy._representative_clause("Ken Mike", None) == "Ken Mike"
    assert copy._representative_clause(None, None) is None
    # A phone with no name shouldn't happen in practice (the backend
    # never sets one without the other), but the helper must still not
    # invent a name.
    assert copy._representative_clause(None, "+15550111") is None
