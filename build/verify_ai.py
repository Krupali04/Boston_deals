from common import ai
from common.db import get_conn
from common.utils import page_text, address_on_page, domain

MAX_SEARCHES = 2   # keep the same as in find_missing_ai.py

def forget_handle(cur, rid, handle):
    """Remove posts (and any deals) collected from a wrong Instagram account."""
    cur.execute("""delete from deals_extracted where source_type = 'instagram' and source_id in
                   (select post_id from raw_ig_posts where restaurant_id = %s and ig_handle = %s)""", (rid, handle))
    cur.execute("delete from raw_ig_posts where restaurant_id = %s and ig_handle = %s", (rid, handle))

def forget_website(cur, rid, website):
    """Remove snippets (and any deals) collected from a wrong website."""
    pattern = f"%{domain(website)}%"
    cur.execute("""delete from deals_extracted where source_type = 'website' and source_id in
                   (select id::text from raw_web_pages where restaurant_id = %s and url like %s)""", (rid, pattern))
    cur.execute("delete from raw_web_pages where restaurant_id = %s and url like %s", (rid, pattern))

def main():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""select restaurant_id, name, address, zip, website, instagram_handle, ig_bio, ig_website,
                              ai_search_attempts
                       from dim_restaurants
                       where verified = 'unchecked' and (website is not null or instagram_handle is not null)""")
        for rid, name, address, zip_, website, handle, bio, ig_website, attempts in cur.fetchall():
            text = page_text(website) if website else ""

            # Cheap checks first: address on the site; Instagram profile links back to the same site
            site_ok = bool(website) and address_on_page(address, text)
            ig_ok = bool(handle and website and ig_website) and domain(website) == domain(ig_website)

            if (site_ok or not website) and (ig_ok or not handle):
                v = ai.Verdict(website_match="yes" if website else "none",
                               website_reason="address found on website" if website else "no website",
                               instagram_match="yes" if handle else "none",
                               instagram_reason="profile links to the same website" if handle else "no handle")
            else:
                try:
                    v = ai.verify_match(name, address, zip_, website, text[:3000], handle, bio, ig_website)
                except ai.QuotaExceeded:
                    print("AI limit reached — the remaining restaurants will be checked next run.")
                    break
                if site_ok:      # keep the cheap check's answer where it was sure
                    v.website_match, v.website_reason = "yes", "address found on website"
                if ig_ok:
                    v.instagram_match, v.instagram_reason = "yes", "profile links to the same website"

            reason = (f"website: {v.website_match} ({v.website_reason}); "
                      f"instagram: {v.instagram_match} ({v.instagram_reason})")
            web_wrong = bool(website) and v.website_match == "no"
            ig_wrong = bool(handle) and v.instagram_match == "no"

            if web_wrong or ig_wrong:
                # Clear only the wrong part, remember it as known-wrong, and queue a new search
                wrong = []
                if web_wrong:
                    forget_website(cur, rid, website)
                    wrong.append(f"web:{domain(website)}")
                if ig_wrong:
                    forget_handle(cur, rid, handle)
                    wrong.append(f"ig:{handle.lower()}")
                status = "needs_review" if attempts >= MAX_SEARCHES else "unchecked"
                cur.execute(
                    """update dim_restaurants set
                         website = case when %s then null else website end,
                         instagram_handle = case when %s then null else instagram_handle end,
                         ig_bio = case when %s then null else ig_bio end,
                         ig_website = case when %s then null else ig_website end,
                         ig_fetched_at = case when %s then null else ig_fetched_at end,
                         rejected_links = coalesce(rejected_links, '{}') || %s::text[],
                         verified = %s, verify_reason = %s,
                         ai_search_attempted_at = null,
                         updated_at = now()
                       where restaurant_id = %s""",
                    (web_wrong, ig_wrong, ig_wrong, ig_wrong, ig_wrong, wrong, status, reason, rid),
                )
                print(f"{name}: cleared {', '.join(wrong)} → {status}")
            else:
                checked = [m for m in (v.website_match, v.instagram_match) if m != "none"]
                status = "yes" if all(m == "yes" for m in checked) else "unsure"
                cur.execute("update dim_restaurants set verified = %s, verify_reason = %s where restaurant_id = %s",
                            (status, reason, rid))
                print(f"{name}: {status} — {reason}")
            conn.commit()

if __name__ == "__main__":
    main()