import json
import os
import time
from datetime import datetime, timedelta, timezone
import requests
from psycopg.types.json import Jsonb
from common.db import get_conn

API_VERSION = "v25.0"   # use the latest version shown in Graph API Explorer
IG_USER_ID = os.environ["IG_USER_ID"]
TOKEN = os.environ["IG_ACCESS_TOKEN"]
RATE_LIMIT_CODES = {4, 17, 32, 613}
MAX_AGE_DAYS = 30                  # older posts are unlikely to have a deal that's still running
MAX_RUN_SECONDS = 5.5 * 3600       # stay under GitHub's 6-hour job limit
LIMIT_PAUSE_SECONDS = 600          # wait this long after hitting the hourly limit

MEDIA_FIELDS = ("id,caption,media_type,media_url,thumbnail_url,permalink,timestamp,"
                "like_count,comments_count,children{media_type,media_url,thumbnail_url}")

UPSERT = """
insert into raw_ig_posts (post_id, restaurant_id, ig_handle, media_type, caption, permalink,
                          media_url, thumbnail_url, children, posted_at, like_count, comments_count)
values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
on conflict (post_id) do update set
  media_url = excluded.media_url,
  thumbnail_url = excluded.thumbnail_url,
  children = excluded.children,
  like_count = excluded.like_count,
  comments_count = excluded.comments_count,
  fetched_at = now(),
  status = case when raw_ig_posts.status = 'url_expired' then 'new' else raw_ig_posts.status end
"""

# ---------- Pacing: stay under ~200 calls per rolling hour ----------
_last_call = 0.0
_gap = 18.0     # seconds between calls; adjusted after every call

def _usage_percent(headers):
    """Highest share (%) of the hourly limit already used, from Meta's usage header, or None."""
    raw = headers.get("x-app-usage")
    try:
        return max(json.loads(raw).values()) if raw else None
    except (ValueError, TypeError):
        return None

def discover(handle):
    """One Business Discovery call (profile + latest 25 posts), paced to respect the hourly limit."""
    global _last_call, _gap
    wait = _gap - (time.time() - _last_call)
    if wait > 0:
        time.sleep(wait)
    fields = (f"business_discovery.username({handle})"
              f"{{username,biography,website,followers_count,media.limit(25){{{MEDIA_FIELDS}}}}}")
    r = requests.get(f"https://graph.facebook.com/{API_VERSION}/{IG_USER_ID}",
                     params={"fields": fields, "access_token": TOKEN}, timeout=30)
    _last_call = time.time()

    usage = _usage_percent(r.headers)
    if usage is None:
        _gap = 18          # no header yet: about 200 calls per hour
    elif usage >= 95:
        _gap = 600         # almost at the limit: pause 10 minutes
    elif usage >= 80:
        _gap = 60
    elif usage >= 50:
        _gap = 18
    else:
        _gap = 5           # plenty of room: go faster
    return r.json()

def is_rate_limited(data):
    return "error" in data and data["error"].get("code") in RATE_LIMIT_CODES

# ---------- Collection ----------
def main():
    start = time.time()
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select source_id from seen_posts where source_type = 'instagram'")
        seen = {row[0] for row in cur.fetchall()}
        cur.execute("""select restaurant_id, instagram_handle from dim_restaurants
                       where instagram_handle is not null and verified <> 'no'
                       order by ig_fetched_at nulls first, restaurant_id""")
        restaurants = cur.fetchall()
        print(f"{len(restaurants)} restaurants to check (about {len(restaurants) / 200:.1f} hours)")

        for rid, handle in restaurants:
            if time.time() - start > MAX_RUN_SECONDS:
                print("Time budget used — the rest will be checked first on the next run.")
                break

            data = discover(handle)
            if is_rate_limited(data):
                print(f"Hourly limit reached — waiting {LIMIT_PAUSE_SECONDS // 60} minutes, then retrying.")
                time.sleep(LIMIT_PAUSE_SECONDS)
                data = discover(handle)
                if is_rate_limited(data):
                    print("Still limited — stopping; the next run continues from here.")
                    break

            if "error" in data:
                print(f"{handle}: {data['error'].get('message')}")
                cur.execute("update dim_restaurants set ig_fetched_at = now() where restaurant_id = %s", (rid,))
                conn.commit()
                continue

            bd = data["business_discovery"]
            cur.execute("""update dim_restaurants
                           set ig_bio = %s, ig_website = %s, ig_fetched_at = now()
                           where restaurant_id = %s""",
                        (bd.get("biography"), bd.get("website"), rid))
            saved = 0
            for m in bd.get("media", {}).get("data", []):
                posted = datetime.strptime(m["timestamp"], "%Y-%m-%dT%H:%M:%S%z")
                if posted < cutoff or m["id"] in seen:
                    continue   # too old for a current deal, or the AI has already read it
                cur.execute(UPSERT, (
                    m["id"], rid, handle, m.get("media_type"), m.get("caption"), m.get("permalink"),
                    m.get("media_url"), m.get("thumbnail_url"),
                    Jsonb(m["children"]) if m.get("children") else None,
                    m.get("timestamp"), m.get("like_count"), m.get("comments_count"),
                ))
                saved += 1
            conn.commit()
            print(f"{handle}: {saved} new or waiting posts")

if __name__ == "__main__":
    main()