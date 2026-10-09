import time
import requests
from psycopg.types.json import Jsonb
from common.db import get_conn

BBOX = (42.335, -71.110, 42.357, -71.067)   # south, west, north, east (Back Bay + Fenway)
QUERY = f"""
[out:json][timeout:90];
nwr["amenity"~"^(restaurant|cafe|fast_food|bar|pub|ice_cream)$"]({BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]});
out center tags;
"""

# Overpass is a free, shared service and is sometimes overloaded, so try a few public servers.
ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
HEADERS = {"User-Agent": "boston-deals-portfolio/0.1 (taybos4811@gmail.com)"}

def run_query(rounds=3):
    for attempt in range(1, rounds + 1):
        for url in ENDPOINTS:
            try:
                r = requests.post(url, data={"data": QUERY}, headers=HEADERS, timeout=180)
                if r.status_code == 200:
                    return r.json()["elements"]
                print(f"{url}: HTTP {r.status_code} (busy or rate-limited)")
            except (requests.RequestException, ValueError) as e:
                print(f"{url}: {e}")
        if attempt < rounds:
            wait = 60 * attempt
            print(f"All servers busy — retrying in {wait} seconds...")
            time.sleep(wait)
    raise RuntimeError("Overpass servers are all busy right now. Try again in a while.")

def main():
    elements = run_query()
    print(f"Found {len(elements)} places")
    with get_conn() as conn, conn.cursor() as cur:
        for el in elements:
            cur.execute(
                """insert into raw_osm_places (osm_id, payload) values (%s, %s)
                   on conflict (osm_id) do update set payload = excluded.payload, fetched_at = now()""",
                (f"{el['type']}/{el['id']}", Jsonb(el)),
            )

if __name__ == "__main__":
    main()