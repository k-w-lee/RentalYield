# PropertyGuru Rental Yield Scraper & Scoring System

A data-driven shortlisting tool for property investors. Scrapes sale and rent listings from **PropertyGuru Malaysia** for KL & Selangor residential properties, calculates rental yield and cash flow, ranks opportunities by a weighted scoring model, and outputs a top 10–20 shortlist for manual review.

## Pipeline

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

## Quick Start

```bash
pip install cloudscraper beautifulsoup4 lxml pyyaml

# Discover district codes & scrape (full run)
python3 scraper.py

# Resume an incomplete run
python3 scraper.py --resume

# Test with a few areas
python3 scraper.py --max-areas 3

# Just discover district codes (preview)
python3 scraper.py --dry-run

# Use a proxy
python3 scraper.py --proxy http://127.0.0.1:8080
```

If you get `no module named 'lxml'`, install via `pip install lxml` or the scraper will fall back to Python's built-in HTML parser.

## Output

- `output/all_sales_listings.csv` — All scored & ranked sale listings with rent estimates
- `output/all_rentals_listings.csv` — Raw rent listing data for traceback
- `output/top_shortlist.csv` — Top 20 shortlist by weighted score
- Terminal summary showing top 10 with score, price, cash flow, yield

## Files

| File | Purpose |
|---|---|
| `scraper.py` | Main entry point — orchestrates full pipeline |
| `discover_districts.py` | Scans areas, discovers & caches PropertyGuru district codes |
| `rent_proxy.py` | Scrapes rent listings, groups by project+bedroom, calculates median rent |
| `loan.py` | Amortisation formula, net monthly cash flow, gross/net yield |
| `score.py` | 7-component scoring engine with normalisation |
| `config.yaml` | All tunable parameters (scraper, loan, costs, scoring weights) |
| `cities.json` | KL & Selangor area definitions |
| `PRD.md` | Full product requirements document |

## Configuration

Edit `config.yaml` to tune:

- **Price range** (min/max price, build year)
- **Loan assumptions** (down payment, tenure, interest rate)
- **Cost buffers** (maintenance fee, repairs, vacancy, tax/insurance, agent fee)
- **Scoring weights** (7 component weights)
- **MRT scores** (per-area manual scores, Phase 1)

## Scoring Model

| Component | Weight |
|---|---|
| Net cash flow estimate | 25% |
| Net rental yield | 20% |
| Rental demand score | 15% |
| Price vs similar listings | 15% |
| MRT/LRT/job hub access | 10% |
| Competition / future supply risk | 10% |
| Building quality proxy | 5% |

## Deduplication

- Primary: PropertyGuru listing URL
- Secondary: project name + price + bedrooms + area size

## Proxy Support

Pass `--proxy http://user:pass@host:port` to route all requests through a proxy. Useful if PropertyGuru starts rate-limiting your IP.
