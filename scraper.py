#!/usr/bin/env python3
"""Main scraper orchestrator for PropertyGuru Rental Yield system.

Pipeline:
  A. Discover district codes (via discover_districts.py)
  B. Scrape sale listings per area with resume support
  C. Scrape rent listings per project, calculate median rent
  D. Join sale + rent, run loan/score calculations
  E. Dedup, rank, write CSV output

Usage:
    python3 scraper.py                     # Full run
    python3 scraper.py --resume            # Resume incomplete scrape
    python3 scraper.py --dry-run           # Discover + preview only
    python3 scraper.py --max-areas 5       # Limit areas for testing
    python3 scraper.py --proxy http://...  # Use proxy
"""

import argparse
import csv
import json
import logging
import os
import sqlite3
import statistics
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from tqdm import tqdm

import cloudscraper
import yaml
from bs4 import BeautifulSoup

import discover_districts as dd
import loan as loan_mod
import rent_proxy as rp
import score as score_mod

log = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent
CITIES_FILE = PROJECT_ROOT / "cities.json"
CONFIG_FILE = PROJECT_ROOT / "config.yaml"
DISTRICT_CACHE = PROJECT_ROOT / "district_cache.yaml"
STATE_DB = PROJECT_ROOT / "scrape_state.db"
RENT_CACHE_DB = PROJECT_ROOT / "rent_cache.db"
OUTPUT_DIR = PROJECT_ROOT / "output"
ALL_SALES_CSV = OUTPUT_DIR / "all_sales_listings.csv"
ALL_RENTALS_CSV = OUTPUT_DIR / "all_rentals_listings.csv"
SHORTLIST_CSV = OUTPUT_DIR / "top_shortlist.csv"

# Number of days before a full re-scrape is triggered
FULL_SCRAPE_TTL_DAYS = 30


# ── State DB Schema ─────────────────────────────────────────────────────────

def create_state_schema(db_path: str):
    """Create scrape_state.db tables if they don't exist."""
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS scrape_state (
            area_name TEXT NOT NULL,
            listing_type TEXT NOT NULL DEFAULT 'sale',
            completed INTEGER NOT NULL DEFAULT 0,
            last_page INTEGER NOT NULL DEFAULT 0,
            total_pages INTEGER NOT NULL DEFAULT 0,
            scraped_at TEXT,
            PRIMARY KEY (area_name, listing_type)
        );

        CREATE TABLE IF NOT EXISTS sale_listings (
            listing_id INTEGER PRIMARY KEY,
            listing_url TEXT,
            project_name TEXT NOT NULL,
            price REAL,
            area_sqft REAL,
            bedrooms INTEGER,
            bathrooms INTEGER,
            address TEXT,
            state TEXT,
            area_name TEXT,
            district_code TEXT,
            build_year INTEGER,
            property_type TEXT,
            tenure TEXT,
            listing_agent TEXT,
            scraped_at TEXT
        );
    """)
    conn.commit()
    conn.close()


def get_scrape_state(db_path: str, area_name: str, listing_type: str = "sale") -> dict | None:
    """Get scrape state for an area. Returns None if never scraped."""
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "SELECT completed, last_page, total_pages, scraped_at FROM scrape_state "
        "WHERE area_name = ? AND listing_type = ?",
        (area_name, listing_type),
    )
    row = cur.fetchone()
    conn.close()
    if row:
        return {
            "completed": bool(row[0]),
            "last_page": row[1],
            "total_pages": row[2],
            "scraped_at": row[3],
        }
    return None


def upsert_scrape_state(db_path: str, area_name: str, listing_type: str,
                        completed: bool, last_page: int, total_pages: int):
    """Save/update scrape state."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT OR REPLACE INTO scrape_state
           (area_name, listing_type, completed, last_page, total_pages, scraped_at)
           VALUES (?, ?, ?, ?, ?, datetime('now'))""",
        (area_name, listing_type, int(completed), last_page, total_pages),
    )
    conn.commit()
    conn.close()


def upsert_sale_listing(db_path: str, listing: dict):
    """Upsert a sale listing into the DB."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """INSERT OR REPLACE INTO sale_listings
               (listing_id, listing_url, project_name, price, area_sqft,
                bedrooms, bathrooms, address, state, area_name, district_code,
                build_year, property_type, tenure, listing_agent, scraped_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (
                listing.get("listing_id"),
                listing.get("listing_url"),
                listing.get("project_name", ""),
                listing.get("price"),
                listing.get("area_sqft"),
                listing.get("bedrooms"),
                listing.get("bathrooms"),
                listing.get("address"),
                listing.get("state"),
                listing.get("area_name"),
                listing.get("district_code"),
                listing.get("build_year"),
                listing.get("property_type"),
                listing.get("tenure"),
                listing.get("listing_agent"),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_all_sale_listings(db_path: str) -> list[dict]:
    """Retrieve all sale listings from DB as list of dicts."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.execute("SELECT * FROM sale_listings")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


# ── Main Scraper Logic ──────────────────────────────────────────────────────

def load_config() -> dict:
    """Load config.yaml."""
    with open(CONFIG_FILE) as f:
        return yaml.safe_load(f)


def load_cities() -> dict:
    """Load cities.json."""
    with open(CITIES_FILE) as f:
        return json.load(f)


def load_district_cache() -> dict:
    """Load district_cache.yaml, return empty dict if missing."""
    if DISTRICT_CACHE.exists():
        with open(DISTRICT_CACHE) as f:
            return yaml.safe_load(f) or {}
    return {}


def build_sale_url(district_code: str | None, area_name: str | None,
                   page: int = 1, config: dict | None = None) -> str:
    """Build sale search URL."""
    cfg = config.get("scraper", {})
    base = "https://www.propertyguru.com.my/property-for-sale"
    params = (
        f"listingType=sale&page={page}"
        f"&propertyTypeGroup=N"
        f"&propertyTypeCode=APT&propertyTypeCode=CONDO&propertyTypeCode=FLAT&propertyTypeCode=SRES"
        f"&isCommercial=false"
        f"&minPrice={cfg.get('min_price', 100000)}"
        f"&maxPrice={cfg.get('max_price', 1000000)}"
        f"&minTopYear={cfg.get('min_top_year_sale', 2009)}"
        f"&maxTopYear={cfg.get('max_top_year_sale', 2026)}"
    )
    if district_code:
        params += f"&districtCode={district_code}"
    if area_name:
        params += f"&_freetextDisplay={area_name.replace(' ', '+')}"
    return f"{base}?{params}"


def parse_sale_listing(entry: dict, area_name: str, state: str,
                       district_code: str | None) -> dict | None:
    """Parse a single sale listing from __NEXT_DATA__ listingsData entry.

    Returns dict with fields matching sale_listings table schema.
    
    Field locations (confirmed by inspection 2026-05-18):
    - bedrooms, bathrooms: direct int fields on listingData
    - floorArea: direct int field on listingData (sqft)
    - price: listingData.price.value (int) or gaProduct.price (string fallback)
    - build_year, property_type, tenure: from listingFeatures array
    """
    try:
        ld = entry.get("listingData", {})
        ga = entry.get("gaProduct", {})

        listing_id = ld.get("id")
        if not listing_id:
            return None

        # Primary price source: listingData.price.value (int)
        price_data = ld.get("price", {})
        if isinstance(price_data, dict):
            price = price_data.get("value", 0)
        else:
            price = price_data or 0

        if not price or price <= 0:
            # Fallback: gaProduct.price (string)
            price_str = ga.get("price", "0")
            price = int(price_str) if price_str and price_str.isdigit() else 0

        # Direct int fields
        bedrooms = ld.get("bedrooms")
        bathrooms = ld.get("bathrooms")
        area_sqft = ld.get("floorArea")

        # Parse listingFeatures for metadata fields
        features = ld.get("listingFeatures", [])
        build_year = None
        property_type = None
        tenure = None

        for f in features:
            if isinstance(f, list):
                continue
            aid = f.get("dataAutomationId", "").lower()
            text = f.get("text", "")
            if "unit-type" in aid:
                property_type = text
            elif "tenure" in aid:
                tenure = text
            elif "build-year" in aid:
                year_part = text.replace("built: ", "").replace("built:", "").strip()
                if year_part.isdigit():
                    build_year = int(year_part)

        # Fallback: badges (Completion: YYYY format)
        if not build_year:
            for badge in ld.get("badges", []):
                badge_text = badge.get("text", "")
                if "Completion:" in badge_text:
                    try:
                        build_year = int(badge_text.split(":")[1].strip())
                    except (ValueError, IndexError):
                        pass

        project_name = ld.get("localizedTitle", "")
        url = ld.get("url", "")
        agent_info = ld.get("agent", {})
        agent_name = agent_info.get("name", "") if isinstance(agent_info, dict) else ""

        return {
            "listing_id": listing_id,
            "listing_url": url,
            "project_name": project_name,
            "price": price,
            "area_sqft": area_sqft,
            "bedrooms": bedrooms,
            "bathrooms": bathrooms,
            "address": ld.get("fullAddress", ""),
            "state": state,
            "area_name": area_name,
            "district_code": district_code or "",
            "build_year": build_year,
            "property_type": property_type,
            "tenure": tenure,
            "listing_agent": agent_name,
        }
    except Exception as e:
        log.warning(f"Failed to parse sale listing: {e}")
        return None


def scrape_sale_page(url: str, scraper: cloudscraper.CloudScraper,
                     delay: float = 2.0, max_retries: int = 3
                     ) -> tuple[list[dict], int, int] | None:
    """Scrape a single sale page. Returns (listings_raw, total_pages, current_page)."""
    for attempt in range(max_retries):
        try:
            log.info(f"Sale page [attempt {attempt + 1}]: {url[:120]}...")
            r = scraper.get(url, timeout=30)
            if r.status_code != 200:
                log.warning(f"HTTP {r.status_code}, retrying...")
                time.sleep(delay * (attempt + 1))
                continue

            soup = BeautifulSoup(r.text, "lxml")
            next_data = soup.find("script", id="__NEXT_DATA__")
            if not next_data or not next_data.string:
                log.warning("No __NEXT_DATA__ found, retrying...")
                time.sleep(delay)
                continue

            data = json.loads(next_data.string)
            page_data = data.get("props", {}).get("pageProps", {}).get("pageData", {}).get("data", {})
            listings_raw = page_data.get("listingsData", [])
            pagination = page_data.get("paginationData", {})

            total_pages = pagination.get("totalPages", 0)
            current_page = pagination.get("currentPage", 0)

            return listings_raw, total_pages, current_page

        except Exception as e:
            log.warning(f"Exception on attempt {attempt + 1}: {e}")
            time.sleep(delay * (attempt + 1))

    log.error(f"Failed to scrape page after {max_retries} retries")
    return None


def scrape_sale_area(area_name: str, state: str, district_code: str | None,
                     config: dict, scraper: cloudscraper.CloudScraper,
                     db_path: str, resume: bool = True) -> int:
    """Scrape all sale pages for one area. Returns total listings scraped."""
    cfg = config.get("scraper", {})
    delay = cfg.get("request_delay_seconds", 2)

    state_info = get_scrape_state(db_path, area_name)
    last_page = 0
    total_pages = 0

    if resume and state_info and not state_info["completed"]:
        last_page = state_info["last_page"]
        total_pages = state_info["total_pages"]
        log.info(f"Resuming {area_name} from page {last_page + 1}/{total_pages}")

    start_page = last_page + 1
    page = start_page
    listings_count = 0

    # For keyword-search areas (no district code), cap at reasonable pages
    # Keyword search returns fuzzy results across many areas
    max_pages = total_pages if total_pages > 0 else 999
    if not district_code:
        log.info(f"  (keyword search — capping at {cfg.get('max_keyword_pages', 5)} max pages)")
        max_pages = min(max_pages, cfg.get('max_keyword_pages', 5))

    while True:
        url = build_sale_url(district_code, area_name, page, config)
        result = scrape_sale_page(url, scraper, delay, cfg.get("max_retries", 3))
        if result is None:
            log.error(f"Failed to scrape {area_name} page {page}, skipping area")
            break

        listings_raw, new_total, current_page = result
        total_pages = new_total if new_total > 0 else total_pages
        max_pages = min(max_pages, total_pages) if total_pages > 0 else max_pages

        if total_pages == 0 and not listings_raw:
            log.info(f"No listings found for {area_name}")
            upsert_scrape_state(db_path, area_name, "sale", True, 0, 0)
            break

        # Parse and save listings
        for entry in listings_raw:
            parsed = parse_sale_listing(entry, area_name, state, district_code)
            if parsed:
                upsert_sale_listing(db_path, parsed)
                listings_count += 1

        upsert_scrape_state(db_path, area_name, "sale", False, page, total_pages)

        # Check if we've hit the last page or max pages
        if page >= max_pages:
            log.info(f"Completed {area_name}: {listings_count} listings on {page}/{total_pages} pages")
            upsert_scrape_state(db_path, area_name, "sale", True, page, total_pages)
            break

        page += 1

    return listings_count


def collect_unique_projects(db_path: str) -> list[dict]:
    """Get unique (project_name, area_name, district_code, state) combos from sale listings."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.execute("""
        SELECT DISTINCT project_name, state, area_name, district_code
        FROM sale_listings
        WHERE project_name != ''
        ORDER BY project_name
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def scrape_rent_for_projects(projects: list[dict], config: dict,
                              scraper: cloudscraper.CloudScraper):
    """Scrape rent listings for all unique projects, cache to rent_cache.db."""
    db_path = str(RENT_CACHE_DB)
    delay = config.get("scraper", {}).get("request_delay_seconds", 2)
    rp.create_rent_cache_schema(db_path)

    rent_pbar = tqdm(projects, desc="Phase C", unit="proj", leave=True,
                     bar_format="{desc}: {n_fmt}/{total_fmt} [{elapsed}<{remaining}]")

    for proj in rent_pbar:
        project_name = proj["project_name"]
        district_code = proj.get("district_code", "")

        rent_pbar.set_postfix_str(project_name[:35], refresh=False)

        # Re-ensure schema (covers fresh DB after resume reset)
        rp.create_rent_cache_schema(db_path)

        log.info(f"Rent: {project_name} {district_code or '(no code)'}")

        # Skip if already cached with data for any bedroom count
        conn = sqlite3.connect(db_path)
        try:
            cur = conn.execute(
                "SELECT COUNT(*) FROM rent_cache WHERE project_name = ?",
                (project_name,),
            )
            cached_count = cur.fetchone()[0]
        except sqlite3.OperationalError:
            cached_count = 0
        conn.close()
        if cached_count > 0:
            continue

        try:
            project_listings = rp.scrape_rent_by_project(
                project_name, district_code, db_path, scraper,
                delay=0.5, max_pages=5,
            )

            medians = rp.calculate_median_rent(project_listings)

            # Cache per-bedroom medians
            for bd_key, val in medians.items():
                if bd_key in ("project_median", "project_count"):
                    continue
                bd = int(bd_key)
                rp.cache_rent(db_path, project_name, bd,
                              val["median_rent"], val["count"])

            # Cache project-wide median with bedrooms=-1 sentinel
            proj_med = medians.get("project_median")
            total_count = medians.get("project_count", 0)
            if proj_med:
                rp.cache_rent(db_path, project_name, -1, proj_med, total_count)

        except Exception as e:
            log.warning(f"  Rent failed for {project_name}: {e}")

        # Small delay between projects
        time.sleep(delay)

    rent_pbar.close()


def get_psf_median(db_path: str, project_name: str) -> float | None:
    """Calculate median sale psf for a project from existing sale listings."""
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "SELECT price, area_sqft FROM sale_listings "
        "WHERE project_name = ? AND area_sqft > 0 AND price > 0",
        (project_name,),
    )
    rows = cur.fetchall()
    conn.close()
    if len(rows) < 2:
        return None
    psfs = [r[0] / r[1] for r in rows]
    return round(statistics.median(psfs), 2)


def get_sale_count_for_project(db_path: str, project_name: str) -> int:
    """Count sale listings for a project."""
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "SELECT COUNT(*) FROM sale_listings WHERE project_name = ?",
        (project_name,),
    )
    count = cur.fetchone()[0]
    conn.close()
    return count


def get_area_level_median_rent(config: dict) -> float | None:
    """Fallback: return a default area-level median from config or None."""
    # In Phase 1, there's no dynamic area-level rent; use a hardcoded fallback
    return None


def calculate_all_metrics(db_path: str, state_db: str, config: dict) -> list[dict]:
    """Join sale listings with rent data, run calculations, return scored results."""
    cfg = config.get("scraper", {})
    loan_cfg = config.get("loan", {})
    costs_cfg = config.get("costs", {})
    scoring_cfg = config.get("scoring", {})
    weights = scoring_cfg.get("weights", {})
    thresholds = scoring_cfg.get("thresholds", {})
    mrt_scores = scoring_cfg.get("mrt_manual_scores", {})

    sale_listings = get_all_sale_listings(state_db)
    log.info(f"Calculating metrics for {len(sale_listings)} sale listings")

    results = []
    rent_db = str(RENT_CACHE_DB)
    rp.create_rent_cache_schema(rent_db)

    for sl in sale_listings:
        project = sl["project_name"]
        bedrooms = sl["bedrooms"]
        area_sqft = sl["area_sqft"] or 0
        price = sl["price"] or 0

        # Skip listings without price or area
        if price <= 0 or area_sqft <= 0:
            continue

        # ── Rent lookup ────────────────────────────────────────────────
        monthly_rent, rent_source = rp.get_rent_for_sale_listing(
            project, bedrooms, rent_db, config
        )
        if monthly_rent is None or monthly_rent <= 0:
            # Fallback: try project-wide
            monthly_rent, rent_source = rp.get_rent_for_sale_listing(
                project, None, rent_db, config
            )
        if monthly_rent is None or monthly_rent <= 0:
            monthly_rent = 0
            rent_source = "no rent data"

        # ── Loan & cash flow ───────────────────────────────────────────
        cf_result = loan_mod.net_monthly_cash_flow(
            monthly_rent=monthly_rent,
            purchase_price=price,
            down_payment_pct=loan_cfg.get("down_payment_percent", 10),
            annual_rate_pct=loan_cfg.get("interest_rate_percent", 4.0),
            tenure_years=loan_cfg.get("tenure_years", 35),
            area_sqft=area_sqft,
            maintenance_psf=costs_cfg.get("maintenance_fee_default_psf", 0.30),
            repairs_pct=costs_cfg.get("repairs_buffer_percent", 5),
            vacancy_pct=costs_cfg.get("vacancy_buffer_percent", 8.33),
            tax_insurance_monthly=costs_cfg.get("tax_insurance_monthly", 150),
            agent_fee_pct=costs_cfg.get("agent_fee_percent", 0),
        )

        # ── Price per sqft ─────────────────────────────────────────────
        psf = round(price / area_sqft, 2) if area_sqft > 0 else 0

        # ── Project-level stats ────────────────────────────────────────
        project_median_psf = get_psf_median(state_db, project)
        sale_count = get_sale_count_for_project(state_db, project)

        # Rent listing count (proxy for demand)
        conn = sqlite3.connect(rent_db)
        cur = conn.execute(
            "SELECT SUM(listing_count) FROM rent_cache WHERE project_name = ? AND bedrooms >= 0",
            (project,),
        )
        rent_count = cur.fetchone()[0] or 0
        conn.close()

        # ── Scoring ────────────────────────────────────────────────────
        cf_score = score_mod.score_cash_flow(
            cf_result["net_monthly_cash_flow"],
            thresholds.get("cash_flow_perfect", 500),
        )
        yield_score = score_mod.score_net_yield(
            cf_result["net_yield_pct"],
            thresholds.get("net_yield_perfect", 6.0),
        )
        demand_score = score_mod.score_rental_demand(rent_count, max(rent_count, 30))
        price_score = score_mod.score_price_vs_similar(
            psf, project_median_psf or psf  # neutral if no comparison
        )
        # MRT: try area-specific score, fallback to default
        area_name = sl.get("area_name", "")
        mrt_val = mrt_scores.get(area_name, mrt_scores.get("default", 5))
        mrt_score = score_mod.score_mrt_access(float(mrt_val))
        comp_score = score_mod.score_competition(sale_count)
        quality_score = score_mod.score_building_quality(
            sl["build_year"] or 2008,
            thresholds.get("building_quality_min_year", 2008),
            thresholds.get("building_quality_max_year", 2026),
        )

        total_score = score_mod.compute_weighted_score(
            cash_flow_score=cf_score,
            net_yield_score=yield_score,
            rental_demand_score=demand_score,
            price_vs_similar_score=price_score,
            mrt_score=mrt_score,
            competition_score=comp_score,
            quality_score=quality_score,
            weights=weights,
        )

        results.append({
            "listing_id": sl["listing_id"],
            "url": sl["listing_url"],
            "project_name": project,
            "address": sl.get("address", ""),
            "state": sl.get("state", ""),
            "area_name": area_name,
            "price": price,
            "psf": psf,
            "area_sqft": area_sqft,
            "bedrooms": sl["bedrooms"],
            "bathrooms": sl["bathrooms"],
            "build_year": sl["build_year"],
            "property_type": sl.get("property_type", ""),
            "tenure": sl.get("tenure", ""),
            "listing_agent": sl.get("listing_agent", ""),
            "monthly_rent": monthly_rent,
            "rent_source": rent_source,
            "rent_listing_count": rent_count,
            "sale_listing_count": sale_count,
            "project_median_psf": project_median_psf,
            "down_payment": cf_result["down_payment"],
            "loan_amount": cf_result["loan_amount"],
            "monthly_loan": cf_result["monthly_loan_repayment"],
            "monthly_maintenance": cf_result["monthly_maintenance"],
            "monthly_repairs": cf_result["monthly_repairs"],
            "monthly_vacancy": cf_result["monthly_vacancy"],
            "monthly_tax_insurance": cf_result["monthly_tax_insurance"],
            "monthly_agent_fee": cf_result["monthly_agent_fee"],
            "total_monthly_costs": cf_result["total_monthly_costs"],
            "net_monthly_cash_flow": cf_result["net_monthly_cash_flow"],
            "annual_rent": cf_result["annual_rent"],
            "gross_yield_pct": cf_result["gross_yield_pct"],
            "net_yield_pct": cf_result["net_yield_pct"],
            "score_cash_flow": round(cf_score, 2),
            "score_net_yield": round(yield_score, 2),
            "score_rental_demand": round(demand_score, 2),
            "score_price_vs_similar": round(price_score, 2),
            "score_mrt_access": round(mrt_score, 2),
            "score_competition": round(comp_score, 2),
            "score_building_quality": round(quality_score, 2),
            "total_score": total_score,
        })

    return results


def deduplicate_listings(listings: list[dict]) -> list[dict]:
    """Deduplicate by listing_url, keeping first occurrence.

    Secondary pass: match on project_name + price + bedrooms + area_sqft
    to catch agent-reposted same units.
    """
    seen_urls = set()
    seen_fingerprints = set()
    deduped = []

    for l in listings:
        url = l.get("url", "")
        if url in seen_urls:
            continue
        seen_urls.add(url)

        # Secondary fingerprint
        fp = (
            l["project_name"],
            l["price"],
            l["bedrooms"],
            round(l["area_sqft"]) if l["area_sqft"] else None,
        )
        if fp in seen_fingerprints:
            continue
        seen_fingerprints.add(fp)

        deduped.append(l)

    log.info(f"Dedup: {len(listings)} → {len(deduped)} unique")
    return deduped


def write_csv(results: list[dict], output_path: Path):
    """Write results to CSV."""
    if not results:
        log.warning("No results to write")
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "total_score", "project_name", "address", "state", "area_name",
        "price", "psf", "area_sqft", "bedrooms", "bathrooms", "build_year",
        "property_type", "tenure",
        "monthly_rent", "rent_source", "rent_listing_count", "sale_listing_count",
        "project_median_psf",
        "down_payment", "loan_amount", "monthly_loan", "monthly_maintenance",
        "monthly_repairs", "monthly_vacancy", "monthly_tax_insurance",
        "monthly_agent_fee", "total_monthly_costs", "net_monthly_cash_flow",
        "annual_rent", "gross_yield_pct", "net_yield_pct",
        "score_cash_flow", "score_net_yield", "score_rental_demand",
        "score_price_vs_similar", "score_mrt_access", "score_competition",
        "score_building_quality",
        "listing_id", "url", "listing_agent",
    ]

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)

    log.info(f"Wrote {len(results)} rows to {output_path}")


def write_rent_listings_csv(db_path: str, output_path: Path):
    """Export raw rent listing data from rent_cache.db to CSV for traceback."""
    if not os.path.exists(db_path):
        log.warning(f"Rent cache DB not found: {db_path}")
        return

    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "SELECT project_name, listing_id, rent, bedrooms, area_sqft, "
        "       address, url, scraped_at "
        "FROM rent_project_listings ORDER BY project_name, bedrooms"
    )
    rows = cur.fetchall()
    conn.close()

    if not rows:
        log.warning("No rent listings found in cache")
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    fieldnames = ["project_name", "listing_id", "rent", "bedrooms",
                  "area_sqft", "address", "url", "scraped_at"]

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "project_name": row[0],
                "listing_id": row[1],
                "rent": row[2],
                "bedrooms": row[3],
                "area_sqft": row[4],
                "address": row[5],
                "url": row[6],
                "scraped_at": row[7],
            })

    log.info(f"Wrote {len(rows)} rent listings to {output_path}")


def print_summary(all_results: list[dict], shortlist: list[dict]):
    """Print a human-readable summary to stdout."""
    print(f"\n{'=' * 70}")
    print(f"  RENTAL YIELD SCRAPER — SUMMARY")
    print(f"{'=' * 70}")
    print(f"  Total unique listings:     {len(all_results)}")
    print(f"  Shortlist (top 20):        {len(shortlist)}")
    print(f"  Areas with sale listings:  {len(set(r['area_name'] for r in all_results if r.get('area_name')))}")
    print(f"  Positive cash flow:        {sum(1 for r in all_results if r.get('net_monthly_cash_flow', 0) > 0)}")
    print(f"  With rent data:            {sum(1 for r in all_results if r.get('monthly_rent', 0) > 0)}")
    print(f"\n{'─' * 70}")
    print(f"  TOP 10 SHORTLIST")
    print(f"{'─' * 70}")
    print(f"  {'#':>3}  {'Score':>6}  {'Project':<30}  {'Price':>10}  {'Cash Flow':>10}  {'Yield':>6}")
    print(f"  {'─' * 3}  {'─' * 6}  {'─' * 30}  {'─' * 10}  {'─' * 10}  {'─' * 6}")
    for i, listing in enumerate(shortlist[:10], 1):
        cf = listing.get("net_monthly_cash_flow", 0)
        yield_val = listing.get("net_yield_pct", 0)
        price = listing.get("price", 0)
        print(f"  {i:>3}  {listing['total_score']:>6.2f}  {listing['project_name']:<30}  "
              f"RM{price:>8,}  {'+RM' if cf >= 0 else 'RM'}{abs(cf):>7.0f}  {yield_val:>5.2f}%")
    print(f"{'─' * 70}")


# ── Main Entry Point ────────────────────────────────────────────────────────

def run_scraper(resume: bool = False, dry_run: bool = False,
                max_areas: int | None = None, proxy_url: str | None = None):
    """Run the full scraper pipeline."""
    config = load_config()

    # ── Phase A: Discover district codes ────────────────────────────────
    log.info("Phase A: Discovering district codes...")
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "darwin", "desktop": True},
    )
    if proxy_url:
        log.info(f"Using proxy: {proxy_url}")
        scraper.proxies = {"http": proxy_url, "https": proxy_url}

    # Don't rediscover if cache exists and we're resuming
    # Use skip_discovery=True to avoid HTTP requests for unknown areas
    if not DISTRICT_CACHE.exists() and not resume:
        log.info("No district cache — creating from seeds (skip_discovery=True)")
        dd.discover_all_districts(
            str(CITIES_FILE),
            str(DISTRICT_CACHE),
            scraper,
            delay=config.get("scraper", {}).get("request_delay_seconds", 1),
            resume=True,
            skip_discovery=True,
        )
    else:
        log.info("Using existing district cache")
        # Still refresh cache to ensure all seeded codes are present
        dd.discover_all_districts(
            str(CITIES_FILE),
            str(DISTRICT_CACHE),
            scraper,
            delay=config.get("scraper", {}).get("request_delay_seconds", 1),
            resume=True,
            skip_discovery=True,
        )

    district_cache = load_district_cache()

    if dry_run:
        areas_with_code = sum(1 for v in district_cache.values()
                              if isinstance(v, dict) and v.get("code"))
        print(f"\nDry run: {len(district_cache)} areas, "
              f"{areas_with_code} with district codes")
        for area, info in sorted(district_cache.items()):
            if isinstance(info, dict):
                print(f"  {info.get('state', '?')}: {area} → code={info.get('code', 'N/A')}")
        return

    # ── Phase B: Scrape sale listings ──────────────────────────────────
    log.info("Phase B: Scraping sale listings...")
    create_state_schema(str(STATE_DB))

    cities = load_cities()
    area_count = 0

    # Count total areas for progress bar
    total_areas = sum(len(areas) for areas in cities.values())
    if max_areas:
        total_areas = min(total_areas, max_areas)

    phase_pbar = tqdm(total=total_areas, desc="Phase B", unit="area", leave=True,
                      bar_format="{desc}: {n_fmt}/{total_fmt} [{elapsed}<{remaining}]")

    for state, areas in cities.items():
        for area_name in areas:
            if max_areas and area_count >= max_areas:
                break

            # Get district code
            area_info = district_cache.get(area_name, {})
            district_code = area_info.get("code") if isinstance(area_info, dict) else None

            # Check if already fully scraped
            state_info = get_scrape_state(str(STATE_DB), area_name)
            if resume and state_info and state_info["completed"]:
                log.info(f"Skipping completed area: {area_name}")
                continue

            phase_pbar.set_postfix_str(area_name[:30], refresh=False)
            count = scrape_sale_area(
                area_name, state, district_code, config, scraper,
                str(STATE_DB), resume=resume,
            )
            log.info(f"  {area_name}: {count} listings scraped")
            phase_pbar.update(1)
            area_count += 1

        if max_areas and area_count >= max_areas:
            break

    phase_pbar.close()

    # ── Phase C: Scrape rent listings ──────────────────────────────────
    log.info("Phase C: Scraping rent listings...")
    projects = collect_unique_projects(str(STATE_DB))
    log.info(f"Found {len(projects)} unique projects")
    scrape_rent_for_projects(projects, config, scraper)

    # ── Phase D + E: Calculate metrics, rank, output ───────────────────
    log.info("Phase D/E: Scoring and ranking...")
    score_pbar = tqdm(total=1, desc="Scoring", unit="batch", leave=True,
                      bar_format="{desc}: {elapsed}")
    all_results = calculate_all_metrics(str(RENT_CACHE_DB), str(STATE_DB), config)
    score_pbar.update(1)
    score_pbar.close()
    all_results = deduplicate_listings(all_results)

    # Sort by total_score descending
    all_results.sort(key=lambda x: x.get("total_score", 0), reverse=True)

    # Write full sales CSV
    write_csv(all_results, ALL_SALES_CSV)
    log.info(f"Sales listings: {len(all_results)} → {ALL_SALES_CSV}")

    # Write rent listings CSV (raw rent data for traceback)
    write_rent_listings_csv(str(RENT_CACHE_DB), ALL_RENTALS_CSV)

    # Write shortlist (top 20)
    shortlist = all_results[:20]
    write_csv(shortlist, SHORTLIST_CSV)

    # Print summary
    print_summary(all_results, shortlist)

    log.info("✅ Scraper run complete!")


def main():
    parser = argparse.ArgumentParser(
        description="PropertyGuru Rental Yield Scraper & Scoring System"
    )
    parser.add_argument("--resume", action="store_true",
                        help="Resume incomplete scrape")
    parser.add_argument("--dry-run", action="store_true",
                        help="Discover district codes and preview only")
    parser.add_argument("--max-areas", type=int, default=None,
                        help="Limit number of areas to scrape (for testing)")
    parser.add_argument("--proxy", type=str, default=None,
                        help="HTTP/HTTPS proxy URL")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Verbose logging")

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        run_scraper(
            resume=args.resume,
            dry_run=args.dry_run,
            max_areas=args.max_areas,
            proxy_url=args.proxy,
        )
    except KeyboardInterrupt:
        log.info("\nInterrupted by user")
        sys.exit(1)
    except Exception as e:
        log.exception(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()