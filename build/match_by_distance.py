import math
from rapidfuzz import fuzz
from build.match_restaurants import norm, coords
from common.db import get_conn
from common.utils import extract_ig_handle

MAX_METERS = 50
MIN_NAME_SCORE = 65

def meters(lat1, lon1, lat2, lon2):
    """Straight-line distance between two points on Earth, in meters."""
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))

def main():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select osm_id from dim_restaurants where osm_id is not null")
        taken = {row[0] for row in cur.fetchall()}

        cur.execute("select osm_id, payload from raw_osm_places")
        places = []
        for osm_id, place in cur.fetchall():
            lat, lon = coords(place)
            if osm_id not in taken and lat is not None and lon is not None:
                places.append((osm_id, place, float(lat), float(lon)))

        cur.execute("""select restaurant_id, name, lat, lon from dim_restaurants
                       where osm_id is null and lat is not null and lon is not null and verified <> 'no'""")
        matched = 0
        for rid, name, lat, lon in cur.fetchall():
            best, best_score = None, 0
            for osm_id, place, plat, plon in places:
                if osm_id in taken or meters(lat, lon, plat, plon) > MAX_METERS:
                    continue
                score = fuzz.token_set_ratio(norm(name), norm(place.get("tags", {}).get("name")))
                if score > best_score:
                    best, best_score = (osm_id, place), score
            if not best or best_score < MIN_NAME_SCORE:
                continue

            osm_id, place = best
            tags = place.get("tags", {})
            website = tags.get("website") or tags.get("contact:website")
            handle = extract_ig_handle(tags.get("contact:instagram"))
            cur.execute(
                """update dim_restaurants set
                     osm_id = %s,
                     website = coalesce(website, %s),
                     instagram_handle = coalesce(instagram_handle, %s),
                     found_by = coalesce(found_by, %s),
                     updated_at = now()
                   where restaurant_id = %s""",
                (osm_id, website, handle, "osm_distance" if (website or handle) else None, rid),
            )
            taken.add(osm_id)
            matched += 1
            print(f"{name}  ↔  {tags.get('name')}  (score {best_score})")
        print(f"{matched} more restaurants matched by location")

if __name__ == "__main__":
    main()