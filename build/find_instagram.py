import requests
from common.db import get_conn
from common.utils import extract_ig_handle, UA

def main():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""select restaurant_id, website from dim_restaurants
                       where website is not null and instagram_handle is null and verified <> 'no'""")
        rows = cur.fetchall()
        print(f"Checking {len(rows)} websites for an Instagram link")
        found = 0
        for rid, website in rows:
            url = website if website.startswith("http") else "https://" + website
            try:
                html = requests.get(url, headers=UA, timeout=15).text
            except requests.RequestException as e:
                print(f"Could not open {url}: {type(e).__name__}")
                continue
            handle = extract_ig_handle(html)
            if handle:
                cur.execute("""update dim_restaurants
                               set instagram_handle = %s, found_by = 'website', updated_at = now()
                               where restaurant_id = %s and instagram_handle is null""", (handle, rid))
                conn.commit()
                found += 1
                print(f"{website}: @{handle}")
        print(f"\nHandles found on websites: {found}")

if __name__ == "__main__":
    main()