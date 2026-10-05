import os

from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SECRET_KEY = os.environ["SUPABASE_SECRET_KEY"]
BACKEND_API_KEY = os.environ["BACKEND_API_KEY"]
ADMIN_TOKEN = os.environ["ADMIN_TOKEN"]
