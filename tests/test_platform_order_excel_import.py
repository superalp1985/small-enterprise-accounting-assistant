#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from finance_store import FinanceDataStore
from modules.batch_import_module import BatchImportModule
from platform_order_excel_import import (
    PlatformOrderExcelImportError,
    read_platform_order_workbook,
)


class PlatformOrderExcelImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "ExportOrderList-test.xlsx"

    def tearDown(self):
        self.temp.cleanup()

    def _write_workbook(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "export"
        sheet.append([
            "订单编号", "支付详情", "总金额", "买家实付金额",
            "订单状态", "物流单号", "物流公司",
        ])
        sheet.append([
            "ORDER-001", "抖音小店支付时间20260729153020", "100.00", "95.00",
            "交易成功", "SF001", "顺丰速运",
        ])
        sheet.append([
            "ORDER-002", "抖音小店未支付", "200.00", "0.00",
            "交易关闭", "", "",
        ])
        workbook.save(self.path)

    def test_reads_success_and_blocks_closed_orders(self):
        self._write_workbook()
        rows = read_platform_order_workbook(
            self.path,
            company_name="测试茶业有限公司",
            company_industry="电商与网络零售",
        )

        self.assertEqual(len(rows), 2)
        success = rows[0]
        self.assertEqual(success["invoice_no"], "ORDER-001")
        self.assertEqual(success["invoice_date"], "2026-07-29")
        self.assertEqual(success["invoice_type"], "销项")
        self.assertEqual(success["direction"], "贷方")
        self.assertEqual(success["counter_subject"], "1012 其他货币资金-平台待结算款")
        self.assertEqual(success["source_type"], "platform_excel")
        self.assertEqual(success["status"], "待处理")
        self.assertFalse(success["non_postable"])
        self.assertEqual(success["amount"], 100.0)
        self.assertEqual(success["paid_amount"], 95.0)
        self.assertTrue(any("实付金额" in warning for warning in success["warnings"]))

        closed = rows[1]
        self.assertTrue(closed["non_postable"])
        self.assertEqual(closed["status"], "不可入账")

    def test_platform_order_posts_to_revenue_and_pending_settlement(self):
        self._write_workbook()
        item = read_platform_order_workbook(
            self.path, company_name="测试茶业有限公司"
        )[0]
        item["matched_subject"] = "5001 主营业务收入-平台商品销售"

        store = FinanceDataStore(
            self.root / "data",
            "enterprise",
            "小企业会计",
            "小企业会计准则",
        )
        try:
            module = BatchImportModule.__new__(BatchImportModule)
            module.store = store
            entry = module._build_posting_entry(item)
            result = store.post_invoice_vouchers([entry])

            self.assertEqual(len(result), 1)
            vouchers = store.list_vouchers()
            self.assertEqual(len(vouchers), 2)
            revenue = next(row for row in vouchers if row["subject"].startswith("5001 "))
            pending = next(row for row in vouchers if row["subject"].startswith("1012 "))
            self.assertEqual(revenue["credit"], 100.0)
            self.assertEqual(pending["debit"], 100.0)

            invoice = store.list_invoices()[0]
            self.assertEqual(invoice["invoice_type"], "销项")
            self.assertEqual(invoice["source"], "platform_excel")
            self.assertEqual(invoice["invoice_form"], "平台订单")
            self.assertEqual(invoice["source_reference"], "ORDER-001")
        finally:
            store.close()

    def test_rejects_unrelated_workbook(self):
        workbook = Workbook()
        workbook.active.append(["姓名", "电话"])
        workbook.save(self.path)
        with self.assertRaises(PlatformOrderExcelImportError):
            read_platform_order_workbook(self.path)


if __name__ == "__main__":
    unittest.main()
