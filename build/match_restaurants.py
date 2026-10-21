import re
from rapidfuzz import fuzz
from common.db import get_conn
from common.utils import extract_ig_handle

# Column names in Boston's food license data
NAME_FIELD = "dbaname"          # name on the sign (often empty)
ALT_NAME_FIELD = "businessname" # used when dbaname is empty
ADDRESS_FIELD = "address"
ZIP_FIELD = "zip"
LAT_FIELD = "latitude"          # the license data has its own coordinates
LON_FIELD = "longitude"
MATCH_THRESHOLD = 90

STOPWORDS = {"the", "inc", "llc", "corp", "co", "company", "ltd"}

def norm(s):
    s = re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())
    return " ".join(w for w in s.split() if w not in STOPWORDS)

def house_number(addr):
    m = re.match(r"\s*(\d+)", addr or "")
    return m.group(1) if m else None

def to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

def coords(place):
    if "lat" in place:
        return place["lat"], place["lon"]
    center = place.get("center", {})
    return center.get("lat"), center.get("lon")

def main():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""select license_key, payload from raw_licenses
                       where snapshot_date = (select max(snapshot_date) from raw_licenses)""")
        licenses = cur.fetchall()
        cur.execute("select osm_id, payload from raw_osm_places")
        places = cur.fetchall()

        matched = 0
        for license_key, lic in licenses:
            name = lic.get(NAME_FIELD) or lic.get(ALT_NAME_FIELD) or ""
            addr = lic.get(ADDRESS_FIELD) or ""
            best, best_score = None, 0
            for osm_id, place in places:
                tags = place.get("tags", {})
                score = fuzz.token_set_ratio(norm(name), norm(tags.get("name")))
                if house_number(addr) and house_number(addr) == tags.get("addr:housenumber"):
                    score += 10
                if score > best_score:
                    best, best_score = (osm_id, place), score

            osm_id, website, handle = None, None, None
            lat, lon = to_float(lic.get(LAT_FIELD)), to_float(lic.get(LON_FIELD))   # license coordinates first
            if best and best_score >= MATCH_THRESHOLD:
                matched += 1
                osm_id, place = best
                tags = place.get("tags", {})
                website = tags.get("website") or tags.get("contact:website")
                handle = extract_ig_handle(tags.get("contact:instagram"))
                if lat is None or lon is None:
                    lat, lon = coords(place)

            cur.execute(
                """insert into dim_restaurants
                     (license_key, name, address, zip, lat, lon, osm_id, website, instagram_handle, found_by)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   on conflict (license_key) do update set
                     osm_id = coalesce(dim_restaurants.osm_id, excluded.osm_id),
                     lat = coalesce(dim_restaurants.lat, excluded.lat),
                     lon = coalesce(dim_restaurants.lon, excluded.lon),
                     website = coalesce(dim_restaurants.website, excluded.website),
                     instagram_handle = coalesce(dim_restaurants.instagram_handle, excluded.instagram_handle),
                     updated_at = now()""",
                (license_key, name, addr, str(lic.get(ZIP_FIELD) or "").zfill(5),
                 lat, lon, osm_id, website, handle, "osm" if (website or handle) else None),
            )
        print(f"{matched} of {len(licenses)} licenses matched to OpenStreetMap")

if __name__ == "__main__":
    main()