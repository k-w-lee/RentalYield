#!/usr/bin/env python3
"""Quickly discover codes for a list of known-missing areas."""
import re, time, yaml, json
from pathlib import Path
import cloudscraper
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent
CACHE_FILE = ROOT / "district_cache.yaml"

MISSING_ARENAS = [
    "Bandar Sri Damansara",
    "Bukit Kiara",
    "Bukit Tunku (Kenny Hills)",
    "Jalan Klang Lama (Old Klang Road)",
    "Kampung Kerinchi (Bangsar South)",
    "Pantai",
    "Taman Desa",
    "Salak Selatan",
    "Jinjang",
    "Hulu Kelang",
    "Ulu Kelang",
    "Sungai Penchala",
    "Sunway Spk",
    "Sungai Buloh",
    "Sungai Besi",
    "Melawati",
    "Kuala Lumpur",
    "Sepang",
    "Puchong Perdana",
    "Damansara Perdana",
    "Port Klang (Pelabuhan Klang)",
    "Bandar Sri Damansara",
]

scraper = cloudscraper.create_scraper(browser={'platform': 'darwin', 'mobile': False})

def discover(area_name):
    encoded = area_name.replace(" ", "+")
    url = (f"https://www.propertyguru.com.my/property-for-sale"
           f"?listingType=sale&page=1"
           f"&propertyTypeGroup=N"
           f"&propertyTypeCode=APT&propertyTypeCode=CONDO&propertyTypeCode=FLAT&propertyTypeCode=SRES"
           f"&isCommercial=false&maxTopYear=2026&minTopYear=2009"
           f"&_freetextDisplay={encoded}")
    
    try:
        r = scraper.get(url, timeout=30, allow_redirects=True)
        if r.status_code != 200:
            return None
        
        # Check final URL
        m = re.search(r'districtCode=([a-z0-9]+)', str(r.url))
        if m:
            return m.group(1)
        
        # Check __NEXT_DATA__
        soup = BeautifulSoup(r.text, "html.parser")
        nd = soup.find("script", id="__NEXT_DATA__")
        if nd and nd.string:
            data = json.loads(nd.string)
            d = data.get("props",{}).get("pageProps",{}).get("pageData",{}).get("data",{})
            dc = d.get("districtConfig",{}).get("code")
            if dc and dc != "__ALL__":
                return dc
            sp = d.get("savedSearchData",{}).get("searchParams",{})
            dc2 = sp.get("district_code")
            if isinstance(dc2, list) and len(dc2) > 0:
                return dc2[0]
        return None
    except:
        return None

found = {}
for area in MISSING_ARENAS:
    print(f"  {area}...", end=" ", flush=True)
    code = discover(area)
    if code:
        found[area] = code
        print(code)
    else:
        print("(no code)")
    time.sleep(0.5)

print(f"\nFound {len(found)}/{len(MISSING_ARENAS)}")
for k, v in sorted(found.items()):
    print(f'    "{k.lower()}": "{v}",')
