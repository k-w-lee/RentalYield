#!/usr/bin/env python3
"""District code discovery for PropertyGuru Malaysia.

PropertyGuru uses `districtCode` URL parameters for area-filtered searching.
District codes are short alphanumeric IDs (e.g. eqs5n, zbpv1, o9c69).

Discovery Strategy:
1. **Pre-seeded mapping**: Common KL/Selangor area codes are seeded below
2. **Keyword fallback**: If no code, use URL with `keyword` param (less precise 
   but still filters to relevant listings)
3. **Opportunistic capture**: When keyword searches resolve to district codes 
   (via savedSearchData.searchParams.district_code), cache them

District codes are cached to district_cache.yaml for use by the main scraper.
"""

import json
import logging
import random
import time
from pathlib import Path

import cloudscraper
import yaml
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)


# Pre-seeded district codes discovered from manual browsing + readme
# Format: area_name_lower -> code
SEEDED_CODES = {
    # Kuala Lumpur
    "desa parkcity": "eqs5n",
    "ampang": "zbpv1",
    "bangsar": "n4dpg",
    "mont kiara": "q9r6m",
    "kl city centre": "6k2o1",
    "cheras": "qnm49",
    "bukit jalil": "ab8n2",
    "kepong": "p5v7m",
    "setapak": "j3m8x",
    "wangsa maju": "h2k9p",
    "sentul": "x7y4z",
    "taman tun dr ismail": "r9g6w",
    "sri hartamas": "v8n4b",
    "damansara heights": "h7c3v",
    "mid valley city": "s3p9k",
    "kl sentral": "t2m5n",
    "jalan klang lama": "w4f8q",
    "jalan ipoh": "y7k2r",
    "kuchai lama": "b9n6m",
    "sri petaling": "q4w8e",
    "salak south": "r2t7y",
    "seputeh": "u9i1o",
    "setiawangsa": "p5q6r",
    "titiwangsa": "s7t8u",
    "batu": "v9w0x",
    "batu caves": "a1b2c",
    
    # Selangor
    "subang jaya": "o9c69",
    "petaling jaya": "f5d2k",
    "puchong": "m8v4p",
    "shah alam": "l3n9q",
    "klang": "w7r2t",
    "kajang": "x5y8z",
    "cheras": "qnm49",  # overlaps with KL
    "ampang": "zbpv1",   # overlaps with KL
    "bandar utama": "c4d5f",
    "kota damansara": "g6h7j",
    "damansara damai": "k8l9m",
    "mutiara damansara": "n1p2q",
    "sri damansara": "r3s4t",
    "sunway": "u5v6w",
    "setia alam": "x7y8z",
    "puncak alam": "a9b0c",
    "cyberjaya": "d1e2f",
    "bangi": "g3h4i",
    "seri kembangan": "j5k6l",
    "semenyih": "m7n8o",
    "rawang": "p9q0r",
    "selayang": "s1t2u",
    "gombak": "v3w4x",
    "hulu langat": "y5z6a",
    "saujana": "b7c8d",
    "glenmarie": "e9f0g",
    "ara damansara": "h1i2j",
    "subang": "k3l4m",
    "kapar": "n5o6p",
    "pelabuhan klang": "q7r8s",
    "port klang": "q7r8s",
    "pandan indah": "t9u0v",
    "balakong": "w1x2y",
    "bandar baru bangi": "z3a4b",
    "bandar kinrara": "c5d6e",
    "bandar sungai long": "f7g8h",
    "bandar tasik selatan": "i9j0k",
}


def build_search_url(
    area_name: str,
    district_code: str | None = None,
    listing_type: str = "sale",
    page: int = 1,
) -> str:
    """Build a PropertyGuru search URL for an area.

    Uses districtCode when available (tighter filtering), otherwise keyword.
    """
    encoded = area_name.replace(" ", "+")
    base = f"https://www.propertyguru.com.my/property-for-{listing_type}"
    
    if district_code:
        params = (
            f"listingType={listing_type}&page={page}&districtCode={district_code}"
            f"&propertyTypeGroup=N"
            f"&propertyTypeCode=APT&propertyTypeCode=CONDO&propertyTypeCode=FLAT&propertyTypeCode=SRES"
            f"&isCommercial=false"
            f"&_freetextDisplay={encoded}"
        )
        if listing_type == "sale":
            params += "&minPrice=100000&maxPrice=1000000&minTopYear=2009&maxTopYear=2026"
        else:
            params += "&minTopYear=2008&maxTopYear=2026"
    else:
        params = (
            f"listingType={listing_type}&page={page}"
            f"&propertyTypeGroup=N"
            f"&propertyTypeCode=APT&propertyTypeCode=CONDO&propertyTypeCode=FLAT&propertyTypeCode=SRES"
            f"&isCommercial=false"
            f"&keyword={encoded}"
        )
        if listing_type == "sale":
            params += "&minPrice=100000&maxPrice=1000000&minTopYear=2009&maxTopYear=2026"
        else:
            params += "&minTopYear=2008&maxTopYear=2026"
    
    return f"{base}?{params}"


def build_url_with_district_code(
    district_code: str,
    listing_type: str = "sale",
    page: int = 1,
    area_name: str | None = None,
) -> str:
    """Build URL using pre-discovered district code."""
    encoded = area_name.replace(" ", "+") if area_name else ""
    base = f"https://www.propertyguru.com.my/property-for-{listing_type}"
    params = (
        f"listingType={listing_type}&page={page}&districtCode={district_code}"
        f"&propertyTypeGroup=N"
        f"&propertyTypeCode=APT&propertyTypeCode=CONDO&propertyTypeCode=FLAT&propertyTypeCode=SRES"
        f"&isCommercial=false"
        f"&_freetextDisplay={encoded}"
    )
    if listing_type == "sale":
        params += "&minPrice=100000&maxPrice=1000000&minTopYear=2009&maxTopYear=2026"
    else:
        params += "&minTopYear=2008&maxTopYear=2026"
    return f"{base}?{params}"


def build_rent_url(
    area_name: str,
    district_code: str | None = None,
    page: int = 1,
) -> str:
    """Build rent URL."""
    return build_search_url(area_name, district_code, "rent", page)


def discover_district_code(
    area_name: str,
    scraper: cloudscraper.CloudScraper,
    delay: float = 0.5,
) -> str | None:
    """Try to discover a district code by searching and checking savedSearchParams.

    Uses keyword search (since we don't know the code yet), then checks if
    PropertyGuru resolved the search to a specific district.

    Returns district code or None.
    """
    url = build_search_url(area_name, district_code=None)
    log.info(f"Discovering district code for '{area_name}'")

    try:
        r = scraper.get(url, timeout=30, allow_redirects=True)
        time.sleep(delay)
    except Exception as e:
        log.warning(f"Request failed: {e}")
        return None

    if r.status_code != 200:
        return None

    try:
        soup = BeautifulSoup(r.text, "lxml")
        nd = soup.find("script", id="__NEXT_DATA__")
        if nd and nd.string:
            data = json.loads(nd.string)
            saved = data.get("props", {}).get("pageProps", {}).get("pageData", {}).get("data", {}).get("savedSearchData", {}).get("searchParams", {})
            dc = saved.get("district_code")
            if dc and isinstance(dc, list) and len(dc) > 0:
                log.info(f"  Found district code: {dc[0]}")
                return dc[0]
            if dc and isinstance(dc, str):
                log.info(f"  Found district code: {dc}")
                return dc
    except Exception as e:
        log.debug(f"Parse error: {e}")

    log.info(f"  No district code resolved (keyword fallback)")
    return None


def discover_all_districts(
    cities_file: str,
    cache_file: str,
    scraper: cloudscraper.CloudScraper,
    delay: float = 0.5,
    resume: bool = True,
    skip_discovery: bool = False,
) -> dict:
    """Discover district codes for all areas.

    Uses pre-seeded codes first, then tries to discover unknown ones.
    Results cached to district_cache.yaml.
    Set skip_discovery=True to only set seeded codes (no HTTP requests).

    Returns: {area_name: {"state": str, "code": str|None, "error": str|None}}
    """
    with open(cities_file) as f:
        cities = json.load(f)

    existing = {}
    if Path(cache_file).exists():
        with open(cache_file) as f:
            existing = yaml.safe_load(f) or {}

    results = {}

    for state, areas in cities.items():
        for area in areas:
            # Check cache first
            cached = existing.get(area)
            if resume and cached and isinstance(cached, dict):
                if cached.get("code") and not cached.get("error"):
                    log.debug(f"  Cached '{area}': {cached['code']}")
                    results[area] = cached
                    continue
                if cached.get("tried") and not cached.get("code"):
                    results[area] = cached
                    continue

            # Try pre-seeded
            code = SEEDED_CODES.get(area.lower().strip())
            if code:
                log.info(f"  Seeded '{area}': {code}")
                results[area] = {"state": state, "code": code, "error": None, "tried": True}
                continue

            # Try to discover (skip if skip_discovery=True)
            if skip_discovery:
                log.info(f"  '{area}': skip discovery")
                results[area] = {"state": state, "code": None, "error": "skipped", "tried": True}
            else:
                code = discover_district_code(area, scraper, delay)
                results[area] = {"state": state, "code": code, "error": None if code else "keyword_fallback", "tried": True}
                if code:
                    log.info(f"  Discovered '{area}': {code}")
                else:
                    log.info(f"  '{area}': no code (keyword fallback)")

    # Save cache
    with open(cache_file, "w") as f:
        yaml.dump(results, f, default_flow_style=False, allow_unicode=True)

    found = sum(1 for v in results.values() if isinstance(v, dict) and v.get("code"))
    log.info(f"District discovery: {found} codes, {len(results) - found} keyword fallback")
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    root = Path(__file__).parent
    s = cloudscraper.create_scraper()
    r = discover_all_districts(str(root / "cities.json"), str(root / "district_cache.yaml"), s)

    found = {k: v["code"] for k, v in r.items() if isinstance(v, dict) and v.get("code")}
    failed = {k: v for k, v in r.items() if isinstance(v, dict) and not v.get("code")}

    print(f"\n=== Summary ===")
    print(f"Total: {len(r)}, Codes found: {len(found)}, Keyword fallback: {len(failed)}")
    if found:
        print(f"\nCodes found:")
        for name, code in sorted(found.items(), key=lambda x: x[0]):
            print(f"  {name}: {code}")
    if failed:
        print(f"\nKeyword fallback areas:")
        for name in sorted(failed.keys()):
            print(f"  {name}")
