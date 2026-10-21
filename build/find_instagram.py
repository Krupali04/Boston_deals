import requests
from common.db import get_conn
from common.utils import extract_ig_handle, UA

def main():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""select restaurant_id, website from dim_restaurants
                       where website is not null and instagram_handle is null""")
        for rid, website in cur.fetchall():
            url = website if website.startswith("http") else "https://" + website
            try:
                html = requests.get(url, headers=UA, timeout=20).text
            except requests.RequestException as e:
                print(f"Could not open {url}: {e}")
                continue
            handle = extract_ig_handle(html)
            if handle:
                cur.execute(
                    """update dim_restaurants
                       set instagram_handle = %s, found_by = coalesce(found_by, 'website_scan'), updated_at = now()
                       where restaurant_id = %s""",
                    (handle, rid),
                )
                print(f"{rid}: {handle}")

if __name__ == "__main__":
    main()