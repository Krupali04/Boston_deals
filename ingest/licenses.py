from collections import Counter
import requests
from psycopg.types.json import Jsonb
from common.db import get_conn

RESOURCE_ID = "f1e13724-284d-478c-b8bc-ef042aa5b70b"
API = "https://data.boston.gov/api/3/action/datastore_search"

INSPECT = False            # first run: print columns and value counts, then switch to False

# Column names in Boston's food license data
ZIP_FIELD = "zip"
STATUS_FIELD = "licstatus"
ACTIVE_VALUE = "Active"
CATEGORY_FIELD = "descript"                 # the license type, e.g. "Eating & Drinking"
KEEP_CATEGORIES = {"Eating & Drinking","Eating & Drinking w/ Take Out"}     # add the other restaurant/café types you see in the inspect output

# The data has no license-number column, so each license gets a key built from these columns.
# The date a license was added doesn't change, so name + address + that date identifies a license.
KEY_FIELDS = ("businessname", "address", "license_add_dt_tm")

TARGET_ZIPS = {"02116", "02199", "02215", "02115"}   # Back Bay + Fenway

def fetch_all():
    records, offset = [], 0
    while True:
        r = requests.get(API, params={"resource_id": RESOURCE_ID, "limit": 1000, "offset": offset}, timeout=60)
        r.raise_for_status()
        batch = r.json()["result"]["records"]
        if not batch:
            break
        records.extend(batch)
        offset += len(batch)
    return records

def norm_zip(value):
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[:5].zfill(5) if digits else ""

def license_key(r):
    return "|".join(str(r.get(f) or "").strip().lower() for f in KEY_FIELDS)

def keep(r):
    if norm_zip(r.get(ZIP_FIELD)) not in TARGET_ZIPS:
        return False
    if str(r.get(STATUS_FIELD, "")).strip().lower() != ACTIVE_VALUE.lower():
        return False
    if CATEGORY_FIELD and r.get(CATEGORY_FIELD) not in KEEP_CATEGORIES:
        return False
    return True

def main():
    records = fetch_all()
    print(f"Downloaded {len(records)} records")
    if INSPECT:
        print("Columns:", list(records[0].keys()))
        print("Example row:", records[0])
        for field in (STATUS_FIELD, CATEGORY_FIELD, "licensecat"):
            print(f"\nValues in {field}:")
            for value, count in Counter(r.get(field) for r in records).most_common():
                print(f"  {value!r}: {count}")
        return
    rows = [r for r in records if keep(r)]
    print(f"Keeping {len(rows)} active licenses in target ZIPs")
    with get_conn() as conn, conn.cursor() as cur:
        for r in rows:
            cur.execute(
                """insert into raw_licenses (license_key, payload) values (%s, %s)
                   on conflict (snapshot_date, license_key) do nothing""",
                (license_key(r), Jsonb(r)),
            )
    print("Saved.")

if __name__ == "__main__":
    main()