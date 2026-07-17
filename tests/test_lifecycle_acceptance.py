#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import tempfile
import unittest
from pathlib import Path

from account_catalog import load_account_catalog, template_summary
from lifecycle_acceptance import SOLO_TEMPLATE_LABELS, run_full_cycle_acceptance


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class IndustryTemplateTests(unittest.TestCase):
    def test_solo_company_templates_are_explainable_and_account_safe(self):
        catalog = load_account_catalog(
            PROJECT_ROOT / "account_catalog_small_enterprise.json"
        )
        known_codes = {str(row["code"]) for row in catalog["accounts"]}
        self.assertEqual(len(known_codes), 66)
        for label in SOLO_TEMPLATE_LABELS:
            profile = catalog["templates"][label]
            enabled = {str(code) for code in profile["enabled_codes"]}
            self.assertTrue(profile["solo_company_template"], label)
            self.assertTrue(enabled <= known_codes, label)
            self.assertGreaterEqual(len(enabled), 45, label)
            self.assertTrue(profile["common_businesses"], label)
            self.assertTrue(profile["recommended_details"], label)
            self.assertTrue(profile["monthly_focus"], label)
            self.assertTrue(profile["risk_hints"], label)
            summary = template_summary(label, catalog)
            self.assertIn("启用", summary)
            self.assertIn("常见业务", summary)
            self.assertIn("风险提示", summary)


class FullLifecycleAcceptanceTests(unittest.TestCase):
    def test_complete_year_uses_all_66_accounts(self):
        with tempfile.TemporaryDirectory(prefix="small-enterprise-full-cycle-") as temp:
            report = run_full_cycle_acceptance(
                Path(temp),
                version="test",
                catalog_path=PROJECT_ROOT / "account_catalog_small_enterprise.json",
            )

        self.assertTrue(report["ok"])
        self.assertEqual(report["account_catalog_count"], 66)
        self.assertEqual(report["covered_account_count"], 66)
        self.assertEqual(report["missing_account_codes"], [])
        self.assertEqual(report["months_processed"], 12)
        self.assertEqual(len(report["archived_periods"]), 12)
        self.assertEqual(
            report["tax_accrual_periods"],
            ["2026-03", "2026-06", "2026-09", "2026-12"],
        )
        self.assertEqual(set(report["solo_templates"]), set(SOLO_TEMPLATE_LABELS))
        self.assertTrue(all(row["ok"] for row in report["stages"]))


if __name__ == "__main__":
    unittest.main()
