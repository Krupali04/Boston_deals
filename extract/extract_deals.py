from datetime import date
import requests
from common import ai
from common.db import get_conn
from common.utils import looks_like_deal

class UrlExpired(Exception):
    pass

def download(url):
    """Load an image or video into memory only; it is never saved to disk or the database."""
    r = requests.get(url, timeout=60)
    if r.status_code in (403, 404, 410):
        raise UrlExpired(url)
    r.raise_for_status()
    return r.content

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
        save_deals(cur, "instagram", post_id, rid, deals)
        cur.execute("update raw_ig_posts set processed = true, status = 'done' where post_id = %s", (post_id,))
        mark_seen(cur, "instagram", post_id)
        conn.commit()
        print(f"IG {post_id}: {len(deals)} deals")

def do_websites(conn, cur):
    cur.execute("select id, restaurant_id, url, content_hash, snippets from raw_web_pages where not processed")
    for page_id, rid, url, content_hash, snippets in cur.fetchall():
        deals = ai.extract_deals(snippets)
        save_deals(cur, "website", str(page_id), rid, deals)
        cur.execute("update raw_web_pages set processed = true where id = %s", (page_id,))
        mark_seen(cur, "website", f"{url}|{content_hash}")
        conn.commit()
        print(f"Web {page_id}: {len(deals)} deals")

def do_rss(conn, cur):
    cur.execute("select link, title, summary from raw_rss_items where not processed")
    for link, title, summary in cur.fetchall():
        deals = ai.extract_deals(f"{title}\n\n{summary}")
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

if __name__ == "__main__":
    main()