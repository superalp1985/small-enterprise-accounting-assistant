#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Turn a colloquial business sentence into a reviewable two-line voucher."""

import re
from datetime import date, timedelta
from typing import Any, Dict, Optional


REVENUE_CODES = {"5001", "5051", "5111", "5301"}
EXPENSE_CODES = {"5401", "5403", "5601", "5602", "5603", "5711", "5801"}
LIABILITY_CODES = {"2001", "2202", "2203", "2211", "2221", "2241"}
EQUITY_CODES = {"3001", "3103", "3104"}
BUSINESS_CONTENT_WORDS = (
    "购买", "采购", "买了", "费用", "服务", "会员", "订阅", "办公",
    "差旅", "招待", "培训", "会议", "销售", "收入", "成本", "资产",
)
SETTLEMENT_PHRASES = (
    "老板垫付", "股东垫付", "法人垫付", "个人垫付", "自己先付",
    "微信支付", "支付宝支付", "银行卡支付", "公司卡支付", "现金支付",
    "客户未付款", "对方未付款", "未收款", "未付款",
)


def _subject_code(subject: str) -> str:
    match = re.match(r"\s*(\d{4})", str(subject or ""))
    return match.group(1) if match else ""


def _scaled_amount(number: str, unit: str = "") -> float:
    value = float(str(number).replace(",", ""))
    if unit == "万":
        value *= 10000
    elif unit == "千":
        value *= 1000
    return round(value, 2)


def extract_amount(text: str) -> float:
    """Extract the transaction total, preferring explicit total/amount labels."""
    normalized = str(text or "").replace("，", ",")
    labeled = re.search(
        r"(?:价税合计|合计|共计|总计|总额|金额)\s*[:：]?\s*"
        r"([0-9][0-9,]*(?:\.\d+)?)\s*(万|千|元|块钱|块)?",
        normalized,
    )
    if labeled:
        return _scaled_amount(labeled.group(1), labeled.group(2) or "")

    candidates = [
        _scaled_amount(number, unit)
        for number, unit in re.findall(
            r"([0-9][0-9,]*(?:\.\d+)?)\s*(万|千|元|块钱|块)",
            normalized,
        )
    ]
    if candidates:
        return max(candidates)

    action_amount = re.search(
        r"(?:支付|付款|花了|收到|收款|借入|借了|投入|转入|报销)"
        r"[^0-9]{0,10}([0-9][0-9,]*(?:\.\d+)?)",
        normalized,
    )
    return _scaled_amount(action_amount.group(1)) if action_amount else 0.0


def extract_tax_amount(text: str) -> float:
    match = re.search(
        r"(?:其中)?税额\s*[:：]?\s*([0-9][0-9,]*(?:\.\d+)?)\s*(万|千|元)?",
        str(text or ""),
    )
    return _scaled_amount(match.group(1), match.group(2) or "") if match else 0.0


def extract_date(text: str, reference_date: Optional[date] = None) -> str:
    reference_date = reference_date or date.today()
    value = str(text or "")
    full = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})日?", value)
    if full:
        return date(int(full.group(1)), int(full.group(2)), int(full.group(3))).isoformat()
    short = re.search(r"(?<!\d)(\d{1,2})月(\d{1,2})日?", value)
    if short:
        return date(reference_date.year, int(short.group(1)), int(short.group(2))).isoformat()
    if "前天" in value:
        return (reference_date - timedelta(days=2)).isoformat()
    if "昨天" in value or "昨日" in value:
        return (reference_date - timedelta(days=1)).isoformat()
    return reference_date.isoformat()


def extract_invoice_no(text: str) -> str:
    match = re.search(
        r"(?:发票号码?|票号)\s*[:：]?\s*([A-Za-z0-9-]{6,30})",
        str(text or ""),
    )
    return match.group(1) if match else ""


def extract_counterparty(text: str) -> str:
    value = str(text or "")
    patterns = (
        r"(?:向|给)([\u4e00-\u9fffA-Za-z0-9（）()·]{2,24}?)(?:支付|付款|转账|采购|购买)",
        r"(?:从)([\u4e00-\u9fffA-Za-z0-9（）()·]{2,24}?)(?:收到|收款|取得)",
    )
    for pattern in patterns:
        match = re.search(pattern, value)
        if match:
            return match.group(1).strip()
    return ""


def extract_transaction_facts(text: str, reference_date: Optional[date] = None) -> Dict[str, Any]:
    amount = extract_amount(text)
    tax_amount = extract_tax_amount(text)
    return {
        "description": str(text or "").strip(),
        "amount": amount,
        "tax_amount": tax_amount,
        "date": extract_date(text, reference_date),
        "invoice_no": extract_invoice_no(text),
        "counterparty": extract_counterparty(text),
    }


def semantic_business_text(text: str) -> str:
    """Remove settlement-role facts only when a separate business object exists."""
    value = str(text or "").strip()
    if not any(word in value for word in BUSINESS_CONTENT_WORDS):
        return value
    for phrase in SETTLEMENT_PHRASES:
        value = value.replace(phrase, " ")
    value = re.sub(r"\s+", " ", value).strip(" ，,。")
    return value or str(text or "").strip()


def infer_primary_direction(subject: str, text: str) -> str:
    code = _subject_code(subject)
    value = str(text or "")
    reversal = any(word in value for word in ("退款给客户", "退货退款", "冲减收入"))
    reversal = reversal or any(word in value for word in ("红字发票", "开红票", "冲红"))
    settle_liability = any(
        word in value for word in (
            "偿还", "归还借款", "还贷款", "支付货款", "付供应商",
            "缴纳", "交税", "发工资", "归还垫款", "还老板",
        )
    )
    collect_receivable = code in {"1122", "1221"} and any(
        word in value for word in ("收到回款", "收回", "客户付款", "回款到账")
    )

    if code in REVENUE_CODES:
        return "借方" if reversal else "贷方"
    if code in LIABILITY_CODES:
        return "借方" if settle_liability else "贷方"
    if code in EQUITY_CODES:
        return "贷方"
    if collect_receivable:
        return "贷方"
    return "借方"


def infer_counter_subject(primary_subject: str, direction: str, text: str) -> Dict[str, str]:
    code = _subject_code(primary_subject)
    value = str(text or "")

    if direction == "借方":
        if any(word in value for word in ("老板垫付", "股东垫付", "个人垫付", "自己先付")):
            return {"subject": "2241 其他应付款", "basis": "摘要表明由股东或个人先行垫付"}
        if any(word in value for word in ("未付款", "赊购", "挂账", "欠供应商")):
            return {"subject": "2202 应付账款", "basis": "摘要表明款项尚未支付"}
        if "现金" in value and not any(word in value for word in ("微信", "支付宝", "银行卡")):
            return {"subject": "1001 库存现金", "basis": "摘要明确使用现金结算"}
        return {"subject": "1002 银行存款", "basis": "已付款业务默认使用企业银行结算科目"}

    if code in REVENUE_CODES and any(
        word in value for word in (
            "未收款", "客户未付款", "对方未付款", "赊销", "挂账", "待收", "客户欠款",
        )
    ):
        return {"subject": "1122 应收账款", "basis": "摘要表明收入已确认但款项尚未收取"}
    if "现金" in value and not any(word in value for word in ("微信", "支付宝", "银行卡")):
        return {"subject": "1001 库存现金", "basis": "摘要明确使用现金收款"}
    return {"subject": "1002 银行存款", "basis": "已收款或融资业务默认进入企业银行账户"}


def build_voucher_plan(text: str, match: Dict[str, Any],
                       reference_date: Optional[date] = None) -> Dict[str, Any]:
    record = match.get("record", match)
    if match.get(
        "manual_review_required", record.get("manual_review_required", False)
    ):
        message = match.get("review_message", record.get("review_message", ""))
        raise ValueError(
            message or "这笔业务包含需要拆分的项目，请核对原始结算单后手工录入多科目凭证"
        )

    facts = extract_transaction_facts(text, reference_date)
    if facts["amount"] <= 0:
        raise ValueError("没有识别到交易金额，请在描述中写明“299元”或“2万元”")
    if facts["tax_amount"] > facts["amount"]:
        raise ValueError("税额不能大于交易总额")

    primary_subject = str(record.get("subject", "")).strip()
    if not primary_subject:
        raise ValueError("语义结果缺少会计科目")
    direction = infer_primary_direction(primary_subject, text)
    counter = infer_counter_subject(primary_subject, direction, text)
    if counter["subject"] == primary_subject:
        counter = {
            "subject": "1002 银行存款" if primary_subject != "1002 银行存款" else "2241 其他应付款",
            "basis": "为保持凭证科目有效，采用默认结算或往来科目",
        }

    amount = facts["amount"]
    tax_amount = facts["tax_amount"]
    is_revenue = _subject_code(primary_subject) in REVENUE_CODES
    if is_revenue and tax_amount > 0:
        net_amount = round(amount - tax_amount, 2)
        lines = [
            {
                "subject": primary_subject,
                "debit": net_amount if direction == "借方" else 0.0,
                "credit": net_amount if direction == "贷方" else 0.0,
            },
            {
                "subject": counter["subject"],
                "debit": amount if direction == "贷方" else 0.0,
                "credit": amount if direction == "借方" else 0.0,
            },
            {
                "subject": "2221 应交税费-应交增值税",
                "debit": tax_amount if direction == "借方" else 0.0,
                "credit": tax_amount if direction == "贷方" else 0.0,
            },
        ]
    else:
        primary_debit = amount if direction == "借方" else 0.0
        primary_credit = amount if direction == "贷方" else 0.0
        lines = [
            {"subject": primary_subject, "debit": primary_debit, "credit": primary_credit},
            {"subject": counter["subject"], "debit": primary_credit, "credit": primary_debit},
        ]
    return {
        **facts,
        "primary_subject": primary_subject,
        "counter_subject": counter["subject"],
        "direction": direction,
        "counter_basis": counter["basis"],
        "match": match,
        "lines": lines,
    }


def post_voucher_plan(store, plan: Dict[str, Any]):
    common = {
        "description": plan["description"],
        "date": plan["date"],
        "source": "natural_language",
        "counterparty": plan.get("counterparty", ""),
    }
    lines = []
    for index, line in enumerate(plan["lines"]):
        payload = {**common, **line}
        if index == 0:
            payload.update({
                "invoice_no": plan.get("invoice_no", ""),
                "tax_amount": plan.get("tax_amount", 0),
            })
        lines.append(payload)
    added = store.add_voucher_lines(lines, voucher_date=plan["date"])

    invoice_no = str(plan.get("invoice_no", "")).strip()
    description = str(plan.get("description", ""))
    code = _subject_code(plan["primary_subject"])
    is_revenue = code in REVENUE_CODES
    document_type = (
        "未开票收入" if is_revenue and "未开票" in description else
        "红字发票" if is_revenue and any(word in description for word in ("红字", "红票", "冲红")) else
        "正常发票"
    )
    if invoice_no or document_type == "未开票收入":
        settings = store.get_settings()
        tax_amount = float(plan.get("tax_amount", 0) or 0)
        store.upsert_invoice({
            "invoice_no": invoice_no,
            "invoice_date": plan["date"],
            "invoice_type": "销项" if is_revenue else "进项",
            "document_type": document_type,
            "invoice_form": (
                "增值税专用发票" if "专票" in description else
                "无票" if document_type == "未开票收入" else "普通发票"
            ),
            "price_tax_mode": "含税",
            "seller": settings["company"].get("name", "") if is_revenue else plan.get("counterparty", ""),
            "buyer": plan.get("counterparty", "") if is_revenue else settings["company"].get("name", ""),
            "amount": max(0.0, plan["amount"] - tax_amount),
            "tax_amount": tax_amount,
            "total_amount": plan["amount"],
            "deductible": bool(settings["tax"].get("input_vat_deductible") and not is_revenue),
            "status": "已确认",
            "source": "natural_language",
        })
    return added
