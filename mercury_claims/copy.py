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

# Section 8.3's words-per-status table. Delivered via call.hangup's
# final_instructions (an instruction to the model), not a literal
# guava.Say: whether a Say-only task (no Field items) completes cleanly
# is unverified against a live call (the plan itself flags this as a
# thing to verify), and verifying it needs a live call, which isn't being
# done right now. This is the plan's own documented fallback for exactly
# that gap. Noted in the README: status wording is close to verbatim,
# not guaranteed word-for-word.


def status_received(adjuster_name: str | None, adjuster_phone: str | None) -> str:
    if adjuster_name:
        return (
            f"Let the caller know their claim was received and is in our system. Their assigned "
            f"representative is {adjuster_name}, reachable at {adjuster_phone}. Thank them and "
            f"politely say goodbye."
        )
    return (
        "Let the caller know their claim was received and is in our system. No representative has "
        "been assigned yet. Thank them and politely say goodbye."
    )


def status_under_review(adjuster_name: str | None, adjuster_phone: str | None) -> str:
    if adjuster_name:
        return (
            f"Let the caller know their claim is currently under review. Their assigned "
            f"representative, {adjuster_name} ({adjuster_phone}), can answer further questions. "
            f"Thank them and politely say goodbye."
        )
    return (
        "Let the caller know their claim is currently under review. No representative has been "
        "assigned yet. Thank them and politely say goodbye."
    )


def status_awaiting_documents(adjuster_name: str | None, adjuster_phone: str | None) -> str:
    base = "Let the caller know additional documents are needed for their claim."
    if adjuster_name:
        base += f" Their representative, {adjuster_name} ({adjuster_phone}), can say which ones."
    else:
        base += " A representative will reach out with specifics."
    base += " Do not guess which documents are needed. Thank them and politely say goodbye."
    return base


def status_approved(adjuster_name: str | None, adjuster_phone: str | None) -> str:
    base = "Let the caller know their claim has been approved."
    if adjuster_name:
        base += f" Their representative, {adjuster_name} ({adjuster_phone}), will go over payment details."
    else:
        base += " A representative will follow up to go over payment details."
    base += " Do not state or estimate any dollar amount. Thank them and politely say goodbye."
    return base


def status_closed(adjuster_name: str | None, adjuster_phone: str | None) -> str:
    base = "Let the caller know their claim is closed."
    if adjuster_name:
        base += f" For further questions, their representative is {adjuster_name} ({adjuster_phone})."
    else:
        base += " A representative is available if they have further questions."
    base += " Thank them and politely say goodbye."
    return base


# H8: denied / needs_human both warm-transfer without ever stating or
# implying a reason.
STATUS_TRANSFER_NO_REASON = (
    "Let the caller know you're connecting them with a representative to discuss their claim "
    "further. Do not mention, speculate about, or confirm any reason for the current status, and "
    "do not state any dollar amount."
)
