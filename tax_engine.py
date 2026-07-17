#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Deterministic tax-period and calculation helpers for supported small firms."""

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional


IIT_BRACKETS = (
    (36000.0, 0.03, 0.0),
    (144000.0, 0.10, 2520.0),
    (300000.0, 0.20, 16920.0),
    (420000.0, 0.25, 31920.0),
    (660000.0, 0.30, 52920.0),
    (960000.0, 0.35, 85920.0),
    (float("inf"), 0.45, 181920.0),
)


def money(value: Any) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


@dataclass(frozen=True)
class TaxPeriod:
    anchor: str
    frequency: str
    key: str
    start_month: str
    end_month: str
    months: tuple[str, ...]


def resolve_tax_period(anchor: str, frequency: str) -> TaxPeriod:
    try:
        year_text, month_text = str(anchor).split("-", 1)
        year, month = int(year_text), int(month_text)
        if month < 1 or month > 12:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError("税务期间应为 YYYY-MM 格式")

    normalized_frequency = str(frequency).strip()
    if normalized_frequency not in {"按月", "按季"}:
        raise ValueError("申报频率只支持“按月”或“按季”")

    if normalized_frequency == "按月":
        value = f"{year:04d}-{month:02d}"
        return TaxPeriod(value, "按月", value, value, value, (value,))

    quarter = (month - 1) // 3 + 1
    start = (quarter - 1) * 3 + 1
    months = tuple(f"{year:04d}-{value:02d}" for value in range(start, start + 3))
    return TaxPeriod(
        f"{year:04d}-{month:02d}", "按季", f"{year:04d}-Q{quarter}",
        months[0], months[-1], months,
    )


def supported_scope(settings: Dict[str, Any]) -> Dict[str, Any]:
    taxpayer_type = str(settings.get("taxpayer_type", "")).strip()
    small_scale = taxpayer_type == "小规模纳税人"
    small_profit_enabled = bool(settings.get("small_low_profit", True))
    checks = [
        {
            "item": "增值税纳税人类型",
            "passed": small_scale,
            "actual": taxpayer_type or "未设置",
            "limit": "小规模纳税人",
        },
        {
            "item": "企业所得税支持口径",
            "passed": small_profit_enabled,
            "actual": "小型微利企业" if small_profit_enabled else "其他企业",
            "limit": "小型微利企业",
        },
    ]
    supported = all(item["passed"] for item in checks)
    return {
        "supported": supported,
        "checks": checks,
        "message": (
            "当前账套属于本工具支持的“小规模纳税人 + 小型微利企业”范围"
            if supported else
            "当前资格超出本工具税额计算支持范围，请改为人工复核并以电子税务局为准"
        ),
    }


def price_tax_split(*, amount: Any = 0, tax_amount: Any = 0,
                    total_amount: Any = 0, rate: Any = 0.01,
                    price_tax_mode: str = "不含税") -> Dict[str, float]:
    """Return a reconciled net/tax/gross triple without changing its sign."""
    net = money(amount)
    tax = money(tax_amount)
    total = money(total_amount)
    levy_rate = max(0.0, float(rate or 0))

    sign = -1.0 if min(net, tax, total) < 0 else 1.0
    net, tax, total = abs(net), abs(tax), abs(total)
    if price_tax_mode == "含税" and not total and net:
        total, net = net, 0.0

    if total and not net and not tax:
        net = round(total / (1 + levy_rate), 2) if levy_rate else total
        tax = round(total - net, 2)
    elif net and not tax:
        tax = round(net * levy_rate, 2)
        total = round(net + tax, 2)
    elif tax and not net and total:
        net = round(total - tax, 2)
    elif net and tax:
        total = round(net + tax, 2)
    elif total and net:
        tax = round(total - net, 2)

    if total and abs(total - net - tax) >= 0.01:
        tax = round(total - net, 2)
    return {
        "amount": round(net * sign, 2),
        "tax_amount": round(tax * sign, 2),
        "total_amount": round(total * sign, 2),
    }


def small_profit_eligibility(settings: Dict[str, Any], *,
                             annual_taxable_income: float,
                             employees: Optional[int] = None,
                             assets: Optional[float] = None,
                             restricted_industry: Optional[bool] = None) -> Dict[str, Any]:
    employee_count = int(
        settings.get("average_employees", 0) if employees is None else employees
    )
    asset_total = money(
        settings.get("average_assets", 0) if assets is None else assets
    )
    restricted = bool(
        settings.get("restricted_industry", False)
        if restricted_industry is None else restricted_industry
    )
    income_limit = money(settings.get("cit_taxable_income_limit", 3000000))
    employee_limit = int(settings.get("cit_employee_limit", 300) or 0)
    asset_limit = money(settings.get("cit_asset_limit", 50000000))

    checks = [
        {
            "item": "非限制和禁止行业",
            "passed": not restricted,
            "actual": "否" if not restricted else "是",
            "limit": "必须为否",
        },
        {
            "item": "年度应纳税所得额",
            "passed": annual_taxable_income <= income_limit,
            "actual": money(annual_taxable_income),
            "limit": income_limit,
        },
        {
            "item": "全年季度平均从业人数",
            "passed": employee_count <= employee_limit,
            "actual": employee_count,
            "limit": employee_limit,
        },
        {
            "item": "全年季度平均资产总额",
            "passed": asset_total <= asset_limit,
            "actual": asset_total,
            "limit": asset_limit,
        },
    ]
    qualified = bool(settings.get("small_low_profit", True)) and all(
        item["passed"] for item in checks
    )
    return {
        "qualified": qualified,
        "supported": qualified,
        "checks": checks,
        "message": (
            "符合小型微利企业预设条件"
            if qualified else
            "未通过小型微利企业资格检查，已超出本工具税额计算支持范围"
        ),
    }


def calculate_small_scale_vat(*, sales: float, non_exempt_sales: float,
                              settings: Dict[str, Any], period: TaxPeriod,
                              exempt_project_sales: float = 0.0) -> Dict[str, Any]:
    sales = max(0.0, money(sales))
    non_exempt_sales = min(sales, max(0.0, money(non_exempt_sales)))
    exempt_project_sales = min(
        max(0.0, sales - non_exempt_sales),
        max(0.0, money(exempt_project_sales)),
    )
    rate = float(settings.get("vat_rate", 0.01) or 0)
    threshold = money(
        settings.get(
            "vat_monthly_exemption_threshold" if period.frequency == "按月"
            else "vat_quarterly_exemption_threshold",
            100000 if period.frequency == "按月" else 300000,
        )
    )
    threshold_sales = max(0.0, round(sales - exempt_project_sales, 2))
    threshold_eligible = threshold_sales <= threshold
    taxable_sales = non_exempt_sales if threshold_eligible else threshold_sales
    exempt_sales = max(0.0, sales - taxable_sales)
    gross_vat = round(threshold_sales * rate, 2)
    payable = round(taxable_sales * rate, 2)
    return {
        "supported": True,
        "period_key": period.key,
        "frequency": period.frequency,
        "sales": sales,
        "threshold_sales": threshold_sales,
        "rate": rate,
        "threshold": threshold,
        "threshold_eligible": threshold_eligible,
        "non_exempt_sales": non_exempt_sales,
        "exempt_project_sales": exempt_project_sales,
        "exempt_sales": exempt_sales,
        "taxable_sales": taxable_sales,
        "gross_vat": gross_vat,
        "vat_payable": payable,
        "vat_reduction": round(gross_vat - payable, 2),
    }


def calculate_cit(*, accounting_profit: float, increase: float, decrease: float,
                  prior_losses: float, prepaid_tax: float,
                  settings: Dict[str, Any]) -> Dict[str, Any]:
    taxable_income = max(
        0.0,
        money(accounting_profit) + money(increase) - money(decrease)
        - money(prior_losses),
    )
    eligibility = small_profit_eligibility(
        settings, annual_taxable_income=taxable_income
    )
    effective_rate = float(settings.get("cit_rate", 0.05) or 0)
    current_tax = round(taxable_income * effective_rate, 2) if eligibility["qualified"] else 0.0
    payable = max(0.0, round(current_tax - money(prepaid_tax), 2))
    return {
        "supported": eligibility["supported"],
        "eligibility": eligibility,
        "accounting_profit": money(accounting_profit),
        "tax_increase": money(increase),
        "tax_decrease": money(decrease),
        "prior_losses": money(prior_losses),
        "taxable_income": taxable_income,
        "effective_rate": effective_rate,
        "current_tax": current_tax,
        "prepaid_tax": money(prepaid_tax),
        "cit_payable": payable,
    }


def cumulative_iit(*, cumulative_income: float, cumulative_deductions: float,
                   prior_withheld: float) -> Dict[str, float]:
    taxable = max(0.0, money(cumulative_income) - money(cumulative_deductions))
    rate, quick = 0.0, 0.0
    for ceiling, bracket_rate, deduction in IIT_BRACKETS:
        if taxable <= ceiling:
            rate, quick = bracket_rate, deduction
            break
    cumulative_tax = max(0.0, round(taxable * rate - quick, 2))
    current = max(0.0, round(cumulative_tax - money(prior_withheld), 2))
    return {
        "cumulative_taxable_income": taxable,
        "rate": rate,
        "quick_deduction": quick,
        "cumulative_tax": cumulative_tax,
        "prior_withheld": money(prior_withheld),
        "current_withholding": current,
    }


def stamp_duty(items: Iterable[Dict[str, Any]], relief_rate: float = 0.5) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    total = 0.0
    for source in items:
        base = max(0.0, money(source.get("amount")))
        rate = max(0.0, float(source.get("rate", 0) or 0))
        original = round(base * rate, 2)
        payable = round(original * float(relief_rate), 2)
        row = dict(source)
        row.update({"taxable_amount": base, "original_tax": original, "payable": payable})
        rows.append(row)
        total += payable
    return {
        "items": rows,
        "relief_rate": float(relief_rate),
        "stamp_duty_payable": round(total, 2),
    }
