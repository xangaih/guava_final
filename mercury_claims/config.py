import os

from dotenv import load_dotenv

load_dotenv()

BACKEND_BASE_URL = os.environ["BACKEND_BASE_URL"]
BACKEND_API_KEY = os.environ["BACKEND_API_KEY"]
HUMAN_LINE_NUMBER = os.environ["HUMAN_LINE_NUMBER"]
ROADSIDE_LINE_NUMBER = os.environ["ROADSIDE_LINE_NUMBER"]
