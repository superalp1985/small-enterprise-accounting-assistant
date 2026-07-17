#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from finance_exporter import export_finance_workbook
from finance_store import FinanceDataStore


DATA_DIR = Path(tempfile.mkdtemp(prefix="accountingdemo-small-qa-"))
OUTPUT = ROOT / "out" / "small-enterprise-finance-demo.xlsx"

store = FinanceDataStore(
    DATA_DIR, "enterprise", "小企业会计", "小企业会计准则"
)
settings = store.get_settings()
settings["company"].update({
    "name": "星河一人科技有限公司",
    "credit_code": "91110101MA00000001",
    "taxpayer_type": "小规模纳税人",
    "industry": "软件和信息技术服务业",
    "legal_representative": "张明",
    "finance_contact": "张明",
    "phone": "13800000000",
    "registered_address": "北京市海淀区示例路 1 号",
    "bank_name": "示例银行北京分行",
    "bank_account": "6222000000000000000",
})
store.save_settings(settings)

for row in (
    {"subject": "1002 银行存款", "debit_balance": 50000},
    {"subject": "1122 应收账款", "debit_balance": 10000},
    {"subject": "1601 固定资产", "debit_balance": 12000},
    {"subject": "1602 累计折旧", "credit_balance": 2000},
    {"subject": "2202 应付账款", "credit_balance": 5000},
    {"subject": "3001 实收资本", "credit_balance": 65000},
):
    store.upsert_opening_balance({"period": "2026-01", **row})

store.add_voucher_lines([
    {"subject": "1002 银行存款", "debit": 10000, "description": "六月服务收款"},
    {"subject": "5001 主营业务收入", "credit": 10000, "description": "六月服务收款"},
], voucher_date="2026-06-20")

for voucher_date, lines in (
    ("2026-07-02", [
        {"subject": "1002 银行存款", "debit": 8000, "description": "软件服务收款"},
        {"subject": "5001 主营业务收入", "credit": 8000, "description": "软件服务收款"},
    ]),
    ("2026-07-05", [
        {"subject": "1405 库存商品", "debit": 3000, "description": "采购办公耗材", "invoice_no": "00000002"},
        {"subject": "1002 银行存款", "credit": 3000, "description": "采购办公耗材"},
    ]),
    ("2026-07-08", [
        {"subject": "1601 固定资产", "debit": 6000, "description": "购置办公电脑", "invoice_no": "00000003"},
        {"subject": "1002 银行存款", "credit": 6000, "description": "购置办公电脑"},
    ]),
    ("2026-07-10", [
        {"subject": "1002 银行存款", "debit": 20000, "description": "取得短期经营借款"},
        {"subject": "2001 短期借款", "credit": 20000, "description": "取得短期经营借款"},
    ]),
    ("2026-07-15", [
        {"subject": "2221 应交税费", "debit": 1000, "description": "缴纳增值税及附加"},
        {"subject": "1002 银行存款", "credit": 1000, "description": "缴纳增值税及附加"},
    ]),
):
    store.add_voucher_lines(lines, voucher_date=voucher_date)

payroll = store.upsert_payroll({
    "period": "2026-07", "employee_name": "张明", "gross_salary": 10000,
    "social_personal": 800, "housing_personal": 400, "income_tax": 100,
    "social_company": 1600, "housing_company": 800, "pay_date": "2026-07-31",
})
store.post_payroll_voucher(payroll["id"])
store.add_voucher_lines([
    {"subject": "2211 应付职工薪酬", "debit": 8700, "description": "发放七月工资"},
    {"subject": "1002 银行存款", "credit": 8700, "description": "发放七月工资"},
], voucher_date="2026-07-31")

store.upsert_fixed_asset({
    "asset_name": "研发电脑", "category": "电子设备",
    "purchase_date": "2026-06-20", "original_cost": 3600,
    "residual_rate": 0.0, "useful_months": 36,
    "depreciation_start_period": "2026-07",
    "asset_subject": "1601 固定资产", "depreciation_subject": "1602 累计折旧",
    "expense_subject": "5602 管理费用", "status": "使用中",
})
store.post_depreciation_voucher("2026-07")

store.upsert_invoice({
    "invoice_date": "2026-07-02", "invoice_code": "011001",
    "invoice_no": "00000001", "invoice_type": "销项",
    "seller": "星河一人科技有限公司", "buyer": "示例客户有限公司",
    "amount": 8000, "tax_amount": 80, "total_amount": 8080,
})
store.upsert_invoice({
    "invoice_date": "2026-07-05", "invoice_code": "011002",
    "invoice_no": "00000002", "invoice_type": "进项",
    "seller": "示例供应商有限公司", "buyer": "星河一人科技有限公司",
    "amount": 3000, "tax_amount": 30, "total_amount": 3030,
})
store.upsert_invoice({
    "invoice_date": "2026-08-12", "document_type": "未开票收入",
    "price_tax_mode": "含税", "total_amount": 5050,
    "seller": "星河一人科技有限公司", "buyer": "示例客户乙",
})
store.upsert_invoice({
    "invoice_date": "2026-08-20", "invoice_no": "00000001-R",
    "original_invoice_no": "00000001", "invoice_type": "销项",
    "document_type": "红字发票", "price_tax_mode": "含税",
    "total_amount": 1010, "seller": "星河一人科技有限公司",
    "buyer": "示例客户有限公司",
})
store.upsert_tax_adjustment({
    "period": "2026-07", "direction": "调增", "category": "无票支出",
    "amount": 500, "basis": "示例：申报前人工复核",
})
store.upsert_tax_adjustment({
    "period": "2026-07", "direction": "已预缴所得税", "category": "以前季度预缴",
    "amount": 100, "basis": "示例完税记录",
})
store.upsert_stamp_duty_item({
    "period": "2026-07", "item": "买卖合同", "taxable_amount": 100000,
    "rate": 0.0003, "contract_no": "XS-2026-001", "counterparty": "示例客户有限公司",
})

bank_rows = [
    ("2026-07-02", "收入", 8000, "软件服务收款", "示例客户有限公司"),
    ("2026-07-05", "支出", 3000, "采购办公耗材", "示例供应商有限公司"),
    ("2026-07-08", "支出", 6000, "购置办公电脑", "电脑销售有限公司"),
    ("2026-07-10", "收入", 20000, "取得短期经营借款", "示例银行"),
    ("2026-07-15", "支出", 1000, "缴纳增值税及附加", "国家税务总局"),
    ("2026-07-31", "支出", 8700, "发放七月工资", "张明"),
]
store.import_bank_transactions([
    {
        "date": tx_date, "direction": direction, "amount": amount,
        "summary": summary, "counterparty": counterparty,
    }
    for tx_date, direction, amount, summary, counterparty in bank_rows
])
store.auto_reconcile_bank_transactions("2026-07")
store.post_profit_close_voucher("2026-07")
store.post_tax_accrual_voucher("2026-09")
store.post_profit_close_voucher("2026-09")

export_finance_workbook(store, OUTPUT, "2026-09")
print(OUTPUT)
shutil.rmtree(DATA_DIR, ignore_errors=True)
