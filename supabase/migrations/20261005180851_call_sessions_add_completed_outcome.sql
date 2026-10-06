-- call_sessions.outcome didn't have a value for "the call completed its
-- purpose normally, with no claim created, no status delivered, and no
-- transfer" (e.g. tow_flow answering general FAQ questions with no
-- dispatch needed). Adding "completed" rather than overloading an
-- existing value (e.g. "status_delivered" would be a mischaracterization
-- here) or leaving it null (which would look indistinguishable from a
-- forgotten write).

alter table call_sessions drop constraint if exists call_sessions_outcome_check;
alter table call_sessions add constraint call_sessions_outcome_check
    check (outcome in ('claim_created', 'status_delivered', 'transferred',
                        'auth_failed', 'abandoned', 'error', 'completed'));
