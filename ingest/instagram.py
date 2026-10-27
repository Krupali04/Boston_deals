import json
import os
import time
from datetime import datetime, timedelta, timezone
import requests
from psycopg.types.json import Jsonb
from common.db import get_conn

API_VERSION = "v25.0"              # use the version that works in your Graph API Explorer
IG_USER_ID = os.environ["IG_USER_ID"]
TOKEN = os.environ["IG_ACCESS_TOKEN"]
RATE_LIMIT_CODES = {4, 17, 32, 613}
MAX_AGE_DAYS = 30                  # older posts are unlikely to have a deal that's still running
POSTS_PER_ACCOUNT = 12             # enough for 30 days at most restaurants; keeps each call light
MAX_RUN_SECONDS = 5.5 * 3600       # stay under GitHub's 6-hour job limit
LIMIT_PAUSE_SECONDS = 600          # wait this long after hitting the hourly limit
RETRIES = 3                        # tries per call when Instagram is slow

# Only the fields the pipeline uses (no like/comment counts): lighter calls, fewer slow-downs.
MEDIA_FIELDS = ("id,caption,media_type,media_url,thumbnail_url,permalink,timestamp,"
                "children{media_type,media_url,thumbnail_url}")

UPSERT = """
insert into raw_ig_posts (post_id, restaurant_id, ig_handle, media_type, caption, permalink,
                          media_url, thumbnail_url, children, posted_at)
values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
on conflict (post_id) do update set
  media_url = excluded.media_url,
  thumbnail_url = excluded.thumbnail_url,
  children = excluded.children,
  fetched_at = now(),
  status = case when raw_ig_posts.status = 'url_expired' then 'new' else raw_ig_posts.status end
"""

# ---------- Pacing: stay under Instagram's hourly limit (~200 calls) ----------
_last_call = 0.0
_gap = 18.0
_calls = 0

def _usage(headers):
    """Meta's usage header as a dict of percentages (call_count, total_cputime, total_time), or None."""
    try:
        data = json.loads(headers.get("x-app-usage") or "null")
        return {k: float(v) for k, v in data.items()} if data else None
    except (ValueError, TypeError, AttributeError):
        return None

def discover(handle):
    """One Business Discovery call (profile + latest posts), paced and retried when Instagram is slow."""
    global _last_call, _gap, _calls
    fields = (f"business_discovery.username({handle})"
              f"{{username,biography,website,media.limit({POSTS_PER_ACCOUNT}){{{MEDIA_FIELDS}}}}}")
    for attempt in range(1, RETRIES + 1):
        wait = _gap - (time.time() - _last_call)
        if wait > 0:
            time.sleep(wait)
        try:
            r = requests.get(f"https://graph.facebook.com/{API_VERSION}/{IG_USER_ID}",
                             params={"fields": fields, "access_token": TOKEN}, timeout=30)
        except requests.RequestException as e:
            _last_call = time.time()
            if attempt == RETRIES:
                return {"error": {"message": f"no answer after {RETRIES} tries ({type(e).__name__})", "code": "timeout"}}
            time.sleep(10 * attempt)
            continue
        _last_call = time.time()
        _calls += 1

        usage = _usage(r.headers)
        top = max(usage.values()) if usage else None
        if top is None:
            _gap = 18
        elif top >= 95:
            _gap = 600       # almost at the limit: pause 10 minutes
        elif top >= 80:
            _gap = 60
        elif top >= 50:
            _gap = 18
        else:
            _gap = 5
        if usage and _calls % 25 == 0:
            print(f"  Meta usage after {_calls} calls: {usage}")

        try:
            return r.json()
        except ValueError:
            if attempt == RETRIES:
                return {"error": {"message": "unreadable answer", "code": "timeout"}}
            time.sleep(10 * attempt)
    return {"error": {"message": "no answer", "code": "timeout"}}

def is_rate_limited(data):
    return "error" in data and data["error"].get("code") in RATE_LIMIT_CODES

# ---------- Collection ----------
def main():
    start = time.time()
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select source_id from seen_posts where source_type = 'instagram'")
        seen = {row[0] for row in cur.fetchall()}
        # One row per Instagram account: chain locations sharing a handle are checked once.
        # Accounts with a never-checked location go first, then the ones checked longest ago.
        cur.execute("""select min(instagram_handle) as handle, min(restaurant_id) as rid, count(*) as locations
                       from dim_restaurants
                       where instagram_handle is not null and verified not in ('no', 'needs_review')
                       group by lower(instagram_handle)
                       order by bool_or(ig_fetched_at is null) desc, min(ig_fetched_at), min(restaurant_id)""")
        accounts = cur.fetchall()
        print(f"{len(accounts)} Instagram accounts to check (about {len(accounts) / 200:.1f} hours)")

        for handle, rid, locations in accounts:
            if time.time() - start > MAX_RUN_SECONDS:
                print("Time budget used — the rest will be checked first on the next run.")
                break

            data = discover(handle)
            while is_rate_limited(data):
                if time.time() - start + LIMIT_PAUSE_SECONDS > MAX_RUN_SECONDS:
                    print("Hourly limit reached and no time left — the next run continues from here.")
                    return
                print(f"Hourly limit reached — waiting {LIMIT_PAUSE_SECONDS // 60} minutes...")
                time.sleep(LIMIT_PAUSE_SECONDS)
                data = discover(handle)

            err = data.get("error")
            if err and err.get("code") == "timeout":
                print(f"{handle}: {err['message']} — will retry next run")
                continue
            if err:
                print(f"{handle}: {err.get('message')}")
                cur.execute("update dim_restaurants set ig_fetched_at = now() where lower(instagram_handle) = lower(%s)",
                            (handle,))
                conn.commit()
                continue

            bd = data["business_discovery"]
            cur.execute("""update dim_restaurants
                           set ig_bio = %s, ig_website = %s, ig_fetched_at = now()
                           where lower(instagram_handle) = lower(%s)""",
                        (bd.get("biography"), bd.get("website"), handle))
            saved = 0
            for m in bd.get("media", {}).get("data", []):
                posted = datetime.strptime(m["timestamp"], "%Y-%m-%dT%H:%M:%S%z")
                if posted < cutoff or m["id"] in seen:
                    continue
                cur.execute(UPSERT, (
                    m["id"], rid, handle, m.get("media_type"), m.get("caption"), m.get("permalink"),
                    m.get("media_url"), m.get("thumbnail_url"),
                    Jsonb(m["children"]) if m.get("children") else None, m.get("timestamp"),
                ))
                saved += 1
            conn.commit()
            where = f" (shared by {locations} locations)" if locations > 1 else ""
            print(f"{handle}: {saved} new or waiting posts{where}")

if __name__ == "__main__":
    main()