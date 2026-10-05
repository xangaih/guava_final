"""General towing/roadside-assistance FAQ, answered via DocumentQA in
server mode (D5). Written in our own words from how Mercury's public
pages describe claims towing and Roadside Assistance -- not scraped or
quoted verbatim (see README for the source note). No dollar limits (D6):
tiers only.

DocumentQA's constructor uploads documents to the Guava server, which is
a real network call -- it is NOT built here at import time. It's built
lazily, on the first question actually asked, so importing this module
(e.g. in offline tests, or if the agent process never gets a towing
call) never touches the network. If it fails to build, or .ask() raises,
callers get a deflection instead of a crash or an invented answer.
"""

import logging

from guava.helpers.rag import DocumentQA

logger = logging.getLogger("mercury_claims.faq")

FAQ_TEXT = """
Mercury Insurance Auto Claims -- Towing and Roadside Assistance (general information)

After an accident: if a vehicle isn't drivable because of an accident, a Mercury claims
representative can help arrange a tow, and can help find a participating repair shop. A new
accident is handled as part of filing a claim, not through this general-questions line.

Roadside Assistance is a separate, optional coverage for situations that are NOT the result
of an accident -- for example a dead battery, being locked out, a flat tire, running out of
gas, or a mechanical breakdown. It is not included automatically on every policy.

What Roadside Assistance can help with, when a policy includes it:
- Towing to the nearest qualified repair facility. Different plan tiers cover different towing
  distances.
- Lockout assistance if you're locked out of your vehicle.
- A jump start for a dead battery.
- Changing a flat tire, if a spare is available.
- Delivery of a small amount of fuel or fluid if you run out.

Mercury can also help with finding a preferred repair shop, and can help arrange or refer a
rental car after a covered loss.

What this general-questions line can't tell a caller: whether their specific policy includes
Roadside Assistance, what it would cost them, or their deductible and coverage limits. A
representative can look up the policy and go over those details.
""".strip()

_FAQ_NAMESPACE = "mercury_roadside_faq"
_qa: DocumentQA | None = None
_init_failed = False


def get_faq_qa() -> DocumentQA | None:
    global _qa, _init_failed
    if _qa is None and not _init_failed:
        try:
            _qa = DocumentQA(documents=[FAQ_TEXT], namespace=_FAQ_NAMESPACE)
        except Exception:
            logger.exception("DocumentQA failed to initialize for the roadside FAQ")
            _init_failed = True
    return _qa


def ask(question: str) -> str | None:
    """Returns an answer, or None if the FAQ is unavailable or the ask
    itself failed -- callers must supply their own deflection in that case,
    never invent an answer."""
    qa = get_faq_qa()
    if qa is None:
        return None
    try:
        return qa.ask(question)
    except Exception:
        logger.exception("DocumentQA.ask failed")
        return None
