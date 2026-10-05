import argparse
import os

from guava import logging_utils

from . import fnol_flow, status_flow, tow_flow  # noqa: F401
from .agent import agent

# Flow modules register themselves with the shared agent as a side effect
# of being imported (see agent.register_flow).

if __name__ == "__main__":
    logging_utils.configure_logging()

    parser = argparse.ArgumentParser(description="Inbound Mercury Insurance Auto Claims agent")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--phone", metavar="PHONE_NUMBER", nargs="?", const="", help="Listen for phone calls.")
    group.add_argument("--webrtc", metavar="WEBRTC_CODE", nargs="?", const="", help="Listen on a WebRTC code.")
    group.add_argument("--local", action="store_true", help="Start a local call.")
    group.add_argument("--sip", metavar="SIP_CODE", help="Listen on a SIP code 'guavasip-...'.")
    args = parser.parse_args()

    if args.phone is not None:
        # Bare --phone (empty string) used to mean "use the account's
        # registered number" in the original example; the live server now
        # rejects an empty phone_number with a 400 ("You must provide
        # exactly one parameter"). Falling back to GUAVA_AGENT_NUMBER
        # (confirmed live, 2026-10-05) instead of requiring the number
        # spelled out on every run.
        agent.listen_phone(args.phone or os.environ.get("GUAVA_AGENT_NUMBER", ""))
    elif args.webrtc is not None:
        agent.listen_webrtc(args.webrtc or None)
    elif args.sip:
        agent.listen_sip(args.sip)
    else:
        agent.call_local()
