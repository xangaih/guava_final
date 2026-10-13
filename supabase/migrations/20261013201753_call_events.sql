-- Events behind the rep dashboard (mock_backend /rep): call started, desk,
-- identity verified, transfer (with hand-off context), call ended (with a
-- follow-up). `details` is free-form on purpose so new context needs no
-- migration. The agent never writes a date of birth or a full caller
-- phone number into it. RLS on with no policies, like every other table:
-- reachable only through the backend's secret key.

create table if not exists call_events (
    id uuid primary key default gen_random_uuid(),
    call_id text not null,
    event_type text not null
        check (event_type in ('call_started', 'desk', 'verified', 'transfer', 'call_ended')),
    details jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create index if not exists call_events_created_at_idx on call_events (created_at desc);
create index if not exists call_events_call_id_idx on call_events (call_id);

alter table call_events enable row level security;
