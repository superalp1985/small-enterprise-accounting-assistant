#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import tempfile
import unittest
from datetime import date
from pathlib import Path

from finance_store import FinanceDataStore
from natural_entry import (
    build_voucher_plan,
    extract_transaction_facts,
    post_voucher_plan,
    semantic_business_text,
)


def match(subject: str, match_type: str = "exact"):
    return {
        "record": {"subject": subject, "law": "测试依据"},
        "match_type": match_type,
        "matched_word": "测试词",
        "score": 100.0,
        "rule_category": "测试分类",
        "rule_basis": "测试规则依据",
        "recommendation_reason": "测试推荐理由",
    }


class NaturalEntryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = FinanceDataStore(
            Path(self.temp.name) / "small_enterprise",
            "enterprise", "小企业会计", "小企业会计准则",
        )

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_extracts_colloquial_amount_date_and_tax(self):
        facts = extract_transaction_facts(
            "7月16日购买电脑，价税合计6,000元，其中税额530元",
            date(2026, 7, 20),
        )
        self.assertEqual(facts["date"], "2026-07-16")
        self.assertEqual(facts["amount"], 6000)
        self.assertEqual(facts["tax_amount"], 530)

    def test_owner_advance_creates_expense_and_other_payable(self):
        self.assertEqual(
            semantic_business_text("今天老板垫付299元购买办公软件会员"),
            "今天 299元购买办公软件会员",
        )
        plan = build_voucher_plan(
            "今天老板垫付299元购买办公软件会员",
            match("5602 管理费用-办公费"),
            date(2026, 7, 16),
        )
        self.assertEqual(plan["date"], "2026-07-16")
        self.assertEqual(plan["direction"], "借方")
        self.assertEqual(plan["counter_subject"], "2241 其他应付款")
        self.assertEqual(plan["lines"][0]["debit"], 299)
        self.assertEqual(plan["lines"][1]["credit"], 299)

    def test_uncollected_revenue_creates_receivable(self):
        plan = build_voucher_plan(
            "7月16日确认软件服务收入8000元，客户未付款",
            match("5001 主营业务收入", "ai_suggested"),
            date(2026, 7, 20),
        )
        self.assertEqual(plan["direction"], "贷方")
        self.assertEqual(plan["counter_subject"], "1122 应收账款")
        self.assertEqual(plan["lines"][0]["credit"], 8000)
        self.assertEqual(plan["lines"][1]["debit"], 8000)

    def test_borrowing_and_repayment_use_opposite_directions(self):
        borrowing = build_voucher_plan(
            "公司从银行借入2万元经营周转",
            match("2001 短期借款"),
            date(2026, 7, 16),
        )
        repayment = build_voucher_plan(
            "公司偿还银行借款2000元",
            match("2001 短期借款"),
            date(2026, 7, 16),
        )
        self.assertEqual(borrowing["direction"], "贷方")
        self.assertEqual(borrowing["amount"], 20000)
        self.assertEqual(repayment["direction"], "借方")
        self.assertEqual(repayment["counter_subject"], "1002 银行存款")

    def test_post_plan_persists_balanced_voucher_and_invoice(self):
        settings = self.store.get_settings()
        settings["company"]["name"] = "星河科技有限公司"
        self.store.save_settings(settings)
        plan = build_voucher_plan(
            "7月16日软件服务收入8000元，客户未付款，发票号12345678，其中税额80元",
            match("5001 主营业务收入"),
            date(2026, 7, 20),
        )
        added = post_voucher_plan(self.store, plan)
        self.assertEqual(len(added), 3)
        self.assertEqual(sum(row["debit"] for row in added), 8000)
        self.assertEqual(sum(row["credit"] for row in added), 8000)
        revenue = next(row for row in added if row["subject"].startswith("5001 "))
        vat = next(row for row in added if row["subject"].startswith("2221 "))
        self.assertEqual(revenue["credit"], 7920)
        self.assertEqual(vat["credit"], 80)
        self.assertEqual({row["source"] for row in added}, {"natural_language"})
        invoice = self.store.list_invoices()[0]
        self.assertEqual(invoice["invoice_type"], "销项")
        self.assertEqual(invoice["total_amount"], 8000)
        self.assertEqual(invoice["tax_amount"], 80)

    def test_unbilled_and_red_revenue_create_signed_tax_records(self):
        unbilled = build_voucher_plan(
            "7月16日确认未开票服务收入1010元，其中税额10元，客户未付款",
            match("5001 主营业务收入"),
            date(2026, 7, 20),
        )
        post_voucher_plan(self.store, unbilled)
        invoice = self.store.list_invoices()[0]
        self.assertEqual(invoice["document_type"], "未开票收入")
        self.assertEqual(invoice["amount"], 1000)

        red = build_voucher_plan(
            "7月17日开红字发票退款给客户101元，发票号87654321，其中税额1元",
            match("5001 主营业务收入"),
            date(2026, 7, 20),
        )
        added = post_voucher_plan(self.store, red)
        self.assertEqual(sum(row["debit"] for row in added), 101)
        self.assertEqual(sum(row["credit"] for row in added), 101)
        red_invoice = next(row for row in self.store.list_invoices() if row["document_type"] == "红字发票")
        self.assertEqual(red_invoice["amount"], -100)
        self.assertEqual(red_invoice["tax_amount"], -1)

    def test_missing_amount_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "没有识别到交易金额"):
            build_voucher_plan("老板买了办公用品", match("5602 管理费用-办公费"))


if __name__ == "__main__":
    unittest.main()
