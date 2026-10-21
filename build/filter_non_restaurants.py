import re
from common.db import get_conn

DRY_RUN = False    # first run: only print the list; set to False once the list looks right

# Words that usually mean a cafeteria, kiosk, or concession stand rather than a public restaurant.
# Review the dry-run list and add or remove patterns as needed.
SKIP_PATTERNS = [
    r"\bfloor\b", r"\bfl\b", r"\d+(st|nd|rd|th) fl", r"cafeteria", r"\bhospital\b", r"\bmedical\b",
    r"\bclinic\b", r"universit", r"\bcollege\b", r"\bschool\b", r"dining hall", r"\bkiosk\b",
    r"concession", r"\bportable\b", r"\bcart\b", r"\bemployee", r"\bstaff\b",
    r"\baramark\b", r"\bsodexo\b", r"compass group", r"\blevy\b", r"\bhall\b",r"hall", r"\bstand\b",
    r"\bdining\b",r"@",
]

KEEP_PATTERNS = [
    r"pizza", r"\btea\b", r"bakery", r"\bgrill\b", r"kitchen", r"\btaqueria\b", r"\bdeli\b",
    r"house of", r"eataly", r"\blicks\b",
]

SKIP_RE = re.compile("|".join(SKIP_PATTERNS), re.I)
KEEP_RE = re.compile("|".join(KEEP_PATTERNS), re.I)

def main():
    with get_conn() as conn, conn.cursor() as cur:
        # Only places with no public footprint: no OSM match, no website, no Instagram
        cur.execute("""select restaurant_id, name, address from dim_restaurants
                       where verified = 'unchecked'
                         and osm_id is null and website is null and instagram_handle is null""")
        hits = []
        for rid, name, address in cur.fetchall():
            skip = SKIP_RE.search(name or "")
            if not skip:
                continue
            keep = KEEP_RE.search(name or "")
            if keep:
                print(f"KEPT    {rid}: {name} — has '{skip.group(0)}' but protected by '{keep.group(0)}'")
                continue
            hits.append((rid, name, address, skip.group(0)))
            print(f"FLAG    {rid}: {name} ({address}) — matched '{skip.group(0)}'")
        print(f"\n{len(hits)} places would be marked as non-restaurants")
        if DRY_RUN:
            print("Dry run only. Review the list, adjust the patterns, then set DRY_RUN = False.")
            return
        for rid, _, _, word in hits:
            cur.execute("update dim_restaurants set verified = 'no', verify_reason = %s where restaurant_id = %s",
                        (f"auto: non-restaurant keyword '{word}'", rid))
        print("Marked.")

if __name__ == "__main__":
    main()