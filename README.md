# Mercury Insurance Auto Claims — Guava voice agent

**Track C: make an example more production-ready.** One inbound phone line for Mercury
Insurance's auto claims customers that handles three call types and routes between them:

- **Report a new auto claim** (first notice of loss). Descends from the starter's `examples/insurance/fnol`.
- **Check the status of an existing claim.** Descends from the starter's `examples/insurance/claims_status`.
- **Towing and roadside assistance questions.** New.

The agent calls a real HTTP API (a FastAPI service standing in for Mercury's claims system,
backed by Supabase Postgres) and uses what comes back in the conversation.

> **Demo only.** All data is fake. This is not affiliated with or endorsed by Mercury Insurance.
> No real Mercury phone numbers appear anywhere; transfers go to numbers set in `.env`.

---

## Why Mercury, and why insurance

I could have picked an unregulated company. I picked insurance on purpose. It's a regulated
industry, so the bar is higher, and the agent is allowed to do *less*: no coverage decisions, no
dollar amounts, no denial reasons, identity before anything. Building something useful under those
limits was the challenge I wanted. Mercury fits because its claims line is exactly the "several
call types one inbound line has to handle" case the brief describes, and its public site documents
the intake checklist, claims-rep lookup, repair-shop network and roadside plans I needed to tailor
the agent to.

Choosing a company headquartered in Los Angeles was also a deliberate decision, with an eye to this
project actually getting the green light to be worked on for real.

While choosing, I called Mercury's 24/7 claims line myself. Nobody picked up, and the line echoed
my own words back to me. It may have been a temporary issue, but it's exactly the moment this agent
is for: someone calling after an accident should always get somewhere.

## Why Track C (and not A or B)

All three tracks end in a working agent. They differ in where the work is:

- **Track A** (an agent from scratch) is conversation design and logic. I'd already built that kind
  of agent at a Guava hackathon, and I wanted to stretch further.
- **Track B** (software on top of Guava) is a meta layer: an internal tool so a customer doesn't
  need to come back every time they want to create or change an agent. It genuinely interested me.
  For Mercury I pictured a simple question-driven setup (which claim type, when to hand off to a
  person, what counts as "done") rather than a config file nobody there would want to write. But the
  more I designed it, the more generic it got. It would configure *an* insurer's agent, not tailor
  one to Mercury.
- **Track C** (make an example production-ready) means working inside an existing agent structure:
  removing the APIs and functions the workflow doesn't need, and adding the integrations,
  deterministic rules and call types it does. It gave me the most room to tailor to Mercury's actual
  claims service, so I chose it.

## What "production-ready" means here

**Production-ready, for this line, means a caller can complete what they called for, end to end,
and never gets stuck.** Either the agent finishes the job, or it hands them to a person with an
honest reason. I could have built an agent that touches many things shallowly. I chose three call
types and made each one actually finish.

Concretely, the rules this build holds to:

1. **Say-do.** The agent never says "filed", "saved" or "tow requested" unless the backend confirmed
   it. Those words only exist in the code path that runs after a successful response.
2. **Identity before anything.** No claim detail until identity checks out. A failed check never
   says *which* piece was wrong.
3. **Honest failure, then a human.** Every backend failure has a specific thing the caller hears
   and a hand-off. The agent never pretends.
4. **No loops.** Every retry is capped (3 for a bad answer, 2 for identity, 2 for the read-back),
   then a person.
5. **Stays in its lane.** No coverage, fault, dollar amounts for a claim, denial reasons or
   timelines. Those go to a licensed human.
6. **An audit trail without personal data.** Every call writes an outcome row; names, phone
   numbers and dates of birth are never in it.
7. **Moves with the caller.** A caller can switch call types mid-call ("actually, I want to check
   my claim") or keep going ("anything else?") without starting over. Once a caller has verified
   their identity, they aren't asked for it again later in the same call. I found this on a live
   call: after filing a claim, checking a status meant giving my date of birth all over again. That's
   a real downside for the caller, so a date of birth verified once now carries through the rest of
   the call (every lookup is still checked by the backend).

## Thought process

An FDE has two customers to think about: the company (Mercury) and the company's customers (the
policyholder on the phone). I designed for both.

**The first problem I thought about, and the main priority throughout:** a policyholder who calls
the claims line after an accident and gets nowhere. Every decision below comes back to that. The
caller must either finish what they called for or reach a person, and Mercury's people must spend
their time on the calls that need them.

**For Mercury:** the point of an AI agent on a 24/7 claims line is to take the routine,
high-volume calls (a fender-bender report, "where's my claim?", "how far will roadside tow me?")
end to end, so live agents spend their time on the calls that need a person: injuries, denials,
fault disputes, lawyers, confused or upset callers. That's why the hand-off rules are in code and
why the agent finishes the routine path itself instead of transferring early.

**For Mercury's customers:** someone calling after an accident is stressed. The agent should get
them to a claim number with as little friction as possible, check the details back before filing,
not make them repeat things it already knows, tell them what happens next, and let them reach a
human at any point. Asking for a person works from the opening menu or mid-call.

## What I built

```
caller ──phone──▶ Guava (speech, turn-taking, the model)
                     │  events ▲ commands
                     ▼         │
              mercury_claims/  (our agent: routing, rules, hand-offs)
                     │  HTTP + API key
                     ▼
              mock_backend/    (FastAPI: "Mercury's claims API")
                     │  secret key, server-side only
                     ▼
              Supabase Postgres (policies, claims, tow requests, call audit)
```

**One agent, three desks.** Think of three counter windows in one office. They share the same
front door, ID rules and logbook (`agent.py`: the opening disclosure, the routing menu, hand-offs,
the "anything else?" wrap-up, the audit log), but each window has its own forms (`fnol_flow.py`,
`status_flow.py`, `tow_flow.py`). Keeping each window in its own file means:

1. **You can read one thing at a time.** Checking how claim status works means opening one file,
   not one large file with all three mixed together.
2. **The diff against the originals stays readable.** `fnol_flow.py` descends from the starter's
   `fnol` and `status_flow.py` from `claims_status`, so each can be diffed against its own original.
3. **A change in one can't quietly break another.** Every name is prefixed (`fnol_`, `status_`,
   `tow_`), so the windows can't collide.
4. **Adding a fourth call type (home claims, say) means adding one file.** It registers itself, and
   nothing else changes. This is what makes the structure ready for real development: a company like
   Mercury can keep adding call types and code to the same line without breaking what already works.

The trade-off: a small amount of repetition (each desk has its own identity step), and the shared
parts have to live in `agent.py`. That's partly forced: some SDK callbacks hold one function per
agent, and registering a second one replaces the first. So those live in exactly one place and
forward to whichever desk the caller is at.

- **Report a claim.** Verify (policy number + date of birth), what happened, the vehicle, a
  read-back of the details before filing, police report, other party, file, offer a tow if the car
  isn't drivable. Injury reported means save what we have, then straight to a human.
- **Check a claim.** Verify (claim number + date of birth + ZIP), then the status and the assigned
  representative. A denied claim or one that needs a person goes to a human without a reason being
  spoken.
- **Towing and roadside.** Answers general questions from a short FAQ, deflects anything about the
  caller's own policy, and transfers to roadside dispatch if they need a tow right now.

**The integration.** Four endpoints (verify policy, create claim, look up claim, request tow) plus
an audit endpoint. Every call returns one of four typed results (OK, not found, unavailable after
one retry, malformed), and the agent handles each one differently. A retry can't create a duplicate
claim: each filing carries a one-time key the database enforces as unique.

**Second modality.** Keypad entry for policy numbers, claim numbers and ZIP codes. It's live, and it
helps on a noisy roadside call. An SMS claim-number confirmation is built behind a flag but **off**:
the sandbox number isn't provisioned for SMS (carrier registration), and I won't claim a text was
sent without proving it lands.

## Tailored to Mercury

Everything below comes from Mercury's public site, written in my own words:

- **Intake matches Mercury's own claim checklist:** when and where, what happened, injuries,
  vehicle year/make/model/color, plate *and state*, damage, **damage to other property** (a
  fence, a mailbox), where the car is now, drivable, police report, other party.
- **Claim status mirrors Mercury's "Find Your Claims Representative".** The lookup returns the
  assigned representative's name and phone, and the agent only mentions a phone number that's
  actually on file.
- **The closing reflects how Mercury handles repairs.** A representative can help find a
  Mercury preferred repair shop (Mercury guarantees that workmanship for as long as you own the
  car) or the customer can use their own, and can arrange a rental. Plus "take photos of the damage
  and the scene" from Mercury's what-to-do list.
- **The roadside FAQ is Mercury's plan.** Three towing tiers (15 / 100 / 200 miles, up to
  $75 / $500 / $1,000), lockout (you pay only for new keys), fuel delivery (you pay only for the
  fuel), reimbursement if you paid a tow yourself, whether using it raises your rate. These are
  public plan limits, not promises about anyone's claim, and "am I covered?" is still deflected
  before the FAQ is consulted.
- **Accident tow vs Roadside Assistance are kept separate, as Mercury does.** An accident tow is
  part of filing a claim; Roadside Assistance is optional coverage for breakdowns.
- **Mercury's other lines are recognized.** "It's about my house" or mechanical protection gets
  "this line handles auto claims, connecting you with the right Mercury team", not a generic
  transfer.
- **24/7.** Mercury's claims line never closes, so the original's business-hours rejection is gone.
- **The caller's details look like Mercury's:** `MCY-` policy numbers, California ZIPs, and guidance
  that the policy number is on the Mercury ID card or in the online account.

## Code vs model

The rule I used: **if a mistake would break a rule or embarrass Mercury, it's in code. If a
mistake would just sound a bit awkward, the model can own it.**

| In code (deterministic) | In the model |
|---|---|
| What each menu choice does, and which mid-call moves exist | Which choice the caller meant; noticing they want to switch |
| Is the answer valid (digit counts, not in the future); 3 strikes, then a human | Pulling the answer out of speech |
| Identity: exact match at the API, same failure shape, 2 attempts | Asking for it naturally |
| "Filed" / "saved" / "requested" only after the backend confirms | How the closing is phrased |
| Every hand-off: injury, identity failure, backend down, denied, needs a tow now | Tone, empathy, recovering from a confused caller |
| The read-back sentence, built from what will actually be submitted | Whether the caller said yes or no |
| "Is this about *your* policy?" guards, before the FAQ is touched | Phrasing the FAQ answer |
| The opening disclosure, read word for word | — |
| What goes in the audit log, and what never does | — |

The trade-off I accepted: the "is this about your policy?" guards are keyword lists. They can miss
a phrasing. But a keyword guard can be tested and explained; a second model call deciding the same
thing can't.

## When things go wrong

| Situation | What the caller hears | Logged as |
|---|---|---|
| Backend times out or errors (after one silent retry) | "Our systems are temporarily unavailable," then a representative | `error` / `backend_unavailable` |
| Backend returns garbage | Same as unavailable | `error` |
| Claim can't be submitted | "Couldn't be submitted right now," then a representative. Never "filed." | `error` / `claim_submit_failed` |
| Identity doesn't match | Asked to give every detail again, in full. Second miss: a representative. Never says which part was wrong. | `auth_failed` |
| Injury reported | Info saved (only if the save succeeded), then straight to a representative | `claim_created` / `injury_reported` |
| Claim is denied or needs a person | Connected to a representative; no reason, no amount spoken | `transferred` / `status_denied` |
| Tow request fails after the claim is filed | Claim number given; told the tow could *not* be requested; a rep will follow up | `claim_created` / `tow_request_failed` |
| Same answer invalid 3 times | A representative, instead of asking forever | `transferred` / `validation_exhausted` |
| Caller says the read-back is wrong twice | A representative, to get the details right | `transferred` / `vehicle_details_unconfirmed` |
| "How many claims do I have?" | "I can only look up one claim at a time by its number," then an offer to transfer | `transferred` / `claim_history_request` (if transferred) |

The backend has a fault-injection switch (`POST /admin/fault`), so the backend failures in this
table can be triggered on a live call.

## What I chose not to build, and why

- **Real tow dispatch.** Out of scope; a caller who needs a tow now is transferred to the roadside
  line. Better a clean hand-off than a fake dispatch.
- **Mercury's other claim types (home, mechanical protection).** Recognized and routed to a person.
  Three call types done well beat five done shallowly.
- **SMS confirmation turned on.** The code works and is tested offline, and I tried it live on a
  real phone: the send was rejected because the sandbox number isn't registered for texting
  (carrier registration). So it stays off. I prioritized a caller getting all the way to a claim
  number over a second channel.
- **Stronger identity** (caller ID matched against the phone on the policy, a one-time code, lockout
  across calls). The right production answer, but it needs systems this demo doesn't have.
- **Spanish.** A real need for Mercury's California customers; not attempted.
- **Latency tuning.** Not measured. See "rough edges".
- **Word-for-word status wording.** The status is an instruction the model phrases, close to the
  script but not guaranteed verbatim.
- **Saving drafts of abandoned calls.** If a caller hangs up mid-report, nothing is filed. Simpler
  and safer than half-finished claims, at the cost of losing what they said.

## Known rough edges

Honest list, so you see them here first:

- **Filler between steps.** "One moment… let me just…" between steps. As far as I could trace it,
  it isn't coming from this code.
- **The model mishears details.** Live, it stored "Ultima" for "Altima" and "MCP" for plate "MCP510".
  **After hitting this, I added a read-back step:** before filing, the agent reads the vehicle
  details back from what it actually stored and asks the caller to confirm. That catches the error
  when the caller is listening, but it can't prevent it. In one test the caller said "yes" to
  "Ultima".
- **The model narrates actions it isn't taking.** "Let me check your claim history" when no such
  lookup exists. Code can't stop a preamble, but it makes sure the next thing said is true.
- **When a handler crashes, the model stalls.** If code throws an error mid-call, the SDK catches it
  and the model fills for over a minute ("still getting things ready…") before giving up. I hit this
  twice live; the defense is that the backend client never raises and every spoken success sits
  behind a confirmed response.
- **No latency numbers.** Worst case, a timeout plus a retry is about 16 seconds of silence. Too long
  for voice.
- **Some fixes are prompt-level and not yet confirmed on a live call:** asking about a tow only once,
  and "give me both again, in full" on an identity retry.
- **One outcome per call in the audit log.** If a caller files a claim and then checks a status, the
  row records the last thing done (the claim number from earlier is kept).
- **The app logs aren't fully free of personal data.** The audit table never holds names, phone
  numbers or dates of birth, and my own end-of-call log line is redacted. But Guava's SDK logs the
  caller's phone number on every incoming call, along with the text of questions and requests, and
  the backend log includes ZIP codes. Production would filter those log lines and keep app logs
  short-lived.

## How to run it

**Prerequisites:** Python 3.12, a Guava sandbox (API key + phone number), a Supabase project.

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.lock.txt
cp .env.example .env   # fill in the values; .env is git-ignored
```

`.env` keys: `GUAVA_API_KEY`, `GUAVA_AGENT_NUMBER`, `SUPABASE_URL`, `SUPABASE_SECRET_KEY`,
`BACKEND_BASE_URL`, `BACKEND_API_KEY`, `ADMIN_TOKEN`, `HUMAN_LINE_NUMBER`, `ROADSIDE_LINE_NUMBER`,
`SMS_CONFIRMATION_ENABLED`. For a demo, set both transfer lines to a phone you can answer.

**Database:** run the files in `supabase/migrations/` in order, then `supabase/seed.sql` (Supabase
SQL editor or `supabase db push`).

**Start it** (two terminals):

```bash
.venv/bin/uvicorn mock_backend.main:app --port 8000   # the claims API
.venv/bin/python -m mercury_claims --phone            # the agent, on GUAVA_AGENT_NUMBER
```

Then call the agent's number. Fake test data:

| Use | Enter |
|---|---|
| Report a claim | policy `100245` (Jordan Alvarez), date of birth April 12, 1988 |
| Lapsed policy (intake continues; the backend flags it for review) | policy `100512`, DOB June 30, 1990 |
| Check a claim: received | claim `0000001`, DOB April 12, 1988, ZIP `90001` |
| Check a claim: approved | claim `0000004`, DOB April 12, 1988, ZIP `90001` |
| Check a claim: denied (transfers, no reason) | claim `0000005`, DOB Sept 8, 1995, ZIP `93710` |

**Break it on purpose** (next 2 backend calls return HTTP 500):

```bash
curl -X POST http://127.0.0.1:8000/admin/fault \
  -H "X-Admin-Token: <ADMIN_TOKEN from .env>" -H "Content-Type: application/json" \
  -d '{"mode": "500", "count": 2}'
```

Modes: `timeout`, `slow`, `500`, `malformed`, `not_found`, `none`.

**Tests and checks:**

```bash
.venv/bin/python -m pytest tests/ -q          # 111 tests, offline, no network
.venv/bin/python scripts/verify_originals.py  # originals/ unchanged
.venv/bin/python scripts/check_secrets.py     # no credentials in tracked files
bash scripts/diff_originals.sh                # regenerate diffs/ against the originals
```

## Test scenarios

Offline tests use the SDK's `MockCall` with the backend client faked to return each result type.
"Live" means confirmed on a real phone call.

| Scenario | Pass means | Offline | Live |
|---|---|---|---|
| Report a claim, drivable | Claim number given only after the API confirmed it; every field saved | ✅ | ✅ |
| Read-back before filing | Details read from what was stored; "no" re-asks once, then a human | ✅ | ✅ (yes path) |
| Injury reported | Partial claim saved, then a human; "saved" only if the save succeeded | ✅ | ✅ |
| Backend fails on submit | Never says "filed"; honest message; transfer | ✅ | — |
| Malformed backend response | Treated as unavailable | ✅ | — |
| Wrong identity twice | Retry, then a human; never says which part was wrong | ✅ | — |
| Lapsed policy | Intake continues; coverage never mentioned | ✅ | — |
| Claim status, denied | Transfer; no reason or amount spoken | ✅ | ✅ |
| Check another claim in the same call | No re-ask of a date of birth already verified | ✅ | ✅ |
| Switch call type mid-call | Lands on the right desk, no transfer | ✅ | ✅ |
| Towing FAQ vs "am I covered?" | General answer from the FAQ; personal question deflected | ✅ | ✅ (FAQ answers) |
| Needs a tow now | Transferred to the roadside line | ✅ | ✅ |
| Same answer invalid 3 times | A human instead of a loop | ✅ | — |

## The originals, and what was wrong with them

`originals/` holds the two starter files byte-for-byte (`originals/UPSTREAM.md` records the commit).
`scripts/verify_originals.py` proves they're unchanged; `scripts/diff_originals.sh` writes
`diffs/fnol.diff` and `diffs/claims_status.diff`.

What I found, by reading the code against the brief, reading the installed SDK source, and live
calls. Some of this is normal demo scaffolding for an example, and I've kept the two apart.

**Behavior that would be wrong on a real call, which I changed:**

- **`fnol` saved 3 fields.** It collected location, vehicles, police report, drivable and injuries,
  then sent only date, description and type to the claim.
- **`fnol` said "filed successfully" no matter what,** and promised "an adjuster will contact them
  within one business day" and "a confirmation email". Nothing behind it did either.
- **`fnol` hung up on a lapsed policy** ("a claim cannot be filed"). That's a coverage decision the
  agent shouldn't make. Now intake continues and the backend flags it for review.
- **`fnol` hung up on the first identity mismatch,** with no retry and no human.
- **`fnol` declined calls outside business hours.** A claims line can't.
- **`fnol` returned one canned answer to every question,** including "what else do you need?".
- **`claims_status` told the model to share settlement details and to "explain the reason [for
  denial] clearly."** The dollar figure in its data is demo data; the instruction is the problem.
  Now the status API doesn't return amounts or reasons at all, so there's nothing for the model to
  leak, and a denied claim goes to a person.
- **`claims_status` verified with claim number + date of birth only.** Now three factors, with ZIP.

**Demo scaffolding I replaced to make it real (fine for an example, not bugs):**

- **The "APIs" were functions in the same file,** with hard-coded data. Nothing could fail, so
  failure handling was never exercised. Now there's a real HTTP service and database.
- **Claim numbers came from the clock, to the minute.** Fine for a demo; with real traffic, two
  claims in the same minute would collide. Now the database assigns them.
- **`claims_status` was an outbound example** (dialing out, voicemail, do-not-call). This line is
  inbound, so I rebuilt it as inbound.

Not changed on purpose: the originals themselves (kept unchanged so you can diff), and the starter's
other examples.

## With another 8 hours

In priority order:

1. **Warm hand-off with context.** Transfers already work end to end (tested: it rang a second
   phone). Next, the live agent should receive what the caller already said (who they are, verified
   or not, which desk, what they asked for), so the person never has to start over.
2. **Notify Mercury after the call.** When the agent says "a representative will follow up", someone
   should actually be told. A post-call event to the claims team (claim filed, needs assignment,
   injury flagged) closes that loop.
3. **Mercury's other claim types.** Home and mechanical protection, as new desks on the same spine.
4. **SMS mid-call.** The send path works; it's blocked on number provisioning. Send the claim number
   by text, and use a one-time code for identity.
5. **Measure latency** and add a "let me look that up" line before backend calls; cut the worst-case
   wait.
6. **Stronger identity:** caller ID vs the phone on the policy, lockout across calls.
7. **Live-verify** the prompt-level fixes listed under rough edges.

## Time spent

About 8 hours across two sittings (I didn't have 8 consecutive hours): **6 hours on day one, 2
hours on day two.**

## How I used AI coding tools

A lot of people use AI coding tools by accepting whatever comes back. I used Claude Code the other
way: every change was explained to me before it was written, and I checked everything. I see the
tool as something that explains the infrastructure and the trade-offs, not just something that
writes code. Writing code is one part; understanding the architecture and the decisions, and keeping
a mental picture of the whole codebase, is what matters. I also set hard rules: no commit without my
review of the diff and message, `originals/` read-only, the installed SDK
source is ground truth over the docs, the say-do rule, and no secrets in the repo. I delegated the
mechanics (porting the examples, the backend, the tests, reading logs after each live call) and kept
the decisions: what "production-ready" means for this line, what the agent must never say, which
changes to keep or cut, and every commit. Two working rules came from early friction: I stopped the
tool from running its own live smoke tests so I could test calls myself, and I required it to
explain each plan before writing code.

It got things wrong, and the most useful catches came from live calls, not the test suite. A change
to reset per-call counters set them to `None`; the tests passed, but on my first call afterwards the
agent crashed right after verifying identity and stalled on filler for over a minute. The tests had
checked the reset, not the handler that ran after it, so I had the fix come with a test that
exercises the real path. Live calls also exposed a towing desk that had no way to transfer at all
(the model told me "no representatives are available"), and a status desk that transferred me when I
asked to check a second claim, because it had no "check another claim" option. When the tool
summarized the starter code from memory it was wrong (it said the originals used a deprecated
helper; they didn't), so I had the README written from the files themselves. I also cut a proposed
prompt-only fix because nothing could verify it, and had a change reverted after a live call showed
it made the conversation worse.

## Change log

Each meaningful change: what, why, and what I considered and rejected. Commit hashes in brackets.

**Backend in front of the database, with server-assigned claim numbers** `[3cc8286]`
What: FastAPI service with 4 endpoints in front of Supabase; claim numbers from a database sequence;
an idempotency key per filing. Why: a real integration goes through an API, never database
credentials in the agent; the original's clock-based numbers collide. Rejected: the agent calling
Supabase directly (credentials in the voice process, and no way to shape what the agent can see).

**Typed results from a resilient client** `[e5d292b]`
What: timeouts, one retry on timeout/5xx, no retry on 4xx, every call returns one of OK / not found /
unavailable / malformed. Why: forces every call site to handle every outcome, and makes "never say
filed after a failure" structural. Rejected: raising exceptions, where one missed `except` lets the
model talk as if nothing happened.

**One shared agent; no business hours** `[dec2944]`
What: one agent, a router, and per-call-type modules that register themselves. Why: the SDK silently
overwrites some callbacks if registered twice; a claims line is 24/7. Rejected: one agent per call
type (one phone number, and the overwrite problem).

**FNOL port: every field saved, say-do, typed date of birth, lapsed continues, hand-offs in code**
`[5619d87]`
What: all collected fields reach the claim; "filed" only after a confirmed response; date of birth
as a date with 2 attempts and no leak of which field was wrong; a lapsed policy continues with a
backend review flag; injury / identity / backend-down hand-offs in code. Rejected: telling a lapsed
caller anything about coverage (a determination).

**Claim status ported to inbound, three-factor identity, nothing to leak** `[4b25112]`
What: inbound; claim number + date of birth + ZIP; the lookup returns only status and the assigned
representative. Why: removing amounts and reasons from the API is stronger than telling the model
not to say them. Rejected: keeping the original's "explain the denial reason".

**Towing/roadside desk with a guarded FAQ** `[9b62f5e]`
What: FAQ answered with the platform's document Q&A; a code-level guard deflects anything about the
caller's own policy first; dispatch needs go to the roadside line. Rejected: real dispatch (out of
scope); a model-based guard (can't be tested the way a keyword guard can).

**PII-free audit trail; the date-of-birth crash** `[c5cfe5f]`
What: every call writes outcome / transfer reason / claim number to the audit table, with no
personal data; my own end-of-call log line is redacted. Found live: a date field arrives as a
dictionary (`{day, month, year}`), exactly as Guava's Field docs say. I had assumed a string
without checking the docs, and it crashed identity on a real call. My miss, not an SDK surprise.

**Keypad entry; SMS behind a flag** `[fae0a1d]`
What: digit-sequence fields so callers can type numbers; SMS confirmation off by default. Rejected:
claiming a text was sent before proving delivery.

**Live-call fixes** `[6dc8adb, e9fee1d, 848b7cc, 2315dcb]`
What: question handling that stopped giving the coverage deflection to every question; the towing
desk's missing transfer path; mid-call switching between call types; rewording switch descriptions
after one misfired. Why: each was found on a real call.

**Read-back, "anything else?", Mercury tailoring, in-call identity carry-over** `[13d1d9f]`
What: vehicle read-back before filing; a shared wrap-up that asks "anything else?" on every success
path; Mercury's intake checklist, roadside plan and repair-shop details; "check another claim" and
"report a second incident"; a date of birth verified once isn't re-asked later in the same call (the
backend still checks every lookup); "how many claims do I have?" answered honestly. Rejected:
correcting a single field on "no" (I didn't find a way to change one collected value in place, so
a full re-ask was the safe option); broadening the towing guard to price questions (it would have blocked the FAQ from
answering them); skipping re-verification across *calls* (never, only within one).
