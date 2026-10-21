"""Web search through Serper (Google results). Free: 2,500 queries, no card needed."""
import os
import requests
from dotenv import load_dotenv

load_dotenv()

class SearchLimit(Exception):
    """Serper refused the request: free queries used up, or the key is missing or wrong."""

def search(query, num=10):
    """Return Google results as a list of {title, link, snippet}."""
    key = os.getenv("SERPER_API_KEY")
    if not key:
        raise SearchLimit("SERPER_API_KEY is not set")
    r = requests.post("https://google.serper.dev/search",
                      headers={"X-API-KEY": key, "Content-Type": "application/json"},
                      json={"q": query, "gl": "us", "num": num}, timeout=30)
    if r.status_code != 200:
        raise SearchLimit(f"Serper returned HTTP {r.status_code}: {r.text[:200]}")
    return [{"title": o.get("title", ""), "link": o.get("link", ""), "snippet": o.get("snippet", "")}
            for o in r.json().get("organic", [])]