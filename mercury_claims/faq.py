"""General towing/roadside-assistance FAQ, answered via DocumentQA in
server mode (D5). Written in our own words from how Mercury's public
Roadside Assistance, Auto Claims, Vehicle Repairs and Find-Your-Claims-
Representative pages describe things -- not scraped or quoted verbatim
(see README for the source note).

D6 revisited (2026-10-05): the per-tier dollar limits ARE included now.
They're public plan limits printed on Mercury's own marketing page, not a
promise about any caller's claim -- a different thing from the
claim-specific amounts the "no dollar amounts" rule protects. Whether a
specific caller gets any of it back still goes through tow_flow's
caller-specific deflection guard before the FAQ is ever consulted.

No phone numbers, ever: transfers use the env-configured lines.

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
representative can help arrange a tow and can help locate one of Mercury's preferred repair
shops. Those shops are held to Mercury's quality standards and are audited regularly, and
paint, bodywork and workmanship done at one of them is guaranteed for as long as the customer
owns the vehicle. A customer is also free to use a shop of their own choosing instead. A
representative can also help arrange or refer a rental car after a covered loss. A new
accident is handled as part of filing a claim, not through this general-questions line.

Roadside Assistance is a separate, optional coverage for situations that are NOT the result
of an accident -- for example a dead battery, being locked out, a flat tire, running out of
gas, or a mechanical breakdown. It is not included automatically on every policy; it has to
have been added to the policy.

What Roadside Assistance can help with, when a policy includes it:
- Towing to the nearest qualified repair facility. There are three coverage tiers, each with
  its own distance and dollar limit per incident: up to 15 miles (up to $75), up to 100 miles
  (up to $500), or up to 200 miles (up to $1,000). Which tier applies depends on the specific
  policy.
- Lockout assistance if you're locked out of your vehicle. The customer pays only for the
  cost of any new keys that have to be made.
- A jump start for a dead battery.
- Changing a flat tire, if a usable spare is available.
- Delivery of a small amount of fuel or fluid if you run out. The customer pays only for the
  fuel or fluid itself.
- Once a tow is dispatched, customers can usually opt in to text updates on its status.

Paying for a tow out of pocket: if a customer already paid for their own tow, Mercury can
generally reimburse a reasonable, customary charge once they submit the receipt. A
representative handles that.

Does using Roadside Assistance raise rates? Generally no. It is treated as a service benefit,
not as a claim against the policy, so a single use is not expected to raise anyone's rate.
Very frequent use could be reviewed like any other policy activity.

Checking or choosing a plan: whether a specific policy includes Roadside Assistance, which
tier it carries, what it costs to add, and what the deductible or coverage limits are, are
all things only a representative can look up on that specific policy. A customer can also
look up their assigned claims representative online using their claim or policy number.

What this general-questions line can't tell a caller: anything about their own specific
policy or claim -- a representative can look that up and go over the details.
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
