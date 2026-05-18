#!/usr/bin/env python3
"""Loan amortisation calculator for Malaysia property investment."""

import math


def monthly_repayment(principal: float, annual_rate_pct: float, tenure_years: int) -> float:
    """Calculate fixed monthly loan repayment using standard amortisation formula.

    M = P × [r(1+r)^n] / [(1+r)^n - 1]

    Args:
        principal: Loan amount (RM)
        annual_rate_pct: Annual interest rate in percent (e.g. 4.0)
        tenure_years: Loan tenure in years

    Returns:
        Monthly repayment amount (RM)
    """
    if principal <= 0 or annual_rate_pct <= 0 or tenure_years <= 0:
        return 0.0

    monthly_rate = annual_rate_pct / 100 / 12
    num_payments = tenure_years * 12

    if monthly_rate == 0:
        return principal / num_payments

    compound = (1 + monthly_rate) ** num_payments
    return principal * (monthly_rate * compound) / (compound - 1)


def total_interest(principal: float, annual_rate_pct: float, tenure_years: int) -> float:
    """Calculate total interest paid over loan tenure."""
    monthly = monthly_repayment(principal, annual_rate_pct, tenure_years)
    return (monthly * tenure_years * 12) - principal


def net_monthly_cash_flow(
    monthly_rent: float,
    purchase_price: float,
    down_payment_pct: float,
    annual_rate_pct: float,
    tenure_years: int,
    area_sqft: float,
    maintenance_psf: float,
    repairs_pct: float,
    vacancy_pct: float,
    tax_insurance_monthly: float,
    agent_fee_pct: float,
) -> dict:
    """Calculate net monthly cash flow and related metrics.

    Returns dict with:
      - monthly_loan_repayment
      - monthly_maintenance
      - monthly_repairs
      - monthly_vacancy
      - monthly_tax_insurance
      - monthly_agent_fee
      - total_monthly_costs
      - net_monthly_cash_flow
      - annual_rent
      - gross_yield_pct
      - net_yield_pct
    """
    loan_amount = purchase_price * (1 - down_payment_pct / 100)
    down_payment = purchase_price * (down_payment_pct / 100)

    m_loan = monthly_repayment(loan_amount, annual_rate_pct, tenure_years)
    m_maint = area_sqft * maintenance_psf
    m_repairs = monthly_rent * (repairs_pct / 100)
    m_vacancy = monthly_rent * (vacancy_pct / 100)
    m_tax_ins = tax_insurance_monthly
    m_agent = monthly_rent * (agent_fee_pct / 100)

    total_costs = m_loan + m_maint + m_repairs + m_vacancy + m_tax_ins + m_agent
    net_cf = monthly_rent - total_costs
    annual_rent = monthly_rent * 12

    # Gross yield = Annual rent / Purchase price
    gross_yield = (annual_rent / purchase_price * 100) if purchase_price > 0 else 0.0

    # Net yield = (Annual rent - Annual costs) / Down payment
    annual_costs = total_costs * 12
    net_yield = ((annual_rent - annual_costs) / down_payment * 100) if down_payment > 0 else 0.0

    return {
        "down_payment": round(down_payment, 2),
        "loan_amount": round(loan_amount, 2),
        "monthly_loan_repayment": round(m_loan, 2),
        "monthly_maintenance": round(m_maint, 2),
        "monthly_repairs": round(m_repairs, 2),
        "monthly_vacancy": round(m_vacancy, 2),
        "monthly_tax_insurance": round(m_tax_ins, 2),
        "monthly_agent_fee": round(m_agent, 2),
        "total_monthly_costs": round(total_costs, 2),
        "net_monthly_cash_flow": round(net_cf, 2),
        "annual_rent": round(annual_rent, 2),
        "gross_yield_pct": round(gross_yield, 2),
        "net_yield_pct": round(net_yield, 2),
    }
