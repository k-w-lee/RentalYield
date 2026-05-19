#!/usr/bin/env python3
"""Bulk discover district codes for ALL cities.json areas.
Thin wrapper around discover_districts.py: live extraction from generic search pages.
"""
import logging
from pathlib import Path
import cloudscraper
from discover_districts import discover_all_districts

log = logging.getLogger(__name__)

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
        print(f"\nCodes found ({len(found)}):")
        for name, code in sorted(found.items(), key=lambda x: x[0]):
            print(f"  {name}: {code}")
    if failed:
        print(f"\nKeyword fallback areas ({len(failed)}):")
        for name in sorted(failed.keys()):
            print(f"  {name}")
