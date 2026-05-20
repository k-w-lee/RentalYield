#!/usr/bin/env python3
"""Quick one-shot: save district cache from SEEDED_CODES + skip discovery."""

import json
import cloudscraper
import time
import yaml
from pathlib import Path

from discover_districts import (
    SEEDED_CODES, discover_district_code, build_search_url,
)

root = Path(__file__).parent
cities_file = root / "cities.json"
cache_file = root / "district_cache.yaml"

with open(cities_file) as f:
    cities = json.load(f)

s = cloudscraper.create_scraper()
results = {}

for state, areas in cities.items():
    for area in areas:
        key = area.lower().strip()
        code = SEEDED_CODES.get(key)
        if code:
            results[area] = {"state": state, "code": code, "error": None, "tried": True}
            print(f"Seeded: {area} -> {code}")
        else:
            results[area] = {"state": state, "code": None, "error": "not_in_seed", "tried": True}
            print(f"No seed: {area}")

with open(cache_file, "w") as f:
    yaml.dump(results, f, default_flow_style=False, allow_unicode=True)

found = sum(1 for v in results.values() if isinstance(v, dict) and v.get("code"))
print(f"\nSaved: {found} codes, {len(results) - found} fallbacks")
