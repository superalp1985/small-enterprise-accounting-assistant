#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
model_runner.py - 内嵌llama-server模型运行器
从WangcaiOfficeAssistant移植，支持Qwen3.5 2B模型本地推理
"""

import json
import re
import subprocess
import time
import requests
import threading
import os
import sys
from pathlib import Path
from typing import Optional, Dict, Any
from dataclasses import dataclass

from account_catalog import enrich_vocab_records, load_account_catalog


PLATFORM_BRAND_ONLY_TERMS = {
    "抖音", "抖店", "巨量百应", "精选联盟", "巨量星图", "巨量千川", "dou+",
    "快手", "快手小店", "快分销", "磁力聚星", "磁力金牛",
    "微信视频号", "视频号", "微信小店", "优选联盟", "腾讯互选", "互选广告",
    "小红书", "蒲公英", "聚光", "薯条",
    "哔哩哔哩", "b站", "花火",
    "淘宝直播", "热浪引擎", "淘宝联盟", "阿里妈妈", "万相台",
}


def format_match_details(match: Dict[str, Any]) -> str:
    """Format rule evidence and model rationale for the UI."""
    record = match.get("record", match)
    subject = record.get("subject", "")
    code = str(record.get("code", "")).strip()
    display_subject = (
        f"{code} {subject}" if code and not subject.startswith(f"{code} ") else subject
    )
    match_type = match.get("match_type", record.get("match_type", ""))
    if match_type == "ai_suggested":
        rule_category = match.get("rule_category", record.get("rule_category", ""))
        rule_basis = match.get("rule_basis", record.get("rule_basis", ""))
        reason = match.get(
            "recommendation_reason", record.get("recommendation_reason", "")
        )
        manual_review = match.get(
            "review_message", record.get("review_message", "")
        ) if match.get(
            "manual_review_required", record.get("manual_review_required", False)
        ) else ""
        details = (
            f"模型推荐科目：{display_subject}\n"
            f"规则词库分类：{rule_category}\n"
            f"规则依据：{rule_basis}\n"
            f"模型推荐理由：{reason}"
        )
        if manual_review:
            details += f"\n人工拆分复核：{manual_review}"
        return details

    matched_word = match.get("matched_word", record.get("matched_word", ""))
    score = match.get("score", record.get("score", 0))
    rule_basis = (
        record.get("logic")
        or record.get("distinction_rule")
        or record.get("law")
        or "明确词库规则命中"
    )
    return (
        f"科目：{display_subject}\n"
        f"匹配词：{matched_word}\n"
        f"匹配类型：{match_type} | 置信度：{score:.1f}\n"
        f"规则依据：{rule_basis}"
    )


@dataclass
class ModelConfig:
    """模型配置"""
    name: str
    model_path: Path
    host: str = "127.0.0.1"
    port: int = 18083
    context_size: int = 4096
    threads: int = 4
    batch_size: int = 512
    ubatch_size: int = 128
    cache_prompt: bool = True
    cache_reuse: int = 0
    max_tokens: int = 256
    temperature: float = 0.0
    prefer_gpu: bool = True
    gpu_layers: str = "all"
    flash_attention: str = "auto"
    reasoning: bool = False
    startup_timeout: int = 120


class LlamaServerRunner:
    """
    llama.cpp服务器运行器
    自动管理服务器进程，支持健康检查和推理请求
    """

    def __init__(self, config: ModelConfig, server_path: Path,
                 cuda_server_path: Optional[Path] = None,
                 log_path: Optional[Path] = None):
        self.config = config
        self.server_path = Path(server_path)
        self.cpu_server_path = Path(server_path)
        self.cuda_server_path = Path(cuda_server_path) if cuda_server_path else None
        self.process: Optional[subprocess.Popen] = None
        self._startup_lock = threading.Lock()
        self._health_checked = False
        self._stop_event = threading.Event()
        self.selected_backend = "not_started"
        self.last_error = ""
        self._log_handle = None
        self.log_path = Path(log_path) if log_path else (
            self.cpu_server_path.parent.parent / "llama-server.log"
        )

    @property
    def backend_label(self) -> str:
        if self.selected_backend == "cuda":
            return "CUDA GPU全卸载"
        if self.selected_backend == "cpu":
            return "CPU后备"
        if self.selected_backend == "existing":
            return "已运行模型服务"
        return "尚未启动"

    def health_check(self) -> bool:
        """检查服务器健康状态，并确认端口上运行的是当前模型。"""
        try:
            response = requests.get(
                f"http://{self.config.host}:{self.config.port}/health",
                timeout=2
            )
            if response.status_code != 200:
                self._health_checked = False
                return False
            models = requests.get(
                f"http://{self.config.host}:{self.config.port}/v1/models",
                timeout=2,
            )
            models.raise_for_status()
            available = {
                str(row.get("id") or row.get("model") or row.get("name") or "")
                for row in models.json().get("data", models.json().get("models", []))
            }
            self._health_checked = self.config.name in available
            if self._health_checked and self.process is None:
                self.selected_backend = "existing"
            return self._health_checked
        except (requests.RequestException, ValueError, TypeError):
            self._health_checked = False
            return False

    def ensure_ready(self, timeout: int = 120) -> bool:
        """确保服务器已启动并就绪"""
        if self.health_check():
            return True

        with self._startup_lock:
            # 双重检查
            if self.health_check():
                return True

            errors = []
            candidates = self._backend_candidates()
            per_backend_timeout = max(15.0, timeout / max(1, len(candidates)))

            for backend, server_path in candidates:
                if self._stop_event.is_set():
                    self.last_error = "模型启动已取消"
                    return False
                try:
                    self._start_server(server_path, backend)
                except Exception as exc:
                    errors.append(f"{backend}: {exc}")
                    continue

                start_time = time.time()
                while time.time() - start_time < per_backend_timeout:
                    if self._stop_event.is_set():
                        self._terminate_process()
                        self.last_error = "模型启动已取消"
                        return False
                    if self.health_check():
                        self.last_error = ""
                        return True
                    if self.process and self.process.poll() is not None:
                        errors.append(
                            f"{backend}: 服务异常退出，代码 {self.process.returncode}"
                        )
                        break
                    time.sleep(0.5)
                else:
                    errors.append(f"{backend}: 启动超时")

                self._terminate_process()

            self.selected_backend = "failed"
            self.last_error = "; ".join(errors) or "没有可用的llama-server运行库"
            return False

    def _backend_candidates(self):
        """Return GPU-first runtimes, with the CPU runtime as a fallback."""
        candidates = []
        if self.config.prefer_gpu and self._cuda_runtime_available():
            candidates.append(("cuda", self.cuda_server_path))
        candidates.append(("cpu", self.cpu_server_path))
        return candidates

    def _cuda_runtime_available(self) -> bool:
        if not self.cuda_server_path or not self.cuda_server_path.exists():
            return False
        try:
            completed = subprocess.run(
                [str(self.cuda_server_path), "--list-devices"],
                cwd=str(self.cuda_server_path.parent),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
                timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            return completed.returncode == 0 and "CUDA" in completed.stdout
        except (OSError, subprocess.SubprocessError):
            return False

    def _start_server(self, server_path: Path, backend: str):
        """启动llama-server进程"""
        if self.process and self.process.poll() is None:
            return  # 已在运行

        if not server_path.exists():
            raise RuntimeError(f"llama-server not found: {server_path}")

        if not self.config.model_path.exists():
            raise RuntimeError(f"model not found: {self.config.model_path}")

        cmd = [
            str(server_path),
            "--model", str(self.config.model_path),
            "--host", self.config.host,
            "--port", str(self.config.port),
            "--ctx-size", str(self.config.context_size),
            "--threads", str(self.config.threads),
            "-np", "1",  # 并行处理数量
            "--no-webui",
            "--alias", self.config.name,
            "--batch-size", str(self.config.batch_size),
            "--ubatch-size", str(self.config.ubatch_size),
            "--flash-attn", self.config.flash_attention,
            "--reasoning", "on" if self.config.reasoning else "off",
            "--reasoning-budget", "-1" if self.config.reasoning else "0",
        ]

        if backend == "cuda":
            cmd.extend(["--gpu-layers", str(self.config.gpu_layers)])

        if self.config.cache_prompt:
            cmd.append("--cache-prompt")

        if self.config.cache_reuse > 0:
            cmd.extend(["--cache-reuse", str(self.config.cache_reuse)])

        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._close_log()
        self._log_handle = open(self.log_path, "a", encoding="utf-8")
        self._log_handle.write(
            f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] starting backend={backend}\n"
        )
        self._log_handle.flush()

        popen_kwargs = {
            "cwd": str(server_path.parent),
            "stdout": self._log_handle,
            "stderr": subprocess.STDOUT,
        }

        # Windows特定：隐藏控制台窗口
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            self.process = subprocess.Popen(
                cmd,
                startupinfo=startupinfo,
                creationflags=subprocess.CREATE_NO_WINDOW,
                **popen_kwargs,
            )
        else:
            self.process = subprocess.Popen(
                cmd,
                **popen_kwargs,
            )
        self.server_path = server_path
        self.selected_backend = backend

    def _close_log(self):
        if self._log_handle:
            try:
                self._log_handle.close()
            except OSError:
                pass
            self._log_handle = None

    def _terminate_process(self):
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=2)
            except OSError:
                try:
                    self.process.kill()
                except OSError:
                    pass
        self.process = None
        self._health_checked = False
        self._close_log()

    def stop_server(self):
        """停止服务器进程"""
        self._stop_event.set()
        self._terminate_process()
        self.selected_backend = "stopped"

    def complete(self, prompt: str, max_tokens: Optional[int] = None,
                 temperature: Optional[float] = None) -> Dict[str, Any]:
        """
        发送补全请求

        Args:
            prompt: 提示文本
            max_tokens: 最大token数（覆盖配置）
            temperature: 温度参数（覆盖配置）

        Returns:
            包含text和elapsed_seconds的字典
        """
        if not self.ensure_ready():
            raise RuntimeError("llama-server is not ready")

        max_tokens = self.config.max_tokens if max_tokens is None else max_tokens
        temperature = self.config.temperature if temperature is None else temperature

        payload = {
            "model": self.config.name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "top_p": 0.8,
            "max_tokens": max_tokens,
            "stream": False
        }

        start_time = time.time()
        try:
            response = requests.post(
                f"http://{self.config.host}:{self.config.port}/v1/chat/completions",
                json=payload,
                timeout=120
            )
            if not response.ok:
                detail = response.text.strip().replace("\n", " ")[:800]
                raise RuntimeError(
                    f"模型服务返回HTTP {response.status_code}：{detail or '未提供错误详情'}"
                )
            result = response.json()
            text = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            elapsed = time.time() - start_time
            return {"text": text, "elapsed_seconds": elapsed}
        except Exception as e:
            raise RuntimeError(f"Model request failed: {e}")

    def __del__(self):
        """析构时自动停止服务器"""
        # 在析构时停止服务器，确保子进程被清理
        try:
            self.stop_server()
        except Exception:
            # 析构函数中不应该抛出异常
            pass


class SemanticMatcher:
    """
    语义匹配器 - 使用本地Qwen模型进行语义理解
    整合AccountingDemo的匹配逻辑和本地模型推理
    """

    def __init__(self, runner: LlamaServerRunner, vocab_library: Path,
                 semantic_categories: Path, account_catalog: Optional[Path] = None):
        self.runner = runner
        self.account_catalog = load_account_catalog(account_catalog)
        self.vocab_library = enrich_vocab_records(
            self._load_json(vocab_library), self.account_catalog
        )
        self.semantic_categories = self._load_json(semantic_categories)

    def _load_json(self, path: Path) -> Any:
        """加载JSON文件"""
        if path.exists():
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict) and "科目" in data:
                return data["科目"]
            return data
        return {}

    def _find_vocab_record(self, subject_label: str) -> Optional[Dict[str, Any]]:
        """Resolve either a bare subject name or a validated 'code name' label."""
        label = str(subject_label).strip()
        direct = next(
            (record for record in self.vocab_library if record.get("subject") == label),
            None,
        )
        if direct:
            return direct

        code, separator, name = label.partition(" ")
        if not separator or not code.isdigit():
            return None
        return next(
            (
                record
                for record in self.vocab_library
                if str(record.get("subject_code") or record.get("code", "")).strip() == code
            ),
            None,
        )

    @staticmethod
    def _is_platform_brand_only(text: str) -> bool:
        """Reject a platform brand without an accounting business activity."""
        normalized = re.sub(
            r"[\s，。！？、,.!?:：;；（）()\[\]【】]+", "", str(text or "")
        ).casefold()
        if normalized.endswith("平台"):
            normalized = normalized[:-2]
        return normalized in PLATFORM_BRAND_ONLY_TERMS

    def _matched_platform_review_category(self, text: str) -> Optional[str]:
        """Return an explicit high-risk platform category before normal rules."""
        text_lower = str(text or "").casefold()
        for category, info in self.semantic_categories.get("categories", {}).items():
            if not info.get("manual_review"):
                continue
            if any(
                str(tag).strip().casefold() in text_lower
                for tag in info.get("tags", [])
                if str(tag).strip()
            ):
                return str(category)
        return None

    def match_exact(self, text: str) -> list:
        """精确匹配（三层词库）"""
        # 从AccountingDemo移植的精确匹配逻辑
        results = []
        text_lower = text.lower().strip()

        for record in self.vocab_library:
            score = 0
            match_type = None
            matched_word = ""

            # Layer1: 精确匹配input字段
            if record.get("input") and record["input"].lower() in text_lower:
                input_word = record["input"].lower().strip()
                score = (2000 if input_word == text_lower else 800) + len(input_word)
                match_type = "exact"
                matched_word = record["input"]

            # Layer2: 同义词匹配
            if score == 0 and record.get("layer2"):
                layer2_words = record["layer2"].lower().split("、")
                for word in sorted(layer2_words, key=len, reverse=True):
                    if word.strip() in text_lower:
                        cleaned_word = word.strip()
                        score = (
                            1500 if cleaned_word == text_lower else 500
                        ) + len(cleaned_word)
                        match_type = "layer2"
                        matched_word = word
                        break

            # Layer3: 口语词匹配
            if score == 0 and record.get("layer3"):
                layer3_text = record["layer3"].split("||")[0]  # 只取||前面的词
                layer3_words = [w.strip().lower() for w in layer3_text.split("、")
                               if w.strip()]
                for word in layer3_words:
                    if len(word) >= 2 and word in text_lower:
                        word_score = 100 + len(word) * 10
                        if word_score > score:
                            score = word_score
                            match_type = "layer3"
                            matched_word = word

            if score > 0:
                enriched_record = dict(record)
                enriched_record.update({
                    "match_type": match_type,
                    "matched_word": matched_word,
                    "score": score,
                })
                results.append({
                    "record": enriched_record,
                    "match_type": match_type,
                    "matched_word": matched_word,
                    "score": score
                })

        # 按分数降序排序
        results.sort(key=lambda x: x["score"], reverse=True)
        return results

    def match_semantic_bridge(self, text: str) -> list:
        """语义桥接匹配（纯查表）"""
        # 从AccountingDemo移植的语义桥接逻辑
        results = []
        text_lower = text.lower().strip()

        if not self.semantic_categories.get("tag_index"):
            return results

        tag_index = self.semantic_categories["tag_index"]
        subject_scores = {}

        for tag, entries in tag_index.items():
            if len(tag) < 2:
                continue
            if tag in text_lower:
                for entry in entries:
                    cat = entry['category']
                    for subj in entry['subjects']:
                        if subj not in subject_scores:
                            subject_scores[subj] = {
                                'categories': set(),
                                'tags': [],
                                'score': 0
                            }
                        subject_scores[subj]['categories'].add(cat)
                        subject_scores[subj]['tags'].append(tag)
                        subject_scores[subj]['score'] += len(tag) * 10

        for subj, info in subject_scores.items():
            record = self._find_vocab_record(subj)
            if record:
                results.append({
                    "record": record,
                    "match_type": "semantic_bridge",
                    "matched_word": "、".join(info['tags'][:5]),
                    "score": info['score'],
                    "categories": list(info['categories'])
                })

        results.sort(key=lambda x: x['score'], reverse=True)
        return results[:3]

    def match_rules(self, text: str) -> list:
        """Return only high-confidence explicit and synonym vocabulary rules."""
        if self._is_platform_brand_only(text) or self._matched_platform_review_category(text):
            return []
        return [
            result
            for result in self.match_exact(text)
            if result.get("match_type") in {"exact", "layer2"}
        ]

    @staticmethod
    def _layer3_terms(record: Dict[str, Any]) -> list:
        """Return editable colloquial terms, excluding conflict metadata."""
        editable = str(record.get("layer3", "")).split("||", 1)[0]
        terms = []
        seen = set()
        for term in editable.replace("，", "、").replace(",", "、").split("、"):
            cleaned = term.strip()
            key = cleaned.casefold()
            if cleaned and key not in seen:
                terms.append(cleaned)
                seen.add(key)
        return terms

    def _category_layer3_hints(self, info: Dict[str, Any], text: str,
                               limit: int = 2) -> list:
        """Attach only Layer 3 phrases actually present in the current input."""
        terms = []
        seen = set()
        for subject in info.get("subjects", []):
            record = self._find_vocab_record(subject)
            if not record:
                continue
            for term in self._layer3_terms(record):
                key = term.casefold()
                if key not in seen:
                    terms.append(term)
                    seen.add(key)

        text_lower = text.casefold()
        relevant = [term for term in terms if term.casefold() in text_lower]
        return relevant[:limit]

    def match_with_ai(self, text: str, max_results: int = 5) -> list:
        """
        使用AI模型进行语义匹配
        当词库无法匹配时，使用本地Qwen模型理解语义
        """
        if self._is_platform_brand_only(text):
            return []

        forced_review_category = self._matched_platform_review_category(text)

        # 构建分类清单
        categories = self.semantic_categories.get("categories", {})
        cat_list = []
        category_layer3_hints = {}
        for i, (cat_name, info) in enumerate(categories.items()):
            # Every category is retained. Input-matching tags are placed first,
            # while two compact representatives keep the 4K prompt bounded.
            raw_tags = [
                str(tag).strip() for tag in info.get("tags", []) if str(tag).strip()
            ]
            matched_tags = [tag for tag in raw_tags if tag.casefold() in text.casefold()]
            remaining_tags = [tag for tag in raw_tags if tag not in matched_tags]
            prompt_tags = (matched_tags + remaining_tags)[:2]
            tags = "、".join(prompt_tags)
            layer3_hints = self._category_layer3_hints(info, text)
            category_layer3_hints[cat_name] = layer3_hints
            subjects = "、".join(info.get("subjects", []))
            review_hint = (
                "；人工拆分复核：是"
                if info.get("manual_review") else ""
            )
            hint_text = (
                f"；三级口语线索：{'、'.join(layer3_hints)}"
                if layer3_hints else ""
            )
            cat_list.append(
                f"{i+1}.【{cat_name}】词：{tags}；科目：{subjects}"
                f"{hint_text}{review_hint}"
            )

        cat_text = "\n".join(cat_list)

        prompt = f"""你是会计费用分类助手。以下是全部{len(categories)}个分类及其关键词：

{cat_text}

规则：
1. 在内部检查上述每一个分类，不要输出检查过程或思考过程
2. 返回最相关且确有可能的分类，最多{max_results}项，宁可适当多选但不要加入明显无关项
3. category和subject必须逐字使用同一分类下已有的分类名和可选科目
4. rule_basis说明摘要与该分类关键词或规则的关系，不超过24个汉字
5. reason说明为什么推荐这个具体科目，不超过16个汉字
6. 优先推荐体现“买了什么、卖了什么、发生了什么成本费用”的业务内容科目
7. 付款方式、收款账户、老板或股东垫付通常是对方科目线索，不得覆盖同时出现的商品、服务、收入或费用内容；只有原句本身就是借款、还款、出资或纯往来款时，才把资产、负债、权益科目作为首选
8. 出现平台净额结算、代扣税费或退款同时扣费时，必须选择标有“人工拆分复核：是”的分类，不得把净到账额直接判断为收入
9. 每项四个字段都必须有值，只输出合法JSON数组，不要Markdown、解释或其他文字
格式：[{{"category": "分类名", "subject": "可选科目", "rule_basis": "规则关联依据", "reason": "推荐该科目的理由"}}]
用户业务原话：「{text}」
JSON："""

        try:
            result = self.runner.complete(prompt)
            response_text = result["text"].strip()

            # 解析JSON响应
            start = response_text.find("[")
            end = response_text.rfind("]") + 1
            if start >= 0 and end > start:
                ai_cats = json.loads(response_text[start:end])
            else:
                return []
        except Exception as e:
            print(f"AI matching failed: {e}")
            return []

        if forced_review_category:
            forced_info = categories[forced_review_category]
            ai_cats = [{
                "category": forced_review_category,
                "subject": next(iter(forced_info.get("subjects", [])), ""),
                "rule_basis": "摘要命中平台多科目强制拆分规则",
                "reason": "结算项目必须逐项拆分",
            }]

        # 后验校验并组装结果
        results = []
        seen = set()

        for ac in ai_cats:
            ai_cat = ac.get("category", "").strip()
            ai_subject = ac.get("subject", "").strip()
            model_rule_basis = ac.get("rule_basis", "").strip()
            reason = ac.get("reason", "").strip()

            # 精确或模糊匹配
            cat_info = categories.get(ai_cat)
            if not cat_info:
                for cname, cinfo in categories.items():
                    if ai_cat in cname or cname in ai_cat:
                        cat_info = cinfo
                        ai_cat = cname
                        break

            if not cat_info:
                continue

            tags = [str(tag).strip() for tag in cat_info.get("tags", []) if str(tag).strip()]
            matched_tags = [tag for tag in tags if tag.lower() in text.lower()]
            layer3_hints = category_layer3_hints.get(ai_cat, [])
            matched_layer3 = [
                term for term in layer3_hints if term.casefold() in text.casefold()
            ]
            allowed_subjects = [
                str(subject).strip()
                for subject in cat_info.get("subjects", [])
                if str(subject).strip()
            ]
            manual_review_required = bool(cat_info.get("manual_review"))
            review_message = str(cat_info.get("review_message", "")).strip()
            if manual_review_required:
                selected_subjects = allowed_subjects
            elif ai_subject in allowed_subjects:
                selected_subjects = [ai_subject]
            else:
                selected_subjects = allowed_subjects[:1]

            # 查找分类下的科目
            for subj_name in selected_subjects:
                result_key = (ai_cat, subj_name)
                if result_key in seen:
                    continue
                seen.add(result_key)
                record = self._find_vocab_record(subj_name)
                if record:
                    vocabulary_rule = (
                        record.get("logic")
                        or record.get("distinction_rule")
                        or record.get("law")
                        or ""
                    )
                    if matched_tags:
                        category_basis = f"摘要命中分类词：{'、'.join(matched_tags[:5])}"
                    else:
                        category_basis = f"分类关键词范围：{'、'.join(tags[:6])}"
                    rule_parts = [category_basis]
                    if matched_layer3:
                        rule_parts.append(
                            f"三级口语词：{'、'.join(matched_layer3[:4])}"
                        )
                    if model_rule_basis:
                        rule_parts.append(f"模型对照：{model_rule_basis}")
                    if vocabulary_rule:
                        rule_parts.append(f"词库规则：{vocabulary_rule[:100]}")
                    if manual_review_required and review_message:
                        rule_parts.append(f"拆分要求：{review_message}")
                    rule_basis = "；".join(rule_parts)
                    recommendation_reason = reason or f"摘要语义符合「{ai_cat}」分类"
                    enriched_record = dict(record)
                    enriched_record.update({
                        "match_type": "ai_suggested",
                        "matched_word": ai_cat,
                        "score": 100,
                        "rule_category": ai_cat,
                        "rule_basis": rule_basis,
                        "model_rule_basis": model_rule_basis,
                        "recommendation_reason": recommendation_reason,
                        "manual_review_required": manual_review_required,
                        "review_message": review_message,
                    })
                    results.append({
                        "record": enriched_record,
                        "match_type": "ai_suggested",
                        "matched_word": ai_cat,
                        "score": 100,  # AI建议的基础分数
                        "category": ai_cat,
                        "rule_category": ai_cat,
                        "rule_basis": rule_basis,
                        "model_rule_basis": model_rule_basis,
                        "matched_rule_terms": matched_tags[:5],
                        "matched_layer3_terms": matched_layer3[:4],
                        "recommendation_reason": recommendation_reason,
                        "reason": recommendation_reason,
                        "manual_review_required": manual_review_required,
                        "review_message": review_message,
                    })

        return results[:max_results]

    def match(self, text: str) -> list:
        """
        综合匹配策略
        1. 精确词库匹配
        2. 规则未命中时，由AI遍历全部语义分类
        """
        # Step 1: 精确匹配
        exact_results = self.match_rules(text)
        if exact_results:
            return exact_results

        # Step 2: 不做规则候选预筛，直接让模型判断全部分类
        ai_results = self.match_with_ai(text)
        return ai_results
