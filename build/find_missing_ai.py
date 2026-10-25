from common import ai
from common.db import get_conn
from common.search import search, SearchLimit
from common.utils import extract_ig_handle
from ingest.instagram import discover, RATE_LIMIT_CODES

MAX_PER_RUN = 1000       # effectively "all that still need a handle"
RETRY_AFTER_DAYS = 30    # a restaurant where nothing was found is searched again after this long

class InstagramLimit(Exception):
    pass

def handles_in(results):
    found = []
    for r in results:
        if "instagram.com/" in r["link"].lower():
            h = extract_ig_handle(r["link"])
            if h and h not in found:
                found.append(h)
    return found

def instagram_profile(handle):
    """('ok', bio, website) if it's a business account, ('missing', ...) if not, ('retry', ...) on timeout."""
    data = discover(handle)
    err = data.get("error")
    if err:
        if err.get("code") in RATE_LIMIT_CODES:
            raise InstagramLimit()
        if err.get("code") == "timeout":
            return "retry", None, None
        return "missing", None, None
    bd = data["business_discovery"]
    return "ok", bd.get("biography"), bd.get("website")

def main():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""select restaurant_id, name, address, zip from dim_restaurants
                       where instagram_handle is null and verified <> 'no'
                         and (ai_search_attempted_at is null
                              or ai_search_attempted_at < now() - make_interval(days => %s))
                       order by restaurant_id
                       limit %s""", (RETRY_AFTER_DAYS, MAX_PER_RUN))
        rows = cur.fetchall()
        print(f"Searching Instagram for {len(rows)} restaurants")
        for rid, name, address, zip_code in rows:
            handle = bio = ig_site = None
            confidence, reason = None, None
            try:
                results = search(f'"{name}" Boston instagram')
                candidates = handles_in(results)
                if candidates:
                    pick = ai.pick_handle(name, address, zip_code, results)
                    h = extract_ig_handle(pick.handle or "")
                    if h in candidates:
                        status, bio, ig_site = instagram_profile(h)
                        if status == "retry":
                            print(f"{name}: Instagram didn't answer — will retry next run")
                            continue
                        if status == "ok":
                            handle, confidence, reason = h, pick.confidence, pick.reason
            except SearchLimit as e:
                print(f"Search stopped: {e}")
                break
            except ai.QuotaExceeded:
                print("Gemini limit reached — the rest will be searched on the next run.")
                break
            except InstagramLimit:
                print("Instagram rate limit reached — stopping for now.")
                break

            low = handle is not None and confidence == "low"
            cur.execute(
                """update dim_restaurants set
                     instagram_handle = coalesce(instagram_handle, %s),
                     ig_bio = coalesce(%s, ig_bio),
                     ig_website = coalesce(%s, ig_website),
                     found_by = case when %s then 'search_ai' else found_by end,
                     verified = case when %s then 'needs_review' else verified end,
                     verify_reason = case when %s then %s else verify_reason end,
                     ai_search_attempted_at = now(),
                     updated_at = now()
                   where restaurant_id = %s""",
                (handle, bio, ig_site, handle is not None, low, low,
                 f"low-confidence Instagram pick: {reason}", rid),
            )
            conn.commit()
            tag = f"  [{confidence}]" if handle else ""
            print(f"{name}: {('@' + handle) if handle else '-'}{tag}")

if __name__ == "__main__":
    main()