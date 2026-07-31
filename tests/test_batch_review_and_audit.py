#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from logger import AuditLogger
from modules.batch_import_module import (
    BatchImportModule,
    apply_bulk_review_updates,
    apply_confirmed_review_data,
    changed_review_fields,
    review_snapshot,
)


def sample_item(**overrides):
    item = {
        "file_name": "invoice.pdf",
        "invoice_code": "011001",
        "invoice_no": "0001",
        "invoice_date": "2026-07-29",
        "seller": "供应商",
        "buyer": "本企业",
        "amount": 101.0,
        "tax_amount": 1.0,
        "net_amount": 100.0,
        "description": "采购办公用品",
        "matched_subject": "5602 管理费用-办公费",
        "counter_subject": "1002 银行存款",
        "invoice_type": "进项",
        "direction": "借方",
        "status": "待处理",
    }
    item.update(overrides)
    return item


class BatchReviewTests(unittest.TestCase):
    def test_platform_order_uses_review_default_without_model_call(self):
        class NoModelMatcher:
            @staticmethod
            def match_rules(_text):
                raise AssertionError("平台订单不应逐条调用规则匹配")

            @staticmethod
            def match_with_ai(_text):
                raise AssertionError("平台订单不应逐条调用本地模型")

        module = object.__new__(BatchImportModule)
        module.semantic_matcher = NoModelMatcher()
        module._subject_match_cache = {}
        module.subject_options = ["5001 主营业务收入", "5602 管理费用-其他"]
        item = sample_item(
            source_type="platform_excel",
            source_reference="ORDER-001",
            description="支付方式：支付宝，支付单号：20260730001",
            invoice_type="销项",
            direction="贷方",
        )

        module._apply_automatic_match(item)

        self.assertEqual(item["matched_subject"], "5001 主营业务收入")
        self.assertEqual(item["match_type"], "platform_order_default")
        self.assertTrue(item["needs_review"])
        self.assertIn("平台待结算款", item["rule_basis"])

    def test_empty_ai_result_is_cached_for_repeated_tax_category(self):
        class EmptyMatcher:
            def __init__(self):
                self.ai_calls = 0

            @staticmethod
            def match_rules(_text):
                return []

            def match_with_ai(self, _text):
                self.ai_calls += 1
                return []

        matcher = EmptyMatcher()
        module = object.__new__(BatchImportModule)
        module.semantic_matcher = matcher
        module._subject_match_cache = {}
        module.subject_options = ["5602 管理费用-其他"]
        items = [
            sample_item(description="茶叶礼盒A", tax_categories=["茶"]),
            sample_item(description="茶叶礼盒B", tax_categories=["茶"]),
        ]

        for item in items:
            module._apply_automatic_match(item)

        self.assertEqual(matcher.ai_calls, 1)
        self.assertTrue(all(item["matched_subject"] for item in items))
        self.assertTrue(all(item["match_type"] == "review_fallback" for item in items))

    def test_bulk_updates_change_only_checked_fields(self):
        item = sample_item()
        unchanged = {
            "amount": item["amount"],
            "invoice_no": item["invoice_no"],
            "seller": item["seller"],
        }

        changed = apply_bulk_review_updates(
            [item],
            {
                "matched_subject": "1405 库存商品",
                "description": "采购待售茶叶",
            },
        )

        self.assertEqual(changed, [item])
        self.assertEqual(item["matched_subject"], "1405 库存商品")
        self.assertEqual(item["description"], "采购待售茶叶")
        self.assertEqual(item["amount"], unchanged["amount"])
        self.assertEqual(item["invoice_no"], unchanged["invoice_no"])
        self.assertEqual(item["seller"], unchanged["seller"])
        self.assertEqual(item["status"], "待定")

    def test_bulk_invoice_type_synchronizes_direction(self):
        item = sample_item()
        apply_bulk_review_updates([item], {"invoice_type": "销项"})
        self.assertEqual(item["invoice_type"], "销项")
        self.assertEqual(item["direction"], "贷方")

    def test_full_invoice_edit_writes_every_supported_field(self):
        item = sample_item()
        before = review_snapshot(item)
        apply_confirmed_review_data(
            item,
            {
                "invoice_code": "NEW-CODE",
                "invoice_no": "NEW-NO",
                "invoice_date": "2026-07-30",
                "seller": "新销售方",
                "buyer": "新购买方",
                "amount": 206.0,
                "total_amount": 206.0,
                "tax_amount": 6.0,
                "net_amount": 200.0,
                "description": "补录后的业务",
                "subject": "1405 库存商品",
            },
        )
        after = review_snapshot(item)
        self.assertEqual(item["invoice_code"], "NEW-CODE")
        self.assertEqual(item["invoice_no"], "NEW-NO")
        self.assertEqual(item["invoice_date"], "2026-07-30")
        self.assertEqual(item["tax_amount"], 6.0)
        self.assertEqual(item["net_amount"], 200.0)
        self.assertEqual(item["matched_subject"], "1405 库存商品")
        self.assertIn("发票代码", changed_review_fields(before, after))
        self.assertIn("税额", changed_review_fields(before, after))


class AuditLoggerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "operation_log.json"
        self.logger = AuditLogger(self.path, default_operator="测试员")

    def tearDown(self):
        self.temp.cleanup()

    def test_signed_log_contains_before_after_and_verifies(self):
        self.assertTrue(
            self.logger.log(
                "批量复核修改",
                "0001；修改字段：会计科目",
                before={"会计科目": "5602 管理费用"},
                after={"会计科目": "1405 库存商品"},
            )
        )
        entries = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(entries[0]["operator"], "测试员")
        self.assertEqual(entries[0]["before"]["会计科目"], "5602 管理费用")
        self.assertEqual(entries[0]["after"]["会计科目"], "1405 库存商品")
        self.assertTrue(entries[0]["event_id"])
        self.assertTrue(entries[0]["entry_hash"])
        self.assertEqual(self.logger.verify_integrity()["status"], "valid")

    def test_manual_tampering_is_detected_and_append_is_refused(self):
        self.assertTrue(self.logger.log("测试", "原始内容", after={"金额": 100}))
        entries = json.loads(self.path.read_text(encoding="utf-8"))
        entries[0]["after"]["金额"] = 999
        self.path.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")

        self.assertEqual(self.logger.verify_integrity()["status"], "invalid")
        with redirect_stdout(io.StringIO()):
            self.assertFalse(self.logger.log("测试", "不应追加"))
        self.assertEqual(len(json.loads(self.path.read_text(encoding="utf-8"))), 1)

    def test_legacy_logs_are_preserved_and_anchored(self):
        legacy = [{"timestamp": "2026-01-01 00:00:00", "operation": "旧操作"}]
        self.path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(self.logger.verify_integrity()["status"], "legacy")
        self.assertTrue(self.logger.log("新操作", "签名日志"))
        entries = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(entries[0], legacy[0])
        self.assertEqual(self.logger.verify_integrity()["status"], "mixed")


if __name__ == "__main__":
    unittest.main()
