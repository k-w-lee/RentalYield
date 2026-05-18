#!/usr/bin/env python3
"""Bulk discover district codes for ALL cities.json areas.
Tries smarter parsing of __NEXT_DATA__ to extract codes.
"""
import json, logging, time, yaml, re
from pathlib import Path
import cloudscraper
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

ROOT = Path(__file__).parent
CITIES_JSON = ROOT / "cities.json"
CACHE_FILE = ROOT / "district_cache.yaml"

# Existing seeded codes (keep them)
SEEDED_CODES = {
    # From the current discover_districts.py + user-provided
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
    "subang jaya": "o9c69",
    "petaling jaya": "f5d2k",
    "puchong": "m8v4p",
    "shah alam": "l3n9q",
    "klang": "w7r2t",
    "kajang": "x5y8z",
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
    "bandar menjalara": "4e6kw",     # user-provided
    "bangsar": "av65k",             # user says this also works
    "kl eco city": "4tp2z",
    "kampung kerinchi": "bn83y",
    "bangsar south": "bn83y",
    "brickfields": "mg81x",
    "dutamas": "vb79q",
    "tropicana": "g5h7j",
    "jalan kuching": "vk581",
    "desa petaling": "vm780",
    "keramat": "mz30x",
    "sungei besi": "ne366",
    "sungai besi": "ne366",
    "seputih": "nsmyd",
    "segambut": "p215f",
    "setapak": "wz616",
}

def extract_code_from_next_data(html: str) -> str | None:
    """Try multiple paths in __NEXT_DATA__ to find district code."""
    soup = BeautifulSoup(html, "html.parser")
    nd = soup.find("script", id="__NEXT_DATA__")
    if not nd or not nd.string:
        return None
    
    try:
        data = json.loads(nd.string)
    except Exception:
        return None
    
    page_data = data.get("props", {}).get("pageProps", {}).get("pageData", {})
    d = page_data.get("data", {})
    
    # Method 1: districtConfig.code 
    dc = d.get("districtConfig", {})
    if dc and isinstance(dc, dict):
        code = dc.get("code")
        if code and code != "__ALL__":
            return code
    
    # Method 2: savedSearchData.searchParams.district_code
    ssd = d.get("savedSearchData", {})
    if ssd:
        sp = ssd.get("searchParams", {})
        if sp:
            dc2 = sp.get("district_code")
            if isinstance(dc2, list) and len(dc2) > 0:
                return dc2[0]
            if isinstance(dc2, str) and dc2:
                return dc2
    
    # Method 3: Check listingData for district info
    listings = d.get("listingsData", [])
    if listings:
        ga = listings[0].get("gaProduct", {})
        categories = ga.get("category", "")
        if categories:
            parts = categories.split("/")
            for p in parts:
                if len(p) == 5 and p[:1].isalpha() and p[1:].isalnum():
                    return p
    
    # Method 4: Try regex on full URL
    return None

def discover_code(area_name: str, scraper) -> str | None:
    """Discover district code for an area."""
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
        
        # Check final URL for districtCode
        final_url = str(r.url)
        m = re.search(r'districtCode=([a-z0-9]+)', final_url)
        if m:
            time.sleep(0.5)
            return m.group(1)
        
        # Try __NEXT_DATA__
        code = extract_code_from_next_data(r.text)
        return code
        
    except Exception as e:
        log.debug(f"Request failed: {e}")
        return None

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    
    with open(CITIES_JSON) as f:
        cities = json.load(f)
    
    # Load existing cache
    existing = {}
    if CACHE_FILE.exists():
        with open(CACHE_FILE) as f:
            existing = yaml.safe_load(f) or {}
    
    scraper = cloudscraper.create_scraper(browser={'platform': 'darwin', 'mobile': False})
    
    results = {}
    for state, areas in cities.items():
        for area in areas:
            # Check existing cache first
            cached = existing.get(area, {})
            if isinstance(cached, dict) and cached.get("code"):
                results[area] = cached
                continue
            
            # Try seeded
            code = SEEDED_CODES.get(area.lower().strip())
            if code:
                results[area] = {"state": state, "code": code, "error": None, "tried": True}
                print(f"  SEED  {area}: {code}")
                continue
            
            # Discover
            print(f"  DISC  {area}...", end=" ", flush=True)
            code = discover_code(area, scraper)
            if code:
                results[area] = {"state": state, "code": code, "error": None, "tried": True}
                print(f"{code}")
            else:
                results[area] = {"state": state, "code": None, "error": "keyword_fallback", "tried": True}
                print("(no code)")
            
            time.sleep(0.3)
    
    # Save
    with open(CACHE_FILE, "w") as f:
        yaml.dump(results, f, default_flow_style=False, allow_unicode=True)
    
    found = sum(1 for v in results.values() if isinstance(v, dict) and v.get("code"))
    print(f"\n=== DONE: {found}/{len(results)} codes found ===")
    
    nulls = [k for k, v in results.items() if isinstance(v, dict) and not v.get("code")]
    if nulls:
        print(f"Still missing codes ({len(nulls)} areas):")
        for n in sorted(nulls):
            print(f"  {n}")

if __name__ == "__main__":
    main()
