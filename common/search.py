"""Web search through Serper (Google results). Free: 2,500 searches, no card needed."""
import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()
RETRIES = 3

class SearchLimit(Exception):
    """Serper refused the request (free searches used up, or key missing/wrong) or didn't answer."""

def search(query, num=10):
    """Return Google results as a list of {title, link, snippet}."""
    key = os.getenv("SERPER_API_KEY")
    if not key:
        raise SearchLimit("SERPER_API_KEY is not set")
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.post("https://google.serper.dev/search",
                              headers={"X-API-KEY": key, "Content-Type": "application/json"},
                              json={"q": query, "gl": "us", "num": num}, timeout=30)
        except requests.RequestException as e:
            if attempt == RETRIES:
                raise SearchLimit(f"Serper didn't answer after {RETRIES} tries ({type(e).__name__})")
            time.sleep(5 * attempt)
            continue
        if r.status_code == 200:
            return [{"title": o.get("title", ""), "link": o.get("link", ""), "snippet": o.get("snippet", "")}
                    for o in r.json().get("organic", [])]
        if r.status_code >= 500 and attempt < RETRIES:
            time.sleep(5 * attempt)
            continue
        raise SearchLimit(f"Serper returned HTTP {r.status_code}: {r.text[:200]}")