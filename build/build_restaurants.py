import re
from common.db import get_conn

DRY_RUN = False    # first run: only print what would be kept/skipped; set to False to write

# Column names in Boston's food license data
NAME_FIELD = "dbaname"          # name on the sign (often empty)
ALT_NAME_FIELD = "businessname" # legal/operator name, used when dbaname is empty
ADDRESS_FIELD = "address"
ZIP_FIELD = "zip"
LAT_FIELD = "latitude"
LON_FIELD = "longitude"

# Words that mean a place inside a university, hospital, hall, or hotel, or a cafeteria/kiosk.
# Checked against BOTH the sign name and the operator name ("Starbucks" run by a university is skipped).
SKIP_PATTERNS = [
    r"universit", r"\bcollege\b", r"\bschool\b", r"\bacademy\b", r"\bcampus\b", r"dining hall",
    r"residence hall", r"\bdorm", r"\bhospital\b", r"\bmedical\b", r"\bclinic\b", r"health care",
    r"\bhotel\b", r"\bmarriott\b", r"\bhilton\b", r"\bsheraton\b", r"\bwestin\b", r"\bhyatt\b",
    r"four seasons", r"\bfairmont\b", r"symphony hall", r"concert hall", r"cafeteria", r"\bkiosk\b",
    r"concession", r"\bportable\b", r"\bcart\b", r"\bemployee", r"\bstaff\b", r"\bfloor\b", r"\bfl\b",
    r"\d+(st|nd|rd|th) fl", r"\baramark\b", r"\bsodexo\b", r"compass group", r"\blevy\b", r"\bB U\b"
]
# Food words that protect a sign name even when a skip word appears ("College Pizza").
KEEP_PATTERNS = [
    r"pizza", r"\btea\b", r"bakery", r"\bgrill\b", r"kitchen", r"\btaqueria\b", r"\bdeli\b",
    r"house of", r"eataly", r"\blicks\b",
]
SKIP_RE = re.compile("|".join(SKIP_PATTERNS), re.I)
KEEP_RE = re.compile("|".join(KEEP_PATTERNS), re.I)

def to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

def norm_zip(value):
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[:5].zfill(5) if digits else ""

def decide(sign_name, operator_name):
    """Return (keep?, reason)."""
    skip = SKIP_RE.search(f"{sign_name} | {operator_name}")
    if not skip:
        return True, ""
    keep = KEEP_RE.search(sign_name)
    if keep:
        return True, f"has '{skip.group(0)}' but protected by '{keep.group(0)}'"
    return False, f"matched '{skip.group(0)}'"

def main():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""select license_key, payload from raw_licenses
                       where snapshot_date = (select max(snapshot_date) from raw_licenses)""")
        licenses = cur.fetchall()
        print(f"{len(licenses)} licenses in the latest snapshot")

        kept, skipped = [], []
        for key, lic in licenses:
            sign = lic.get(NAME_FIELD) or lic.get(ALT_NAME_FIELD) or ""
            operator = lic.get(ALT_NAME_FIELD) or ""
            ok, why = decide(sign, operator)
            if ok:
                kept.append((key, lic, sign))
                if why:
                    print(f"KEPT  {sign} — {why}")
            else:
                skipped.append(sign)
                print(f"SKIP  {sign} ({operator}) — {why}")
        print(f"\n{len(kept)} restaurants kept, {len(skipped)} skipped")

        if DRY_RUN:
            print("Dry run only. Review the lists, adjust the patterns, then set DRY_RUN = False.")
            return

        for key, lic, sign in kept:
            cur.execute(
                """insert into dim_restaurants (license_key, name, address, zip, lat, lon)
                   values (%s, %s, %s, %s, %s, %s)
                   on conflict (license_key) do update set
                     name = excluded.name,
                     address = excluded.address,
                     zip = excluded.zip,
                     lat = coalesce(excluded.lat, dim_restaurants.lat),
                     lon = coalesce(excluded.lon, dim_restaurants.lon),
                     updated_at = now()""",
                (key, sign, lic.get(ADDRESS_FIELD), norm_zip(lic.get(ZIP_FIELD)),
                 to_float(lic.get(LAT_FIELD)), to_float(lic.get(LON_FIELD))),
            )

        # Restaurants whose license disappeared from the city list are set aside (safety check:
        # only when the latest snapshot looks complete).
        if len(licenses) > 100:
            cur.execute("""update dim_restaurants set verified = 'no', verify_reason = 'no longer in license list'
                           where verified <> 'no' and license_key <> all(%s)""",
                        ([key for key, _, _ in kept],))
            print(f"{cur.rowcount} restaurants set aside: no longer in the license list")
        print("Saved.")

if __name__ == "__main__":
    main()