import os
import re
import time
from urllib.parse import urlparse, urlunparse
import requests
from dotenv import load_dotenv
from rapidfuzz import fuzz
from common.db import get_conn

load_dotenv()
API_KEY = os.environ["GOOGLE_MAPS_API_KEY"]
URL = "https://places.googleapis.com/v1/places:searchText"
# Only these fields are requested. websiteUri makes each request an "Enterprise" request
# (1,000 free per month); the other fields add no extra cost. Never add reviews or other
# "Atmosphere" fields — they cost more.
FIELD_MASK = ("places.id,places.displayName,places.formattedAddress,places.websiteUri,"
              "places.businessStatus,places.primaryType")
SET_ASIDE_TYPES = {"hotel", "lodging", "hospital", "university", "school", "stadium", "arena",
                   "convention_center"}
MIN_NAME_SCORE = 70
RETRIES = 3
MONTHLY_LIMIT = 900   # Google gives 1,000 free per month; 100 spare covers retried requests

_requests = 0         # every request sent this run, including retries

class GoogleStop(Exception):
    """Daily cap reached, or the key/API isn't set up — stop the run."""

STOPWORDS = {"the", "inc", "llc", "corp", "co", "company", "ltd", "restaurant", "cafe", "boston"}

def norm(s):
    s = re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())
    return " ".join(w for w in s.split() if w not in STOPWORDS)

def clean_website(url):
    """Drop tracking bits like ?utm_source=... so we store the plain homepage link."""
    if not url:
        return None
    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc, p.path or "/", "", "", ""))

def search_place(name, address, zip_code, lat, lon):
    body = {"textQuery": f"{name}, {address}, Boston, MA {zip_code}", "pageSize": 3}
    if lat is not None and lon is not None:
        body["locationBias"] = {"circle": {"center": {"latitude": lat, "longitude": lon}, "radius": 200.0}}
    headers = {"Content-Type": "application/json", "X-Goog-Api-Key": API_KEY, "X-Goog-FieldMask": FIELD_MASK}
    global _requests
    for attempt in range(1, RETRIES + 1):
        _requests += 1
        try:
            r = requests.post(URL, json=body, headers=headers, timeout=30)
        except requests.RequestException:
            if attempt == RETRIES:
                return None          # no answer: skip for now, retried next run
            time.sleep(5 * attempt)
            continue
        if r.status_code == 200:
            return r.json().get("places", [])
        if r.status_code == 429:
            raise GoogleStop("daily request cap reached — the rest are looked up next run")
        if r.status_code in (400, 401, 403):
            raise GoogleStop(f"Google refused the request (HTTP {r.status_code}): {r.text[:200]}")
        if attempt == RETRIES:
            return None
        time.sleep(5 * attempt)

def best_match(places, name, address):
    """The returned place that really is this restaurant, or None."""
    number = (re.match(r"\s*(\d+)", address or "") or [None, None])[1]
    best, best_score = None, 0
    for p in places:
        score = fuzz.token_set_ratio(norm(name), norm(p.get("displayName", {}).get("text")))
        if number and (p.get("formattedAddress") or "").startswith(number + " "):
            score += 15
        if score > best_score:
            best, best_score = p, score
    return best if best_score >= MIN_NAME_SCORE else None

def main():
    with get_conn() as conn, conn.cursor() as cur:
        # How many lookups were already done this month (each saved lookup sets google_checked_at)
        cur.execute("select count(*) from dim_restaurants where google_checked_at >= date_trunc('month', now())")
        used = cur.fetchone()[0]
        print(f"Google lookups used this month: {used} of {MONTHLY_LIMIT}")
        if used >= MONTHLY_LIMIT:
            print("Monthly Google limit reached — the rest continue next month.")
            return

        cur.execute("""select restaurant_id, name, address, zip, lat, lon from dim_restaurants
                       where google_checked_at is null and verified <> 'no'
                       order by restaurant_id""")
        rows = cur.fetchall()
        print(f"{len(rows)} restaurants to look up on Google")
        found = 0
        for rid, name, address, zip_code, lat, lon in rows:
            if used + _requests + RETRIES > MONTHLY_LIMIT:
                print(f"Monthly Google limit reached ({MONTHLY_LIMIT}) — the rest continue next month.")
                break
            try:
                places = search_place(name, address, zip_code, lat, lon)
            except GoogleStop as e:
                print(f"Stopping: {e}")
                break
            if places is None:
                print(f"{name}: Google didn't answer — will retry next run")
                continue

            match = best_match(places, name, address)
            status = match.get("businessStatus", "UNKNOWN") if match else "NO_MATCH"
            website = clean_website(match.get("websiteUri")) if match else None
            set_aside, reason = False, None
            if status == "CLOSED_PERMANENTLY":
                set_aside, reason = True, "Google: permanently closed"
            elif match and match.get("primaryType") in SET_ASIDE_TYPES:
                set_aside, reason = True, f"Google type: {match.get('primaryType')}"

            cur.execute(
                """update dim_restaurants set
                     google_place_id = %s,
                     google_status = %s,
                     google_checked_at = now(),
                     website = coalesce(website, %s),
                     verified = case when %s then 'no' else verified end,
                     verify_reason = case when %s then %s else verify_reason end,
                     updated_at = now()
                   where restaurant_id = %s""",
                (match.get("id") if match else None, status, website, set_aside, set_aside, reason, rid),
            )
            conn.commit()
            found += bool(website)
            print(f"{name}: {website or '-'}  [{status}]{'  → set aside: ' + reason if set_aside else ''}")
        print(f"\nWebsites found: {found}")

if __name__ == "__main__":
    main()