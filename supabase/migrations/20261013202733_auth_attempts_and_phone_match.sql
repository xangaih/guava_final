-- Stronger identity (post-interview).
--
-- auth_attempts: every identity check (policy verify, claim lookup) is
-- recorded so the backend can lock identity after too many failures across
-- calls: 5 failures within 30 minutes for the same policy/claim or from the
-- same phone, locked for 30 minutes (mock_backend/db.py). The caller's
-- number is stored only as a hash. A locked caller is transferred to a
-- person, not blocked.
--
-- call_sessions.phone_match: did the caller's number match the phone on
-- the policy. A signal for the rep and the audit row, not a gate. Nullable
-- (hidden number, web call), so every existing row stays valid.

create table if not exists auth_attempts (
    id uuid primary key default gen_random_uuid(),
    identifier text not null,
    caller_phone_hash text,
    success boolean not null,
    created_at timestamptz not null default now()
);
create index if not exists auth_attempts_identifier_idx on auth_attempts (identifier, created_at desc);
create index if not exists auth_attempts_phone_idx on auth_attempts (caller_phone_hash, created_at desc);
alter table auth_attempts enable row level security;

alter table call_sessions add column if not exists phone_match boolean;
