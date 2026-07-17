#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import tempfile
import unittest
import json
from pathlib import Path

from main import AppConfig
from model_runner import LlamaServerRunner, SemanticMatcher, format_match_details
from modules.vocabulary_module import load_vocab, parse_terms, save_vocab, split_layer3
from natural_entry import build_voucher_plan
from ocr_service import OcrService


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config.json"
INVOICE_FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "real_invoice_cn.png"
INVOICE_PDF_FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "real_invoice_cn.pdf"
OCR_ADAPTER_AVAILABLE = AppConfig(
    CONFIG_PATH, "enterprise"
).ocr_adapter_path.exists()


class AccountingProfileTests(unittest.TestCase):
    def test_project_uses_only_small_enterprise_rules_and_data(self):
        enterprise = AppConfig(CONFIG_PATH, "enterprise")

        self.assertEqual(list(enterprise.profiles), ["enterprise"])
        self.assertEqual(enterprise.accounting_type, "small_enterprise")
        self.assertEqual(enterprise.accounting_standards, ["小企业会计准则"])
        self.assertEqual(enterprise.primary_accounting_standard, "小企业会计准则")
        self.assertGreaterEqual(len(load_vocab(enterprise.vocab_path)), 10)
        self.assertEqual(enterprise.model_config.context_size, 4096)
        self.assertEqual(enterprise.model_config.port, 18083)
        self.assertEqual(enterprise.model_config.max_tokens, 256)
        self.assertFalse(enterprise.model_config.reasoning)
        self.assertEqual(enterprise.model_config.gpu_layers, "all")
        raw_config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertFalse(Path(raw_config["models"]["semantic"]["modelPath"]).is_absolute())
        self.assertFalse(Path(raw_config["runtime"]["llamaServerPath"]).is_absolute())
        self.assertFalse(Path(raw_config["models"]["ocr"]["adapterPath"]).is_absolute())

        runner = LlamaServerRunner(enterprise.model_config, enterprise.llama_server_path)
        matcher = SemanticMatcher(
            runner,
            enterprise.vocab_path,
            enterprise.semantic_categories_path,
        )
        matches = matcher.match_exact("办公系统技术服务")
        self.assertEqual(
            matches[0]["record"]["subject"],
            "5602 管理费用-技术服务费",
        )

        enterprise_vocab = load_vocab(enterprise.vocab_path)
        self.assertGreaterEqual(len(enterprise_vocab), 39)
        for record in enterprise_vocab:
            self.assertTrue(record.get("input"), f"企业词库缺少第一层: {record}")
            self.assertTrue(record.get("layer2"), f"企业词库缺少第二层: {record}")
            self.assertTrue(record.get("layer3"), f"企业词库缺少第三层: {record}")
            self.assertRegex(record.get("subject", ""), r"^\d{4} .+")

        deterministic_terms = {}
        for record in enterprise_vocab:
            for layer, terms in (
                ("input", [record.get("input", "")]),
                ("layer2", parse_terms(record.get("layer2", ""))),
            ):
                for term in terms:
                    key = (layer, term.casefold())
                    previous = deterministic_terms.setdefault(key, record["subject"])
                    self.assertEqual(
                        previous,
                        record["subject"],
                        f"企业确定性规则冲突: {layer}={term}",
                    )

        for category in matcher.semantic_categories["categories"].values():
            for subject in category.get("subjects", []):
                self.assertIsNotNone(
                    matcher._find_vocab_record(subject),
                    f"企业语义分类未映射到词库科目: {subject}",
                )

    @unittest.skipUnless(
        INVOICE_FIXTURE.exists() and OCR_ADAPTER_AVAILABLE,
        "OCR fixture or local runtime is not available",
    )
    def test_rapidocr_extracts_invoice_fields(self):
        config = AppConfig(CONFIG_PATH, "enterprise")
        with tempfile.TemporaryDirectory(prefix="accountingdemo-ocr-") as temp_dir:
            config.ocr_config = dict(config.ocr_config)
            config.ocr_config["outputDir"] = temp_dir
            result = OcrService(config).recognize_invoice(INVOICE_FIXTURE)

        self._assert_invoice_fields(result)

    @unittest.skipUnless(
        INVOICE_PDF_FIXTURE.exists() and OCR_ADAPTER_AVAILABLE,
        "PDF fixture or local runtime is not available",
    )
    def test_pdf_invoice_extracts_text_layer_or_ocr(self):
        config = AppConfig(CONFIG_PATH, "enterprise")
        with tempfile.TemporaryDirectory(prefix="accountingdemo-pdf-ocr-") as temp_dir:
            config.ocr_config = dict(config.ocr_config)
            config.ocr_config["outputDir"] = temp_dir
            result = OcrService(config).recognize_invoice(INVOICE_PDF_FIXTURE)

        self._assert_invoice_fields(result)
        self.assertIn(result["ocr_engine"], {"rapidocr", "pdf_text_layer"})

    def _assert_invoice_fields(self, result):
        self.assertEqual(result["invoice_code"], "032002400111")
        self.assertEqual(result["invoice_no"], "24567891")
        self.assertEqual(result["invoice_date"], "2026-06-03")
        self.assertEqual(result["buyer"], "某市机关事务管理局")
        self.assertEqual(result["seller"], "南京云栖信息技术有限公司")
        self.assertEqual(result["description"], "办公系统技术服务")
        self.assertAlmostEqual(result["amount"], 13568.00)
        self.assertAlmostEqual(result["tax_amount"], 768.00)
        self.assertGreater(result["confidence"], 0.99)

    def test_vocab_edit_helpers_preserve_metadata_and_normalize_terms(self):
        self.assertEqual(
            parse_terms("出差、差旅\n出差，外勤 ; 交通"),
            ["出差", "差旅", "外勤", "交通"],
        )
        terms, suffix = split_layer3("出差、差旅||【重复】培训、会议")
        self.assertEqual(terms, ["出差", "差旅"])
        self.assertEqual(suffix, "||【重复】培训、会议")

        with tempfile.TemporaryDirectory(prefix="accountingdemo-vocab-save-") as temp_dir:
            vocab_path = Path(temp_dir) / "vocab.json"
            vocab_path.write_text(
                json.dumps(
                    {
                        "version": "test",
                        "科目": [{"id": "1", "subject": "测试科目", "layer2": "旧词"}],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            save_vocab(
                [{"id": "1", "subject": "测试科目", "layer2": "新词"}],
                vocab_path,
            )
            saved = json.loads(vocab_path.read_text(encoding="utf-8"))

        self.assertEqual(saved["version"], "test")
        self.assertEqual(saved["科目"][0]["layer2"], "新词")

    def test_enterprise_three_layer_rules_only_auto_post_certain_terms(self):
        class NoopRunner:
            pass

        enterprise = AppConfig(CONFIG_PATH, "enterprise")
        matcher = SemanticMatcher(
            NoopRunner(),
            enterprise.vocab_path,
            enterprise.semantic_categories_path,
        )

        office = matcher.match_rules("购买办公用品")
        self.assertEqual(office[0]["match_type"], "exact")
        self.assertEqual(
            office[0]["record"]["subject"],
            "5602 管理费用-办公费",
        )
        self.assertEqual(
            matcher.match_rules("支付押金")[0]["record"]["subject"],
            "1221 其他应收款",
        )
        self.assertEqual(
            matcher.match_rules("收取押金")[0]["record"]["subject"],
            "2241 其他应付款",
        )
        self.assertEqual(matcher.match_rules("押金"), [])
        self.assertEqual(matcher.match_rules("技术开发费"), [])

    def test_software_subscription_routes_to_office_expense(self):
        class SubscriptionRunner:
            def complete(self, prompt, max_tokens=None, temperature=None):
                return {
                    "text": (
                        '[{"category":"软件订阅","subject":"5602 管理费用-办公费",'
                        '"rule_basis":"办公软件会员用于日常经营",'
                        '"reason":"属于日常办公软件订阅支出"}]'
                    ),
                    "elapsed_seconds": 0.01,
                }

        enterprise = AppConfig(CONFIG_PATH, "enterprise")
        matcher = SemanticMatcher(
            SubscriptionRunner(),
            enterprise.vocab_path,
            enterprise.semantic_categories_path,
        )

        self.assertEqual(matcher.match_rules("购买办公软件会员"), [])
        self.assertEqual(
            matcher.semantic_categories["categories"]["软件订阅"]["subjects"],
            ["5602 管理费用-办公费"],
        )
        for tag in matcher.semantic_categories["categories"]["软件订阅"]["tags"]:
            subscription_rows = [
                row
                for row in matcher.semantic_categories["tag_index"][tag]
                if row["category"] == "软件订阅"
            ]
            self.assertEqual(
                subscription_rows[0]["subjects"],
                ["5602 管理费用-办公费"],
            )

        match = matcher.match("今天老板垫付299元购买办公软件会员")[0]
        self.assertEqual(match["record"]["subject"], "5602 管理费用-办公费")
        self.assertEqual(match["rule_category"], "软件订阅")


class SemanticRoutingTests(unittest.TestCase):
    def test_all_66_accounts_have_fixed_semantic_regressions(self):
        class NoopRunner:
            pass

        config = AppConfig(CONFIG_PATH, "enterprise")
        raw_vocab = json.loads(config.vocab_path.read_text(encoding="utf-8"))
        allowed = {
            "id", "input", "subject_code", "subject_detail", "layer2", "layer3",
            "logic", "overlap_risk", "distinction_rule",
        }
        self.assertTrue(raw_vocab)
        self.assertTrue(all(set(row) <= allowed for row in raw_vocab))
        self.assertEqual(len({row["subject_code"] for row in raw_vocab}), 66)

        matcher = SemanticMatcher(
            NoopRunner(), config.vocab_path, config.semantic_categories_path,
            config.account_catalog_path,
        )
        corpus_path = PROJECT_ROOT / "tests" / "fixtures" / "semantic_regression_small_enterprise.json"
        corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(corpus), 200)
        self.assertEqual(len({row["subject_code"] for row in corpus}), 66)
        for row in corpus:
            matches = matcher.match_exact(row["text"])
            self.assertTrue(matches, row["text"])
            self.assertEqual(
                matches[0]["record"]["subject_code"], row["subject_code"], row["text"]
            )
        subsidy = matcher.match_rules("收到财政局补贴")
        self.assertEqual(subsidy[0]["record"]["subject_code"], "5301")
        self.assertIn("政府补助", subsidy[0]["record"]["subject"])

        platform_rows = [
            row for row in corpus if row.get("scenario") == "内容平台资金往来"
        ]
        self.assertGreaterEqual(len(platform_rows), 60)
        self.assertEqual(
            len({
                (row.get("input"), row.get("subject_code"), row.get("subject_detail"))
                for row in raw_vocab
            }),
            len(raw_vocab),
            "语义生成器产生了重复业务映射记录",
        )

    def test_platform_compound_activities_map_without_brand_only_autopost(self):
        class FailIfCalledRunner:
            def complete(self, *_args, **_kwargs):
                raise AssertionError("孤立平台品牌词不应调用模型推荐科目")

        config = AppConfig(CONFIG_PATH, "enterprise")
        matcher = SemanticMatcher(
            FailIfCalledRunner(),
            config.vocab_path,
            config.semantic_categories_path,
        )
        cases = {
            "收到巨量星图商单收入5000元": ("5001", "内容商业合作"),
            "结算快分销带货佣金3000元": ("5001", "平台带货佣金"),
            "支付小红书聚光投放费800元": ("5601", "平台推广投流"),
            "支付抖店店铺保证金2000元": ("1221", "平台保证金"),
            "发生淘宝直播订单退货退款600元": ("5001", "平台销售退回"),
            "支付MCN机构分成1200元": ("5401", "内容合作分成"),
            "微信小店货款待结算9000元": ("1012", "平台待结算款"),
            "收到B站创作激励收入700元": ("5001", "创作者激励与打赏"),
        }
        for text, expected in cases.items():
            result = matcher.match_rules(text)[0]["record"]
            self.assertEqual(
                (result["subject_code"], result.get("subject_detail")),
                expected,
                text,
            )

        for brand in ("抖音", "抖音平台", "快手", "视频号", "小红书", "B站", "淘宝直播"):
            self.assertEqual(matcher.match_rules(brand), [], brand)
            self.assertEqual(matcher.match(brand), [], brand)

    def test_platform_net_settlement_requires_multi_subject_review(self):
        class NetSettlementRunner:
            def complete(self, prompt, max_tokens=None, temperature=None):
                self.prompt = prompt
                return {
                    "text": (
                        '[{"category":"平台净额结算拆分",'
                        '"subject":"1012 其他货币资金-平台待结算款",'
                        '"rule_basis":"结算单为扣费后净到账",'
                        '"reason":"需要按结算项目拆分"}]'
                    ),
                    "elapsed_seconds": 0.01,
                }

        config = AppConfig(CONFIG_PATH, "enterprise")
        runner = NetSettlementRunner()
        matcher = SemanticMatcher(
            runner,
            config.vocab_path,
            config.semantic_categories_path,
        )
        results = matcher.match("平台结算单净额9000元，已扣服务费和税费")

        self.assertGreaterEqual(len(results), 4)
        self.assertTrue(all(row["manual_review_required"] for row in results))
        self.assertIn("人工拆分复核：是", runner.prompt)
        self.assertIn("不得把净到账额直接判断为收入", runner.prompt)
        self.assertTrue({"1012", "5001", "5601", "2221"} <= {
            row["record"]["subject_code"] for row in results
        })
        self.assertIn("人工拆分复核", format_match_details(results[0]))
        with self.assertRaisesRegex(ValueError, "净额结算不能按到账净额直接记收入"):
            build_voucher_plan(
                "平台结算单净额9000元，已扣服务费和税费",
                results[0],
            )

    def test_full_category_prompt_stays_compact_for_four_k_context(self):
        class PromptCaptureRunner:
            def complete(self, prompt, max_tokens=None, temperature=None):
                self.prompt = prompt
                return {
                    "text": (
                        '[{"category":"内容商业合作",'
                        '"subject":"5001 主营业务收入-内容商业合作",'
                        '"rule_basis":"蒲公英报备内容已交付",'
                        '"reason":"属于品牌内容商单"}]'
                    ),
                    "elapsed_seconds": 0.01,
                }

        config = AppConfig(CONFIG_PATH, "enterprise")
        runner = PromptCaptureRunner()
        matcher = SemanticMatcher(
            runner,
            config.vocab_path,
            config.semantic_categories_path,
        )
        results = matcher.match_with_ai("蒲公英报备合作内容已经交付，收到5000元")

        self.assertEqual(results[0]["rule_category"], "内容商业合作")
        categories = matcher.semantic_categories["categories"]
        self.assertTrue(all(f"【{name}】" in runner.prompt for name in categories))
        self.assertLess(len(runner.prompt), 12000)
        self.assertIn("三级口语线索：蒲公英报备", runner.prompt)
        self.assertNotIn("三级口语线索：星图商单", runner.prompt)

    def test_enterprise_colloquial_business_flow_uses_full_ai_categories(self):
        class EnterpriseRunner:
            def __init__(self):
                self.prompt = ""

            def complete(self, prompt, max_tokens=None, temperature=None):
                self.prompt = prompt
                return {
                    "text": (
                        '[{"category":"股东垫付款","subject":"2241 其他应付款",'
                        '"rule_basis":"个人代公司付款","reason":"形成股东往来"}]'
                    ),
                    "elapsed_seconds": 0.01,
                }

        config = AppConfig(CONFIG_PATH, "enterprise")
        runner = EnterpriseRunner()
        matcher = SemanticMatcher(
            runner,
            config.vocab_path,
            config.semantic_categories_path,
        )

        results = matcher.match("老板垫付了软件费")

        self.assertEqual(results[0]["record"]["subject"], "2241 其他应付款")
        self.assertEqual(results[0]["rule_category"], "股东垫付款")
        self.assertIn("股东垫付款", runner.prompt)
        self.assertIn("营业外收益", runner.prompt)
        self.assertIn("三级口语线索", runner.prompt)
        self.assertIn("三级口语词：老板垫付", results[0]["rule_basis"])
        self.assertIn("规则词库分类：股东垫付款", format_match_details(results[0]))

    def test_rule_miss_goes_directly_to_ai_with_all_categories(self):
        class RecordingRunner:
            def __init__(self):
                self.prompt = ""
                self.max_tokens = "not-called"

            def complete(self, prompt, max_tokens=None, temperature=None):
                self.prompt = prompt
                self.max_tokens = max_tokens
                return {
                    "text": (
                        '[{"category":"AI分类","rule_basis":"摘要符合模糊关键词",'
                        '"subject":"模型科目","reason":"模型判断"}]'
                    ),
                    "elapsed_seconds": 0.01,
                }

        vocab = [
            {"input": "明确规则", "subject": "规则科目"},
            {"input": "仅供映射", "subject": "模型科目"},
        ]
        categories = {
            "categories": {
                "AI分类": {"tags": ["模糊关键词"], "subjects": ["模型科目"]},
                "另一分类": {"tags": ["其他"], "subjects": ["规则科目"]},
            },
            "tag_index": {
                "模糊关键词": [
                    {"category": "错误规则候选", "subjects": ["规则科目"]}
                ]
            },
        }

        with tempfile.TemporaryDirectory(prefix="accountingdemo-routing-") as temp_dir:
            temp_path = Path(temp_dir)
            vocab_path = temp_path / "vocab.json"
            categories_path = temp_path / "categories.json"
            vocab_path.write_text(json.dumps(vocab, ensure_ascii=False), encoding="utf-8")
            categories_path.write_text(
                json.dumps(categories, ensure_ascii=False), encoding="utf-8"
            )

            runner = RecordingRunner()
            matcher = SemanticMatcher(runner, vocab_path, categories_path)
            results = matcher.match("模糊关键词")

        self.assertEqual(results[0]["match_type"], "ai_suggested")
        self.assertEqual(results[0]["record"]["subject"], "模型科目")
        self.assertIn("AI分类", runner.prompt)
        self.assertIn("另一分类", runner.prompt)
        self.assertIn('"rule_basis"', runner.prompt)
        self.assertIn('"subject"', runner.prompt)
        self.assertIn("优先推荐体现", runner.prompt)
        self.assertIn("老板或股东垫付通常是对方科目线索", runner.prompt)
        self.assertIsNone(runner.max_tokens)
        self.assertEqual(results[0]["rule_category"], "AI分类")
        self.assertIn("分类词：模糊关键词", results[0]["rule_basis"])
        self.assertEqual(results[0]["recommendation_reason"], "模型判断")
        self.assertEqual(results[0]["record"]["rule_category"], "AI分类")
        details = format_match_details(results[0])
        self.assertIn("规则词库分类：AI分类", details)
        self.assertIn("规则依据：", details)
        self.assertIn("模型推荐理由：模型判断", details)

    def test_colloquial_layer_is_not_treated_as_a_certain_rule(self):
        class NoopRunner:
            pass

        vocab = [
            {
                "input": "正式科目词",
                "subject": "规则科目",
                "layer2": "明确同义词",
                "layer3": "口语表达",
            }
        ]
        with tempfile.TemporaryDirectory(prefix="accountingdemo-rules-") as temp_dir:
            temp_path = Path(temp_dir)
            vocab_path = temp_path / "vocab.json"
            categories_path = temp_path / "categories.json"
            vocab_path.write_text(json.dumps(vocab, ensure_ascii=False), encoding="utf-8")
            categories_path.write_text('{"categories":{}}', encoding="utf-8")
            matcher = SemanticMatcher(NoopRunner(), vocab_path, categories_path)

            self.assertEqual(matcher.match_rules("正式科目词")[0]["match_type"], "exact")
            self.assertEqual(matcher.match_rules("明确同义词")[0]["match_type"], "layer2")
            self.assertEqual(matcher.match_rules("口语表达"), [])


if __name__ == "__main__":
    unittest.main()
