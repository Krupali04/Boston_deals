import hashlib
import re
from urllib import robotparser
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
from common.db import get_conn
from common.utils import UA, DEAL_WORDS

LINK_WORDS = ("special", "promo", "deal", "offer", "happy-hour", "happy hour", "event", "discount")
MAX_EXTRA_PAGES = 3

def deal_snippets(soup):
    """Return only the text blocks that might contain a deal, each with its heading for context."""
    snippets, last_heading = [], ""
    for el in soup.find_all(["h1", "h2", "h3", "h4", "p", "li", "span", "div"]):
        if el.find(["p", "li", "div"]):      # skip containers; their children are checked instead
            continue
        text = " ".join(el.get_text(" ").split())
        if not text:
            continue
        if el.name in ("h1", "h2", "h3", "h4"):
            last_heading = text
        if DEAL_WORDS.search(text) and len(text) < 500:
            snippets.append(f"{last_heading}: {text}" if last_heading and last_heading != text else text)
    return list(dict.fromkeys(snippets))     # remove duplicates, keep order

def allowed(url):
    parts = urlparse(url)
    rp = robotparser.RobotFileParser(f"{parts.scheme}://{parts.netloc}/robots.txt")
    try:
        rp.read()
    except Exception:
        return True
    return rp.can_fetch(UA["User-Agent"], url)

def fetch(url):
    if not allowed(url):
        print(f"robots.txt disallows {url}")
        return None
    try:
        r = requests.get(url, headers=UA, timeout=15)
        r.raise_for_status()
        return BeautifulSoup(r.text, "html.parser")
    except requests.RequestException as e:
        print(f"Could not open {url}: {e}")
        return None

def save(cur, rid, url, soup, seen):
    snippets = deal_snippets(soup)
    if not snippets:
        return 0                              # nothing deal-like on this page: save nothing
    text = " · ".join(snippets)
    digest = hashlib.sha256(text.encode()).hexdigest()
    if f"{url}|{digest}" in seen:
        return 0                              # the AI already read exactly this text
    cur.execute("""insert into raw_web_pages (restaurant_id, url, content_hash, snippets)
                   values (%s, %s, %s, %s) on conflict (url, content_hash) do nothing""",
                (rid, url, digest, text))
    return cur.rowcount

def main():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select source_id from seen_posts where source_type = 'website'")
        seen = {row[0] for row in cur.fetchall()}
        cur.execute("select restaurant_id, website from dim_restaurants where website is not null and verified <> 'no'")
        for rid, website in cur.fetchall():
            home = website if website.startswith("http") else "https://" + website
            soup = fetch(home)
            if not soup:
                continue
            saved = save(cur, rid, home, soup, seen)
            extra = []
            for a in soup.find_all("a", href=True):
                label = (a.get_text() + " " + a["href"]).lower()
                link = urljoin(home, a["href"])
                if any(k in label for k in LINK_WORDS) and urlparse(link).netloc == urlparse(home).netloc:
                    if link not in extra and link != home:
                        extra.append(link)
            for link in extra[:MAX_EXTRA_PAGES]:
                page = fetch(link)
                if page:
                    saved += save(cur, rid, link, page, seen)
            conn.commit()
            print(f"{home}: {saved} new snippet sets")

if __name__ == "__main__":
    main()