import re
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup
from common import ai
from common.db import get_conn
from common.utils import UA

def page_text(url):
    try:
        html = requests.get(url if url.startswith("http") else "https://" + url, headers=UA, timeout=15).text
    except requests.RequestException:
        return ""
    return " ".join(BeautifulSoup(html, "html.parser").get_text(" ").split())

def address_on_page(address, text):
    m = re.match(r"\s*(\d+)\s+(\w+)", address or "")
    return bool(m) and f"{m.group(1)} {m.group(2)}".lower() in text.lower()

def domain(u):
    return urlparse(u if u.startswith("http") else "https://" + u).netloc.lower().removeprefix("www.")

def main():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""select restaurant_id, name, address, zip, website, instagram_handle, ig_bio, ig_website
                       from dim_restaurants
                       where verified = 'unchecked' and (website is not null or instagram_handle is not null)""")
        for rid, name, address, zip_, website, handle, bio, ig_website in cur.fetchall():
            text = page_text(website) if website else ""

            # Cheap check first: address on the site, and Instagram links back to the same site
            site_ok = bool(website) and address_on_page(address, text)
            ig_ok = (not handle) or (bool(website) and bool(ig_website) and domain(website) == domain(ig_website))
            if site_ok and ig_ok:
                match, reason = "yes", "address on website; Instagram links to same site"
            else:
                try:
                    verdict = ai.verify_match(name, address, zip_, text[:3000], bio, ig_website)
                except ai.QuotaExceeded:
                    print("AI limit reached — the remaining restaurants will be checked next run.")
                    break
                match, reason = verdict.match, verdict.reason

            cur.execute("update dim_restaurants set verified = %s, verify_reason = %s where restaurant_id = %s",
                        (match, reason, rid))
            conn.commit()
            print(f"{name}: {match} — {reason}")

if __name__ == "__main__":
    main()