#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest

from tax_engine import (
    calculate_cit,
    calculate_small_scale_vat,
    cumulative_iit,
    price_tax_split,
    resolve_tax_period,
    stamp_duty,
    supported_scope,
)


class TaxEngineTests(unittest.TestCase):
    def setUp(self):
        self.settings = {
            "taxpayer_type": "小规模纳税人",
            "vat_rate": 0.01,
            "vat_monthly_exemption_threshold": 100000,
            "vat_quarterly_exemption_threshold": 300000,
            "cit_rate": 0.05,
            "cit_taxable_income_limit": 3000000,
            "cit_employee_limit": 300,
            "cit_asset_limit": 50000000,
            "average_employees": 1,
            "average_assets": 100000,
            "restricted_industry": False,
            "small_low_profit": True,
        }

    def test_monthly_and_quarterly_period_resolution(self):
        month = resolve_tax_period("2026-07", "按月")
        self.assertEqual(month.key, "2026-07")
        self.assertEqual(month.months, ("2026-07",))
        quarter = resolve_tax_period("2026-08", "按季")
        self.assertEqual(quarter.key, "2026-Q3")
        self.assertEqual(quarter.months, ("2026-07", "2026-08", "2026-09"))
        with self.assertRaisesRegex(ValueError, "按月.*按季"):
            resolve_tax_period("2026-07", "按年")

    def test_price_tax_split_and_red_sign(self):
        self.assertEqual(
            price_tax_split(total_amount=101, rate=0.01, price_tax_mode="含税"),
            {"amount": 100.0, "tax_amount": 1.0, "total_amount": 101.0},
        )
        self.assertEqual(
            price_tax_split(total_amount=-101, rate=0.01, price_tax_mode="含税"),
            {"amount": -100.0, "tax_amount": -1.0, "total_amount": -101.0},
        )

    def test_vat_exemption_thresholds_and_special_invoice_sales(self):
        monthly = resolve_tax_period("2026-07", "按月")
        exempt = calculate_small_scale_vat(
            sales=100000, non_exempt_sales=0,
            settings=self.settings, period=monthly,
        )
        self.assertTrue(exempt["threshold_eligible"])
        self.assertEqual(exempt["vat_payable"], 0)
        special = calculate_small_scale_vat(
            sales=100000, non_exempt_sales=10000,
            settings=self.settings, period=monthly,
        )
        self.assertEqual(special["vat_payable"], 100)
        over = calculate_small_scale_vat(
            sales=100000.01, non_exempt_sales=0,
            settings=self.settings, period=monthly,
        )
        self.assertFalse(over["threshold_eligible"])
        self.assertEqual(over["vat_payable"], 1000)

        quarterly = resolve_tax_period("2026-05", "按季")
        quarter = calculate_small_scale_vat(
            sales=300000, non_exempt_sales=0,
            settings=self.settings, period=quarterly,
        )
        self.assertEqual(quarter["threshold"], 300000)
        self.assertEqual(quarter["vat_payable"], 0)

    def test_small_profit_cit_and_qualification_failure(self):
        result = calculate_cit(
            accounting_profit=100000, increase=10000, decrease=5000,
            prior_losses=5000, prepaid_tax=1000, settings=self.settings,
        )
        self.assertTrue(result["supported"])
        self.assertEqual(result["taxable_income"], 100000)
        self.assertEqual(result["current_tax"], 5000)
        self.assertEqual(result["cit_payable"], 4000)

        disqualified = dict(self.settings, average_employees=301)
        result = calculate_cit(
            accounting_profit=100000, increase=0, decrease=0,
            prior_losses=0, prepaid_tax=0, settings=disqualified,
        )
        self.assertFalse(result["supported"])
        self.assertEqual(result["current_tax"], 0)

    def test_scope_iit_and_stamp_duty(self):
        self.assertTrue(supported_scope(self.settings)["supported"])
        self.assertFalse(supported_scope(dict(self.settings, taxpayer_type="一般纳税人"))["supported"])
        iit = cumulative_iit(
            cumulative_income=120000, cumulative_deductions=60000,
            prior_withheld=2000,
        )
        self.assertEqual(iit["rate"], 0.10)
        self.assertEqual(iit["cumulative_tax"], 3480)
        self.assertEqual(iit["current_withholding"], 1480)
        duty = stamp_duty([
            {"item": "买卖合同", "amount": 100000, "rate": 0.0003},
            {"item": "租赁合同", "amount": 12000, "rate": 0.001},
        ], relief_rate=0.5)
        self.assertEqual(duty["stamp_duty_payable"], 21)


if __name__ == "__main__":
    unittest.main()
