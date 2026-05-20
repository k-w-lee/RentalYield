# PropertyGuru Rental Yield Scraper & Scoring System

A data-driven shortlisting tool for property investors. Scrapes sale and rent listings from **PropertyGuru Malaysia** for KL & Selangor residential properties, calculates rental yield and cash flow, ranks opportunities by a weighted scoring model, and outputs a top 10–20 shortlist for manual review.

## Pipeline

```
cities.json ─────────┐
                     ▼
          discover_districts.py ──── live district extraction
          (generic search pages)     (no cache file)
                                                  │
                  config.yaml ──► scraper.py ◄────┘
                                     │
                  ┌────────────────────┼────────────────────┐
                  ▼                    ▼                    ▼
     sale_listings.db           rent_proxy.py         scrape_state.db
     (sale_listings table)      (per-project rent)    (resume state)
                  │                    │
                  └──────────┬─────────┘
                             ▼
                   join by project+bedroom
                             │
                    ┌────────┴────────┐
                    ▼                 ▼
               loan.py           score.py
                    │                 │
                    └────────┬────────┘
                             ▼
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
     all_sales_listings  all_rentals     top_shortlist
     .csv                _listings.csv   .csv
     
area_progress.csv (per-area status)
terminal summary (top 10)
```

## Quick Start

```bash
pip install cloudscraper beautifulsoup4 lxml pyyaml

# Full run: discover districts, scrape sales + rent, score, output
python3 scraper.py

# Resume an incomplete run (skips completed areas)
python3 scraper.py --resume

# Test with a few areas
python3 scraper.py --max-areas 3

# Scrape a specific area
python3 scraper.py --area "Bangsar"

# Just discover district codes (preview only, no scrape)
python3 scraper.py --dry-run

# Route through a proxy
python3 scraper.py --proxy http://127.0.0.1:8080

# Verbose debug logging
python3 scraper.py --verbose
```

If you get `no module named 'lxml'`, install via `pip install lxml` or the scraper will fall back to Python's built-in HTML parser.

## Output Files

| File | Description |
|---|---|
| `output/all_sales_listings.csv` | All scored & ranked sale listings with rent estimates and full metrics |
| `output/all_rentals_listings.csv` | Raw rent listing data for traceback |
| `output/top_shortlist.csv` | Top 20 shortlist by weighted score |
| `output/area_progress.csv` | Per-area scrape status, listing counts, completion state |
| Terminal summary | Top 10 listings with score, price, cash flow, yield |

## CLI Options

| Flag | Default | Description |
|---|---|---|
| `--resume` | off | Resume incomplete scrape (skips completed areas) |
| `--dry-run` | off | Discover districts and preview only (no scrape) |
| `--max-areas N` | all | Limit number of areas to scrape |
| `--area NAME` | — | Scrape only a specific area (case-insensitive substring match) |
| `--proxy URL` | — | HTTP/HTTPS proxy for all requests |
| `--verbose` / `-v` | off | Debug-level logging |

## Files

| File | Purpose |
|---|---|
| `scraper.py` | Main entry point — orchestrates full pipeline (Phase A–E) |
| `discover_districts.py` | Live district code discovery from generic PG search pages |
| `discover_all_codes.py` | Bulk code discovery helper script |
| `discover_missing.py` | Script for investigating unmatched/fallback areas |
| `rent_proxy.py` | Scrapes rent listings, groups by project+bedroom, calculates median rent |
| `loan.py` | Amortisation formula, net monthly cash flow, gross/net yield |
| `score.py` | 7-component scoring engine with linear normalisation |
| `config.yaml` | All tunable parameters (scraper, loan, costs, scoring weights) |
| `cities.json` | KL & Selangor area definitions |
| `save_cache.py` | Cache utility |
| `PRD.md` | Full product requirements document |

## Configuration

Edit `config.yaml` to tune:

- **Price range** — `min_price`, `max_price` (RM 100k–1M default)
- **Build year** — `min_top_year_sale`, `max_top_year_sale`, `min_top_year_rent`
- **Request rate** — `request_delay_seconds`, `max_retries`
- **Keyword search** — `max_keyword_pages` (5 default; areas without district codes)
- **Loan assumptions** — `down_payment_percent` (10%), `tenure_years` (35), `interest_rate_percent` (4.0%)
- **Cost buffers** — maintenance fee, repairs (5%), vacancy (8.33% ≈ 1 mo/yr), tax/insurance, agent fee
- **Scoring weights** — all 7 component weights
- **MRT scores** — per-area manual scores (0–10, default 5)
- **Thresholds** — cash flow perfect (RM 500), net yield perfect (6%), building quality year range

## Scoring Model

| Component | Weight | Score = 10 | Score = 0 |
|---|---|---|---|
| Net cash flow estimate | 25% | ≥ RM 500/mo | ≤ RM 0 |
| Net rental yield | 20% | ≥ 6.0% | ≤ 0% |
| Rental demand score | 15% | Top percentile | No rent listings |
| Price vs similar listings | 15% | psf ≥20% below median | psf ≥20% above median |
| MRT/LRT/job hub access | 10% | Config (manual) | Config (manual) |
| Competition / future supply risk | 10% | ≤ 5 listings | ≥ 30 listings |
| Building quality proxy | 5% | 2026 build | 2008 build |

All components use **linear interpolation** between floor and ceiling → 0–10 score, then weighted sum.

## District Code Discovery

District codes are **not stored** — they are extracted **live every run**:

1. **Phase A-1**: Scrape up to 20 generic search pages (no district filter)
2. **Extract every listing's** `additionalData.districtCode` + `districtText`
3. **Match each area** to a district: exact → parenthetical → comma-part → fuzzy substring (>0.6 ratio)
4. **Fallback**: unmatched areas use keyword search (`_freetextDisplay`) capped at 5 pages
5. **Validation**: if a district code returns 0 listings, auto-fallback to keyword

PG rotates codes regularly — live discovery is the only reliable approach.

## Deduplication

- **Primary**: PropertyGuru listing ID (`listingData.id`)
- **Secondary**: project name + price + bedrooms + area size (catches agent-reposted units)

## Proxy Support

Pass `--proxy http://user:pass@host:port` to route all requests through a proxy. Useful if PropertyGuru starts rate-limiting your IP.

## Resume Flow

Run with `--resume` to pick up where you left off:
- Completed areas are skipped
- Partially-scraped areas resume from last page
- Rent data is cached per project — already-scraped projects are skipped
- State is stored in `scrape_state.db` and `rent_cache.db`
