-- Mercury Claims demo schema.
-- Supabase stands in for Mercury's claims system of record; the agent never
-- talks to it directly, only the FastAPI backend (mock_backend/) does, using
-- the service-role secret key. RLS is enabled with no policies on every
-- table, so nothing is reachable except through that server-side key.

create extension if not exists pgcrypto;

create table if not exists policies (
    policy_number text primary key,
    holder_name text not null,
    dob date not null,
    zip text not null,
    status text not null check (status in ('active', 'lapsed', 'cancelled')),
    effective_date date not null,
    expiration_date date not null,
    phone text not null,
    created_at timestamptz not null default now()
);

-- Seeded claims use explicit low numbers (CLM-0000001 ..); this sequence
-- starts well above them so newly created claims never collide.
create sequence if not exists claim_number_seq start 1000;

create table if not exists claims (
    id uuid primary key default gen_random_uuid(),
    claim_number text unique not null
        default ('CLM-' || lpad(nextval('claim_number_seq')::text, 7, '0')),
    policy_number text not null references policies (policy_number),
    idempotency_key text unique not null,
    status text not null default 'received'
        check (status in ('received', 'under_review', 'awaiting_documents',
                           'approved', 'denied', 'closed', 'needs_human')),
    intake_complete boolean not null default false,
    loss_type text,
    loss_at timestamptz,
    loss_location text,
    description text,
    injuries boolean,
    drivable boolean,
    police_report_filed text,
    police_report_number text,
    police_department text,
    details jsonb not null default '{}'::jsonb,
    coverage_review_flag boolean not null default false,
    coverage_review_reason text,
    adjuster_name text,
    adjuster_phone text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists claims_policy_number_idx on claims (policy_number);

create or replace function set_updated_at()
returns trigger language plpgsql
set search_path = ''
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists claims_set_updated_at on claims;
create trigger claims_set_updated_at
    before update on claims
    for each row execute function set_updated_at();

create table if not exists tow_providers (
    id uuid primary key default gen_random_uuid(),
    name text not null,
    zip_prefixes text[] not null,
    phone text not null,
    avg_eta_minutes integer not null,
    active boolean not null default true
);

create table if not exists tow_requests (
    id uuid primary key default gen_random_uuid(),
    claim_id uuid not null references claims (id),
    provider_id uuid references tow_providers (id),
    pickup_address text not null,
    destination_type text not null
        check (destination_type in ('preferred_shop', 'home', 'other')),
    destination_address text,
    status text not null default 'requested',
    eta_minutes integer,
    created_at timestamptz not null default now()
);

create index if not exists tow_requests_claim_id_idx on tow_requests (claim_id);

-- Audit trail of calls. Deliberately no PII: no caller phone number, no DOB.
create table if not exists call_sessions (
    id uuid primary key default gen_random_uuid(),
    call_id text not null,
    purpose text,
    outcome text
        check (outcome in ('claim_created', 'status_delivered', 'transferred',
                            'auth_failed', 'abandoned', 'error')),
    transfer_reason text,
    claim_number text,
    api_errors jsonb not null default '[]'::jsonb,
    created_at timestamptz not null default now()
);

alter table policies enable row level security;
alter table claims enable row level security;
alter table tow_providers enable row level security;
alter table tow_requests enable row level security;
alter table call_sessions enable row level security;
