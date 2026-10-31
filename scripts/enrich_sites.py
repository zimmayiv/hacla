"""Look up each address on the LA County Assessor's public parcel layer and LA City council districts.

Input: JSON list of {key, address, zip}. Output: JSON {key: {...parcel facts}}. Caches results, so reruns are incremental.
"""
import json, os, re, sys, time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

PARCELS = "https://public.gis.lacounty.gov/public/rest/services/LACounty_Cache/LACounty_Parcel/MapServer/0/query"
FIELDS = "AIN,APN,SitusFullAddress,SitusHouseNo,SitusStreet,SitusZIP,TaxRateCity,UseDescription,UseType,YearBuilt1,EffectiveYear1,Units1,SQFTmain1,Roll_Year,Roll_LandBaseYear,Roll_ImpBaseYear,CENTER_LAT,CENTER_LON"
STOP = {"N", "S", "E", "W", "NORTH", "SOUTH", "EAST", "WEST", "ST", "STREET", "AVE", "AVENUE", "BLVD", "BOULEVARD", "DR", "DRIVE", "PL", "PLACE", "ET", "AL", "ALL", "ST.", "AVE.", "BLVD.", "DR.", "PL."}
ORD = {"1ST", "2ND", "3RD"}


def street_key(address):
    """(house number, primary street word) from an address such as '12100 West Sheldon Street'."""
    m = re.match(r"\s*(\d+)(?:-\d+)?(?:\s+\d/\d)?\s+(.*)", address)
    if not m:
        return None, None
    words = [w for w in re.sub(r"[.,]", " ", m.group(2).upper()).split() if w not in STOP]
    return m.group(1), words[0] if words else None


SUFFIXES = {"ST", "AVE", "BLVD", "DR", "PL", "RD", "WAY", "CT", "LN", "TER", "HWY", "CIR", "PARK", "CLAIR", "BLVD,", "UNIT", "APT", "#"}
DIRS = {"N": "N", "NORTH": "N", "S": "S", "SOUTH": "S", "E": "E", "EAST": "E", "W": "W", "WEST": "W"}


def listed_direction(address):
    m = re.match(r"\s*\d+(?:-\d+)?(?:\s+\d/\d)?\s+([A-Za-z.]+)", address)
    return DIRS.get(m.group(1).rstrip(".").upper()) if m else None


def plausible(parcel, house, word, direction, zipcode):
    """Reject same-numbered parcels elsewhere: the street word must match exactly and directions must not conflict."""
    full = (parcel.get("SitusFullAddress") or "").upper()
    tokens = full.split()
    if not tokens or tokens[0] != house:
        return False
    head = full.split(" LOS ANGELES ")[0]
    words = head.split()
    if word not in words:
        return False
    following = words[words.index(word) + 1:]
    if following and following[0] not in SUFFIXES:  # "HARBOR" must not match "HARBOR VIEW AVE".
        return False
    parcel_dir = tokens[1] if len(tokens) > 1 and tokens[1] in ("N", "S", "E", "W") else None
    if direction and parcel_dir and direction != parcel_dir:
        return False
    return str(parcel.get("SitusZIP") or "")[:5] == str(zipcode)[:5] or " LOS ANGELES CA " in full


def lookup(session, house, word, direction, zipcode):
    params = {"where": f"SitusHouseNo='{house}' AND SitusStreet LIKE '%{word}%'", "outFields": FIELDS, "returnGeometry": "false", "f": "json"}
    r = session.get(PARCELS, params=params, timeout=60)
    r.raise_for_status()
    feats = [f["attributes"] for f in r.json().get("features", []) if plausible(f["attributes"], house, word, direction, zipcode)]
    in_zip = [f for f in feats if str(f.get("SitusZIP") or "")[:5] == str(zipcode)[:5]]
    return in_zip or feats, bool(in_zip)


COUNCIL = "https://maps.lacity.org/lahub/rest/services/Boundaries/MapServer/13/query"
CENSUS = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"


def census(session, s):
    if not re.match(r"\s*\d", s["address"] or ""):
        return None
    line = f"{s['address']}, {s.get('city') or 'Los Angeles'}, CA {s.get('zip') or ''}"
    r = session.get(CENSUS, params={"address": line, "benchmark": "Public_AR_Current", "format": "json"}, timeout=60)
    matches = r.json().get("result", {}).get("addressMatches", [])
    return (matches[0]["coordinates"]["y"], matches[0]["coordinates"]["x"]) if matches else None


def council(session, lat, lon):
    params = {"geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "inSR": 4326, "spatialRel": "esriSpatialRelIntersects", "outFields": "*", "returnGeometry": "false", "f": "json"}
    feats = session.get(COUNCIL, params=params, timeout=60).json().get("features", [])
    if not feats:
        return None
    a = feats[0]["attributes"]
    for k in ("DISTRICT", "District", "district", "CD", "COUNCIL_DISTRICT"):
        if a.get(k) is not None:
            return int(a[k])
    return None


def main(src, out):
    cache = json.load(open(out)) if os.path.exists(out) else {}
    session = requests.Session()
    retry = Retry(total=6, backoff_factor=2, status_forcelist=(429, 500, 502, 503, 504))
    session.mount("https://", HTTPAdapter(max_retries=retry))
    for s in json.load(open(src)):
        if s["key"] in cache:
            continue
        house, word = street_key(s["address"])
        rows, zip_ok = lookup(session, house, word, listed_direction(s["address"]), s.get("zip") or "") if house and word else ([], False)
        if not rows:
            cache[s["key"]] = {"status": "no_match", "house": house, "word": word}
        else:
            # Prefer improved parcels; a campus can span several AINs.
            rows.sort(key=lambda f: -(f.get("Units1") or 0))
            cache[s["key"]] = {"status": "matched" if zip_ok else "matched_other_zip", "n_parcels": len(rows), **rows[0]}
        rec = cache[s["key"]]
        lat, lon = rec.get("CENTER_LAT"), rec.get("CENTER_LON")
        if lat is None:
            try:
                hit = census(session, s)
            except Exception as exc:
                hit, rec["census_error"] = None, str(exc)[:120]
            if hit:
                lat, lon = hit
                rec["census_lat"], rec["census_lon"] = hit
        if lat is not None:
            try:
                rec["council_district"] = council(session, lat, lon)
            except Exception as exc:
                rec["council_error"] = str(exc)[:120]
        json.dump(cache, open(out, "w"), indent=0)
        time.sleep(0.15)
    print({k: sum(1 for v in cache.values() if v["status"] == k) for k in {v["status"] for v in cache.values()}})


main(*sys.argv[1:3])
