"""Fixed spoken text. Anything that must not drift between calls lives
here as a named constant, never inline in a flow module."""

OPENING_DISCLOSURE = (
    "Thank you for calling Mercury Insurance Auto Claims. This call may be "
    "recorded, and you are speaking with an AI assistant, not a live "
    "representative. If you are experiencing a medical emergency or are in "
    "danger, please hang up and dial 911 now. Otherwise, I can help you "
    "report a new auto claim, check the status of an existing claim, or "
    "answer questions about towing and roadside assistance."
)

TRANSFER_GENERIC = (
    "Let the caller know you're connecting them with someone who can help, "
    "and transfer the call."
)

TRANSFER_BACKEND_DOWN = (
    "Let the caller know our systems are temporarily unavailable and you "
    "can't complete this right now. Apologize, and connect them with a "
    "representative."
)

# Section 8.3's words-per-status table. Delivered as the objective of the
# shared wrap-up task (agent.end_call_with_wrapup) -- an instruction to
# the model, not a literal guava.Say -- so the status is spoken and then
# the caller is asked whether there's anything else. The goodbye itself
# is deliberately NOT part of these strings any more: it's said only once
# the caller answers "no" to that question (agent._on_wrap_up_complete).
# Noted in the README: status wording is close to verbatim, not
# guaranteed word-for-word.


def _representative_clause(adjuster_name: str | None, adjuster_phone: str | None) -> str | None:
    # Confirmed live (2026-10-05): a hand-edited claim had adjuster_name
    # set but adjuster_phone still null. The model happened to drop the
    # phone number gracefully that one time rather than literally saying
    # "reachable at None," but that's model luck, not something this
    # code should rely on -- only mention a phone number that's actually
    # on file, independent of whether a name is.
    if adjuster_name and adjuster_phone:
        return f"{adjuster_name}, reachable at {adjuster_phone}"
    if adjuster_name:
        return adjuster_name
    return None


def status_received(adjuster_name: str | None, adjuster_phone: str | None) -> str:
    rep = _representative_clause(adjuster_name, adjuster_phone)
    base = "Let the caller know their claim was received and is in our system."
    base += f" Their assigned representative is {rep}." if rep else " No representative has been assigned yet."
    return base


def status_under_review(adjuster_name: str | None, adjuster_phone: str | None) -> str:
    rep = _representative_clause(adjuster_name, adjuster_phone)
    base = "Let the caller know their claim is currently under review."
    base += f" Their assigned representative, {rep}, can answer further questions." if rep else " No representative has been assigned yet."
    return base


def status_awaiting_documents(adjuster_name: str | None, adjuster_phone: str | None) -> str:
    rep = _representative_clause(adjuster_name, adjuster_phone)
    base = "Let the caller know additional documents are needed for their claim."
    base += f" Their representative, {rep}, can say which ones." if rep else " A representative will reach out with specifics."
    base += " Do not guess which documents are needed."
    return base


def status_approved(adjuster_name: str | None, adjuster_phone: str | None) -> str:
    rep = _representative_clause(adjuster_name, adjuster_phone)
    base = "Let the caller know their claim has been approved."
    base += f" Their representative, {rep}, will go over payment details." if rep else " A representative will follow up to go over payment details."
    base += " Do not state or estimate any dollar amount."
    return base


def status_closed(adjuster_name: str | None, adjuster_phone: str | None) -> str:
    rep = _representative_clause(adjuster_name, adjuster_phone)
    base = "Let the caller know their claim is closed."
    base += f" For further questions, their representative is {rep}." if rep else " A representative is available if they have further questions."
    return base


# H8: denied / needs_human both warm-transfer without ever stating or
# implying a reason.
STATUS_TRANSFER_NO_REASON = (
    "Let the caller know you're connecting them with a representative to discuss their claim "
    "further. Do not mention, speculate about, or confirm any reason for the current status, and "
    "do not state any dollar amount."
)

# H9: coverage/cost questions specific to the caller are deflected, not
# answered -- that's a determination only a human can make.
COVERAGE_DEFLECTION = (
    "I'm not able to tell you whether this is covered under your specific policy, or what it "
    "would cost -- that depends on your policy details. I can connect you with a representative "
    "who can look that up for you."
)

# Used when the FAQ itself is unavailable (failed to initialize, or the
# question couldn't be answered) -- never invent an answer instead.
FAQ_DEFLECTION = (
    "I don't have an answer to that readily available. Let me connect you with someone who can "
    "help."
)

# fnol_flow: a question during intake that isn't about coverage/fault/
# legal (e.g. "what else do you need?") -- acknowledge honestly rather
# than giving the coverage-deflection non-sequitur (confirmed live,
# 2026-10-05, this was returned for every question regardless of
# content).
INTAKE_QUESTION_ACKNOWLEDGMENT = (
    "I'm just gathering the details needed to get your claim started. If there's something "
    "specific I can clarify about that, let me know -- otherwise, let's continue."
)

# status_flow: F11's safe default for anything that looks like it's
# about the claim just checked -- never reveals amounts or reasons.
STATUS_CLAIM_DETAIL_DEFLECTION = (
    "I'm not able to share additional details beyond your claim's current status -- your "
    "assigned representative can go over the specifics with you."
)

# Both desks: "how many claims do I have?" / "my claim history". There is
# deliberately no list-or-count lookup anywhere (it would be enumeration),
# so the honest answer is the limitation itself. Confirmed live
# (2026-10-06): without this the model said "let me check your claim
# history" -- an action it cannot take -- before transferring generically.
CLAIM_HISTORY_DEFLECTION = (
    "I can only look up one claim at a time, by its claim number, so I'm not able to list or "
    "count your claims. A representative can review your full claim history -- would you like "
    "me to connect you?"
)

TRANSFER_CLAIM_HISTORY = (
    "Let the caller know you can only look up one claim at a time by its claim number, and "
    "that you're connecting them with a representative who can review their full claim history."
)

# status_flow: a question that's clearly about something else entirely
# (towing, enrollment) -- confirmed live, 2026-10-05, a towing question
# got the claim-detail deflection above, which was a non-sequitur.
STATUS_UNRELATED_QUESTION_ACKNOWLEDGMENT = (
    "That's outside what I can help with on this call. If you'd like to ask about towing or "
    "roadside assistance, or start a new claim, you're welcome to call back and choose that "
    "option. For now, is there anything else about this claim I can help with?"
)
