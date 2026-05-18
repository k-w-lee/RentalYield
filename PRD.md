# PRD: PropertyGuru Rental Yield Scraper & Scoring System

**Version:** 1.3
**Date:** 2026-05-18 — Implemented. See deliverables below.
**Author:** [Your Name]

---

## 1. Purpose

Build a scraper that collects property listings from **PropertyGuru Malaysia** for KL & Selangor residential properties, derives average rental income (by scraping rent listings for the same project), calculates rental yield and cash flow estimates, scores each listing using the shortlist logic weights, and ranks the top opportunities.

The system is a **data-driven shortlisting tool** — it narrows hundreds of listings to a manageable top 10–20 for manual validation.

---

## 2. Dual-Source Design: Sale + Rent

The scraper collects data from **two related listing types** and joins them by project:

### 2.1. Sale URL Pattern

```
https://www.propertyguru.com.my/property-for-sale
  ?listingType=sale
  &page=<N>
  &districtCode=<code>
  &propertyTypeGroup=N
  &propertyTypeCode=APT&propertyTypeCode=CONDO&propertyTypeCode=FLAT&propertyTypeCode=SRES
  &isCommercial=false
  &minPrice=100000&maxPrice=1000000
  &minTopYear=2009&maxTopYear=2026
```

### 2.2. Rent URL Pattern

```
https://www.propertyguru.com.my/property-for-rent
  ?listingType=rent
  &page=<N>
  &districtCode=<code>
  &propertyTypeGroup=N
  &propertyTypeCode=APT&propertyTypeCode=CONDO&propertyTypeCode=FLAT&propertyTypeCode=SRES
  &isCommercial=false
  &minTopYear=2008&maxTopYear=2026
```

No price range on rent (area-level filtering is sufficient).

### 2.3. Rental Data → Sale Data Join Strategy

For each **unique project** discovered in sale listings:

1. Scrape rent listings scoped to that project name
2. Group rent listings by **bedroom count** (same bedrooms as the sale unit)
3. Calculate **median asking rent** per bedroom count
4. If insufficient data for that bedroom count → fall back to project-wide median
5. If no rent data for the project → fall back to area-level median
6. Assign the matched median rent to each sale listing

This produces the best rental income proxy: a 3BR unit uses median rent of other 3BR units in the same project.

---

## 3. Project Scope

### In Scope

- **Source:** PropertyGuru Malaysia (propertyguru.com.my)
- **Listing types:** Sale + Rent
- **Geography:** Kuala Lumpur & Selangor (as defined in `cities.json`)
- **Property types:** Apartment, Condominium, Flat, Service Residence (`isCommercial=false`)
- **Sale price range:** RM 100,000 – RM 1,000,000
- **Build year:** 2008+
  - Sale: `minTopYear=2009`, `maxTopYear=2026`
  - Rent: `minTopYear=2008`, `maxTopYear=2026`
- **Extracted data (sale):** Asking price, built-up size (sqft), bedrooms, bathrooms, furnishing, location, project name, listing URL, maintenance fee (when available), listing agent
- **Extracted data (rent):** Monthly asking rent, bedrooms, project name, location, listing URL
- **Calculated metrics:** Price psf, gross rental yield, net rental yield, net monthly cash flow, each scoring component, final weighted score
- **Scoring model:** 7-component weighted table (Section 7)
- **Output:** Full CSV + ranked shortlist CSV (top 10–20; final manual review should narrow to top 3–5)

### Deduplication

Listings duplicated across overlapping KL/Selangor areas will be deduplicated by:
- Primary key: `listing_url` (PropertyGuru unique listing ID)
- Secondary pass: match on project name + unit size + bedrooms + price (catches agent-reposted same units)

### Tenant Profile Inference

Where possible, infer target tenant type from location context (e.g., university proximity → students, MRT → working adults, expat areas → expats). This informs the rental demand score (Phase 1: simple tags; Phase 2: richer inference).

### Out of Scope (Phase 1)

- iProperty scraping
- Transaction / actual sale price data (Brickz / EdgeProp / JPPH)
- Actual rental achieved (vs. asking rent)
- Building management quality assessment (site visit needed)
- Property management effort / tenant screening
- Legal / title checks
- Airbnb / short-stay permission check
- Capital appreciation prediction

---

## 4. Decisions & Clarifications (Resolved)

| # | Question | Decision |
|---|---|---|
| 1 | Rental data source? | Scrape PropertyGuru **rent** listings. Join by project + bedroom count. |
| 2 | Missing maintenance fee? | Configurable default (admin-changeable, e.g. RM 0.30/sqft). |
| 3 | Loan assumptions? | Malaysia averages: 10% down, 35 yrs, 4.0% interest. All configurable. |
| 4 | District codes? | Auto-discovered by scraper (search → redirect → extract `districtCode`). |
| 5 | Overlapping areas (KL vs Selangor)? | Keep both — district codes likely differ. Tag by state. |
| 6 | Rural areas? | Scrape all; if 0 results, skip. No manual filter. |
| 7 | iProperty? | Phase 2. |
| 8 | Frequency? | One-time initial batch + monthly runs with resume support. |
| 9 | Build year filter? | URL param `minTopYear`/`maxTopYear` works on both sale and rent URLs. Rent uses `minTopYear=2008`. |

---

## 5. Technical Approach

### 5.1. Stack

- **Language:** Python 3
- **Scraping:** `cloudscraper` (bypasses Cloudflare) + `BeautifulSoup` for parse + built-in Next.js `__NEXT_DATA__` JSON extraction
- **Data storage:** CSV (output); SQLite (dedup, rent cache, resume state)
- **Loan calculator:** Standard amortisation formula
- **Scoring:** Python module with configurable weights from `config.yaml`

### 5.2. Pipeline

```
PHASE A — DISCOVERY
1.  Read cities.json
2.  For each area in KUALA_LUMPUR + SELANGOR:
    a. Build free-text search URL with area name
    b. Request → follow redirect → extract districtCode from final URL
    c. Cache to district_cache.yaml

PHASE B — SALE SCRAPE
3.  For each area with known districtCode:
    a. Paginate sale listings (2s delay between pages)
    b. Extract `__NEXT_DATA__` JSON from page HTML → `pageData.data.listingsData[*]`
    c. Parse each listing: price (from `gaProduct.price`), sqft, bedrooms, bathrooms,
       project name (`localizedTitle` + address), build year, unit area, listing URL,
       maintenance fee (when available), listing ID
    d. Collect unique project names → feed Phase C
    e. Save resume state (area, page) to scrape_state.db
    f. Use `paginationData.totalPages` to stop when exceeded

PHASE C — RENT SCRAPE
4.  For each unique project from Phase B:
    a. Search rent listings for that project
    b. Parse: monthly rent, bedrooms, URL
    c. Group by bedroom count
    d. Calculate median rent per bedroom count
    e. Cache to rent_cache.db (project + bedrooms → median_rent)

PHASE D — JOIN & CALCULATE
5.  For each sale listing:
    a. Lookup: rent_cache[project][bedrooms] → median monthly rent
       Fallback: rent_cache[project][*] → project median
       Fallback: area-level median from config
    b. Calculate monthly loan repayment via loan.py
    c. Calculate gross yield, net yield, net monthly cash flow
    d. Calculate each scoring component (Section 7)
    e. Compute weighted total score

PHASE E — OUTPUT
6.  Deduplicate across areas (same listing URL → keep first)
7.  Sort descending by score
8.  Write all_listings.csv + top_shortlist.csv
9.  Print summary: areas scraped, listings found, top 10
```

### 5.3. Resume & Monthly Run Logic

- **State file:** `scrape_state.db` (SQLite)
  - Table `scrape_state`: `area_name`, `listing_type`, `last_page`, `total_pages`, `completed`, `scraped_at`
  - Table `rent_cache`: `project_name`, `bedrooms`, `median_rent`, `listing_count`, `scraped_at`
- **Monthly run:**
  - If full scrape completed < 30 days → skip
  - If incomplete (`last_page < total_pages`) → resume from last page
  - If > 30 days → full re-scrape (clear state)
- **Idempotency:** Sale listings keyed by PropertyGuru URL → upsert

### 5.4. District Code Discovery

```
For each area in cities.json:
  - Build: /property-for-sale?listingType=sale&_freetextDisplay=<area>&...
  - Send request → follow redirects
  - Parse districtCode from final URL (or `districtConfig.code` in `__NEXT_DATA__`)
  - If resolved → cache: district_cache.yaml {area: {state: code}}

Edge cases:
  - Multiple areas → same code (e.g. "Desa Parkcity" / "Desa Park City")
    → map all aliases to the same code
  - No results for area → log and skip
  - Code changes between runs → re-discover each full cycle
```

### 5.5. Page Data Extraction (Next.js)

PropertyGuru is built on Next.js. Listing data is serialised into the page HTML via:

```
<script id="__NEXT_DATA__" type="application/json">
  { "props": { "pageProps": { "pageData": { "data": { ... } } } } }
</script>
```

**Listing data location:** `.__NEXT_DATA__.props.pageProps.pageData.data.listingsData[*]`
- `listingData.id` — unique listing ID
- `listingData.localizedTitle` — project name
- `listingData.fullAddress` — address string
- `listingData.listingFeatures` — array of feature groups (bedrooms, bathrooms, area, property type, tenure, build year)
- `listingData.property.id` — project ID
- `listingData.property.typeCode` — SALE or RENT
- `gaProduct.price` — price as string integer
- `gaProduct.brand` — developer name
- `paginationData` — currentPage, totalPages
- `segment.legacyParameters.metaData.Price` — price as number

**No HTML parsing needed** — all structured data is in the embedded JSON.

---

## 6. Metrics Calculation

### 6.1. Gross Rental Yield

```
Gross Yield (%) = (Median Annual Asking Rent / Purchase Price) × 100
```

Example: RM 500k purchase, RM 2,000/month median rent → (24,000 / 500,000) × 100 = **4.8%**

### 6.2. Net Rental Yield

```
Net Annual Cash Flow = Annual Rent − Annual Loan Repayment − Annual Costs
Net Yield (%) = (Net Annual Cash Flow / Down Payment) × 100
```

### 6.3. Net Monthly Cash Flow

```
Net Monthly Cash Flow = Monthly Rental Income
                      − Monthly Loan Repayment
                      − Monthly Maintenance Fee
                      − Monthly Repairs Buffer (5% of rent)
                      − Monthly Vacancy Buffer (8.33% = 1 month/year)
                      − Monthly Tax/Insurance (configurable, e.g. RM 150)
                      − Monthly Agent Fee (0% if self-manage)
```

### 6.4. Loan Repayment (Standard Amortisation)

```
M = P × [r(1+r)^n] / [(1+r)^n − 1]

  P = Loan amount   = Purchase Price − (Purchase Price × down_payment_pct)
  r = Monthly rate  = Annual interest rate / 12
  n = Total months  = Tenure years × 12
```

Default: 10% down → 90% loan. Rate 4.0% p.a. → 0.333% monthly. 35 years → 420 months.

### 6.5. Price Per Sqft (psf)

```
psf = Purchase Price / Built-Up Size (sqft)
```

---

## 7. Shortlist Scoring Model

Exact mapping from `resources/context.md` recommended shortlist logic:

| # | Score Component | Weight | Calculation Source |
|---|---|---|---|
| 1 | **Net cash flow estimate** | **25%** | From Section 6.3 — higher positive cash flow = better |
| 2 | **Net rental yield** | **20%** | From Section 6.2 — higher yield = better |
| 3 | **Rental demand score** | **15%** | Count of rent listings in same project (more = higher demand) |
| 4 | **Price vs similar listings** | **15%** | Sale psf vs median psf of same project — below median = good value |
| 5 | **MRT/LRT/job hub access** | **10%** | Phase 1: manual tag per area in config; Phase 2: GIS auto-calc |
| 6 | **Competition / future supply risk** | **10%** | Inverse: count of similar sale listings in same project — more = riskier |
| 7 | **Building quality proxy** | **5%** | Based on build year recency — newer = higher score |

**Total: 100%**

### 7.1. Normalisation (Each Component → 0–10 Score)

| Component | Score = 10 when | Score = 0 when |
|---|---|---|
| Net cash flow | ≥ RM 500/month positive | ≤ RM 0 |
| Net rental yield | ≥ 6.0% | ≤ 0% |
| Rental demand | Top percentile of listing counts | No rent listings found |
| Price vs similar | psf ≥20% below project median | psf ≥20% above project median |
| MRT/LRT access | Manual config (Phase 1: 10 = prime, 5 = good, 0 = poor) | — |
| Competition risk | ≤ 5 similar sale listings in project | ≥ 30 similar sale listings |
| Building quality | Build year = 2026 | Build year = 2008 |

Linear interpolation between floor and ceiling.

### 7.2. Final Score

```
Final Score (0–10) = (Cash Flow × 0.25)
                   + (Net Yield × 0.20)
                   + (Rental Demand × 0.15)
                   + (Price vs Similar × 0.15)
                   + (MRT Access × 0.10)
                   + (Competition Risk × 0.10)
                   + (Building Quality × 0.05)
```

Shortlist = top **10–20 listings** by final score.

---

## 8. Configuration (`config.yaml`)

```yaml
scraper:
  min_price: 100000
  max_price: 1000000
  min_top_year_sale: 2009
  max_top_year_sale: 2026
  min_top_year_rent: 2008
  max_top_year_rent: 2026
  request_delay_seconds: 2
  max_retries: 3
  user_agents:
    - "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ..."
    - "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ..."

loan:
  down_payment_percent: 10
  tenure_years: 35
  interest_rate_percent: 4.0

costs:
  maintenance_fee_default_psf: 0.30
  repairs_buffer_percent: 5           # of monthly rent
  vacancy_buffer_percent: 8.33        # ~1 month/year
  tax_insurance_monthly: 150          # RM
  agent_fee_percent: 0                # 0 if self-manage

scoring:
  weights:
    net_cash_flow: 0.25
    net_rental_yield: 0.20
    rental_demand: 0.15
    price_vs_similar: 0.15
    mrt_access: 0.10
    competition_risk: 0.10
    building_quality: 0.05
  thresholds:
    cash_flow_perfect: 500            # RM/month → score 10
    net_yield_perfect: 6.0            # % → score 10
    building_quality_min_year: 2008
    building_quality_max_year: 2026
  mrt_manual_scores:
    # Phase 1: manual MRT scores per area (0–10)
    # Phase 2: auto-calculated
    Desa Parkcity: 7
    Mont Kiara: 6
    Bangsar: 8
    KL City Centre: 9
    # ... populate as needed
```

All values admin-changeable — no code changes needed.

---

## 9. Deliverables

| # | File | Purpose |
|---|---|---|
| 1 | `discover_districts.py` | Scans areas → discovers & caches district codes |
| 2 | `scraper.py` | Main entry point — orchestrates sale + rent scraping |
| 3 | `rent_proxy.py` | Scrapes rent listings, groups by project+bedroom, calculates median |
| 4 | `loan.py` | Amortisation formula → monthly repayment |
| 5 | `score.py` | Scoring engine (7 components, normalisation, weighted sum) |
| 6 | `config.yaml` | All tunable parameters |
| 7 | `rent_cache.db` | Auto-created SQLite — cached rent data per project |
| 8 | `scrape_state.db` | Auto-created SQLite — resume state |
| 9 | `output/all_listings.csv` | Full scraped dataset with all metrics |
| 10 | `output/top_shortlist.csv` | Ranked shortlist (top 20) |
| 11 | `README.md` | Setup & usage instructions |

---

## 10. Constraints & Risks

| Risk | Mitigation |
|---|---|
| Cloudflare bot protection | Use `cloudscraper` (TLS fingerprint + cookie reverse-engineer) — confirmed working on 2026-05-18 |
| Rate limiting / IP blocking | 2s between pages; rotating user-agents from config |
| Next.js client-side rendering bypass | Extract listing data from `__NEXT_DATA__` script tag (server-serialised JSON) — no Playwright needed |
| Missing maintenance fee | Configurable default in `config.yaml` |
| Missing rental data for some projects | Area-level median fallback |
| URL structure changes | Log errors prominently; flexible URL builder |
| District code churn | Re-discover on each full re-scrape |
| Legal / ToS compliance | Review `robots.txt`; respect crawl-delay; limit frequency |

---

## 11. Architecture Diagram (Text)

```
cities.json ──► discover_districts.py ──► district_cache.yaml
                                                  │
                  config.yaml ──► scraper.py ◄────┘
                                     │
                       ┌─────────────┼──────────────┐
                       ▼             ▼              ▼
                sale_listings   rent_scraper    scrape_state.db
                       │             │
                       └──────┬──────┘
                              ▼
                    join by project+bedroom
                              │
                     ┌────────┴────────┐
                     ▼                 ▼
                loan.py           score.py
                     │                 │
                     └────────┬────────┘
                              ▼
                    output/top_shortlist.csv
```

---

## 12. Future Phases

| Phase | Scope |
|---|---|
| **Phase 2** | iProperty integration, dual-source dedup |
| **Phase 3** | Transaction price lookup (Brickz/EdgeProp API) |
| **Phase 4** | Auto MRT/LRT distance via GIS (replace manual scores) |
| **Phase 5** | Dashboard / web UI for interactive filtering & map view |
| **Phase 6** | Automated monthly run with email/notification delivery |

---

## 13. Filesystem Layout

```
RentalYield/
├── PRD.md                          ← This document
├── readme.md
├── cities.json                     ← Area definitions (KL + Selangor)
├── config.yaml                     ← All tunable parameters
├── district_cache.yaml             ← Auto-generated (district codes)
├── scrape_state.db                 ← Auto-generated (resume state)
├── rent_cache.db                   ← Auto-generated (rental data cache)
├── scraper.py                      ← Main entry point
├── discover_districts.py           ← District code discovery
├── rent_proxy.py                   ← Rent scraper & median calculator
├── loan.py                         ← Amortisation & cash flow
├── score.py                        ← Scoring engine
├── output/
│   ├── all_listings.csv            ← Full scored dataset
│   └── top_shortlist.csv           ← Ranked top 20
├── resources/
│   └── context.md                  ← Source of scoring weights etc.
└── README.md                       ← Setup & usage
```
