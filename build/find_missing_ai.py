from common import ai
from common.db import get_conn
from common.search import search, SearchLimit
from common.utils import extract_ig_handle, page_text, address_on_page, domain
from build.match_restaurants import norm
from ingest.instagram import discover, RATE_LIMIT_CODES

MAX_PER_RUN = 1000        # restaurants per run (about 2 Serper searches each)
MAX_SEARCHES = 2        # after this many searches, a restaurant stops being searched automatically
RETRY_AFTER_DAYS = 30   # a restaurant where nothing was found is searched again after this long
SITE_CANDIDATES = 3     # how many top website results to try

BLOCKED_SITES = ("yelp.", "doordash.", "grubhub.", "ubereats.", "seamless.", "tripadvisor.",
                 "facebook.", "instagram.", "google.", "opentable.", "yellowpages.", "mapquest.",
                 "tiktok.", "x.com", "twitter.", "linkedin.", "wikipedia.")

class InstagramLimit(Exception):
    pass

def known_wrong(rejected_links):
    """Split saved rejections ("web:domain" / "ig:handle") into two sets."""
    sites = {v[4:] for v in rejected_links if v.startswith("web:")}
    handles = {v[3:] for v in rejected_links if v.startswith("ig:")}
    return sites, handles

def check_website(url, name, address):
    """The site must not be a directory, must load, and must mention the restaurant's name or address."""
    if any(b in url.lower() for b in BLOCKED_SITES):
        return False
    text = page_text(url)
    return bool(text) and (address_on_page(address, text) or (norm(name) and norm(name) in norm(text)))

def check_instagram(handle):
    """Return (bio, profile website) if the handle is a business account, otherwise None."""
    data = discover(handle)
    if "error" in data:
        if data["error"].get("code") in RATE_LIMIT_CODES:
            raise InstagramLimit()
        return None
    bd = data["business_discovery"]
    return bd.get("biography"), bd.get("website")

def handles_in(results, bad_handles):
    found = []
    for r in results:
        if "instagram.com/" in r["link"].lower():
            h = extract_ig_handle(r["link"])
            if h and h.lower() not in bad_handles and h not in found:
                found.append(h)
    return found

def find_links(name, address, zip_code, need_site, need_handle, bad_sites, bad_handles):
    """Rules first; Gemini only when the results are unclear. Returns site, handle, bio, ig_site, used_ai."""
    results = search(f'"{name}" {address} Boston')
    ig_results = search(f'"{name}" Boston instagram') if need_handle else []
    all_results = results + ig_results
    site = handle = bio = ig_site = None
    unclear = False

    # Website rule: the first non-directory result (not known to be wrong) that passes the check
    if need_site:
        candidates = [r["link"] for r in results
                      if not any(b in r["link"].lower() for b in BLOCKED_SITES)
                      and domain(r["link"]) not in bad_sites][:SITE_CANDIDATES]
        site = next((link for link in candidates if check_website(link, name, address)), None)
        if site is None and candidates:
            unclear = True

    # Instagram rule: exactly one handle in the results → check it; several → unclear
    handles = handles_in(all_results, bad_handles)
    if need_handle:
        if len(handles) == 1:
            info = check_instagram(handles[0])
            if info:
                handle, (bio, ig_site) = handles[0], info
        elif len(handles) > 1:
            unclear = True

    # Gemini picks only when the rules couldn't decide, and only from links that appeared in the results
    if unclear:
        pick = ai.pick_links(name, address, zip_code, all_results, sorted(bad_sites | bad_handles))
        result_links = {r["link"] for r in results}
        if need_site and site is None and pick.website in result_links and check_website(pick.website, name, address):
            site = pick.website
        h = extract_ig_handle(pick.instagram_handle or "")
        if need_handle and handle is None and h in handles:
            info = check_instagram(h)
            if info:
                handle, (bio, ig_site) = h, info
    return site, handle, bio, ig_site, unclear

def main():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""select restaurant_id, name, address, zip, website, instagram_handle, rejected_links
                       from dim_restaurants
                       where verified not in ('no', 'needs_review')
                         and (website is null or instagram_handle is null)
                         and ai_search_attempts < %s
                         and (ai_search_attempted_at is null
                              or ai_search_attempted_at < now() - make_interval(days => %s))
                       order by ai_search_attempted_at nulls first, restaurant_id
                       limit %s""", (MAX_SEARCHES, RETRY_AFTER_DAYS, MAX_PER_RUN))
        rows = cur.fetchall()
        print(f"Searching for {len(rows)} restaurants")
        for rid, name, address, zip_code, website, handle, rejected in rows:
            bad_sites, bad_handles = known_wrong(rejected or [])
            try:
                site, ig, bio, ig_site, used_ai = find_links(
                    name, address, zip_code, website is None, handle is None, bad_sites, bad_handles)
            except SearchLimit as e:
                print(f"Search stopped: {e}")
                break
            except ai.QuotaExceeded:
                print("Gemini limit reached — the rest will be searched on the next run.")
                break
            except InstagramLimit:
                print("Instagram rate limit reached — stopping for today.")
                break
            found = bool(site or ig)
            cur.execute(
                """update dim_restaurants set
                     website = coalesce(website, %s),
                     instagram_handle = coalesce(instagram_handle, %s),
                     ig_bio = coalesce(%s, ig_bio),
                     ig_website = coalesce(%s, ig_website),
                     found_by = coalesce(found_by, %s),
                     verified = case when %s then 'unchecked' else verified end,
                     ai_search_attempts = ai_search_attempts + 1,
                     ai_search_attempted_at = now(),
                     updated_at = now()
                   where restaurant_id = %s""",
                (site, ig, bio, ig_site, ("search+ai" if used_ai else "search") if found else None, found, rid),
            )
            conn.commit()
            print(f"{name}: website={site or '-'}  instagram={ig or '-'}{'  (Gemini helped)' if used_ai else ''}")

if __name__ == "__main__":
    main()