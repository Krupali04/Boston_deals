import os
import psycopg
from dotenv import load_dotenv

load_dotenv()

def get_conn():
    return psycopg.connect(os.environ["SUPABASE_DB_URL"])