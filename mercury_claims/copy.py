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
