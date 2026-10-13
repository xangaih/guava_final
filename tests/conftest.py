"""Shared test setup.

Rep-dashboard events are sent on a background thread to the real backend
URL. In tests that would mean a thread per handler call hitting a closed
port (or, worse, writing to the real table if a backend happens to be
running). Stub it everywhere; tests that care read the recorded list.
"""

import pytest

from mercury_claims import agent as agent_module


@pytest.fixture(autouse=True)
def recorded_events(monkeypatch):
    events: list[tuple[str, str, dict]] = []

    def fake_record_call_event(call_id, event_type, details=None):
        events.append((call_id, event_type, details or {}))

    monkeypatch.setattr(agent_module.client, "record_call_event", fake_record_call_event)
    return events
