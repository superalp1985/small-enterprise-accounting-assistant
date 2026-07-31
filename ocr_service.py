#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RapidOCR subprocess integration for invoice images and PDFs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


class OcrRecognitionError(RuntimeError):
    """Raised when the OCR adapter cannot produce a usable draft."""


class OcrService:
    def __init__(self, config):
        self.project_root = Path(config.project_root)
        self.profile_key = config.profile_key
        self.settings = dict(config.ocr_config)
        self.enabled = bool(self.settings.get("enabled", True))
        self.threshold = float(self.settings.get("lowConfidenceThreshold", 0.85))
        self.adapter_path = Path(
            getattr(
                config,
                "ocr_adapter_path",
                self.project_root / self.settings.get(
                    "adapterPath", "runtime/ocr/bin/rapidocr_adapter.py"
                ),
            )
        )
        configured_output = Path(self.settings.get("outputDir", "out/ocr"))
        output_root = (
            configured_output
            if configured_output.is_absolute()
            else Path(getattr(config, "data_dir", self.project_root / "data")) / "ocr"
        )
        self.output_dir = output_root / self.profile_key / "raw"
        self.work_dir = output_root / self.profile_key / "work"

    def recognize_invoice(self, source: Path) -> Dict[str, Any]:
        source = Path(source).resolve()
        draft = self.recognize(source)
        cells = [cell for table in draft.get("tables", []) for cell in table.get("cells", [])]
        texts = [str(cell.get("text", "")).strip() for cell in cells if str(cell.get("text", "")).strip()]
        confidences = [float(cell.get("confidence", 0.0)) for cell in cells]
        raw_text = "\n".join(texts)
        average_confidence = sum(confidences) / len(confidences) if confidences else 0.0
        fields = self._extract_invoice_fields(texts, raw_text)

        total = fields.get("total_amount")
        missing = []
        if not fields.get("invoice_no"):
            missing.append("发票号码")
        if total is None:
            missing.append("价税合计")

        description = fields.get("item_name") or self._first_business_line(texts) or source.stem
        warnings = list(draft.get("warnings") or [])
        if missing:
            warnings.append("缺少关键字段：" + "、".join(missing))
        if average_confidence < self.threshold:
            warnings.append(f"平均置信度 {average_confidence:.1%} 低于复核阈值 {self.threshold:.1%}")

        return {
            "file_name": source.name,
            "filepath": str(source),
            "status": "待处理",
            "amount": float(total or fields.get("amount") or 0.0),
            "tax_amount": float(fields.get("tax_amount") or 0.0),
            "description": description,
            "invoice_date": fields.get("invoice_date", ""),
            "invoice_code": fields.get("invoice_code", ""),
            "invoice_no": fields.get("invoice_no", ""),
            "seller": fields.get("seller", ""),
            "seller_tax_id": fields.get("seller_tax_id", ""),
            "buyer": fields.get("buyer", ""),
            "buyer_tax_id": fields.get("buyer_tax_id", ""),
            "matched_subject": "",
            "match_score": 0.0,
            "match_type": "",
            "confidence": average_confidence,
            "needs_review": bool(missing or average_confidence < self.threshold),
            "ocr_engine": draft.get("engine", "rapidocr"),
            "ocr_status": draft.get("status", ""),
            "ocr_text": raw_text,
            "ocr_tables": draft.get("tables", []),
            "warnings": warnings,
        }

    def recognize(self, source: Path) -> Dict[str, Any]:
        if not self.enabled:
            raise OcrRecognitionError("OCR功能未启用")
        if not source.exists():
            raise OcrRecognitionError(f"票据文件不存在：{source}")
        if source.suffix.lower() not in {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".pdf"}:
            raise OcrRecognitionError(f"不支持的票据格式：{source.suffix}")
        if not self.adapter_path.exists():
            raise OcrRecognitionError(f"OCR适配器不存在：{self.adapter_path}")

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        fingerprint = hashlib.sha256(
            f"{source}|{source.stat().st_size}|{source.stat().st_mtime_ns}".encode("utf-8")
        ).hexdigest()[:12]
        output = self.output_dir / f"{source.stem}.{fingerprint}.ocr.json"

        command = self._python_command() + [
            str(self.adapter_path),
            "--source", str(source),
            "--output", str(output),
            "--engine", "rapidocr",
            "--workdir", str(self.work_dir / fingerprint),
        ]
        startupinfo = None
        creationflags = 0
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            creationflags = subprocess.CREATE_NO_WINDOW

        try:
            completed = subprocess.run(
                command,
                cwd=self.project_root,
                env={
                    **os.environ,
                    "ACCOUNTINGDEMO_OCR_CACHE": str(self.work_dir.parent / "cache"),
                },
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=int(self.settings.get("timeoutSeconds", 300)),
                startupinfo=startupinfo,
                creationflags=creationflags,
            )
        except subprocess.TimeoutExpired as exc:
            raise OcrRecognitionError(f"OCR识别超时：{source.name}") from exc
        except OSError as exc:
            raise OcrRecognitionError(f"无法启动OCR运行时：{exc}") from exc
        if completed.returncode != 0 or not output.exists():
            detail = (completed.stderr or completed.stdout or "OCR适配器未生成结果").strip()
            raise OcrRecognitionError(detail[-1000:])

        with open(output, encoding="utf-8") as f:
            draft = json.load(f)
        if draft.get("status") != "recognized_unreviewed":
            detail = "；".join(draft.get("warnings") or []) or "未识别到文字"
            raise OcrRecognitionError(detail)
        return draft

    def _python_command(self) -> list[str]:
        embedded = self.project_root / "runtime" / "python" / "python.exe"
        if embedded.exists():
            return [str(embedded)]

        version = str(self.settings.get("pythonVersion", "3.12"))
        py_launcher = shutil.which("py")
        if py_launcher:
            return [py_launcher, f"-{version}"]
        if sys.version_info[:2] == tuple(int(part) for part in version.split(".")[:2]):
            return [sys.executable]
        raise OcrRecognitionError(f"未找到 Python {version}，无法加载内嵌 OCR 运行时")

    @classmethod
    def _extract_invoice_fields(cls, lines: Iterable[str], raw_text: str) -> Dict[str, Any]:
        normalized_lines = [
            str(line).replace("\u3000", " ").strip()
            for line in lines
            if str(line).strip()
        ]
        fields: Dict[str, Any] = {}
        patterns = {
            "invoice_code": r"发票代码\s*[：:]\s*([0-9A-Za-z]{8,20})",
            "invoice_no": r"发票号码\s*[：:]\s*([0-9A-Za-z]{8,20})",
            "invoice_date": r"开票日期\s*[：:]\s*(20\d{2}[年./-]\d{1,2}[月./-]\d{1,2}日?)",
            "buyer": r"购买方名称\s*[：:]\s*([^\n]+)",
            "buyer_tax_id": r"购买方(?:税号|纳税人识别号)\s*[：:]\s*([0-9A-Z]{15,20})",
            "seller": r"销售方名称\s*[：:]\s*([^\n]+)",
            "seller_tax_id": r"销售方(?:税号|纳税人识别号)\s*[：:]\s*([0-9A-Z]{15,20})",
            "item_name": r"项目名称\s*[：:]\s*([^\n]+)",
        }
        for key, pattern in patterns.items():
            match = re.search(pattern, raw_text, re.IGNORECASE)
            if match:
                fields[key] = match.group(1).strip()

        if not fields.get("invoice_no"):
            match = re.search(r"(?<!\d)(\d{20})(?!\d)", raw_text)
            if match:
                fields["invoice_no"] = match.group(1)
        if not fields.get("invoice_date"):
            match = re.search(r"(20\d{2}[年./-]\d{1,2}[月./-]\d{1,2}日?)", raw_text)
            if match:
                fields["invoice_date"] = match.group(1)

        party_fields = cls._extract_party_fields(normalized_lines)
        for key, value in party_fields.items():
            fields.setdefault(key, value)
        if not fields.get("item_name"):
            fields["item_name"] = cls._extract_item_name(normalized_lines)

        fields["invoice_date"] = cls._normalize_date(fields.get("invoice_date", ""))
        fields["amount"] = cls._extract_amount(raw_text, ("不含税金额", "金额"))
        fields["tax_amount"] = cls._extract_amount(raw_text, ("税额",))
        fields["total_amount"] = cls._extract_amount(raw_text, ("价税合计", "小写合计", "合计"))
        reconciled = cls._extract_reconciled_currency_amounts(raw_text)
        if reconciled:
            fields["amount"] = fields.get("amount") or reconciled[0]
            fields["tax_amount"] = fields.get("tax_amount") or reconciled[1]
            fields["total_amount"] = fields.get("total_amount") or reconciled[2]
        return fields

    @classmethod
    def _extract_party_fields(cls, lines: list[str]) -> Dict[str, str]:
        fields: Dict[str, str] = {}
        compact_lines = [re.sub(r"\s+", "", line) for line in lines]

        for index, compact in enumerate(compact_lines):
            for marker, key in (("购买方信息", "buyer"), ("销售方信息", "seller")):
                if marker not in compact or fields.get(key):
                    continue
                for candidate in lines[index + 1:index + 6]:
                    match = re.search(r"名称\s*[：:]\s*(.+)", candidate)
                    if match and match.group(1).strip() not in {"名称", "名称："}:
                        fields[key] = match.group(1).strip()
                        break

        tax_ids: list[tuple[int, str]] = []
        for index, line in enumerate(lines):
            for match in re.finditer(
                r"(?<![0-9A-Z])((?:[0-9A-Z]\s*){18})(?![0-9A-Z])",
                line.upper(),
            ):
                value = re.sub(r"\s+", "", match.group(1))
                if value not in {item[1] for item in tax_ids}:
                    tax_ids.append((index, value))

        if tax_ids:
            fields.setdefault("buyer_tax_id", tax_ids[0][1])
        if len(tax_ids) >= 2:
            fields.setdefault("seller_tax_id", tax_ids[1][1])

        for party_index, key in ((0, "buyer"), (1, "seller")):
            if fields.get(key) or len(tax_ids) <= party_index:
                continue
            identifier_index = tax_ids[party_index][0]
            for candidate in reversed(lines[max(0, identifier_index - 3):identifier_index]):
                compact = re.sub(r"\s+", "", candidate)
                if cls._is_party_name_candidate(compact):
                    fields[key] = re.sub(r"^名称\s*[：:]\s*", "", candidate).strip()
                    break
        return fields

    @staticmethod
    def _is_party_name_candidate(value: str) -> bool:
        ignored = (
            "统一社会信用代码", "纳税人识别号", "购买方", "销售方", "发票",
            "开票日期", "项目名称", "规格型号", "合计",
        )
        return (
            len(value) >= 3
            and not any(label in value for label in ignored)
            and not re.fullmatch(r"[0-9A-Z]+", value, re.IGNORECASE)
            and not re.search(r"[¥￥]", value)
        )

    @staticmethod
    def _extract_item_name(lines: list[str]) -> str:
        units = {"件", "个", "台", "套", "箱", "盒", "包", "瓶", "次", "项"}
        for index, line in enumerate(lines):
            compact = re.sub(r"\s+", "", line)
            if not compact.startswith("*") or len(compact) < 4:
                continue
            parts = [compact]
            for candidate in lines[index + 1:index + 5]:
                candidate = re.sub(r"\s+", "", candidate)
                if (
                    not candidate
                    or candidate.startswith("*")
                    or candidate in units
                    or re.search(r"[¥￥]|\d+(?:\.\d+)?%", candidate)
                    or re.fullmatch(r"[-+]?\d[\d,.]*", candidate)
                ):
                    break
                parts.append(candidate)
            return "".join(parts)[:240]
        return ""

    @staticmethod
    def _extract_reconciled_currency_amounts(text: str) -> Optional[tuple[float, float, float]]:
        values = [
            float(match.group(1).replace(",", ""))
            for match in re.finditer(r"[¥￥]\s*([-+]?\d[\d,]*\.\d{2})", text)
        ]
        for first in range(len(values) - 2):
            for second in range(first + 1, len(values) - 1):
                for third in range(second + 1, len(values)):
                    amount, tax_amount, total_amount = (
                        values[first], values[second], values[third]
                    )
                    if abs(amount + tax_amount - total_amount) <= 0.011:
                        return amount, tax_amount, total_amount
        return None

    @staticmethod
    def _extract_amount(text: str, labels: Iterable[str]) -> Optional[float]:
        for label in labels:
            match = re.search(
                rf"{re.escape(label)}(?:\s*\([^\n)]*\))?\s*[：:]?\s*[¥￥]?\s*([0-9][0-9,]*\.\d{{2}})",
                text,
            )
            if match:
                return float(match.group(1).replace(",", ""))
        return None

    @staticmethod
    def _normalize_date(value: str) -> str:
        if not value:
            return ""
        match = re.match(r"(20\d{2})[年./-](\d{1,2})[月./-](\d{1,2})", value)
        if not match:
            return value
        year, month, day = match.groups()
        return f"{year}-{int(month):02d}-{int(day):02d}"

    @staticmethod
    def _first_business_line(lines: Iterable[str]) -> str:
        ignored = ("发票", "购买方", "销售方", "税号", "纳税人识别号", "开票日期", "合计", "税额", "金额")
        for line in lines:
            if len(line) >= 4 and not any(label in line for label in ignored):
                return line[:120]
        return ""
