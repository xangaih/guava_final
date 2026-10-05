-- Demo data only. Fake names, fake 555-01xx numbers, fictional policy and
-- claim numbers. Nothing here corresponds to a real Mercury customer or
-- claim. Keep this file in sync with DEMO_CHEATSHEET.md.

insert into policies (policy_number, holder_name, dob, zip, status, effective_date, expiration_date, phone)
values
    ('MCY-100245', 'Jordan Alvarez',   date '1988-04-12', '90001', 'active',    date '2026-01-01', date '2027-01-01', '+15550101'),
    ('MCY-100389', 'Priya Natarajan',  date '1979-11-02', '94103', 'active',    date '2026-02-01', date '2027-02-01', '+15550102'),
    ('MCY-100512', 'Evan Brooks',      date '1990-06-30', '95814', 'lapsed',    date '2025-06-01', date '2026-08-01', '+15550103'),
    ('MCY-100733', 'Dana Whitfield',   date '1983-02-19', '92101', 'cancelled', date '2024-03-01', date '2025-03-01', '+15550104'),
    ('MCY-100921', 'Sam Okafor',       date '1995-09-08', '93710', 'active',    date '2026-05-01', date '2027-05-01', '+15550105')
on conflict (policy_number) do nothing;

-- Explicit low claim numbers so they never collide with claim_number_seq
-- (which starts at 1000). One claim per status, for S9/S11/S12.
insert into claims (
    claim_number, policy_number, idempotency_key, status, intake_complete,
    loss_type, loss_at, loss_location, description, injuries, drivable,
    police_report_filed, police_report_number, police_department, details,
    coverage_review_flag, coverage_review_reason, adjuster_name, adjuster_phone
) values
    ('CLM-0000001', 'MCY-100245', 'seed-0000001', 'received', true,
     'auto_collision', now() - interval '1 day', '5th Ave & Main St, Los Angeles, CA',
     'Rear-ended at a stoplight.', false, true,
     'yes', 'LAPD-88123', 'Los Angeles PD', '{}'::jsonb,
     false, null, null, null),

    ('CLM-0000002', 'MCY-100389', 'seed-0000002', 'under_review', true,
     'auto_collision', now() - interval '6 days', 'Market St & 4th St, San Francisco, CA',
     'Sideswiped while changing lanes.', false, true,
     'no', null, null, '{}'::jsonb,
     false, null, 'Morgan Price', '+15550111'),

    ('CLM-0000003', 'MCY-100512', 'seed-0000003', 'awaiting_documents', true,
     'auto_collision', now() - interval '10 days', 'Capitol Mall, Sacramento, CA',
     'Single-vehicle incident, hit a guardrail.', false, true,
     'yes', 'SACPD-55210', 'Sacramento PD', '{}'::jsonb,
     true, 'Loss date is after the policy expiration on file.', 'Riley Chen', '+15550112'),

    ('CLM-0000004', 'MCY-100245', 'seed-0000004', 'approved', true,
     'auto_theft', now() - interval '20 days', 'Residential driveway, Los Angeles, CA',
     'Vehicle reported stolen overnight.', false, null,
     'yes', 'LAPD-88099', 'Los Angeles PD', '{}'::jsonb,
     false, null, 'Taylor Brooks', '+15550113'),

    ('CLM-0000005', 'MCY-100921', 'seed-0000005', 'denied', true,
     'auto_collision', now() - interval '45 days', 'Highway 99, Fresno, CA',
     'Collision with another vehicle.', false, true,
     'not sure', null, null, '{}'::jsonb,
     false, null, 'Appeals Department', '+15550114'),

    ('CLM-0000006', 'MCY-100389', 'seed-0000006', 'closed', true,
     'auto_collision', now() - interval '90 days', 'Van Ness Ave, San Francisco, CA',
     'Minor fender bender, resolved.', false, true,
     'no', null, null, '{}'::jsonb,
     false, null, 'Morgan Price', '+15550111'),

    ('CLM-0000007', 'MCY-100733', 'seed-0000007', 'needs_human', false,
     'auto_collision', now() - interval '2 days', 'Interstate 8, San Diego, CA',
     'Multi-car collision, caller reported injuries.', true, null,
     'yes', 'SDPD-77301', 'San Diego PD', '{}'::jsonb,
     false, null, null, null)
on conflict (claim_number) do nothing;

insert into tow_providers (name, zip_prefixes, phone, avg_eta_minutes, active)
values
    ('Golden State Towing & Recovery', array['90', '91'], '+15550150', 25, true),
    ('Bay Area Rapid Tow',             array['94', '95'], '+15550151', 30, true),
    ('SoCal Express Towing',           array['92', '93'], '+15550152', 35, true)
on conflict do nothing;
