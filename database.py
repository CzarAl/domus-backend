import os
from supabase import create_client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

print("SUPABASE KEY START:", SUPABASE_KEY[:10])

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
