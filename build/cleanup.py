from common.db import get_conn

CLEANUP_SQL = {
    "Instagram posts with no deal": """
        delete from raw_ig_posts p
        where processed and fetched_at < now() - interval '7 days'
          and not exists (select 1 from deals_extracted d
                          where d.source_type = 'instagram' and d.source_id = p.post_id)""",
    "Instagram posts never read and now older than 30 days": """
        delete from raw_ig_posts
        where not processed and posted_at < now() - interval '30 days'""",
    "Website snippets with no deal": """
        delete from raw_web_pages p
        where processed and fetched_at < now() - interval '7 days'
          and not exists (select 1 from deals_extracted d
                          where d.source_type = 'website' and d.source_id = p.id::text)""",
    "RSS items with no deal": """
        delete from raw_rss_items p
        where processed and fetched_at < now() - interval '7 days'
          and not exists (select 1 from deals_extracted d
                          where d.source_type = 'rss' and d.source_id = p.link)""",
    # Safe to forget: the Instagram collector ignores posts older than 30 days anyway
    "Old Instagram IDs in seen_posts": """
        delete from seen_posts
        where source_type = 'instagram' and seen_at < now() - interval '60 days'""",
}

def main():
    with get_conn() as conn, conn.cursor() as cur:
        for label, sql in CLEANUP_SQL.items():
            cur.execute(sql)
            print(f"{label}: deleted {cur.rowcount} rows")

if __name__ == "__main__":
    main()