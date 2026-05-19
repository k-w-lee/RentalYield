#!/usr/bin/env python3
"""District code discovery for PropertyGuru Malaysia.

PropertyGuru uses `districtCode` URL parameters for area-filtered searching.
District codes are short alphanumeric IDs (5 chars, e.g. roix4, zbpv1).

Discovery Strategy:
1. **Live extraction**: Scrape generic search pages (no district filter) and 
   extract ALL current district_code + district_text pairs from listing data.
   This builds a current, live map of district names → codes.
2. **Fuzzy matching**: For areas where the city name doesn't match a PG
   district name exactly, try similarity matching (case-insensitive, substring).
3. **Keyword fallback**: If no district matches, use keyword search (less 
   precise but still filters to relevant listings).

District codes are cached to district_cache.yaml for use by the main scraper.
Codes expire and are refreshed on each run.
"""

import json
import logging
import random
import time
import difflib
from pathlib import Path

import cloudscraper
import yaml
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────

DEFAULT_PAGES = 20  # Number of generic search pages to scrape for district map

# Pre-seeded district codes — MINIMAL SET. The discovery mechanism handles most.
# Only keep codes for areas that are not display-named on PG listing cards
# (e.g. sub-areas, neighborhoods within larger districts).
SEEDED_CODES: dict[str, str] = {}


def build_generic_url(page: int = 1, listing_type: str = "sale") -> str:
    """Build a PropertyGuru search URL with no district filter.

    Used to scrape listing data and extract current district codes.
    """
    return (
        f"https://www.propertyguru.com.my/property-for-{listing_type}"
        f"?listingType={listing_type}&page={page}"
        f"&propertyTypeGroup=N"
        f"&propertyTypeCode=APT&propertyTypeCode=CONDO&propertyTypeCode=FLAT&propertyTypeCode=SRES"
        f"&isCommercial=false&minTopYear=2009&maxTopYear=2026"
    )


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
        )
        # Add year filters
        params += "&minTopYear=2009&maxTopYear=2026"
        if listing_type == "sale":
            params += f"&_freetextDisplay={encoded}"
        return f"{base}?{params}"
    else:
        params = (
            f"listingType={listing_type}&page={page}"
            f"&propertyTypeGroup=N"
            f"&propertyTypeCode=APT&propertyTypeCode=CONDO&propertyTypeCode=FLAT&propertyTypeCode=SRES"
            f"&isCommercial=false&minTopYear=2009&maxTopYear=2026"
        )
        # Use keyword search as fallback
        params += f"&_freetextDisplay={encoded}"
        return f"{base}?{params}"


def build_rent_url(
    area_name: str,
    district_code: str | None = None,
    page: int = 1,
) -> str:
    """Build a rent search URL (aligned with build_search_url signature)."""
    return build_search_url(area_name, district_code, "rent", page)


def extract_live_district_map(
    scraper: cloudscraper.CloudScraper,
    pages: int = DEFAULT_PAGES,
    delay: float = 0.3,
    listing_type: str = "sale",
) -> dict[str, str]:
    """Scrape generic search pages and extract ALL current district codes.

    Returns dict mapping district name → district code (e.g. {'Batu': 'roix4', ...}).
    
    This is the ONLY reliable way to get current codes, since PG's autocomplete
    API (used by the "Search by State" modal) is blocked with 403 for non-browser
    requests, and keyword search doesn't resolve small districts.
    """
    all_codes: dict[str, str] = {}
    attempts = 0

    for page in range(1, pages + 1):
        url = build_generic_url(page, listing_type)
        try:
            r = scraper.get(url, timeout=30)
            time.sleep(delay)
        except Exception as e:
            log.warning(f"  Page {page} fetch failed: {e}")
            attempts += 1
            if attempts >= 3:
                log.warning("  Too many failures, stopping page scrape")
                break
            continue
        attempts = 0

        if r.status_code != 200:
            continue

        try:
            soup = BeautifulSoup(r.text, "lxml")
            nd = soup.find("script", id="__NEXT_DATA__")
            if not (nd and nd.string):
                continue

            data = json.loads(nd.string)
            listings = (
                data.get("props", {})
                .get("pageProps", {})
                .get("pageData", {})
                .get("data", {})
                .get("listingsData", [])
            )

            new_this_page = 0
            for listing in listings:
                addl = listing.get("listingData", {}).get("additionalData", {})
                dc = addl.get("districtCode", "")
                dt = addl.get("districtText", "")
                if dc and dt:
                    dt_clean = dt.strip()
                    if dt_clean not in all_codes:
                        all_codes[dt_clean] = dc
                        new_this_page += 1

            if new_this_page > 0:
                log.debug(f"    Page {page}: discovered {new_this_page} new districts")
        except Exception as e:
            log.debug(f"  Page {page} parse error: {e}")

    log.info(f"  Extracted {len(all_codes)} districts from {pages} pages")
    return all_codes


def match_area_to_district(
    area_name: str,
    district_map: dict[str, str],
) -> str | None:
    """Try to match an area name to a known district by fuzzy comparison.
    
    Matching strategy:
    1. Exact case-insensitive match
    2. If area contains parentheses, try the "primary" name
    3. Fuzzy substring matching with similarity threshold
    """
    area_lower = area_name.lower().strip()

    # 1. Exact case-insensitive match
    for d_name, d_code in district_map.items():
        if area_lower == d_name.lower().strip():
            return d_code

    # 2. If area has parenthetical text (e.g. "Port Klang (Pelabuhan Klang)"),
    #    check each part
    if "(" in area_name:
        parts = [p.strip() for p in area_name.replace(")", "").split("(")]
        for part in parts:
            if part:
                for d_name, d_code in district_map.items():
                    if part.lower() == d_name.lower().strip():
                        return d_code

    # 3. Exact inverse: check if any district name matches a comma-separated
    #    part from the area
    if "," in area_name:
        for part in area_name.split(","):
            part = part.strip()
            if part:
                for d_name, d_code in district_map.items():
                    if part.lower() == d_name.lower().strip():
                        return d_code

    # 4. Substring match: district name contains area name or vice versa
    #    (but only for longer names to avoid false matches)
    for d_name, d_code in sorted(district_map.items(), key=lambda x: -len(x[0])):
        dn_lower = d_name.lower().strip()
        # Only match if one name is fully contained in the other (not partial)
        if area_lower == dn_lower:
            return d_code
        if area_lower in dn_lower or dn_lower in area_lower:
            # Additional guard: label must be close enough
            ratio = difflib.SequenceMatcher(None, area_lower, dn_lower).ratio()
            if ratio > 0.6:
                return d_code

    return None


def discover_district_code(
    area_name: str,
    scraper: cloudscraper.CloudScraper,
    delay: float = 0.5,
    district_map: dict[str, str] | None = None,
) -> str | None:
    """Discover district code for an area using the live district map.
    
    Falls back to keyword search (returning None = no code) if no match.
    """
    # First try live map
    if district_map:
        code = match_area_to_district(area_name, district_map)
        if code:
            log.info(f"  '{area_name}': {code}")
            return code

    # Fallback: keyword search + check if PG resolves (works for well-known areas)
    url = build_search_url(area_name, district_code=None)
    log.debug(f"  Keyword fallback for '{area_name}'")

    try:
        r = scraper.get(url, timeout=30, allow_redirects=True)
        time.sleep(delay)
    except Exception as e:
        log.warning(f"  Request failed: {e}")
        return None

    if r.status_code != 200:
        return None

    try:
        soup = BeautifulSoup(r.text, "lxml")
        nd = soup.find("script", id="__NEXT_DATA__")
        if nd and nd.string:
            data = json.loads(nd.string)
            saved = (
                data.get("props", {})
                .get("pageProps", {})
                .get("pageData", {})
                .get("data", {})
                .get("savedSearchData", {})
                .get("searchParams", {})
            )
            dc = saved.get("district_code")
            if dc and isinstance(dc, list) and len(dc) > 0:
                log.info(f"  '{area_name}': {dc[0]} (keyword-resolved)")
                return dc[0]
            if dc and isinstance(dc, str):
                log.info(f"  '{area_name}': {dc} (keyword-resolved)")
                return dc
    except Exception as e:
        log.debug(f"  Parse error: {e}")

    log.debug(f"  '{area_name}': no code (keyword fallback)")
    return None


def discover_all_districts(
    cities_file: str,
    cache_file: str,
    scraper: cloudscraper.CloudScraper,
    delay: float = 0.3,
    resume: bool = True,
    skip_discovery: bool = False,
    pages_to_scrape: int = DEFAULT_PAGES,
) -> dict:
    """Discover district codes for ALL areas using live extraction.

    1. Scrapes generic search pages to build a current district code map
    2. Matches each area to a district by name
    3. Caches results to district_cache.yaml

    Set skip_discovery=True to skip generic page scraping (use cached map only).
    Set pages_to_scrape to control coverage (default 20).
    
    Returns: {area_name: {"state": str, "code": str|None, "error": str|None}}
    """
    # ── Phase 1: Build live district map ────────────────────────────────
    log.info("Phase 1: Extracting live district codes from generic search pages...")
    district_map: dict[str, str] = {}

    if not skip_discovery:
        district_map = extract_live_district_map(
            scraper, pages=pages_to_scrape, delay=delay
        )
    else:
        # Try to load from existing cache to avoid HTTP
        if Path(cache_file).exists():
            with open(cache_file) as f:
                cached = yaml.safe_load(f) or {}
            for area, info in cached.items():
                if isinstance(info, dict) and info.get("code"):
                    district_map[area] = info["code"]
        log.info(f"  Using cached map ({len(district_map)} entries)")

    # ── Phase 2: Match areas to codes ──────────────────────────────────
    with open(cities_file) as f:
        cities = json.load(f)

    results: dict[str, dict] = {}

    for state, areas in cities.items():
        for area in areas:
            # Try seeded codes first (rare, only for edge cases)
            code = SEEDED_CODES.get(area.lower().strip())
            if code:
                log.info(f"  Seeded '{area}': {code}")
                results[area] = {"state": state, "code": code, "error": None, "tried": True}
                continue

            # Match against live map
            code = match_area_to_district(area, district_map)
            if code:
                results[area] = {"state": state, "code": code, "error": None, "tried": True}
                log.debug(f"  '{area}': {code}")
                continue

            # No district match — mark as keyword fallback
            results[area] = {"state": state, "code": None, "error": "keyword_fallback", "tried": True}
            log.debug(f"  '{area}': keyword fallback (no district match)")

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
    r = discover_all_districts(
        str(root / "cities.json"),
        str(root / "district_cache.yaml"),
        s,
        skip_discovery=False,
    )

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
