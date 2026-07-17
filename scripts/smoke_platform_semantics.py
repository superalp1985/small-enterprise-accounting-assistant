#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Run real-model acceptance cases for mainstream content-platform semantics."""

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import model_runner as MR
from main import AppConfig
from natural_entry import build_voucher_plan


MODEL_CASES = (
    ("蒲公英报备合作内容已经交付，收到5000元", "内容商业合作", "5001"),
    ("这个月B站充电和直播礼物分成到账800元", "创作者激励与打赏", "5001"),
    ("万相台充值里本月实际消耗500元做推广", "平台推广投流", "5601"),
)
MANUAL_REVIEW_TEXT = "平台结算单净额9000元，已扣服务费和税费"


def main() -> int:
    config = AppConfig(PROJECT_ROOT / "config.json")
    runner = MR.LlamaServerRunner(
        config.model_config,
        config.llama_server_path,
        config.llama_cuda_server_path,
    )
    report = {"version": config.app_version, "cases": []}
    try:
        matcher = MR.SemanticMatcher(
            runner,
            config.vocab_path,
            config.semantic_categories_path,
        )
        for text, expected_category, expected_code in MODEL_CASES:
            matches = matcher.match_with_ai(text, max_results=5)
            selected = next(
                (
                    item for item in matches
                    if item.get("rule_category") == expected_category
                    and item.get("record", {}).get("subject_code") == expected_code
                ),
                None,
            )
            if selected is None:
                report["cases"].append({
                    "text": text,
                    "ok": False,
                    "expected_category": expected_category,
                    "actual": [
                        {
                            "category": item.get("rule_category"),
                            "subject": item.get("record", {}).get("subject"),
                        }
                        for item in matches
                    ],
                })
                continue
            plan = build_voucher_plan(text, selected)
            debit = sum(float(line["debit"]) for line in plan["lines"])
            credit = sum(float(line["credit"]) for line in plan["lines"])
            report["cases"].append({
                "text": text,
                "ok": debit == credit and debit > 0,
                "category": selected.get("rule_category"),
                "subject": selected.get("record", {}).get("subject"),
                "rule_basis": selected.get("rule_basis"),
                "reason": selected.get("recommendation_reason"),
                "balanced_amount": debit,
            })

        review_matches = matcher.match(MANUAL_REVIEW_TEXT)
        review_codes = {
            item.get("record", {}).get("subject_code") for item in review_matches
        }
        review_ok = bool(review_matches) and all(
            item.get("manual_review_required") for item in review_matches
        ) and {"1012", "5001", "5601", "2221"} <= review_codes
        try:
            build_voucher_plan(MANUAL_REVIEW_TEXT, review_matches[0])
        except ValueError:
            blocked = True
        else:
            blocked = False
        report["cases"].append({
            "text": MANUAL_REVIEW_TEXT,
            "ok": review_ok and blocked,
            "category": review_matches[0].get("rule_category") if review_matches else "",
            "manual_review_required": review_ok,
            "one_sentence_posting_blocked": blocked,
            "subject_codes": sorted(code for code in review_codes if code),
        })
        report["backend"] = runner.backend_label
        report["ok"] = all(row["ok"] for row in report["cases"])
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1
    finally:
        runner.stop_server()


if __name__ == "__main__":
    raise SystemExit(main())
