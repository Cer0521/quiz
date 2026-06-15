"""Supabase admin client — initialised once at import time."""

import os

from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY: str = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
    raise RuntimeError(
        "Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in environment. "
        "Copy .env.example to .env and fill in the values."
    )

supabase_admin: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
