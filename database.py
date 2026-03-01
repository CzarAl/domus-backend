import os
from dotenv import load_dotenv
from supabase import create_client

# Cargar variables del archivo .env
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise Exception("Faltan variables de entorno SUPABASE_URL o SUPABASE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

print("SUPABASE_URL:", SUPABASE_URL)