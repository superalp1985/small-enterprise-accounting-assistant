#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import tempfile
import unittest
from pathlib import Path

from finance_store import FinanceDataStore
from modules.manual_entry_module import ManualEntryModule


class _Var:
    def __init__(self):
        self.value = ""

    def set(self, value):
        self.value = value


class ManualEntryDirectionTests(unittest.TestCase):
    def test_explicit_invoice_type_is_persisted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FinanceDataStore(
                Path(temp_dir) / "data",
                "enterprise",
                "小企业会计",
                "小企业会计准则",
            )
            try:
                settings = store.get_settings()
                settings["company"]["name"] = "测试茶业有限公司"
                store.save_settings(settings)

                module = ManualEntryModule.__new__(ManualEntryModule)
                module.store = store
                module.status_var = _Var()
                module.reload_from_store = lambda: None
                module._add_voucher(
                    "平台订单收入",
                    "5602 管理费用",
                    "1002 银行存款",
                    100.0,
                    "贷方",
                    "销项",
                    "2026-07-29",
                    "MANUAL-001",
                    "平台订单客户",
                    0.0,
                )

                invoice = store.list_invoices()[0]
                self.assertEqual(invoice["invoice_type"], "销项")
                self.assertEqual(invoice["seller"], "测试茶业有限公司")
                self.assertEqual(invoice["buyer"], "平台订单客户")
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
