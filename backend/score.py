#!/usr/bin/env python3
"""Scoring engine for PropertyGuru rental yield shortlist.

7-component weighted model, 0-10 per component, linear interpolation.
"""


def _linear_score(value, perfect, zero):
    """Linear interpolation: value between zero and perfect → 0-10 score."""
    if perfect == zero:
        return 5.0
    clamped = max(min(value, perfect), zero)
    return 10.0 * (clamped - zero) / (perfect - zero)


def score_cash_flow(net_monthly_cf: float, perfect: float = 500.0) -> float:
    """Net cash flow ≥ perfect → 10. ≤ 0 → 0. Linear between."""
    return _linear_score(net_monthly_cf, perfect, 0.0)


def score_net_yield(net_yield_pct: float, perfect: float = 6.0) -> float:
    """Net yield ≥ perfect → 10. ≤ 0 → 0. Linear between."""
    return _linear_score(net_yield_pct, perfect, 0.0)


def score_rental_demand(
    rent_listing_count: int,
    max_count: int,
    min_count: int = 0,
) -> float:
    """More rent listings in same project = higher demand.
    Uses distribution percentile: top → 10, no listings → 0.

    To keep it simple without pre-scanning all data:
    - 0 listings → 0
    - 1-5 → low (2)
    - 6-15 → medium (5)
    - 16-30 → good (7)
    - 30+ → excellent (10)
    """
    if rent_listing_count <= 0:
        return 0.0
    # Use linear scale up to a cap
    effective_max = max(max_count, 30)
    return min(10.0, 10.0 * rent_listing_count / effective_max)


def score_price_vs_similar(
    listing_psf: float,
    project_median_psf: float,
) -> float:
    """Sale psf vs median psf of same project.
    - 20% below median psf → 10
    - Equal to median → 5
    - 20% above median psf → 0
    """
    if project_median_psf <= 0:
        return 5.0  # neutral when no comparison

    ratio = listing_psf / project_median_psf
    # ratio=0.8 → 10, ratio=1.0 → 5, ratio=1.2 → 0
    raw = 10 - ((ratio - 0.8) / 0.4) * 10
    return max(0.0, min(10.0, raw))


def score_mrt_access(manual_score: float) -> float:
    """MRT/LRT access: manual score from config (0-10)."""
    return max(0.0, min(10.0, manual_score))


def score_competition(sale_listing_count: int) -> float:
    """More similar sale listings in same project = higher competition risk.
    ≤ 5 → 10 (low risk), ≥ 30 → 0 (high risk).
    """
    return _linear_score(-sale_listing_count, -5, -30)


def score_building_quality(
    build_year: int,
    min_year: int = 2008,
    max_year: int = 2026,
) -> float:
    """Newer building → higher score.
    max_year → 10, min_year → 0.
    """
    return _linear_score(build_year, max_year, min_year)


def compute_weighted_score(
    cash_flow_score: float,
    net_yield_score: float,
    rental_demand_score: float,
    price_vs_similar_score: float,
    mrt_score: float,
    competition_score: float,
    quality_score: float,
    weights: dict,
) -> float:
    """Compute final weighted score (0-10).

    Args:
        weights: dict with keys matching exactly:
            net_cash_flow, net_rental_yield, rental_demand,
            price_vs_similar, mrt_access, competition_risk, building_quality
    """
    total = (
        cash_flow_score * weights.get("net_cash_flow", 0.25)
        + net_yield_score * weights.get("net_rental_yield", 0.20)
        + rental_demand_score * weights.get("rental_demand", 0.15)
        + price_vs_similar_score * weights.get("price_vs_similar", 0.15)
        + mrt_score * weights.get("mrt_access", 0.10)
        + competition_score * weights.get("competition_risk", 0.10)
        + quality_score * weights.get("building_quality", 0.05)
    )
    return round(total, 2)


__all__ = [
    "score_cash_flow",
    "score_net_yield",
    "score_rental_demand",
    "score_price_vs_similar",
    "score_mrt_access",
    "score_competition",
    "score_building_quality",
    "compute_weighted_score",
]


# Quick smoke test
if __name__ == "__main__":
    sample_weights = {
        "net_cash_flow": 0.25,
        "net_rental_yield": 0.20,
        "rental_demand": 0.15,
        "price_vs_similar": 0.15,
        "mrt_access": 0.10,
        "competition_risk": 0.10,
        "building_quality": 0.05,
    }
    total = compute_weighted_score(
        cash_flow_score=score_cash_flow(200.0),
        net_yield_score=score_net_yield(4.5),
        rental_demand_score=score_rental_demand(15, 50),
        price_vs_similar_score=score_price_vs_similar(500, 550),
        mrt_score=score_mrt_access(7),
        competition_score=score_competition(8),
        quality_score=score_building_quality(2019),
        weights=sample_weights,
    )
    print(f"Sample weighted score: {total} / 10")
