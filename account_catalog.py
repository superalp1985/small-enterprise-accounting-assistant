#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Official small-enterprise account catalog and industry-template helpers."""

import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


CATALOG_FILENAME = "account_catalog_small_enterprise.json"
SEMANTIC_FIELDS = (
    "id", "input", "subject_code", "subject_detail", "layer2", "layer3", "logic",
    "overlap_risk", "distinction_rule",
)


def default_catalog_path() -> Path:
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        bundled = Path(bundle_root) / CATALOG_FILENAME
        if bundled.exists():
            return bundled
    return Path(__file__).resolve().parent / CATALOG_FILENAME


def load_account_catalog(path: Optional[Path] = None) -> Dict[str, Any]:
    catalog_path = Path(path or default_catalog_path())
    with open(catalog_path, encoding="utf-8") as handle:
        catalog = json.load(handle)
    validate_account_catalog(catalog)
    return catalog


def validate_account_catalog(catalog: Dict[str, Any]) -> None:
    accounts = list(catalog.get("accounts", []))
    if len(accounts) != 66:
        raise ValueError(f"小企业会计科目目录必须正好包含66个科目，当前为{len(accounts)}个")
    codes = [str(account.get("code", "")) for account in accounts]
    if any(not code.isdigit() for code in codes):
        raise ValueError("科目目录存在无效科目编码")
    if len(set(codes)) != 66:
        raise ValueError("科目目录存在重复科目编码")
    orders = [int(account.get("order", 0) or 0) for account in accounts]
    if sorted(orders) != list(range(1, 67)):
        raise ValueError("科目目录顺序号必须为1至66且不得重复")

    known = set(codes)
    templates = catalog.get("templates", {})
    if not isinstance(templates, dict) or not templates:
        raise ValueError("科目目录缺少行业启用模板")
    for label, template in templates.items():
        enabled = [str(code) for code in template.get("enabled_codes", [])]
        if not enabled:
            raise ValueError(f"科目模板“{label}”没有启用任何科目")
        invalid = sorted(set(enabled) - known)
        if invalid:
            raise ValueError(f"科目模板“{label}”引用未知科目：{', '.join(invalid)}")
        if len(enabled) != len(set(enabled)):
            raise ValueError(f"科目模板“{label}”存在重复科目编码")
        if template.get("solo_company_template"):
            for field in ("common_businesses", "monthly_focus", "risk_hints"):
                values = template.get(field, [])
                if not isinstance(values, list) or not values:
                    raise ValueError(f"一人公司模板“{label}”缺少{field}")
            details = template.get("recommended_details", {})
            if not isinstance(details, dict):
                raise ValueError(f"一人公司模板“{label}”的建议明细科目格式无效")
            invalid_details = sorted(set(str(code) for code in details) - set(enabled))
            if invalid_details:
                raise ValueError(
                    f"一人公司模板“{label}”的建议明细引用未启用科目："
                    + ", ".join(invalid_details)
                )


def account_index(catalog: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(account["code"]): dict(account)
        for account in catalog.get("accounts", [])
    }


def account_label(account: Dict[str, Any]) -> str:
    return f"{account.get('code', '')} {account.get('name', '')}".strip()


def template_labels(catalog: Optional[Dict[str, Any]] = None) -> List[str]:
    data = catalog or load_account_catalog()
    return list(data.get("templates", {}).keys())


def template_profile(template: str,
                     catalog: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = catalog or load_account_catalog()
    templates = data.get("templates", {})
    selected = templates.get(template) or templates.get("服务业") or {}
    return dict(selected)


def template_summary(template: str,
                     catalog: Optional[Dict[str, Any]] = None) -> str:
    profile = template_profile(template, catalog)
    enabled_count = len(profile.get("enabled_codes", []))
    lines = [
        str(profile.get("description", "按业务实质启用常用科目。")),
        f"启用 {enabled_count}/66 个一级科目；未启用科目仍保留在完整目录中，可切换模板。",
    ]
    businesses = [str(value) for value in profile.get("common_businesses", [])]
    focus = [str(value) for value in profile.get("monthly_focus", [])]
    risks = [str(value) for value in profile.get("risk_hints", [])]
    if businesses:
        lines.append("常见业务：" + "、".join(businesses))
    if focus:
        lines.append("月末关注：" + "；".join(focus))
    if risks:
        lines.append("风险提示：" + "；".join(risks))
    return "\n".join(lines)


def enabled_account_codes(template: str,
                          catalog: Optional[Dict[str, Any]] = None) -> List[str]:
    data = catalog or load_account_catalog()
    templates = data.get("templates", {})
    selected = templates.get(template) or templates.get("服务业") or {}
    return [str(code) for code in selected.get("enabled_codes", [])]


def enabled_accounts(template: str,
                     catalog: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    data = catalog or load_account_catalog()
    enabled = set(enabled_account_codes(template, data))
    return [
        dict(account) for account in data.get("accounts", [])
        if str(account.get("code", "")) in enabled
    ]


def catalog_basis(account: Dict[str, Any], catalog: Dict[str, Any]) -> str:
    source = catalog.get("source", {})
    return (
        f"【科目依据】《小企业会计准则》官方会计科目表第{account.get('order')}项："
        f"{account_label(account)}。\n"
        f"【适用说明】{account.get('usage', '按业务实质和准则主要账务处理使用。')}\n"
        f"【官方来源】{source.get('title', '')} {source.get('url', '')}"
    )


def enrich_vocab_records(records: Iterable[Dict[str, Any]],
                         catalog: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    data = catalog or load_account_catalog()
    by_code = account_index(data)
    enriched: List[Dict[str, Any]] = []
    for source in records:
        record = dict(source)
        code = str(record.get("subject_code") or record.get("code") or "").strip()
        original_subject = str(record.get("subject", "")).strip()
        if not code:
            candidate = original_subject.split(" ", 1)[0]
            code = candidate if candidate.isdigit() else ""
        account = by_code.get(code)
        if not account:
            enriched.append(record)
            continue
        detail = str(record.get("subject_detail", "")).strip().lstrip("-")
        base_label = account_label(account)
        if not detail and original_subject.startswith(base_label):
            detail = original_subject[len(base_label):].strip().lstrip("-")
        record["subject_code"] = code
        if detail:
            record["subject_detail"] = detail
        record["code"] = code
        record["subject_name"] = str(account["name"])
        record["subject"] = f"{base_label}-{detail}" if detail else base_label
        record["account_class"] = str(account.get("class", ""))
        record["normal_balance"] = str(account.get("normal_balance", ""))
        record["law"] = catalog_basis(account, data)
        enriched.append(record)
    return enriched


def semantic_payload(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Strip account-master data before persisting the editable semantic map."""
    payload = []
    for source in records:
        record = {
            key: source.get(key)
            for key in SEMANTIC_FIELDS
            if source.get(key) not in (None, "")
        }
        code = str(
            record.get("subject_code")
            or source.get("code")
            or str(source.get("subject", "")).split(" ", 1)[0]
        ).strip()
        if code:
            record["subject_code"] = code
        payload.append(record)
    return payload
