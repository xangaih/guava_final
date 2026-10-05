import os

from dotenv import load_dotenv

load_dotenv()

BACKEND_BASE_URL = os.environ["BACKEND_BASE_URL"]
BACKEND_API_KEY = os.environ["BACKEND_API_KEY"]
HUMAN_LINE_NUMBER = os.environ["HUMAN_LINE_NUMBER"]
ROADSIDE_LINE_NUMBER = os.environ["ROADSIDE_LINE_NUMBER"]
GUAVA_AGENT_NUMBER = os.environ["GUAVA_AGENT_NUMBER"]

# C15, stretch: off until the 10-minute send_sms test has passed on the
# phone used for the demo (see README). Left off by default on purpose.
SMS_CONFIRMATION_ENABLED = os.environ["SMS_CONFIRMATION_ENABLED"].strip().lower() == "true"
