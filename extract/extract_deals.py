import time
from datetime import date
import requests
from common import ai
from common.db import get_conn
from common.utils import looks_like_deal

class UrlExpired(Exception):
    pass

DOWNLOAD_RETRIES = 3

def download(url):
    """Load an image or video into memory only; it is never saved to disk or the database.
    A link that is expired, dead, or keeps failing raises UrlExpired: the post is skipped and gets
    fresh links on the next Instagram collection."""
    for attempt in range(1, DOWNLOAD_RETRIES + 1):
        try:
            r = requests.get(url, timeout=60)
        except requests.RequestException:          # connection problem, timeout, or an address that doesn't exist
            if attempt == DOWNLOAD_RETRIES:
                raise UrlExpired(url)
            time.sleep(5 * attempt)
            continue
        if r.status_code in (403, 404, 410):
            raise UrlExpired(url)                  # the link has expired
        if r.status_code >= 500 and attempt < DOWNLOAD_RETRIES:
            time.sleep(5 * attempt)                # Instagram's image server had a hiccup
            continue
        if r.status_code != 200:
            raise UrlExpired(url)
        return r.content
    raise UrlExpired(url)

def image_urls(media_type, media_url, thumbnail_url, children):
    if media_type == "VIDEO":
        return [thumbnail_url] if thumbnail_url else []
    if media_type == "CAROUSEL_ALBUM" and children:
        urls = []
        for c in children.get("data", [])[:5]:
            urls.append(c.get("thumbnail_url") if c.get("media_type") == "VIDEO" else c.get("media_url"))
        return [u for u in urls if u]
    return [media_url] if media_url else []

def to_date(value):
    try:
        return date.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None

def save_deals(cur, source_type, source_id, restaurant_id, deals):
    for d in deals:
        cur.execute(
            """insert into deals_extracted (source_type, source_id, restaurant_id, restaurant_name_raw, offer_type,
                   item, value, conditions, code, code_kind, valid_from, valid_until, confidence)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (source_type, source_id, restaurant_id, d.restaurant_name, d.offer_type,
             d.item, d.value, d.conditions, d.code, d.code_kind,
             to_date(d.valid_from), to_date(d.valid_until), d.confidence),
        )

def mark_seen(cur, source_type, source_id):
    """Record that the AI has read this item, so collectors never save it again."""
    cur.execute("""insert into seen_posts (source_type, source_id) values (%s, %s)
                   on conflict do nothing""", (source_type, source_id))

def read_post(media_type, caption, media_url, thumbnail_url, children):
    """Return the deals found in one Instagram post."""
    images = [download(u) for u in image_urls(media_type, media_url, thumbnail_url, children)]
    if not images and not caption:
        return []
    deals = ai.extract_deals(caption, images=images)

    # Reels: if the caption or cover hints at a deal, let Gemini watch the whole video for the details
    if media_type == "VIDEO" and media_url and ai.can_watch_video() and (deals or looks_like_deal(caption)):
        try:
            deals = ai.extract_deals(caption, images=images, video=download(media_url)) or deals
        except ai.QuotaExceeded:
            raise
        except Exception as e:     # a failed video read shouldn't lose the cover-image result
            print(f"Video read failed, keeping the cover-image result: {e}")
    return deals

MAX_FAILURES_IN_A_ROW = 5   # if Gemini fails this many items in a row, it's probably down: stop

class GeminiDown(Exception):
    pass

_failures_in_a_row = 0

def note_failure(label, err):
    """An item couldn't be read right now: leave it unread, and stop if Gemini keeps failing."""
    global _failures_in_a_row
    _failures_in_a_row += 1
    print(f"{label}: Gemini didn't answer ({err}) — left unread, will retry next run")
    if _failures_in_a_row >= MAX_FAILURES_IN_A_ROW:
        raise GeminiDown()

def note_success():
    global _failures_in_a_row
    _failures_in_a_row = 0

def do_instagram(conn, cur):
    cur.execute("""select post_id, restaurant_id, media_type, caption, media_url, thumbnail_url, children
                   from raw_ig_posts where not processed and status = 'new'""")
    for post_id, rid, media_type, caption, media_url, thumb, children in cur.fetchall():
        try:
            deals = read_post(media_type, caption, media_url, thumb, children)
        except UrlExpired:
            cur.execute("update raw_ig_posts set status = 'url_expired' where post_id = %s", (post_id,))
            conn.commit()
            print(f"IG {post_id}: link expired, will retry after the next collection")
            continue
        except ai.AITryLater as e:
            note_failure(f"IG {post_id}", e)
            continue
        note_success()
        save_deals(cur, "instagram", post_id, rid, deals)
        cur.execute("update raw_ig_posts set processed = true, status = 'done' where post_id = %s", (post_id,))
        mark_seen(cur, "instagram", post_id)
        conn.commit()
        print(f"IG {post_id}: {len(deals)} deals")

def do_websites(conn, cur):
    cur.execute("select id, restaurant_id, url, content_hash, snippets from raw_web_pages where not processed")
    for page_id, rid, url, content_hash, snippets in cur.fetchall():
        try:
            deals = ai.extract_deals(snippets)
        except ai.AITryLater as e:
            note_failure(f"Web {page_id}", e)
            continue
        note_success()
        save_deals(cur, "website", str(page_id), rid, deals)
        cur.execute("update raw_web_pages set processed = true where id = %s", (page_id,))
        mark_seen(cur, "website", f"{url}|{content_hash}")
        conn.commit()
        print(f"Web {page_id}: {len(deals)} deals")

def do_rss(conn, cur):
    cur.execute("select link, title, summary from raw_rss_items where not processed")
    for link, title, summary in cur.fetchall():
        try:
            deals = ai.extract_deals(f"{title}\n\n{summary}")
        except ai.AITryLater as e:
            note_failure("RSS", e)
            continue
        note_success()
        save_deals(cur, "rss", link, None, deals)
        cur.execute("update raw_rss_items set processed = true where link = %s", (link,))
        mark_seen(cur, "rss", link)
        conn.commit()
        print(f"RSS: {len(deals)} deals — {title}")

def main():
    with get_conn() as conn, conn.cursor() as cur:
        try:
            do_instagram(conn, cur)
            do_websites(conn, cur)
            do_rss(conn, cur)
        except ai.QuotaExceeded:
            print("AI limit reached — unread items stay in the queue for the next run.")
        except GeminiDown:
            print(f"Gemini failed {MAX_FAILURES_IN_A_ROW} items in a row and seems to be down — "
                  "stopping; unread items will be retried next run.")

if __name__ == "__main__":
    main()