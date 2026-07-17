#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Run one real-model smoke test for the natural-language bookkeeping flow."""

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import model_runner as MR
from main import AppConfig
from natural_entry import build_voucher_plan, semantic_business_text


DEFAULT_TEXT = "今天老板垫付299元购买办公软件会员"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("text", nargs="?", default=DEFAULT_TEXT)
    args = parser.parse_args()

    config = AppConfig(PROJECT_ROOT / "config.json")
    runner = MR.LlamaServerRunner(
        config.model_config,
        config.llama_server_path,
        config.llama_cuda_server_path,
    )
    try:
        matcher = MR.SemanticMatcher(
            runner,
            config.vocab_path,
            config.semantic_categories_path,
        )
        business_text = semantic_business_text(args.text)
        matches = matcher.match_with_ai(business_text, max_results=5)
        plan = build_voucher_plan(args.text, matches[0]) if matches else None
        payload = {
            "input": args.text,
            "semantic_business_text": business_text,
            "backend": runner.backend_label,
            "matches": [
                {
                    "subject": item.get("record", {}).get("subject"),
                    "category": item.get("rule_category"),
                    "rule_basis": item.get("rule_basis"),
                    "reason": item.get("recommendation_reason"),
                    "score": item.get("score"),
                }
                for item in matches
            ],
            "plan": None if plan is None else {
                "primary_subject": plan["primary_subject"],
                "counter_subject": plan["counter_subject"],
                "lines": plan["lines"],
            },
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))

        if not plan:
            return 1
        if plan["primary_subject"].startswith(("1221 ", "2241 ")):
            return 2
        if plan["counter_subject"] != "2241 其他应付款":
            return 3
        total_debit = sum(float(line["debit"]) for line in plan["lines"])
        total_credit = sum(float(line["credit"]) for line in plan["lines"])
        return 0 if total_debit == total_credit == 299.0 else 4
    finally:
        runner.stop_server()


if __name__ == "__main__":
    raise SystemExit(main())
