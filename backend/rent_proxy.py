#!/usr/bin/env python3
"""Rent scraper: collects rent listings, groups by project+bedroom, calculates median rent.

This runs AFTER sale scrape. It uses known project names from sale data
to search rent listings and builds a rent cache keyed by (project_name, bedrooms).

Data source: __NEXT_DATA__ embedded JSON (same as sale scraper).
"""

import json
import logging
import sqlite3
import statistics
import time
from pathlib import Path

import cloudscraper
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)


def create_rent_cache_schema(db_path: str):
    """Ensure rent_cache.db has the right schema."""
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rent_cache (
            project_name TEXT NOT NULL,
            bedrooms INTEGER NOT NULL,
            median_rent REAL,
            listing_count INTEGER,
            scraped_at TEXT,
            PRIMARY KEY (project_name, bedrooms)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rent_project_listings (
            project_name TEXT NOT NULL,
            listing_id INTEGER NOT NULL,
            rent REAL,
            bedrooms INTEGER,
            area_sqft REAL,
            address TEXT,
            url TEXT,
            scraped_at TEXT,
            PRIMARY KEY (project_name, listing_id)
        )
    """)
    conn.commit()
    conn.close()


def get_cached_rent(db_path: str, project_name: str, bedrooms: int) -> float | None:
    """Get cached median rent for a project+bedroom combo. Returns None if missing."""
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "SELECT median_rent FROM rent_cache WHERE project_name = ? AND bedrooms = ?",
        (project_name, bedrooms),
    )
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


def cache_rent(db_path: str, project_name: str, bedrooms: int, median_rent: float, count: int):
    """Upsert median rent into cache."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT OR REPLACE INTO rent_cache
           (project_name, bedrooms, median_rent, listing_count, scraped_at)
           VALUES (?, ?, ?, ?, datetime('now'))""",
        (project_name, bedrooms, median_rent, count),
    )
    conn.commit()
    conn.close()


def save_listing(db_path: str, project_name: str, listing: dict):
    """Save individual rent listing detail."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT OR REPLACE INTO rent_project_listings
           (project_name, listing_id, rent, bedrooms, area_sqft, address, url, scraped_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
        (
            project_name,
            listing.get("id"),
            listing.get("rent"),
            listing.get("bedrooms"),
            listing.get("area_sqft"),
            listing.get("address"),
            listing.get("url"),
        ),
    )
    conn.commit()
    conn.close()


def build_rent_url(district_code: str, page: int = 1) -> str:
    """Build PropertyGuru rent search URL."""
    base = "https://www.propertyguru.com.my/property-for-rent"
    params = (
        f"?listingType=rent&page={page}&districtCode={district_code}"
        f"&propertyTypeGroup=N"
        f"&propertyTypeCode=APT&propertyTypeCode=CONDO&propertyTypeCode=FLAT&propertyTypeCode=SRES"
        f"&isCommercial=false"
        f"&minTopYear=2008&maxTopYear=2026"
    )
    return base + params


def parse_listing_card(listing_entry: dict) -> dict | None:
    """Extract fields from a single listingsData entry.

    Returns dict with: id, rent, bedrooms, bathrooms, area_sqft,
    project_title, address, build_year, url, property_type.
    """
    try:
        ld = listing_entry.get("listingData", {})
        ga = listing_entry.get("gaProduct", {})

        # Price
        price_str = ga.get("price", "0")
        rent = int(price_str) if price_str else 0

        # Features
        features = ld.get("listingFeatures", [])
        bedrooms = None
        bathrooms = None
        area_sqft = None
        build_year = None
        property_type = None

        for f in features:
            if isinstance(f, list):
                for sub in f:
                    aid = sub.get("dataAutomationId", "")
                    text = sub.get("text", "")
                    if "bedrooms" in aid:
                        bedrooms = int(text) if text.isdigit() else None
                    elif "bathrooms" in aid:
                        bathrooms = int(text) if text.isdigit() else None
            elif isinstance(f, dict):
                aid = f.get("dataAutomationId", "")
                text = f.get("text", "")
                if "area" in aid:
                    # e.g. "1,077 sqft"
                    area_str = text.replace(" sqft", "").replace(",", "")
                    area_sqft = float(area_str) if area_str.replace(".", "").isdigit() else None
                elif "unit-type" in aid:
                    property_type = text
                elif "build-year" in aid or "build year" in aid:
                    # e.g. "Built: 2019"
                    year_part = text.replace("Built: ", "").replace("Built:", "").strip()
                    build_year = int(year_part) if year_part.isdigit() else None

        listing_id = ld.get("id")
        if not listing_id:
            return None

        return {
            "id": listing_id,
            "rent": rent,
            "bedrooms": bedrooms,
            "bathrooms": bathrooms,
            "area_sqft": area_sqft,
            "project_title": ld.get("localizedTitle", ""),
            "address": ld.get("fullAddress", ""),
            "build_year": build_year,
            "property_type": property_type,
            "url": f"https://www.propertyguru.com.my/property-for-rent/listing-{listing_id}",
        }
    except Exception as e:
        log.warning(f"Failed to parse rent listing: {e}")
        return None


def scrape_rent_page(url: str, scraper: cloudscraper.CloudScraper, delay: float = 2.0) -> tuple[list[dict], int, int]:
    """Scrape a single rent page. Returns (listings, total_pages, current_page)."""
    pg = url.split("page=")[1].split("&")[0] if "page=" in url else "?"
    dc = url.split("districtCode=")[1].split("&")[0] if "districtCode=" in url else ""
    log.debug(f"  rent pg {pg} [{dc or '*'}]: {url[:60]}")
    r = scraper.get(url, timeout=30)
    time.sleep(delay)

    soup = BeautifulSoup(r.text, "lxml")
    next_data = soup.find("script", id="__NEXT_DATA__")
    if not next_data or not next_data.string:
        log.warning("  No __NEXT_DATA__ on rent page")
        return [], 0, 0

    data = json.loads(next_data.string)
    page_data = data.get("props", {}).get("pageProps", {}).get("pageData", {}).get("data", {})
    listings_raw = page_data.get("listingsData", [])
    pagination = page_data.get("paginationData", {})

    total_pages = pagination.get("totalPages", 0)
    current_page = pagination.get("currentPage", 0)

    parsed = []
    for entry in listings_raw:
        listing = parse_listing_card(entry)
        if listing:
            parsed.append(listing)

    return parsed, total_pages, current_page


def scrape_rent_by_project(
    project_name: str,
    district_code: str,
    db_path: str,
    scraper: cloudscraper.CloudScraper,
    delay: float = 2.0,
    max_pages: int = 5,
) -> list[dict]:
    """Scrape rent listings for a specific project+area.

    The best match happens by scraping all pages and filtering by project name.

    Returns list of {bedrooms, rent, area_sqft, ...}
    """
    all_listings = []
    url = build_rent_url(district_code, page=1)
    listings, total_pages, _ = scrape_rent_page(url, scraper, delay)
    all_listings.extend(listings)

    if total_pages > 1:
        for page in range(2, min(total_pages + 1, max_pages + 1)):
            url = build_rent_url(district_code, page=page)
            try:
                listings, _, _ = scrape_rent_page(url, scraper, delay)
                all_listings.extend(listings)
            except Exception as e:
                log.warning(f"Error on rent page {page} for {district_code}: {e}")
                break

    # Filter to this project (fuzzy match on localizedTitle)
    project_listings = [
        l for l in all_listings
        if _project_matches(l.get("project_title", ""), project_name)
    ]

    # Save all matching listings to DB
    for l in project_listings:
        save_listing(db_path, project_name, l)

    return project_listings


def _project_matches(title_a: str, title_b: str) -> bool:
    """Simple fuzzy project name match (case-insensitive)."""
    a = title_a.lower().strip()
    b = title_b.lower().strip()
    if a == b:
        return True
    if a in b or b in a:
        return True
    # Handle common abbreviations/shortenings
    words_a = set(a.split())
    words_b = set(b.split())
    intersection = words_a & words_b
    # At least 2 words match or >50% overlap
    if len(intersection) >= 2:
        return True
    if len(words_a) > 0 and len(words_b) > 0:
        overlap_ratio = len(intersection) / max(len(words_a), len(words_b))
        if overlap_ratio >= 0.5:
            return True
    return False


def calculate_median_rent(project_listings: list[dict]) -> dict:
    """Group rent listings by bedroom count, calculate median.

    Returns dict:
      {bedrooms: {"median_rent": float, "count": int}}
    plus special keys:
      "project_median": overall median across all bedrooms
      "project_count": total listings
    """
    by_bedroom: dict[int, list[float]] = {}
    for l in project_listings:
        bd = l.get("bedrooms")
        rent = l.get("rent", 0)
        if bd is not None and rent > 0:
            by_bedroom.setdefault(bd, []).append(rent)

    result = {}
    all_rents = []
    for bd, rents in by_bedroom.items():
        med = statistics.median(rents)
        result[str(bd)] = {
            "median_rent": round(med, 2),
            "count": len(rents),
        }
        all_rents.extend(rents)

    if all_rents:
        result["project_median"] = round(statistics.median(all_rents), 2)
    else:
        result["project_median"] = None

    result["project_count"] = len(project_listings)
    return result


def get_rent_for_sale_listing(
    project_name: str,
    bedrooms: int | None,
    db_path: str,
    config: dict,
) -> tuple[float | None, str]:
    """Get best available rent estimate for a sale listing.

    Lookup order:
    1. Project + exact bedroom count (from cache)
    2. Project-wide median (any bedroom)
    3. None (caller handles area-level fallback)

    Returns (median_rent, source_description)
    """
    conn = sqlite3.connect(db_path)

    # Try exact match
    if bedrooms is not None:
        cur = conn.execute(
            "SELECT median_rent, listing_count FROM rent_cache WHERE project_name = ? AND bedrooms = ?",
            (project_name, bedrooms),
        )
        row = cur.fetchone()
        if row and row[0] and row[0] > 0:
            conn.close()
            return row[0], f"{project_name} {bedrooms}BR (n={row[1]})"

    # Try project-wide median
    cur = conn.execute(
        "SELECT median_rent, listing_count FROM rent_cache WHERE project_name = ? AND bedrooms = -1",
        (project_name,),
    )
    row = cur.fetchone()
    if row and row[0] and row[0] > 0:
        conn.close()
        return row[0], f"{project_name} all BR (n={row[1]})"

    conn.close()
    return None, f"{project_name} no rent data"


if __name__ == "__main__":
    # Smoke test: scrape rent for Desa Parkcity
    logging.basicConfig(level=logging.INFO)
    db = "/tmp/test_rent_cache.db"
    create_rent_cache_schema(db)

    scraper = cloudscraper.create_scraper()
    listings = scrape_rent_by_project("Westside Three", "eqs5n", db, scraper, delay=0.5, max_pages=2)
    print(f"Found {len(listings)} matching rent listings")
    medians = calculate_median_rent(listings)
    for k, v in medians.items():
        print(f"  {k}: {v}")
