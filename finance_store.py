#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import uuid
import zipfile
from calendar import monthrange
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from account_catalog import (
    account_index,
    enabled_account_codes,
    load_account_catalog,
    template_labels,
)

from legal_notice import (
    POLICY_EFFECTIVE_THROUGH,
    POLICY_PRESET_REVIEW_DATE,
    POLICY_SOURCES,
)
from tax_engine import (
    calculate_cit,
    calculate_small_scale_vat,
    cumulative_iit,
    price_tax_split,
    resolve_tax_period,
    stamp_duty,
    supported_scope,
)


CASH_FLOW_CATEGORIES = {
    "operating_sales_receipt": "销售商品、提供劳务收到的现金",
    "operating_other_receipt": "收到其他与经营活动有关的现金",
    "operating_purchase_payment": "购买原材料、商品、接受劳务支付的现金",
    "operating_payroll_payment": "支付的职工薪酬",
    "operating_tax_payment": "支付的税费",
    "operating_other_payment": "支付其他与经营活动有关的现金",
    "investing_recovery_receipt": "收回投资收到的现金",
    "investing_income_receipt": "取得投资收益收到的现金",
    "investing_disposal_receipt": "处置非流动资产收回的现金净额",
    "investing_investment_payment": "投资支付的现金",
    "investing_asset_payment": "购建非流动资产支付的现金",
    "financing_borrowing_receipt": "取得借款收到的现金",
    "financing_capital_receipt": "吸收投资者投资收到的现金",
    "financing_principal_payment": "偿还借款本金支付的现金",
    "financing_interest_payment": "偿还借款利息支付的现金",
    "financing_distribution_payment": "分配利润支付的现金",
}

CASH_FLOW_RECEIPT_CATEGORIES = {
    key for key in CASH_FLOW_CATEGORIES if key.endswith("_receipt")
}
CASH_FLOW_PAYMENT_CATEGORIES = {
    key for key in CASH_FLOW_CATEGORIES if key.endswith("_payment")
}

SMALL_ENTERPRISE_STANDARD = "小企业会计准则"
ENTERPRISE_STANDARD = "企业会计准则"

STANDARD_CODE_SETS = {
    SMALL_ENTERPRISE_STANDARD: {
        "operating_revenue": {"5001", "5051"},
        "other_income": {"5111", "5301"},
        "profit_expense": {
            "5401", "5402", "5403", "5601", "5602", "5603", "5711", "5801",
        },
        "profit_subject": "3103 本年利润",
        "management_expense_subject": "5602 管理费用",
        "production_cost": {"4001"},
    },
    ENTERPRISE_STANDARD: {
        "operating_revenue": {"6001", "6051"},
        "other_income": {"6111", "6301"},
        "profit_expense": {
            "6401", "6402", "6403", "6601", "6602", "6603", "6701", "6711",
            "6801", "6901",
        },
        "profit_subject": "4103 本年利润",
        "management_expense_subject": "6602 管理费用",
        "production_cost": {"5001"},
    },
}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _today() -> str:
    return date.today().isoformat()


def _money(value: Any) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def subject_code(subject: str) -> str:
    head = str(subject or "").strip().split(" ", 1)[0]
    return head if head.isdigit() else ""


def _month_index(period: str) -> int:
    try:
        year, month = str(period).split("-", 1)
        return int(year) * 12 + int(month) - 1
    except (TypeError, ValueError):
        raise ValueError("期间应为 YYYY-MM 格式")


def _period_end(period: str) -> str:
    year, month = map(int, str(period).split("-", 1))
    return f"{year:04d}-{month:02d}-{monthrange(year, month)[1]:02d}"


class FinanceDataStore:
    """Profile-isolated persistent ledger, invoice, tax, and settings store."""

    DATA_FILES = (
        "settings.json",
        "ledger.json",
        "invoices.json",
        "drafts.json",
        "tax_periods.json",
        "tax_adjustments.json",
        "stamp_duty.json",
        "opening_balances.json",
        "bank_transactions.json",
        "payroll.json",
        "fixed_assets.json",
        "operation_log.json",
    )
    DATABASE_FILE = "accounting.db"
    UNPOSTED_STATUS = "已反过账"

    def __init__(self, data_dir: Path, profile_key: str, profile_label: str,
                 accounting_standard: str = ENTERPRISE_STANDARD):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.profile_key = profile_key
        self.profile_label = profile_label
        self.accounting_standard = (
            accounting_standard if profile_key == "enterprise" else "政府会计准则制度"
        )
        self.backup_dir = self.data_dir / "backups"
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.database_path = self.data_dir / self.DATABASE_FILE
        self._db: Optional[sqlite3.Connection] = None
        self.recovery_notice = ""
        self.account_catalog = load_account_catalog()
        self._initialize_database()
        self._ensure_files()

    @property
    def is_enterprise(self) -> bool:
        return self.profile_key == "enterprise"

    @property
    def is_small_enterprise_standard(self) -> bool:
        return self.is_enterprise and self.accounting_standard == SMALL_ENTERPRISE_STANDARD

    @property
    def standard_code_set(self) -> Dict[str, Any]:
        return STANDARD_CODE_SETS.get(
            self.accounting_standard,
            STANDARD_CODE_SETS[ENTERPRISE_STANDARD],
        )

    @property
    def management_expense_subject(self) -> str:
        return str(self.standard_code_set["management_expense_subject"])

    @property
    def profit_subject(self) -> str:
        return str(self.standard_code_set["profit_subject"])

    @property
    def settings_path(self) -> Path:
        return self.data_dir / "settings.json"

    @property
    def ledger_path(self) -> Path:
        return self.data_dir / "ledger.json"

    @property
    def invoices_path(self) -> Path:
        return self.data_dir / "invoices.json"

    @property
    def drafts_path(self) -> Path:
        return self.data_dir / "drafts.json"

    @property
    def tax_periods_path(self) -> Path:
        return self.data_dir / "tax_periods.json"

    @property
    def tax_adjustments_path(self) -> Path:
        return self.data_dir / "tax_adjustments.json"

    @property
    def stamp_duty_path(self) -> Path:
        return self.data_dir / "stamp_duty.json"

    @property
    def opening_balances_path(self) -> Path:
        return self.data_dir / "opening_balances.json"

    @property
    def bank_transactions_path(self) -> Path:
        return self.data_dir / "bank_transactions.json"

    @property
    def payroll_path(self) -> Path:
        return self.data_dir / "payroll.json"

    @property
    def fixed_assets_path(self) -> Path:
        return self.data_dir / "fixed_assets.json"

    def default_settings(self) -> Dict[str, Any]:
        enterprise = self.is_enterprise
        return {
            "company": {
                "name": "",
                "credit_code": "",
                "taxpayer_type": "小规模纳税人" if enterprise else "非企业单位",
                "industry": "商务服务业" if enterprise else "",
                "legal_representative": "",
                "finance_contact": "",
                "phone": "",
                "registered_address": "",
                "bank_name": "",
                "bank_account": "",
                "currency": "人民币",
            },
            "tax": {
                "vat_filing_frequency": "按季",
                "vat_rate": 0.01 if enterprise else 0.0,
                "vat_monthly_exemption_threshold": 100000.0 if enterprise else 0.0,
                "vat_quarterly_exemption_threshold": 300000.0 if enterprise else 0.0,
                "surcharge_rate": 0.06 if enterprise else 0.0,
                "cit_filing_frequency": "按季",
                "stamp_duty_filing_frequency": "按季",
                "cit_rate": 0.05 if enterprise else 0.0,
                "cit_taxable_income_limit": 3000000.0 if enterprise else 0.0,
                "cit_employee_limit": 300 if enterprise else 0,
                "cit_asset_limit": 50000000.0 if enterprise else 0.0,
                "average_employees": 1 if enterprise else 0,
                "average_assets": 0.0,
                "restricted_industry": False,
                "small_low_profit": bool(enterprise),
                "invoice_required": bool(enterprise),
                "input_vat_deductible": False,
                "default_price_tax_mode": "含税",
                "iit_monthly_deduction": 5000.0 if enterprise else 0.0,
                "stamp_duty_relief_rate": 0.5 if enterprise else 1.0,
                "policy_reference_date": POLICY_PRESET_REVIEW_DATE,
                "policy_effective_through": POLICY_EFFECTIVE_THROUGH,
            },
            "accounting": {
                "standard": self.accounting_standard,
                "account_template": "服务业" if enterprise else "完整66科目",
                "fiscal_year_start": "01-01",
                "opening_date": "",
                "default_cash_subject": "1002 银行存款" if enterprise else "1002 银行存款",
                "default_payable_subject": "2202 应付账款" if enterprise else "2302 应付账款",
                "auto_backup": True,
            },
            "export": {
                "default_dir": "exports",
                "include_policy_basis": True,
            },
            "policy_sources": POLICY_SOURCES,
            "updated_at": _now(),
        }

    def _ensure_files(self):
        if not self.settings_path.exists():
            self._write_json(self.settings_path, self.default_settings())
        defaults = {
            self.ledger_path: [],
            self.invoices_path: [],
            self.drafts_path: [],
            self.tax_periods_path: {},
            self.tax_adjustments_path: [],
            self.stamp_duty_path: [],
            self.opening_balances_path: [],
            self.bank_transactions_path: [],
            self.payroll_path: [],
            self.fixed_assets_path: [],
            self.data_dir / "operation_log.json": [],
        }
        for path, payload in defaults.items():
            if self._read_database_document(path.name) is None and not path.exists():
                self._write_json(path, payload)
            else:
                self._read_json(path, payload)

    def _connect_database(self, path: Optional[Path] = None) -> sqlite3.Connection:
        connection = sqlite3.connect(
            str(path or self.database_path), timeout=30, check_same_thread=False,
        )
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA wal_autocheckpoint=1000")
        return connection

    @staticmethod
    def _database_integrity(connection: sqlite3.Connection) -> List[str]:
        return [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]

    def _initialize_database(self):
        """Open the SQLite vault and migrate readable legacy JSON documents."""
        try:
            self._db = self._connect_database()
            integrity = self._database_integrity(self._db)
            if integrity != ["ok"]:
                raise sqlite3.DatabaseError("；".join(integrity[:5]))
        except sqlite3.DatabaseError as exc:
            self._quarantine_database(exc)
            self._db = self._connect_database()

        with self._db:
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    name TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    checksum TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            self._db.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES('schema_version', '1')"
            )

        for name in self.DATA_FILES:
            path = self.data_dir / name
            if self._read_database_document(name) is not None or not path.exists():
                continue
            try:
                with open(path, encoding="utf-8") as handle:
                    payload = json.load(handle)
            except (OSError, json.JSONDecodeError):
                continue
            self._write_database_document(name, payload)

    def _quarantine_database(self, error: Exception):
        if self._db is not None:
            try:
                self._db.close()
            except sqlite3.Error:
                pass
            self._db = None
        if self.database_path.exists():
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            quarantine = self.data_dir / f"accounting-corrupt-{timestamp}.db"
            self.database_path.replace(quarantine)
            self.recovery_notice = (
                f"检测到账套数据库异常，已隔离为 {quarantine.name}，"
                "并从可读数据镜像重建。"
            )
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{self.database_path}{suffix}")
            try:
                sidecar.unlink(missing_ok=True)
            except OSError:
                pass
        if not self.recovery_notice:
            self.recovery_notice = f"账套数据库已重新初始化：{error}"

    def _read_database_document(self, name: str) -> Optional[Any]:
        if self._db is None or name not in self.DATA_FILES:
            return None
        try:
            row = self._db.execute(
                "SELECT payload, checksum FROM documents WHERE name = ?", (name,),
            ).fetchone()
        except sqlite3.Error:
            return None
        if not row:
            return None
        payload_text, checksum = row
        actual = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
        if actual != checksum:
            raise ValueError(f"账套数据校验失败：{name}")
        return json.loads(payload_text)

    def _write_database_document(self, name: str, payload: Any):
        if self._db is None or name not in self.DATA_FILES:
            return
        payload_text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        checksum = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
        with self._db:
            self._db.execute(
                """
                INSERT INTO documents(name, payload, checksum, updated_at)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    payload=excluded.payload,
                    checksum=excluded.checksum,
                    updated_at=excluded.updated_at
                """,
                (name, payload_text, checksum, _now()),
            )

    @staticmethod
    def _write_json_mirror(path: Path, payload: Any):
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.tmp")
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        temp_path.replace(path)

    def _read_json(self, path: Path, default: Any) -> Any:
        with self._lock:
            database_payload = self._read_database_document(path.name)
            if database_payload is not None:
                try:
                    if not path.exists():
                        self._write_json_mirror(path, database_payload)
                    else:
                        with open(path, encoding="utf-8") as handle:
                            json.load(handle)
                except (OSError, json.JSONDecodeError):
                    self._write_json_mirror(path, database_payload)
                    self.recovery_notice = (
                        f"已从SQLite账套修复损坏的数据镜像：{path.name}"
                    )
                return database_payload
            if not path.exists():
                return default
            try:
                with open(path, encoding="utf-8") as handle:
                    payload = json.load(handle)
                self._write_database_document(path.name, payload)
                return payload
            except (OSError, json.JSONDecodeError):
                return default

    def _write_json(self, path: Path, payload: Any):
        with self._lock:
            self._write_database_document(path.name, payload)
            self._write_json_mirror(path, payload)

    def get_settings(self) -> Dict[str, Any]:
        stored = self._read_json(self.settings_path, {})
        merged = self.default_settings()
        for section, value in stored.items():
            if isinstance(value, dict) and isinstance(merged.get(section), dict):
                merged[section].update(value)
            else:
                merged[section] = value
        export = merged.setdefault("export", {})
        export["default_dir"] = self._portable_export_dir_value(
            export.get("default_dir")
        )
        # Policy sources are maintained with the release, while tax parameters
        # remain editable per account set.
        merged["policy_sources"] = [dict(source) for source in POLICY_SOURCES]
        return merged

    def enabled_account_codes(self) -> List[str]:
        template = str(
            self.get_settings().get("accounting", {}).get("account_template", "服务业")
        )
        return enabled_account_codes(template, self.account_catalog)

    def enabled_accounts(self) -> List[Dict[str, Any]]:
        enabled = set(self.enabled_account_codes())
        return [
            dict(account) for account in self.account_catalog.get("accounts", [])
            if str(account.get("code", "")) in enabled
        ]

    def all_accounts(self) -> List[Dict[str, Any]]:
        return [dict(account) for account in self.account_catalog.get("accounts", [])]

    def save_settings(self, settings: Dict[str, Any]):
        settings = dict(settings)
        accounting = dict(settings.get("accounting", {}))
        accounting["standard"] = self.accounting_standard
        opening_date = str(accounting.get("opening_date", "")).strip()
        if opening_date:
            try:
                opening_period = datetime.strptime(opening_date, "%Y-%m-%d").strftime("%Y-%m")
            except ValueError:
                raise ValueError("开账日期应为 YYYY-MM-DD 格式，例如 2026-01-01")
            existing_periods = {
                str(row.get("period", ""))
                for row in self.list_vouchers(include_unposted=True)
                if len(str(row.get("period", ""))) == 7
            }
            existing_periods.update(
                str(row.get("invoice_date", ""))[:7]
                for row in self.list_invoices()
                if len(str(row.get("invoice_date", ""))) >= 7
            )
            existing_periods.update(
                str(row.get("period", ""))
                for row in self.list_opening_balances()
                if len(str(row.get("period", ""))) == 7
            )
            earlier = sorted(
                period for period in existing_periods
                if _month_index(period) < _month_index(opening_period)
            )
            if earlier:
                raise ValueError(
                    f"开账日期不能晚于现有最早数据期间 {earlier[0]}，请先核对历史账套"
                )
        accounting["opening_date"] = opening_date
        account_template = str(accounting.get("account_template", "服务业")).strip()
        if account_template not in template_labels(self.account_catalog):
            raise ValueError("科目启用模板无效，请选择服务业、商贸业等内置模板")
        accounting["account_template"] = account_template
        settings["accounting"] = accounting
        tax = dict(settings.get("tax", {}))
        for key in ("vat_rate", "surcharge_rate", "cit_rate"):
            try:
                value = float(tax.get(key, 0) or 0)
            except (TypeError, ValueError):
                raise ValueError(f"税务参数 {key} 必须是数字")
            if not 0 <= value <= 1:
                raise ValueError(f"税务参数 {key} 应在0%至100%之间")
            tax[key] = value
        for key in (
            "vat_monthly_exemption_threshold", "vat_quarterly_exemption_threshold",
            "cit_taxable_income_limit", "cit_asset_limit", "average_assets",
            "iit_monthly_deduction",
        ):
            try:
                value = float(tax.get(key, 0) or 0)
            except (TypeError, ValueError):
                raise ValueError(f"税务参数 {key} 必须是数字")
            if value < 0:
                raise ValueError(f"税务参数 {key} 不能小于0")
            tax[key] = value
        try:
            employee_limit = int(tax.get("cit_employee_limit", 0) or 0)
        except (TypeError, ValueError):
            raise ValueError("小型微利企业从业人数上限必须是整数")
        if employee_limit < 0:
            raise ValueError("小型微利企业从业人数上限不能小于0")
        tax["cit_employee_limit"] = employee_limit
        try:
            average_employees = int(tax.get("average_employees", 0) or 0)
        except (TypeError, ValueError):
            raise ValueError("全年季度平均从业人数必须是整数")
        if average_employees < 0:
            raise ValueError("全年季度平均从业人数不能小于0")
        tax["average_employees"] = average_employees
        try:
            stamp_relief = float(tax.get("stamp_duty_relief_rate", 0.5) or 0)
        except (TypeError, ValueError):
            raise ValueError("印花税减征系数必须是数字")
        if not 0 <= stamp_relief <= 1:
            raise ValueError("印花税减征系数应在0%至100%之间")
        tax["stamp_duty_relief_rate"] = stamp_relief
        price_tax_mode = str(tax.get("default_price_tax_mode", "含税")).strip()
        if price_tax_mode not in {"含税", "不含税"}:
            raise ValueError("默认价税口径只能选择“含税”或“不含税”")
        tax["default_price_tax_mode"] = price_tax_mode
        for key, label in (
            ("vat_filing_frequency", "增值税申报频率"),
            ("cit_filing_frequency", "企业所得税预缴频率"),
            ("stamp_duty_filing_frequency", "印花税申报频率"),
        ):
            value = str(tax.get(key, "按季")).strip()
            if value not in {"按月", "按季"}:
                raise ValueError(f"{label}只能选择按月或按季")
            tax[key] = value
        for key, label in (
            ("policy_reference_date", "政策参数复核日"),
            ("policy_effective_through", "优惠政策截止日"),
        ):
            value = str(tax.get(key, "")).strip()
            try:
                datetime.strptime(value, "%Y-%m-%d")
            except ValueError:
                raise ValueError(f"{label}应为 YYYY-MM-DD 格式")
            tax[key] = value
        settings["tax"] = tax
        export = dict(settings.get("export", {}))
        export["default_dir"] = self._portable_export_dir_value(
            export.get("default_dir")
        )
        settings["export"] = export
        settings["policy_sources"] = POLICY_SOURCES
        settings["updated_at"] = _now()
        self._write_json(self.settings_path, settings)

    def _portable_export_dir_value(self, value: Any) -> str:
        """Migrate the old generated absolute default without changing custom paths."""
        text = str(value or "exports").strip() or "exports"
        path = Path(text).expanduser()
        expected_tail = ("data", self.data_dir.name, "exports")
        generated_default = self.data_dir / "exports"
        if path.is_absolute() and (
            path == generated_default or tuple(path.parts[-3:]) == expected_tail
        ):
            return "exports"
        return text

    def resolve_export_dir(self, value: Any = None) -> Path:
        path = Path(self._portable_export_dir_value(value)).expanduser()
        return path if path.is_absolute() else self.data_dir / path

    def list_vouchers(self, source: Optional[str] = None,
                      include_unposted: bool = False) -> List[Dict[str, Any]]:
        records = self._read_json(self.ledger_path, [])
        if not include_unposted:
            records = [
                record for record in records
                if record.get("status") != self.UNPOSTED_STATUS
            ]
        if source:
            records = [record for record in records if record.get("source") == source]
        return [dict(record) for record in records]

    def _assert_period_editable(self, period: str):
        _month_index(period)
        opening_date = str(
            self.get_settings().get("accounting", {}).get("opening_date", "")
        ).strip()
        if opening_date:
            try:
                opening_period = datetime.strptime(opening_date, "%Y-%m-%d").strftime("%Y-%m")
            except ValueError:
                opening_period = ""
            if opening_period and _month_index(period) < _month_index(opening_period):
                raise ValueError(
                    f"期间 {period} 早于开账日期 {opening_date}，不能录入或修改数据"
                )
        status = self.get_tax_periods().get(str(period), {}).get("status", "")
        if status == "已归档":
            raise ValueError(f"期间 {period} 已归档，历史申报数据已锁定，不能修改")

    def next_voucher_no(self, voucher_date: Optional[str] = None) -> str:
        voucher_date = voucher_date or _today()
        period = voucher_date[:7].replace("-", "")
        prefix = f"{period}-"
        numbers = []
        for record in self.list_vouchers(include_unposted=True):
            value = str(record.get("voucher_no", ""))
            if value.startswith(prefix):
                try:
                    numbers.append(int(value.rsplit("-", 1)[1]))
                except (IndexError, ValueError):
                    pass
        return f"{prefix}{max(numbers, default=0) + 1:04d}"

    def _normalize_voucher_lines(self, lines: Iterable[Dict[str, Any]],
                                 voucher_no: str,
                                 voucher_date: str) -> List[Dict[str, Any]]:
        normalized_lines = []
        for line_no, raw in enumerate(lines, start=1):
            line = dict(raw)
            subject = str(line.get("科目", line.get("subject", ""))).strip()
            if not subject:
                raise ValueError("凭证科目不能为空")
            debit = _money(line.get("借方", line.get("debit", 0)))
            credit = _money(line.get("贷方", line.get("credit", 0)))
            amount = max(debit, credit, _money(line.get("金额", line.get("amount", 0))))
            if not debit and not credit:
                direction = line.get("方向", line.get("direction", "借方"))
                debit = amount if direction == "借方" else 0.0
                credit = amount if direction == "贷方" else 0.0
            if debit and credit:
                raise ValueError("同一凭证分录不能同时填写借方和贷方")
            if amount <= 0:
                raise ValueError("凭证金额必须大于0")
            normalized_lines.append({
                "id": line.get("id") or uuid.uuid4().hex,
                "voucher_no": voucher_no,
                "line_no": line_no,
                "date": str(line.get("date") or line.get("日期") or voucher_date),
                "period": str(line.get("period") or voucher_date[:7]),
                "description": str(line.get("摘要", line.get("description", ""))).strip(),
                "subject": subject,
                "subject_code": str(line.get("subject_code") or subject_code(subject)),
                "debit": debit,
                "credit": credit,
                "amount": amount,
                "direction": "借方" if debit else "贷方",
                "status": str(line.get("状态", line.get("status", "已记账"))),
                "source": str(line.get("source", "manual")),
                "invoice_no": str(line.get("invoice_no", "")),
                "invoice_code": str(line.get("invoice_code", "")),
                "counterparty": str(line.get("counterparty", "")),
                "tax_amount": _money(line.get("tax_amount", 0)),
                "tax_period_key": str(line.get("tax_period_key", "")),
                "attachment": str(line.get("attachment", line.get("file_path", ""))),
                "cash_flow_category": str(line.get("cash_flow_category", "")),
                "created_at": str(line.get("created_at") or _now()),
            })
        if len(normalized_lines) < 2:
            raise ValueError("一张凭证至少需要两条借贷分录")
        debit_total = sum(line["debit"] for line in normalized_lines)
        credit_total = sum(line["credit"] for line in normalized_lines)
        if abs(debit_total - credit_total) >= 0.01:
            raise ValueError(
                f"凭证借贷不平衡：借方{debit_total:.2f}，贷方{credit_total:.2f}"
            )
        return normalized_lines

    def add_voucher_lines(self, lines: Iterable[Dict[str, Any]],
                          voucher_no: Optional[str] = None,
                          voucher_date: Optional[str] = None) -> List[Dict[str, Any]]:
        voucher_date = voucher_date or _today()
        self._assert_period_editable(voucher_date[:7])
        voucher_no = voucher_no or self.next_voucher_no(voucher_date)
        ledger = self.list_vouchers(include_unposted=True)
        added = self._normalize_voucher_lines(lines, voucher_no, voucher_date)
        ledger.extend(added)
        self._write_json(self.ledger_path, ledger)
        return added

    def replace_voucher_group(self, voucher_no: str,
                              lines: Iterable[Dict[str, Any]],
                              voucher_date: Optional[str] = None) -> List[Dict[str, Any]]:
        voucher_date = voucher_date or _today()
        self._assert_period_editable(voucher_date[:7])
        for record in self.list_vouchers(include_unposted=True):
            if str(record.get("voucher_no", "")) == str(voucher_no):
                self._assert_period_editable(str(record.get("period", "")))
        replacement = self._normalize_voucher_lines(lines, voucher_no, voucher_date)
        ledger = [
            record for record in self.list_vouchers(include_unposted=True)
            if str(record.get("voucher_no", "")) != str(voucher_no)
        ]
        ledger.extend(replacement)
        ledger.sort(key=lambda row: (
            str(row.get("date", "")),
            str(row.get("voucher_no", "")),
            int(row.get("line_no", 0)),
        ))
        self._write_json(self.ledger_path, ledger)
        return replacement

    def update_voucher(self, record_id: str, updates: Dict[str, Any]) -> bool:
        ledger = self.list_vouchers(include_unposted=True)
        changed = False
        for record in ledger:
            if record.get("id") != record_id:
                continue
            self._assert_period_editable(str(record.get("period", "")))
            new_period = str(updates.get("period") or updates.get("date") or "")[:7]
            if len(new_period) == 7:
                self._assert_period_editable(new_period)
            record.update(updates)
            record["debit"] = _money(record.get("debit"))
            record["credit"] = _money(record.get("credit"))
            record["amount"] = max(record["debit"], record["credit"])
            record["subject_code"] = subject_code(record.get("subject", ""))
            record["updated_at"] = _now()
            changed = True
            break
        if changed:
            self._write_json(self.ledger_path, ledger)
        return changed

    def delete_vouchers(self, record_ids: Iterable[str]):
        ids = set(record_ids)
        current = self.list_vouchers(include_unposted=True)
        for record in current:
            if record.get("id") in ids:
                self._assert_period_editable(str(record.get("period", "")))
        ledger = [record for record in current if record.get("id") not in ids]
        self._write_json(self.ledger_path, ledger)

    def delete_voucher_numbers(self, voucher_numbers: Iterable[str]):
        numbers = {str(value) for value in voucher_numbers}
        current = self.list_vouchers(include_unposted=True)
        for record in current:
            if str(record.get("voucher_no", "")) in numbers:
                self._assert_period_editable(str(record.get("period", "")))
        ledger = [record for record in current if str(record.get("voucher_no", "")) not in numbers]
        self._write_json(self.ledger_path, ledger)

    def replace_vouchers(self, records: List[Dict[str, Any]]):
        self._write_json(self.ledger_path, records)

    def voucher_balance_issues(self, period: str) -> List[Dict[str, Any]]:
        """Return voucher-level differences for the forced close check."""
        _month_index(period)
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for record in self.list_vouchers():
            if record.get("period") == period:
                grouped.setdefault(str(record.get("voucher_no", "未编号")), []).append(record)
        issues = []
        for voucher_no, lines in sorted(grouped.items()):
            debit = round(sum(_money(line.get("debit")) for line in lines), 2)
            credit = round(sum(_money(line.get("credit")) for line in lines), 2)
            difference = round(debit - credit, 2)
            if abs(difference) >= 0.01:
                issues.append({
                    "voucher_no": voucher_no,
                    "date": min(str(line.get("date", "")) for line in lines),
                    "description": str(lines[0].get("description", "")),
                    "debit": debit,
                    "credit": credit,
                    "difference": difference,
                })
        return issues

    def unpost_voucher(self, voucher_no: str) -> Dict[str, Any]:
        """Reverse posting without deleting the voucher or its audit trail."""
        voucher_no = str(voucher_no).strip()
        ledger = self.list_vouchers(include_unposted=True)
        group = [row for row in ledger if str(row.get("voucher_no", "")) == voucher_no]
        if not group:
            raise ValueError(f"未找到凭证 {voucher_no}")
        if all(row.get("status") == self.UNPOSTED_STATUS for row in group):
            raise ValueError(f"凭证 {voucher_no} 已经反过账")
        period = str(group[0].get("period", ""))
        self._assert_period_editable(period)
        if any(row.get("source") == "period_close" for row in group):
            raise ValueError("损益结转凭证请使用“撤销结转”功能")
        if any(row.get("source") == "tax_accrual" for row in group):
            raise ValueError("税费计提凭证请在财税工作台使用“撤销税费计提凭证”")

        self.create_backup("反过账前自动备份")
        for row in ledger:
            if str(row.get("voucher_no", "")) == voucher_no:
                row["status"] = self.UNPOSTED_STATUS
                row["unposted_at"] = _now()
        self._write_json(self.ledger_path, ledger)
        self._detach_generated_voucher(voucher_no, period, group)
        return {
            "voucher_no": voucher_no,
            "period": period,
            "line_count": len(group),
            "status": self.UNPOSTED_STATUS,
        }

    def _detach_generated_voucher(self, voucher_no: str, period: str,
                                  group: List[Dict[str, Any]]):
        sources = {str(row.get("source", "")) for row in group}
        bank_rows = self.list_bank_transactions()
        bank_changed = False
        for row in bank_rows:
            if str(row.get("voucher_no", "")) == voucher_no:
                row["voucher_no"] = ""
                row["voucher_line_id"] = ""
                row["status"] = "未匹配"
                row["matched_at"] = ""
                bank_changed = True
        if bank_changed:
            self._write_json(self.bank_transactions_path, bank_rows)

        if "payroll" in sources:
            payroll = self.list_payroll()
            for row in payroll:
                if str(row.get("voucher_no", "")) == voucher_no:
                    row["voucher_no"] = ""
                    row["status"] = "未计提"
                    row["updated_at"] = _now()
            self._write_json(self.payroll_path, payroll)

        if "depreciation" in sources:
            assets = self.list_fixed_assets()
            for asset in assets:
                vouchers = dict(asset.get("depreciation_vouchers", {}))
                if vouchers.get(period) != voucher_no:
                    continue
                vouchers.pop(period, None)
                asset["depreciation_vouchers"] = vouchers
                asset["posted_periods"] = [
                    value for value in asset.get("posted_periods", []) if value != period
                ]
                asset["updated_at"] = _now()
            self._write_json(self.fixed_assets_path, assets)

    def list_invoices(self) -> List[Dict[str, Any]]:
        return [dict(record) for record in self._read_json(self.invoices_path, [])]

    def upsert_invoice(self, invoice: Dict[str, Any]) -> Dict[str, Any]:
        records = self.list_invoices()
        normalized = dict(invoice)
        normalized.setdefault("id", uuid.uuid4().hex)
        normalized.setdefault("invoice_type", "进项")
        normalized.setdefault("document_type", "正常发票")
        normalized.setdefault("invoice_form", "普通发票")
        normalized.setdefault(
            "price_tax_mode",
            self.get_settings().get("tax", {}).get("default_price_tax_mode", "含税"),
        )
        normalized.setdefault("tax_treatment", "自动判断")
        normalized.setdefault("deductible", False)
        normalized.setdefault("status", "已确认")
        normalized.setdefault("created_at", _now())
        if normalized["invoice_type"] not in {"进项", "销项"}:
            raise ValueError("发票方向只能选择“进项”或“销项”")
        if normalized["document_type"] not in {"正常发票", "红字发票", "未开票收入"}:
            raise ValueError("业务单据类型只能选择正常发票、红字发票或未开票收入")
        if normalized["price_tax_mode"] not in {"含税", "不含税"}:
            raise ValueError("价税口径只能选择“含税”或“不含税”")
        if normalized["tax_treatment"] not in {"自动判断", "不得免税", "免税项目", "不征税"}:
            raise ValueError("税务处理只能选择自动判断、不得免税、免税项目或不征税")
        if normalized["document_type"] == "未开票收入":
            normalized["invoice_type"] = "销项"
            normalized["invoice_form"] = "无票"
            normalized["invoice_code"] = ""
            normalized["invoice_no"] = ""
        invoice_period = str(normalized.get("invoice_date", ""))[:7]
        if len(invoice_period) == 7:
            self._assert_period_editable(invoice_period)
        raw_values = (
            _money(normalized.get("amount")),
            _money(normalized.get("tax_amount")),
            _money(normalized.get("total_amount")),
        )
        sign = -1 if normalized["document_type"] == "红字发票" or any(
            value < 0 for value in raw_values
        ) else 1
        split = price_tax_split(
            amount=abs(raw_values[0]),
            tax_amount=abs(raw_values[1]),
            total_amount=abs(raw_values[2]),
            rate=self.get_settings().get("tax", {}).get("vat_rate", 0.01),
            price_tax_mode=normalized["price_tax_mode"],
        )
        normalized.update({key: round(value * sign, 2) for key, value in split.items()})
        normalized["sign"] = sign
        if abs(normalized["total_amount"]) < 0.01:
            raise ValueError("发票或未开票收入的价税合计必须大于0")
        if normalized["invoice_type"] == "销项":
            normalized["deductible"] = False
        normalized["original_invoice_no"] = str(
            normalized.get("original_invoice_no", "")
        ).strip()
        if normalized["document_type"] == "红字发票" and not normalized["original_invoice_no"]:
            normalized["review_note"] = "红字发票未填写原蓝字发票号码，请人工核对"
        key = (
            str(normalized.get("invoice_code", "")),
            str(normalized.get("invoice_no", "")),
        )
        replaced = False
        for index, record in enumerate(records):
            same_id = normalized["id"] == record.get("id")
            same_key = any(key) and key == (
                    str(record.get("invoice_code", "")),
                    str(record.get("invoice_no", "")),
                )
            if same_id or same_key:
                normalized["id"] = record.get("id", normalized["id"])
                records[index] = normalized
                replaced = True
                break
        if not replaced:
            records.append(normalized)
        records.sort(key=lambda row: (
            str(row.get("invoice_date", "")), str(row.get("invoice_type", "")),
            str(row.get("invoice_no", "")),
        ))
        self._write_json(self.invoices_path, records)
        return normalized

    def delete_invoice(self, record_id: str):
        records = self.list_invoices()
        for record in records:
            if record.get("id") == record_id:
                period = str(record.get("invoice_date", ""))[:7]
                if len(period) == 7:
                    self._assert_period_editable(period)
        self._write_json(
            self.invoices_path,
            [record for record in records if record.get("id") != record_id],
        )

    def list_tax_adjustments(self, periods: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
        records = [dict(row) for row in self._read_json(self.tax_adjustments_path, [])]
        if periods is not None:
            allowed = {str(value) for value in periods}
            records = [row for row in records if str(row.get("period", "")) in allowed]
        return records

    def upsert_tax_adjustment(self, adjustment: Dict[str, Any]) -> Dict[str, Any]:
        row = dict(adjustment)
        period = str(row.get("period", "")).strip()
        _month_index(period)
        self._assert_period_editable(period)
        direction = str(row.get("direction", "调增")).strip()
        if direction not in {"调增", "调减", "弥补以前年度亏损", "已预缴所得税"}:
            raise ValueError("纳税调整方向无效")
        amount = abs(_money(row.get("amount")))
        if amount <= 0:
            raise ValueError("纳税调整金额必须大于0")
        normalized = {
            "id": row.get("id") or uuid.uuid4().hex,
            "period": period,
            "tax_type": str(row.get("tax_type", "企业所得税")).strip(),
            "category": str(row.get("category", "其他调整")).strip() or "其他调整",
            "direction": direction,
            "amount": amount,
            "basis": str(row.get("basis", "")).strip(),
            "note": str(row.get("note", "")).strip(),
            "updated_at": _now(),
        }
        records = self.list_tax_adjustments()
        for index, existing in enumerate(records):
            if existing.get("id") == normalized["id"]:
                records[index] = normalized
                break
        else:
            records.append(normalized)
        records.sort(key=lambda item: (item.get("period", ""), item.get("direction", "")))
        self._write_json(self.tax_adjustments_path, records)
        return normalized

    def delete_tax_adjustment(self, record_id: str):
        records = self.list_tax_adjustments()
        for record in records:
            if record.get("id") == record_id:
                self._assert_period_editable(str(record.get("period", "")))
        self._write_json(
            self.tax_adjustments_path,
            [record for record in records if record.get("id") != record_id],
        )

    def list_stamp_duty_items(self, periods: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
        records = [dict(row) for row in self._read_json(self.stamp_duty_path, [])]
        if periods is not None:
            allowed = {str(value) for value in periods}
            records = [row for row in records if str(row.get("period", "")) in allowed]
        return records

    def upsert_stamp_duty_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        row = dict(item)
        period = str(row.get("period", "")).strip()
        _month_index(period)
        self._assert_period_editable(period)
        taxable_amount = abs(_money(row.get("taxable_amount", row.get("amount"))))
        try:
            rate = float(row.get("rate", 0) or 0)
        except (TypeError, ValueError):
            raise ValueError("印花税税率必须是数字")
        if taxable_amount <= 0 or rate <= 0 or rate > 1:
            raise ValueError("印花税计税金额必须大于0，税率应在0%至100%之间")
        normalized = {
            "id": row.get("id") or uuid.uuid4().hex,
            "period": period,
            "item": str(row.get("item", "买卖合同")).strip() or "买卖合同",
            "taxable_amount": taxable_amount,
            "amount": taxable_amount,
            "rate": rate,
            "counterparty": str(row.get("counterparty", "")).strip(),
            "contract_no": str(row.get("contract_no", "")).strip(),
            "note": str(row.get("note", "")).strip(),
            "updated_at": _now(),
        }
        records = self.list_stamp_duty_items()
        for index, existing in enumerate(records):
            if existing.get("id") == normalized["id"]:
                records[index] = normalized
                break
        else:
            records.append(normalized)
        records.sort(key=lambda value: (value.get("period", ""), value.get("item", "")))
        self._write_json(self.stamp_duty_path, records)
        return normalized

    def delete_stamp_duty_item(self, record_id: str):
        records = self.list_stamp_duty_items()
        for record in records:
            if record.get("id") == record_id:
                self._assert_period_editable(str(record.get("period", "")))
        self._write_json(
            self.stamp_duty_path,
            [record for record in records if record.get("id") != record_id],
        )

    def list_drafts(self) -> List[Dict[str, Any]]:
        return [dict(record) for record in self._read_json(self.drafts_path, [])]

    def add_draft(self, draft: Dict[str, Any]) -> Dict[str, Any]:
        drafts = self.list_drafts()
        record = dict(draft)
        record.setdefault("id", uuid.uuid4().hex)
        record["saved_at"] = _now()
        for index, existing in enumerate(drafts):
            if existing.get("id") == record["id"]:
                drafts[index] = record
                break
        else:
            drafts.append(record)
        self._write_json(self.drafts_path, drafts)
        return record

    def delete_draft(self, draft_id: str):
        self._write_json(
            self.drafts_path,
            [draft for draft in self.list_drafts() if draft.get("id") != draft_id],
        )

    def list_opening_balances(self, period: Optional[str] = None) -> List[Dict[str, Any]]:
        records = [dict(row) for row in self._read_json(self.opening_balances_path, [])]
        if period:
            records = [row for row in records if row.get("period") == period]
        return records

    def upsert_opening_balance(self, balance: Dict[str, Any]) -> Dict[str, Any]:
        record = dict(balance)
        period = str(record.get("period", "")).strip()
        _month_index(period)
        self._assert_period_editable(period)
        subject = str(record.get("subject", "")).strip()
        if not subject:
            raise ValueError("期初余额科目不能为空")
        debit = _money(record.get("debit_balance"))
        credit = _money(record.get("credit_balance"))
        if debit and credit:
            raise ValueError("同一科目期初余额不能同时在借方和贷方")
        if not debit and not credit:
            raise ValueError("期初余额必须大于0")
        normalized = {
            "id": record.get("id") or uuid.uuid4().hex,
            "period": period,
            "subject": subject,
            "subject_code": subject_code(subject),
            "debit_balance": debit,
            "credit_balance": credit,
            "note": str(record.get("note", "")).strip(),
            "updated_at": _now(),
        }
        records = self.list_opening_balances()
        for index, existing in enumerate(records):
            same_id = existing.get("id") == normalized["id"]
            same_key = existing.get("period") == period and existing.get("subject") == subject
            if same_id or same_key:
                normalized["id"] = existing.get("id", normalized["id"])
                records[index] = normalized
                break
        else:
            records.append(normalized)
        records.sort(key=lambda row: (row.get("period", ""), row.get("subject_code", ""), row.get("subject", "")))
        self._write_json(self.opening_balances_path, records)
        return normalized

    def delete_opening_balance(self, record_id: str):
        records = self.list_opening_balances()
        for record in records:
            if record.get("id") == record_id:
                self._assert_period_editable(str(record.get("period", "")))
        self._write_json(
            self.opening_balances_path,
            [record for record in records if record.get("id") != record_id],
        )

    def opening_balance_totals(self, period: str) -> Dict[str, float]:
        records = self.list_opening_balances(period)
        debit = sum(_money(row.get("debit_balance")) for row in records)
        credit = sum(_money(row.get("credit_balance")) for row in records)
        return {
            "debit": round(debit, 2),
            "credit": round(credit, 2),
            "difference": round(debit - credit, 2),
        }

    def opening_balances_for_period(self, report_period: str) -> List[Dict[str, Any]]:
        target = _month_index(report_period)
        available = sorted({
            row.get("period", "") for row in self.list_opening_balances()
            if row.get("period") and _month_index(row["period"]) <= target
        })
        return self.list_opening_balances(available[-1]) if available else []

    def list_bank_transactions(self, period: Optional[str] = None) -> List[Dict[str, Any]]:
        records = [dict(row) for row in self._read_json(self.bank_transactions_path, [])]
        if period:
            records = [row for row in records if str(row.get("date", ""))[:7] == period]
        return records

    @staticmethod
    def _bank_fingerprint(record: Dict[str, Any]) -> str:
        raw = "|".join((
            str(record.get("date", "")), str(record.get("direction", "")),
            f"{_money(record.get('amount')):.2f}", str(record.get("summary", "")).strip(),
            str(record.get("counterparty", "")).strip(),
        ))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def import_bank_transactions(self, transactions: Iterable[Dict[str, Any]]) -> Dict[str, int]:
        records = self.list_bank_transactions()
        fingerprints = {row.get("fingerprint") for row in records}
        imported = 0
        skipped = 0
        for raw in transactions:
            row = dict(raw)
            transaction_date = str(row.get("date", "")).strip()
            try:
                datetime.strptime(transaction_date, "%Y-%m-%d")
            except ValueError:
                raise ValueError(f"银行流水日期格式错误：{transaction_date}")
            self._assert_period_editable(transaction_date[:7])
            direction = str(row.get("direction", "")).strip()
            if direction not in ("收入", "支出"):
                raise ValueError("银行流水方向必须为“收入”或“支出”")
            amount = _money(row.get("amount"))
            if amount <= 0:
                raise ValueError("银行流水金额必须大于0")
            normalized = {
                "id": row.get("id") or uuid.uuid4().hex,
                "date": transaction_date,
                "direction": direction,
                "amount": amount,
                "summary": str(row.get("summary", "")).strip(),
                "counterparty": str(row.get("counterparty", "")).strip(),
                "account": str(row.get("account", "")).strip(),
                "balance": _money(row.get("balance")),
                "status": str(row.get("status", "未匹配")),
                "voucher_no": str(row.get("voucher_no", "")),
                "voucher_line_id": str(row.get("voucher_line_id", "")),
                "cash_flow_category": str(row.get("cash_flow_category", "")),
                "source_file": str(row.get("source_file", "")),
                "imported_at": str(row.get("imported_at") or _now()),
            }
            normalized["fingerprint"] = self._bank_fingerprint(normalized)
            if normalized["fingerprint"] in fingerprints:
                skipped += 1
                continue
            records.append(normalized)
            fingerprints.add(normalized["fingerprint"])
            imported += 1
        records.sort(key=lambda row: (row.get("date", ""), row.get("imported_at", "")))
        self._write_json(self.bank_transactions_path, records)
        return {"imported": imported, "skipped": skipped}

    def delete_bank_transactions(self, record_ids: Iterable[str]):
        ids = set(record_ids)
        records = self.list_bank_transactions()
        for record in records:
            if record.get("id") in ids:
                self._assert_period_editable(str(record.get("date", ""))[:7])
        self._write_json(
            self.bank_transactions_path,
            [record for record in records if record.get("id") not in ids],
        )

    def auto_reconcile_bank_transactions(self, period: str,
                                         tolerance_days: int = 3) -> Dict[str, int]:
        _month_index(period)
        self._assert_period_editable(period)
        records = self.list_bank_transactions()
        bank_lines = [
            row for row in self.list_vouchers()
            if row.get("period") == period and "银行存款" in str(row.get("subject", ""))
        ]
        used_line_ids = {
            row.get("voucher_line_id") for row in records if row.get("voucher_line_id")
        }
        matched = 0
        for transaction in records:
            if str(transaction.get("date", ""))[:7] != period or transaction.get("voucher_no"):
                continue
            transaction_date = datetime.strptime(transaction["date"], "%Y-%m-%d").date()
            amount = _money(transaction.get("amount"))
            candidates = []
            for line in bank_lines:
                if line.get("id") in used_line_ids:
                    continue
                ledger_amount = _money(line.get("debit" if transaction["direction"] == "收入" else "credit"))
                if abs(ledger_amount - amount) >= 0.01:
                    continue
                try:
                    ledger_date = datetime.strptime(str(line.get("date", "")), "%Y-%m-%d").date()
                except ValueError:
                    continue
                days = abs((ledger_date - transaction_date).days)
                if days <= tolerance_days:
                    description = str(line.get("description", ""))
                    text_bonus = 0 if (
                        transaction.get("summary") and transaction["summary"] in description
                    ) else 1
                    candidates.append((days, text_bonus, str(line.get("voucher_no", "")), line))
            if not candidates:
                continue
            line = sorted(candidates, key=lambda item: item[:3])[0][3]
            transaction["voucher_no"] = line.get("voucher_no", "")
            transaction["voucher_line_id"] = line.get("id", "")
            transaction["status"] = "自动匹配"
            transaction["matched_at"] = _now()
            used_line_ids.add(line.get("id"))
            matched += 1
        self._write_json(self.bank_transactions_path, records)
        return {
            "matched": matched,
            "unmatched": sum(
                1 for row in records
                if str(row.get("date", ""))[:7] == period and not row.get("voucher_no")
            ),
        }

    def set_bank_match(self, transaction_id: str, voucher_no: str = ""):
        records = self.list_bank_transactions()
        voucher_no = str(voucher_no).strip()
        changed = False
        for record in records:
            if record.get("id") != transaction_id:
                continue
            self._assert_period_editable(str(record.get("date", ""))[:7])
            voucher_lines = []
            if voucher_no:
                used_line_ids = {
                    row.get("voucher_line_id") for row in records
                    if row.get("id") != transaction_id and row.get("voucher_line_id")
                }
                amount_key = "debit" if record.get("direction") == "收入" else "credit"
                voucher_lines = [
                    row for row in self.list_vouchers()
                    if row.get("voucher_no") == voucher_no
                    and "银行存款" in str(row.get("subject", ""))
                    and row.get("id") not in used_line_ids
                    and abs(_money(row.get(amount_key)) - _money(record.get("amount"))) < 0.01
                ]
                if not voucher_lines:
                    raise ValueError("所选凭证没有方向和金额一致的可用银行存款分录")
                voucher_lines.sort(key=lambda row: abs(
                    (datetime.strptime(str(row.get("date", "")), "%Y-%m-%d").date()
                     - datetime.strptime(str(record.get("date", "")), "%Y-%m-%d").date()).days
                ))
            record["voucher_no"] = voucher_no
            record["voucher_line_id"] = voucher_lines[0].get("id", "") if voucher_lines else ""
            record["status"] = "手工匹配" if voucher_no else "未匹配"
            record["matched_at"] = _now() if voucher_no else ""
            changed = True
            break
        if not changed:
            raise ValueError("未找到银行流水")
        self._write_json(self.bank_transactions_path, records)

    def set_bank_cash_flow_category(self, transaction_id: str, category: str = ""):
        category = str(category).strip()
        if category and category not in CASH_FLOW_CATEGORIES:
            raise ValueError("无效的现金流量项目")
        records = self.list_bank_transactions()
        changed = False
        for record in records:
            if record.get("id") != transaction_id:
                continue
            self._assert_period_editable(str(record.get("date", ""))[:7])
            expected = (
                CASH_FLOW_RECEIPT_CATEGORIES
                if record.get("direction") == "收入"
                else CASH_FLOW_PAYMENT_CATEGORIES
            )
            if category and category not in expected:
                raise ValueError("现金流量项目与银行流水收支方向不一致")
            record["cash_flow_category"] = category
            record["updated_at"] = _now()
            changed = True
            break
        if not changed:
            raise ValueError("未找到银行流水")
        self._write_json(self.bank_transactions_path, records)

    @staticmethod
    def cash_flow_category_options(direction: str = "") -> List[str]:
        if direction == "收入":
            keys = CASH_FLOW_RECEIPT_CATEGORIES
        elif direction == "支出":
            keys = CASH_FLOW_PAYMENT_CATEGORIES
        else:
            keys = CASH_FLOW_CATEGORIES
        return [
            label for key, label in CASH_FLOW_CATEGORIES.items()
            if key in keys
        ]

    @staticmethod
    def cash_flow_category_key(label: str) -> str:
        label = str(label).strip()
        for key, value in CASH_FLOW_CATEGORIES.items():
            if value == label:
                return key
        return label if label in CASH_FLOW_CATEGORIES else ""

    def list_payroll(self, period: Optional[str] = None) -> List[Dict[str, Any]]:
        records = [dict(row) for row in self._read_json(self.payroll_path, [])]
        if period:
            records = [row for row in records if row.get("period") == period]
        return records

    def upsert_payroll(self, payroll: Dict[str, Any]) -> Dict[str, Any]:
        row = dict(payroll)
        period = str(row.get("period", "")).strip()
        _month_index(period)
        self._assert_period_editable(period)
        employee = str(row.get("employee_name", "")).strip()
        if not employee:
            raise ValueError("员工姓名不能为空")
        gross = _money(row.get("gross_salary"))
        if gross <= 0:
            raise ValueError("应发工资必须大于0")
        social_personal = _money(row.get("social_personal"))
        housing_personal = _money(row.get("housing_personal"))
        income_tax = _money(row.get("income_tax"))
        deductions = social_personal + housing_personal + income_tax
        if deductions > gross:
            raise ValueError("个人承担社保、公积金和个税合计不能大于应发工资")
        normalized = {
            "id": row.get("id") or uuid.uuid4().hex,
            "period": period,
            "employee_name": employee,
            "gross_salary": gross,
            "social_personal": social_personal,
            "housing_personal": housing_personal,
            "income_tax": income_tax,
            "net_salary": round(gross - deductions, 2),
            "social_company": _money(row.get("social_company")),
            "housing_company": _money(row.get("housing_company")),
            "pay_date": str(row.get("pay_date") or _period_end(period)),
            "status": str(row.get("status", "未计提")),
            "voucher_no": str(row.get("voucher_no", "")),
            "note": str(row.get("note", "")).strip(),
            "updated_at": _now(),
        }
        records = self.list_payroll()
        for index, existing in enumerate(records):
            same_id = existing.get("id") == normalized["id"]
            same_key = existing.get("period") == period and existing.get("employee_name") == employee
            if same_id or same_key:
                if existing.get("voucher_no"):
                    raise ValueError("该工资记录已生成凭证，不能再修改")
                normalized["id"] = existing.get("id", normalized["id"])
                records[index] = normalized
                break
        else:
            records.append(normalized)
        records.sort(key=lambda item: (item.get("period", ""), item.get("employee_name", "")))
        self._write_json(self.payroll_path, records)
        return normalized

    def delete_payroll(self, record_id: str):
        records = self.list_payroll()
        for record in records:
            if record.get("id") == record_id:
                self._assert_period_editable(record.get("period", ""))
                if record.get("voucher_no"):
                    raise ValueError("该工资记录已经生成凭证，不能直接删除")
        self._write_json(
            self.payroll_path,
            [record for record in records if record.get("id") != record_id],
        )

    def post_payroll_voucher(self, record_id: str) -> str:
        if self.profile_key != "enterprise":
            raise ValueError("事业单位工资涉及财务会计与预算会计，请使用手工凭证录入")
        records = self.list_payroll()
        payroll = next((row for row in records if row.get("id") == record_id), None)
        if not payroll:
            raise ValueError("未找到工资记录")
        if payroll.get("voucher_no"):
            raise ValueError(f"已生成凭证：{payroll['voucher_no']}")
        period = payroll["period"]
        self._assert_period_editable(period)
        total_cost = round(
            _money(payroll.get("gross_salary"))
            + _money(payroll.get("social_company"))
            + _money(payroll.get("housing_company")), 2,
        )
        social_housing_payable = round(
            _money(payroll.get("social_personal"))
            + _money(payroll.get("housing_personal"))
            + _money(payroll.get("social_company"))
            + _money(payroll.get("housing_company")), 2,
        )
        income_tax_payable = _money(payroll.get("income_tax"))
        description = f"计提{period}工资社保-{payroll['employee_name']}"
        lines = [
            {"subject": self.management_expense_subject, "debit": total_cost,
             "description": description, "source": "payroll"},
            {"subject": "2211 应付职工薪酬", "credit": payroll["net_salary"],
             "description": description, "source": "payroll"},
        ]
        if social_housing_payable > 0:
            lines.append({
                "subject": "2241 其他应付款", "credit": social_housing_payable,
                "description": description, "source": "payroll",
            })
        if income_tax_payable > 0:
            lines.append({
                "subject": "2221 应交税费", "credit": income_tax_payable,
                "description": description, "source": "payroll",
            })
        added = self.add_voucher_lines(
            lines, voucher_date=payroll.get("pay_date") or _period_end(period)
        )
        voucher_no = added[0]["voucher_no"]
        payroll["voucher_no"] = voucher_no
        payroll["status"] = "已计提"
        payroll["updated_at"] = _now()
        self._write_json(self.payroll_path, records)
        return voucher_no

    def list_fixed_assets(self) -> List[Dict[str, Any]]:
        return [dict(row) for row in self._read_json(self.fixed_assets_path, [])]

    def upsert_fixed_asset(self, asset: Dict[str, Any]) -> Dict[str, Any]:
        row = dict(asset)
        name = str(row.get("asset_name", "")).strip()
        if not name:
            raise ValueError("资产名称不能为空")
        purchase_date = str(row.get("purchase_date", "")).strip()
        try:
            datetime.strptime(purchase_date, "%Y-%m-%d")
        except ValueError:
            raise ValueError("购置日期应为 YYYY-MM-DD 格式")
        cost = _money(row.get("original_cost"))
        useful_months = int(row.get("useful_months") or 0)
        residual_rate = float(row.get("residual_rate") or 0)
        if cost <= 0 or useful_months <= 0:
            raise ValueError("资产原值和使用月数必须大于0")
        if not 0 <= residual_rate < 1:
            raise ValueError("预计净残值率应在0%到100%之间")
        start_period = str(row.get("depreciation_start_period", "")).strip()
        _month_index(start_period)
        self._assert_period_editable(purchase_date[:7])
        self._assert_period_editable(start_period)
        normalized = {
            "id": row.get("id") or uuid.uuid4().hex,
            "asset_name": name,
            "category": str(row.get("category", "办公设备")),
            "purchase_date": purchase_date,
            "original_cost": cost,
            "residual_rate": round(residual_rate, 6),
            "useful_months": useful_months,
            "depreciation_start_period": start_period,
            "monthly_depreciation": round(cost * (1 - residual_rate) / useful_months, 2),
            "asset_subject": str(row.get("asset_subject", "1601 固定资产")),
            "depreciation_subject": str(row.get("depreciation_subject", "1602 累计折旧")),
            "expense_subject": str(
                row.get("expense_subject", self.management_expense_subject)
            ),
            "status": str(row.get("status", "使用中")),
            "posted_periods": list(row.get("posted_periods", [])),
            "depreciation_vouchers": dict(row.get("depreciation_vouchers", {})),
            "note": str(row.get("note", "")).strip(),
            "updated_at": _now(),
        }
        records = self.list_fixed_assets()
        for index, existing in enumerate(records):
            if existing.get("id") == normalized["id"]:
                if existing.get("posted_periods"):
                    raise ValueError("该资产已经计提折旧，不能修改折旧参数")
                normalized["posted_periods"] = list(existing.get("posted_periods", []))
                normalized["depreciation_vouchers"] = dict(existing.get("depreciation_vouchers", {}))
                records[index] = normalized
                break
        else:
            records.append(normalized)
        records.sort(key=lambda item: (item.get("purchase_date", ""), item.get("asset_name", "")))
        self._write_json(self.fixed_assets_path, records)
        return normalized

    def delete_fixed_asset(self, record_id: str):
        records = self.list_fixed_assets()
        for record in records:
            if record.get("id") != record_id:
                continue
            self._assert_period_editable(str(record.get("purchase_date", ""))[:7])
            if record.get("posted_periods"):
                raise ValueError("该资产已经计提折旧，不能直接删除")
        self._write_json(
            self.fixed_assets_path,
            [record for record in records if record.get("id") != record_id],
        )

    def depreciation_schedule(self, period: str) -> List[Dict[str, Any]]:
        target_index = _month_index(period)
        schedule = []
        for asset in self.list_fixed_assets():
            start_index = _month_index(asset.get("depreciation_start_period", ""))
            month_number = target_index - start_index + 1
            eligible = (
                asset.get("status") == "使用中"
                and 1 <= month_number <= int(asset.get("useful_months", 0))
            )
            monthly = _money(asset.get("monthly_depreciation"))
            accumulated_months = max(0, min(month_number, int(asset.get("useful_months", 0))))
            depreciable = _money(asset.get("original_cost")) * (1 - float(asset.get("residual_rate", 0)))
            previous_months = max(0, min(month_number - 1, int(asset.get("useful_months", 0))))
            previous_accumulated = min(depreciable, previous_months * monthly)
            accumulated = min(depreciable, accumulated_months * monthly)
            amount = round(accumulated - previous_accumulated, 2) if eligible else 0.0
            schedule.append({
                **asset,
                "period": period,
                "month_number": max(0, month_number),
                "depreciation_amount": round(amount, 2),
                "accumulated_depreciation": round(accumulated, 2),
                "net_book_value": round(_money(asset.get("original_cost")) - accumulated, 2),
                "posted": period in asset.get("posted_periods", []),
                "voucher_no": asset.get("depreciation_vouchers", {}).get(period, ""),
            })
        return schedule

    def post_depreciation_voucher(self, period: str) -> str:
        if self.profile_key != "enterprise":
            raise ValueError("事业单位固定资产折旧科目规则不同，请使用手工凭证录入")
        self._assert_period_editable(period)
        schedule = [
            row for row in self.depreciation_schedule(period)
            if row.get("depreciation_amount", 0) > 0 and not row.get("posted")
        ]
        if not schedule:
            raise ValueError("本期没有待计提的固定资产折旧")
        debits: Dict[str, float] = {}
        credits: Dict[str, float] = {}
        for row in schedule:
            debits[row["expense_subject"]] = round(
                debits.get(row["expense_subject"], 0) + row["depreciation_amount"], 2
            )
            credits[row["depreciation_subject"]] = round(
                credits.get(row["depreciation_subject"], 0) + row["depreciation_amount"], 2
            )
        description = f"计提{period}固定资产折旧"
        lines = [
            {"subject": subject, "debit": amount, "description": description, "source": "depreciation"}
            for subject, amount in debits.items()
        ] + [
            {"subject": subject, "credit": amount, "description": description, "source": "depreciation"}
            for subject, amount in credits.items()
        ]
        added = self.add_voucher_lines(lines, voucher_date=_period_end(period))
        voucher_no = added[0]["voucher_no"]
        assets = self.list_fixed_assets()
        asset_ids = {row["id"] for row in schedule}
        for asset in assets:
            if asset.get("id") not in asset_ids:
                continue
            asset.setdefault("posted_periods", []).append(period)
            asset["posted_periods"] = sorted(set(asset["posted_periods"]))
            asset.setdefault("depreciation_vouchers", {})[period] = voucher_no
            asset["updated_at"] = _now()
        self._write_json(self.fixed_assets_path, assets)
        return voucher_no

    @staticmethod
    def _split_signed_balance(value: float) -> Dict[str, float]:
        return {
            "debit": round(max(value, 0.0), 2),
            "credit": round(max(-value, 0.0), 2),
        }

    def _balance_snapshot(self, report_period: str,
                          include_report_period: bool) -> Dict[tuple, float]:
        _month_index(report_period)
        opening = self.opening_balances_for_period(report_period)
        opening_period = opening[0].get("period", "") if opening else ""
        signed: Dict[tuple, float] = {}
        for row in opening:
            key = (str(row.get("subject_code", "")), str(row.get("subject", "")))
            signed[key] = signed.get(key, 0.0) + _money(row.get("debit_balance"))
            signed[key] -= _money(row.get("credit_balance"))
        for row in self.list_vouchers():
            voucher_period = str(row.get("period", ""))
            if opening_period and voucher_period < opening_period:
                continue
            if voucher_period > report_period:
                continue
            if not include_report_period and voucher_period == report_period:
                continue
            key = (str(row.get("subject_code", "")), str(row.get("subject", "")))
            signed[key] = signed.get(key, 0.0) + _money(row.get("debit"))
            signed[key] -= _money(row.get("credit"))
        return {key: round(value, 2) for key, value in signed.items()}

    def account_balances(self, report_period: str) -> List[Dict[str, Any]]:
        """Return report-period, year-to-date, and balance-sheet audit data."""
        _month_index(report_period)
        year_start = f"{report_period[:4]}-01"
        period_opening = self._balance_snapshot(report_period, False)
        year_opening = self._balance_snapshot(year_start, False)
        ending = self._balance_snapshot(report_period, True)
        current_moves: Dict[tuple, Dict[str, float]] = {}
        ytd_moves: Dict[tuple, Dict[str, float]] = {}
        for row in self.list_vouchers():
            voucher_period = str(row.get("period", ""))
            if row.get("source") == "period_close":
                continue
            key = (str(row.get("subject_code", "")), str(row.get("subject", "")))
            if voucher_period == report_period:
                movement = current_moves.setdefault(key, {"debit": 0.0, "credit": 0.0})
                movement["debit"] += _money(row.get("debit"))
                movement["credit"] += _money(row.get("credit"))
            if year_start <= voucher_period <= report_period:
                movement = ytd_moves.setdefault(key, {"debit": 0.0, "credit": 0.0})
                movement["debit"] += _money(row.get("debit"))
                movement["credit"] += _money(row.get("credit"))

        keys = set(period_opening) | set(year_opening) | set(ending)
        keys.update(current_moves)
        keys.update(ytd_moves)
        rows = []
        for code, subject in sorted(keys, key=lambda item: (item[0], item[1])):
            key = (code, subject)
            year_balance = self._split_signed_balance(year_opening.get(key, 0.0))
            opening_balance = self._split_signed_balance(period_opening.get(key, 0.0))
            ending_balance = self._split_signed_balance(ending.get(key, 0.0))
            current = current_moves.get(key, {})
            ytd = ytd_moves.get(key, {})
            rows.append({
                "subject_code": code,
                "subject": subject,
                "year_opening_debit": year_balance["debit"],
                "year_opening_credit": year_balance["credit"],
                "opening_debit": opening_balance["debit"],
                "opening_credit": opening_balance["credit"],
                "period_debit": round(current.get("debit", 0.0), 2),
                "period_credit": round(current.get("credit", 0.0), 2),
                "ytd_debit": round(ytd.get("debit", 0.0), 2),
                "ytd_credit": round(ytd.get("credit", 0.0), 2),
                "ending_debit": ending_balance["debit"],
                "ending_credit": ending_balance["credit"],
            })
        return rows

    @staticmethod
    def _is_cash_line(row: Dict[str, Any]) -> bool:
        code = str(row.get("subject_code", ""))
        subject = str(row.get("subject", ""))
        return code in ("1001", "1002", "1012") or any(
            name in subject for name in ("库存现金", "银行存款", "其他货币资金")
        )

    def _infer_cash_flow_category(self, direction: str,
                                  counterparts: List[Dict[str, Any]]) -> tuple:
        categories = set()
        for row in counterparts:
            code = str(row.get("subject_code", ""))
            subject = str(row.get("subject", ""))
            if direction == "收入":
                if "投资收益" in subject:
                    categories.add("investing_income_receipt")
                elif code in ("1101", "1501", "1511") or "投资" in subject:
                    categories.add("investing_recovery_receipt")
                elif code in ("1606", "1701", "1801") or "固定资产清理" in subject:
                    categories.add("investing_disposal_receipt")
                elif code in ("2001", "2501") or "借款" in subject:
                    categories.add("financing_borrowing_receipt")
                elif code in ("3001", "3002", "4001", "4002") or any(
                    word in subject for word in ("实收资本", "资本公积")
                ):
                    categories.add("financing_capital_receipt")
                elif (
                    "收入" in subject
                    or code in self.standard_code_set["operating_revenue"]
                ):
                    categories.add("operating_sales_receipt")
                else:
                    categories.add("operating_other_receipt")
            else:
                if code == "2211" or "职工薪酬" in subject:
                    categories.add("operating_payroll_payment")
                elif code == "2221" or "应交税费" in subject:
                    categories.add("operating_tax_payment")
                elif code in ("1101", "1501", "1511") or "投资" in subject:
                    categories.add("investing_investment_payment")
                elif code in ("1601", "1604", "1701", "1801") or any(
                    word in subject for word in ("固定资产", "在建工程", "无形资产")
                ):
                    categories.add("investing_asset_payment")
                elif code in ("2231",) or "利息" in subject:
                    categories.add("financing_interest_payment")
                elif code in ("2001", "2501") or "借款" in subject:
                    categories.add("financing_principal_payment")
                elif code in ("2232", "3104", "4104") or any(
                    word in subject for word in ("应付利润", "利润分配")
                ):
                    categories.add("financing_distribution_payment")
                elif code.startswith("14") or code in (
                    {"1123", "2202", "2203", "5401", "6401"}
                    | set(self.standard_code_set["production_cost"])
                ) or any(
                    word in subject for word in ("存货", "原材料", "库存商品", "营业成本", "应付账款", "预付账款")
                ):
                    categories.add("operating_purchase_payment")
                else:
                    categories.add("operating_other_payment")
        fallback = (
            "operating_other_receipt" if direction == "收入"
            else "operating_other_payment"
        )
        if len(categories) == 1:
            return next(iter(categories)), False
        return fallback, True

    def _cash_flow_entries_between(self, start_period: str,
                                   end_period: str) -> List[Dict[str, Any]]:
        overrides = {
            str(row.get("voucher_line_id")): str(row.get("cash_flow_category", ""))
            for row in self.list_bank_transactions()
            if row.get("voucher_line_id") and row.get("cash_flow_category")
        }
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for row in self.list_vouchers():
            period = str(row.get("period", ""))
            if start_period <= period <= end_period:
                grouped.setdefault(str(row.get("voucher_no", "")), []).append(row)
        entries = []
        for voucher_no, lines in grouped.items():
            cash_lines = [row for row in lines if self._is_cash_line(row)]
            counterparts = [row for row in lines if not self._is_cash_line(row)]
            if cash_lines and not counterparts:
                continue
            for line in cash_lines:
                direction = "收入" if _money(line.get("debit")) > 0 else "支出"
                category = str(
                    line.get("cash_flow_category")
                    or overrides.get(str(line.get("id", "")), "")
                )
                needs_review = False
                source = "手工" if category else "自动"
                if not category:
                    category, needs_review = self._infer_cash_flow_category(
                        direction, counterparts
                    )
                if category not in CASH_FLOW_CATEGORIES:
                    category = (
                        "operating_other_receipt" if direction == "收入"
                        else "operating_other_payment"
                    )
                    needs_review = True
                expected = (
                    CASH_FLOW_RECEIPT_CATEGORIES if direction == "收入"
                    else CASH_FLOW_PAYMENT_CATEGORIES
                )
                if category not in expected:
                    needs_review = True
                entries.append({
                    "line_id": line.get("id", ""),
                    "voucher_no": voucher_no,
                    "date": line.get("date", ""),
                    "period": line.get("period", ""),
                    "description": line.get("description", ""),
                    "direction": direction,
                    "amount": _money(line.get("debit") or line.get("credit")),
                    "category": category,
                    "category_label": CASH_FLOW_CATEGORIES[category],
                    "classification_source": source,
                    "needs_review": needs_review,
                })
        entries.sort(key=lambda row: (row["date"], row["voucher_no"], row["line_id"]))
        return entries

    def cash_flow_summary(self, period: str) -> Dict[str, Any]:
        _month_index(period)
        year_start = f"{period[:4]}-01"
        month_entries = self._cash_flow_entries_between(period, period)
        ytd_entries = self._cash_flow_entries_between(year_start, period)

        def totals(entries):
            result = {key: 0.0 for key in CASH_FLOW_CATEGORIES}
            for entry in entries:
                result[entry["category"]] += _money(entry.get("amount"))
            return {key: round(value, 2) for key, value in result.items()}

        balances = self.account_balances(period)
        cash_rows = [row for row in balances if self._is_cash_line(row)]
        month_opening = sum(
            row["opening_debit"] - row["opening_credit"] for row in cash_rows
        )
        year_opening = sum(
            row["year_opening_debit"] - row["year_opening_credit"] for row in cash_rows
        )
        ending = sum(row["ending_debit"] - row["ending_credit"] for row in cash_rows)
        month = totals(month_entries)
        ytd = totals(ytd_entries)

        def net(values):
            receipts = sum(values[key] for key in CASH_FLOW_RECEIPT_CATEGORIES)
            payments = sum(values[key] for key in CASH_FLOW_PAYMENT_CATEGORIES)
            return round(receipts - payments, 2)

        month_net = net(month)
        ytd_net = net(ytd)
        return {
            "month": month,
            "ytd": ytd,
            "month_entries": month_entries,
            "period_opening_cash": round(month_opening, 2),
            "year_opening_cash": round(year_opening, 2),
            "ending_cash": round(ending, 2),
            "month_net_change": month_net,
            "ytd_net_change": ytd_net,
            "month_cash_difference": round(ending - month_opening - month_net, 2),
            "ytd_cash_difference": round(ending - year_opening - ytd_net, 2),
            "needs_review_count": sum(
                1 for entry in month_entries if entry.get("needs_review")
            ),
        }

    def profit_close_preview(self, period: str) -> Dict[str, Any]:
        """Return the reversible period-close voucher without mutating the ledger."""
        if self.profile_key != "enterprise":
            raise ValueError("事业单位账套不使用企业损益结转流程")
        _month_index(period)
        posted_lines = [
            row for row in self.list_vouchers()
            if row.get("period") == period and row.get("source") == "period_close"
        ]
        if posted_lines:
            profit_lines = [
                row for row in posted_lines
                if str(row.get("subject_code", "")) in ("3103", "4103")
            ]
            net_profit = round(sum(
                _money(row.get("credit")) - _money(row.get("debit"))
                for row in profit_lines
            ), 2)
            return {
                "period": period,
                "posted": True,
                "voucher_no": str(posted_lines[0].get("voucher_no", "")),
                "net_profit": net_profit,
                "income_total": 0.0,
                "expense_total": 0.0,
                "lines": [dict(row) for row in posted_lines],
            }

        closing_lines = []
        income_total = 0.0
        expense_total = 0.0
        profit_income_codes = (
            set(self.standard_code_set["operating_revenue"])
            | set(self.standard_code_set["other_income"])
        )
        profit_expense_codes = set(self.standard_code_set["profit_expense"])
        description = f"结转截至{period}的损益"
        for row in self.account_balances(period):
            code = str(row.get("subject_code", ""))
            if code not in profit_income_codes and code not in profit_expense_codes:
                continue
            debit_balance = _money(row.get("ending_debit"))
            credit_balance = _money(row.get("ending_credit"))
            if debit_balance <= 0 and credit_balance <= 0:
                continue
            line = {
                "subject": row.get("subject", ""),
                "subject_code": code,
                "description": description,
                "source": "period_close",
            }
            if debit_balance > 0:
                line["credit"] = debit_balance
            else:
                line["debit"] = credit_balance
            closing_lines.append(line)
            if code in profit_income_codes:
                income_total += credit_balance - debit_balance
            else:
                expense_total += debit_balance - credit_balance

        income_total = round(income_total, 2)
        expense_total = round(expense_total, 2)
        net_profit = round(income_total - expense_total, 2)
        if closing_lines and abs(net_profit) >= 0.01:
            profit_subject = self.profit_subject
            profit_line = {
                "subject": profit_subject,
                "subject_code": subject_code(profit_subject),
                "description": description,
                "source": "period_close",
            }
            if net_profit > 0:
                profit_line["credit"] = net_profit
            else:
                profit_line["debit"] = abs(net_profit)
            closing_lines.append(profit_line)
        return {
            "period": period,
            "posted": False,
            "voucher_no": "",
            "net_profit": net_profit,
            "income_total": income_total,
            "expense_total": expense_total,
            "lines": closing_lines,
        }

    def _assert_no_later_archived_period(self, period: str):
        later_archived = sorted(
            key for key, value in self.get_tax_periods().items()
            if key > period and value.get("status") == "已归档"
        )
        if later_archived:
            raise ValueError(
                f"后续期间 {later_archived[0]} 已归档，不能修改 {period} 的损益结转"
            )

    def post_profit_close_voucher(self, period: str) -> str:
        self._assert_period_editable(period)
        self._assert_no_later_archived_period(period)
        tax_preview = self.tax_accrual_preview(period)
        if tax_preview.get("can_post") and tax_preview.get("lines") and not tax_preview.get("posted"):
            raise ValueError("当前为税务申报期末，请先生成税费计提凭证，再结转损益")
        preview = self.profit_close_preview(period)
        if preview["posted"]:
            raise ValueError(f"{period} 已生成损益结转凭证 {preview['voucher_no']}")
        if not preview["lines"]:
            raise ValueError(f"{period} 没有需要结转的损益科目余额")
        added = self.add_voucher_lines(
            preview["lines"], voucher_date=_period_end(period)
        )
        return str(added[0].get("voucher_no", ""))

    def unpost_profit_close_voucher(self, period: str) -> str:
        self._assert_period_editable(period)
        self._assert_no_later_archived_period(period)
        preview = self.profit_close_preview(period)
        if not preview["posted"]:
            raise ValueError(f"{period} 尚未生成损益结转凭证")
        voucher_no = preview["voucher_no"]
        self.create_backup(f"撤销{period}损益结转前自动备份")
        self.delete_voucher_numbers([voucher_no])
        return voucher_no

    def get_tax_periods(self) -> Dict[str, Dict[str, Any]]:
        return self._read_json(self.tax_periods_path, {})

    def set_period_status(self, period: str, status: str, note: str = ""):
        _month_index(period)
        periods = self.get_tax_periods()
        current_status = periods.get(period, {}).get("status", "")
        if current_status == "已归档" and status != "已归档":
            raise ValueError("已归档期间不能直接改回其他状态，请使用“重新打开归档期”")
        if status == "已归档" and self.voucher_balance_issues(period):
            raise ValueError("存在借贷不平衡凭证，不能归档")
        periods[period] = {
            "status": status,
            "note": note,
            "updated_at": _now(),
        }
        self._write_json(self.tax_periods_path, periods)

    def reopen_archived_period(self, period: str, note: str = ""):
        _month_index(period)
        periods = self.get_tax_periods()
        if periods.get(period, {}).get("status") != "已归档":
            raise ValueError(f"期间 {period} 当前不是已归档状态")
        self.create_backup(f"重新打开{period}前自动备份")
        periods[period] = {
            "status": "待复核",
            "note": note or "用户确认重新打开已归档期间",
            "updated_at": _now(),
            "reopened_at": _now(),
        }
        self._write_json(self.tax_periods_path, periods)

    def _profit_components(self, periods: Iterable[str]) -> Dict[str, float]:
        allowed_periods = {str(value) for value in periods}
        vouchers = [
            row for row in self.list_vouchers()
            if row.get("period") in allowed_periods
            and row.get("source") not in {"period_close", "tax_accrual"}
        ]
        revenue = 0.0
        other_income = 0.0
        expenses = 0.0
        operating_revenue_codes = set(self.standard_code_set["operating_revenue"])
        other_income_codes = set(self.standard_code_set["other_income"])
        profit_expense_codes = set(self.standard_code_set["profit_expense"])
        for record in vouchers:
            name = str(record.get("subject", ""))
            code = str(record.get("subject_code") or subject_code(name))
            debit = _money(record.get("debit"))
            credit = _money(record.get("credit"))
            if code in operating_revenue_codes:
                revenue += credit - debit
            elif code in other_income_codes:
                other_income += credit - debit
            elif code in profit_expense_codes:
                expenses += debit - credit
            elif not code:
                if "收入" in name:
                    revenue += credit - debit
                elif any(word in name for word in ("费用", "成本", "支出")):
                    expenses += debit - credit
        return {
            "revenue": round(revenue, 2),
            "other_income": round(other_income, 2),
            "expenses": round(expenses, 2),
            "profit": round(revenue + other_income - expenses, 2),
        }

    def tax_summary(self, period: Optional[str] = None) -> Dict[str, Any]:
        anchor = period or date.today().strftime("%Y-%m")
        settings = self.get_settings()
        tax = settings["tax"]
        vat_period = resolve_tax_period(anchor, tax.get("vat_filing_frequency", "按季"))
        cit_period = resolve_tax_period(anchor, tax.get("cit_filing_frequency", "按季"))
        components = self._profit_components(vat_period.months)
        invoices = [
            record for record in self.list_invoices()
            if str(record.get("invoice_date", ""))[:7] in set(vat_period.months)
        ]

        input_vat = sum(
            _money(invoice.get("tax_amount"))
            for invoice in invoices
            if invoice.get("invoice_type") == "进项" and invoice.get("deductible")
        )
        output_vat = sum(
            _money(invoice.get("tax_amount"))
            for invoice in invoices
            if invoice.get("invoice_type") == "销项"
        )
        output_records = [
            invoice for invoice in invoices
            if invoice.get("invoice_type") == "销项"
        ]
        if output_records:
            vat_sales = round(sum(
                _money(invoice.get("amount"))
                for invoice in output_records
                if invoice.get("tax_treatment", "自动判断") != "不征税"
            ), 2)
            non_exempt_sales = round(sum(
                _money(invoice.get("amount"))
                for invoice in output_records
                if invoice.get("tax_treatment") == "不得免税"
                or invoice.get("invoice_form") in {"专用发票", "增值税专用发票"}
            ), 2)
            exempt_project_sales = round(sum(
                _money(invoice.get("amount"))
                for invoice in output_records
                if invoice.get("tax_treatment") == "免税项目"
            ), 2)
        else:
            vat_sales = max(0.0, components["revenue"])
            non_exempt_sales = 0.0
            exempt_project_sales = 0.0

        scope_settings = dict(tax)
        scope_settings["taxpayer_type"] = settings.get("company", {}).get(
            "taxpayer_type", ""
        )
        scope = supported_scope(scope_settings)
        if scope_settings["taxpayer_type"] == "小规模纳税人":
            vat_result = calculate_small_scale_vat(
                sales=max(0.0, vat_sales),
                non_exempt_sales=max(0.0, non_exempt_sales),
                exempt_project_sales=max(0.0, exempt_project_sales),
                settings=tax,
                period=vat_period,
            )
        else:
            vat_result = {
                "supported": False,
                "period_key": vat_period.key,
                "frequency": vat_period.frequency,
                "sales": max(0.0, vat_sales),
                "threshold": 0.0,
                "threshold_eligible": False,
                "taxable_sales": 0.0,
                "exempt_sales": 0.0,
                "vat_payable": 0.0,
                "message": "非小规模纳税人，已停止自动计算增值税",
            }
        vat_payable = _money(vat_result.get("vat_payable"))
        surcharge = round(vat_payable * float(tax.get("surcharge_rate", 0)), 2)

        end_year, end_month = map(int, cit_period.end_month.split("-"))
        ytd_months = tuple(
            f"{end_year:04d}-{value:02d}" for value in range(1, end_month + 1)
        )
        ytd_components = self._profit_components(ytd_months)
        adjustments = self.list_tax_adjustments(ytd_months)
        increase = sum(
            _money(row.get("amount")) for row in adjustments
            if row.get("tax_type") == "企业所得税" and row.get("direction") == "调增"
        )
        decrease = sum(
            _money(row.get("amount")) for row in adjustments
            if row.get("tax_type") == "企业所得税" and row.get("direction") == "调减"
        )
        prior_losses = sum(
            _money(row.get("amount")) for row in adjustments
            if row.get("tax_type") == "企业所得税"
            and row.get("direction") == "弥补以前年度亏损"
        )
        prepaid_tax = sum(
            _money(row.get("amount")) for row in adjustments
            if row.get("tax_type") == "企业所得税"
            and row.get("direction") == "已预缴所得税"
        )
        cit_result = calculate_cit(
            accounting_profit=ytd_components["profit"],
            increase=increase,
            decrease=decrease,
            prior_losses=prior_losses,
            prepaid_tax=prepaid_tax,
            settings=tax,
        )
        cit_result["period"] = {
            "anchor": anchor,
            "key": cit_period.key,
            "frequency": cit_period.frequency,
            "start_month": cit_period.start_month,
            "end_month": cit_period.end_month,
            "months": list(cit_period.months),
        }
        return {
            "period": {
                "anchor": anchor,
                "key": vat_period.key,
                "frequency": vat_period.frequency,
                "start_month": vat_period.start_month,
                "end_month": vat_period.end_month,
                "months": list(vat_period.months),
            },
            "scope": scope,
            "vat": vat_result,
            "cit": cit_result,
            "adjustments": adjustments,
            "revenue": components["revenue"],
            "other_income": components["other_income"],
            "expenses": components["expenses"],
            "profit": components["profit"],
            "input_vat": round(input_vat, 2),
            "output_vat": round(output_vat, 2),
            "vat_payable": round(vat_payable, 2),
            "surcharge": round(surcharge, 2),
            "cit_payable": _money(cit_result.get("cit_payable")),
        }

    def cit_prepayment_summary(self, period: str) -> Dict[str, Any]:
        summary = self.tax_summary(period)
        result = dict(summary["cit"])
        result.update({
            "period": dict(summary["period"]),
            "scope": dict(summary["scope"]),
            "adjustments": [dict(row) for row in summary["adjustments"]],
        })
        return result

    def annual_cit_summary(self, year: Any) -> Dict[str, Any]:
        try:
            year_number = int(str(year)[:4])
        except (TypeError, ValueError):
            raise ValueError("汇算年度应为四位年份")
        settings = self.get_settings()["tax"]
        months = tuple(f"{year_number:04d}-{month:02d}" for month in range(1, 13))
        components = self._profit_components(months)
        adjustments = self.list_tax_adjustments(months)
        direction_total = lambda direction: sum(
            _money(row.get("amount")) for row in adjustments
            if row.get("tax_type") == "企业所得税"
            and row.get("direction") == direction
        )
        result = calculate_cit(
            accounting_profit=components["profit"],
            increase=direction_total("调增"),
            decrease=direction_total("调减"),
            prior_losses=direction_total("弥补以前年度亏损"),
            prepaid_tax=direction_total("已预缴所得税"),
            settings=settings,
        )
        result.update({
            "year": year_number,
            "adjustments": adjustments,
            "revenue": components["revenue"],
            "other_income": components["other_income"],
            "expenses": components["expenses"],
        })
        return result

    def individual_income_tax_summary(self, period: str) -> Dict[str, Any]:
        _month_index(period)
        year, month = map(int, period.split("-"))
        settings = self.get_settings()["tax"]
        basic_deduction = _money(settings.get("iit_monthly_deduction", 5000))
        employees = sorted({
            str(row.get("employee_name", "")).strip()
            for row in self.list_payroll()
            if str(row.get("period", "")).startswith(f"{year:04d}-")
            and str(row.get("period", "")) <= period
            and str(row.get("employee_name", "")).strip()
        })
        rows = []
        for employee in employees:
            payroll = [
                row for row in self.list_payroll()
                if row.get("employee_name") == employee
                and str(row.get("period", "")).startswith(f"{year:04d}-")
                and str(row.get("period", "")) <= period
            ]
            cumulative_income = sum(_money(row.get("gross_salary")) for row in payroll)
            personal_deductions = sum(
                _money(row.get("social_personal")) + _money(row.get("housing_personal"))
                for row in payroll
            )
            prior_withheld = sum(
                _money(row.get("income_tax")) for row in payroll
                if str(row.get("period", "")) < period
            )
            calculated = cumulative_iit(
                cumulative_income=cumulative_income,
                cumulative_deductions=basic_deduction * month + personal_deductions,
                prior_withheld=prior_withheld,
            )
            current_recorded = sum(
                _money(row.get("income_tax")) for row in payroll
                if str(row.get("period", "")) == period
            )
            rows.append({
                "employee_name": employee,
                "cumulative_income": round(cumulative_income, 2),
                "basic_deduction": round(basic_deduction * month, 2),
                "other_deductions": round(personal_deductions, 2),
                **calculated,
                "current_recorded": round(current_recorded, 2),
                "difference": round(current_recorded - calculated["current_withholding"], 2),
            })
        return {
            "period": period,
            "rows": rows,
            "current_withholding": round(sum(row["current_withholding"] for row in rows), 2),
            "current_recorded": round(sum(row["current_recorded"] for row in rows), 2),
        }

    def stamp_duty_summary(self, period: str) -> Dict[str, Any]:
        settings = self.get_settings()["tax"]
        tax_period = resolve_tax_period(
            period, settings.get("stamp_duty_filing_frequency", "按季")
        )
        result = stamp_duty(
            self.list_stamp_duty_items(tax_period.months),
            relief_rate=float(settings.get("stamp_duty_relief_rate", 0.5)),
        )
        result["period"] = {
            "key": tax_period.key,
            "frequency": tax_period.frequency,
            "months": list(tax_period.months),
        }
        return result

    def tax_accrual_preview(self, period: str) -> Dict[str, Any]:
        summary = self.tax_summary(period)
        vat_period = summary["period"]
        cit_period = summary["cit"]["period"]
        period_keys = []
        if period == vat_period["end_month"]:
            period_keys.append(f"增值税{vat_period['key']}")
        if period == cit_period["end_month"]:
            period_keys.append(f"所得税{cit_period['key']}")
        period_key = "/".join(period_keys) or f"{period}-非申报期末"
        existing = [
            row for row in self.list_vouchers()
            if row.get("source") == "tax_accrual"
            and row.get("tax_period_key") == period_key
        ]
        lines: List[Dict[str, Any]] = []
        description = f"计提{period_key}税费"
        common = {
            "description": description,
            "source": "tax_accrual",
            "tax_period_key": period_key,
        }
        if period == vat_period["end_month"] and summary["surcharge"] > 0:
            lines.extend([
                {**common, "subject": "5403 营业税金及附加", "debit": summary["surcharge"]},
                {**common, "subject": "2221 应交税费-附加税费", "credit": summary["surcharge"]},
            ])
        if (
            period == cit_period["end_month"]
            and summary["cit_payable"] > 0
            and summary["cit"].get("supported")
        ):
            lines.extend([
                {**common, "subject": "5801 所得税费用", "debit": summary["cit_payable"]},
                {**common, "subject": "2221 应交税费-企业所得税", "credit": summary["cit_payable"]},
            ])
        return {
            "period_key": period_key,
            "voucher_date": _period_end(period),
            "can_post": bool(period_keys),
            "posted": bool(existing),
            "voucher_no": str(existing[0].get("voucher_no", "")) if existing else "",
            "lines": lines,
            "summary": summary,
        }

    def post_tax_accrual_voucher(self, period: str) -> str:
        preview = self.tax_accrual_preview(period)
        if preview["posted"]:
            raise ValueError(f"{preview['period_key']} 已生成税费计提凭证")
        if not preview["can_post"]:
            raise ValueError("当前月份不是税务申报期末，默认按季时请在3、6、9、12月生成计提凭证")
        if not preview["lines"]:
            raise ValueError("本期没有需要计提的附加税费或企业所得税")
        added = self.add_voucher_lines(
            preview["lines"], voucher_date=preview["voucher_date"]
        )
        return str(added[0].get("voucher_no", ""))

    def unpost_tax_accrual_voucher(self, period: str) -> str:
        preview = self.tax_accrual_preview(period)
        if not preview["posted"]:
            raise ValueError(f"{preview['period_key']} 尚未生成税费计提凭证")
        self.create_backup(f"撤销{preview['period_key']}税费计提前自动备份")
        self.delete_voucher_numbers([preview["voucher_no"]])
        return preview["voucher_no"]

    def validate(self, period: Optional[str] = None) -> List[Dict[str, Any]]:
        issues = []
        settings = self.get_settings()
        company = settings["company"]
        if self.profile_key == "enterprise":
            missing = [
                label for key, label in (
                    ("name", "企业名称"),
                    ("credit_code", "统一社会信用代码"),
                    ("taxpayer_type", "纳税人类型"),
                ) if not str(company.get(key, "")).strip()
            ]
            if missing:
                issues.append({
                    "level": "错误",
                    "code": "COMPANY_PROFILE",
                    "message": f"企业资料未完整：{'、'.join(missing)}",
                    "count": len(missing),
                })
            policy_end = str(settings["tax"].get("policy_effective_through", "")).strip()
            try:
                policy_expired = date.today() > datetime.strptime(
                    policy_end, "%Y-%m-%d"
                ).date()
            except ValueError:
                policy_expired = True
            if policy_expired:
                issues.append({
                    "level": "警告",
                    "code": "POLICY_REVIEW",
                    "message": "税务优惠政策预设已到期或日期无效，请核对现行政策并更新系统设置",
                    "count": 1,
                })
            if period:
                tax_view = self.tax_summary(period)
                if not tax_view.get("scope", {}).get("supported"):
                    issues.append({
                        "level": "错误",
                        "code": "UNSUPPORTED_TAX_SCOPE",
                        "message": tax_view.get("scope", {}).get(
                            "message", "当前纳税人资格超出工具支持范围"
                        ),
                        "count": 1,
                    })
                if not tax_view.get("cit", {}).get("supported"):
                    issues.append({
                        "level": "错误",
                        "code": "SMALL_PROFIT_QUALIFICATION",
                        "message": tax_view.get("cit", {}).get("eligibility", {}).get(
                            "message", "未通过小型微利企业资格检查"
                        ),
                        "count": 1,
                    })

        all_vouchers = self.list_vouchers(include_unposted=True)
        unposted = [
            row for row in all_vouchers
            if row.get("status") == self.UNPOSTED_STATUS
            and (not period or row.get("period") == period)
        ]
        if unposted:
            voucher_numbers = sorted({str(row.get("voucher_no", "")) for row in unposted})
            issues.append({
                "level": "错误",
                "code": "UNPOSTED_VOUCHER",
                "message": f"存在尚未重新入账的反过账凭证：{', '.join(voucher_numbers[:8])}",
                "count": len(voucher_numbers),
            })

        vouchers = self.list_vouchers()
        if period:
            vouchers = [record for record in vouchers if record.get("period") == period]
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for record in vouchers:
            grouped.setdefault(str(record.get("voucher_no", "未编号")), []).append(record)
        unbalanced = []
        for voucher_no, lines in grouped.items():
            debit = sum(_money(line.get("debit")) for line in lines)
            credit = sum(_money(line.get("credit")) for line in lines)
            if abs(debit - credit) >= 0.01:
                unbalanced.append(voucher_no)
        if unbalanced:
            issues.append({
                "level": "错误",
                "code": "UNBALANCED",
                "message": f"存在借贷不平衡凭证：{', '.join(unbalanced[:8])}",
                "count": len(unbalanced),
            })

        invoices = self.list_invoices()
        if period:
            invoices = [
                record for record in invoices
                if str(record.get("invoice_date", ""))[:7] == period
            ]
        invoice_keys: Dict[str, int] = {}
        for invoice in invoices:
            key = f"{invoice.get('invoice_code', '')}/{invoice.get('invoice_no', '')}".strip("/")
            if key:
                invoice_keys[key] = invoice_keys.get(key, 0) + 1
        duplicates = [key for key, count in invoice_keys.items() if count > 1]
        if duplicates:
            issues.append({
                "level": "错误",
                "code": "DUPLICATE_INVOICE",
                "message": f"存在重复发票：{', '.join(duplicates[:8])}",
                "count": len(duplicates),
            })
        red_without_original = [
            invoice for invoice in invoices
            if invoice.get("document_type") == "红字发票"
            and not str(invoice.get("original_invoice_no", "")).strip()
        ]
        if red_without_original:
            issues.append({
                "level": "警告",
                "code": "RED_INVOICE_REFERENCE",
                "message": "存在未填写原蓝字发票号码的红字发票，请补充后再申报",
                "count": len(red_without_original),
            })
        unreconciled_tax = [
            invoice for invoice in invoices
            if abs(
                _money(invoice.get("amount")) + _money(invoice.get("tax_amount"))
                - _money(invoice.get("total_amount"))
            ) >= 0.01
        ]
        if unreconciled_tax:
            issues.append({
                "level": "错误",
                "code": "PRICE_TAX_MISMATCH",
                "message": "存在价款、税额与价税合计不一致的发票记录",
                "count": len(unreconciled_tax),
            })

        if settings["tax"].get("invoice_required"):
            missing_invoice = [
                record for record in vouchers
                if record.get("debit", 0) > 0
                and any(word in str(record.get("subject", "")) for word in ("费用", "成本", "支出", "费"))
                and not record.get("invoice_no")
                and record.get("source") not in ("payroll", "depreciation", "period_close")
            ]
            if missing_invoice:
                issues.append({
                    "level": "警告",
                    "code": "MISSING_INVOICE",
                    "message": "部分成本费用凭证未关联发票或合规税前扣除凭证",
                    "count": len(missing_invoice),
                })

        if period:
            opening = self.opening_balances_for_period(period)
            if opening:
                opening_debit = sum(_money(row.get("debit_balance")) for row in opening)
                opening_credit = sum(_money(row.get("credit_balance")) for row in opening)
                if abs(opening_debit - opening_credit) >= 0.01:
                    issues.append({
                        "level": "错误",
                        "code": "OPENING_UNBALANCED",
                        "message": (
                            f"期初余额借贷不平衡：借方{opening_debit:.2f}，"
                            f"贷方{opening_credit:.2f}"
                        ),
                        "count": len(opening),
                    })

            unmatched_bank = [
                row for row in self.list_bank_transactions(period)
                if not row.get("voucher_no")
            ]
            if unmatched_bank:
                issues.append({
                    "level": "警告",
                    "code": "BANK_UNRECONCILED",
                    "message": "本期存在尚未匹配凭证的银行流水",
                    "count": len(unmatched_bank),
                })

            unposted_payroll = [
                row for row in self.list_payroll(period) if not row.get("voucher_no")
            ]
            if unposted_payroll:
                issues.append({
                    "level": "警告",
                    "code": "PAYROLL_UNPOSTED",
                    "message": "本期工资社保记录尚未生成计提凭证",
                    "count": len(unposted_payroll),
                })

            unposted_depreciation = [
                row for row in self.depreciation_schedule(period)
                if row.get("depreciation_amount", 0) > 0 and not row.get("posted")
            ]
            if unposted_depreciation:
                issues.append({
                    "level": "警告",
                    "code": "DEPRECIATION_UNPOSTED",
                    "message": "本期存在尚未生成凭证的固定资产折旧",
                    "count": len(unposted_depreciation),
                })

            if self.profile_key == "enterprise":
                cash_flow = self.cash_flow_summary(period)
                if cash_flow["needs_review_count"]:
                    issues.append({
                        "level": "警告",
                        "code": "CASH_FLOW_REVIEW",
                        "message": "本期存在需要人工确认的现金流量项目分类",
                        "count": cash_flow["needs_review_count"],
                    })
                cash_difference = max(
                    abs(cash_flow["month_cash_difference"]),
                    abs(cash_flow["ytd_cash_difference"]),
                )
                if cash_difference >= 0.01:
                    issues.append({
                        "level": "警告",
                        "code": "CASH_FLOW_TIE",
                        "message": (
                            "现金流量净额与货币资金变动未勾稽："
                            f"本月差额{cash_flow['month_cash_difference']:.2f}，"
                            f"本年差额{cash_flow['ytd_cash_difference']:.2f}"
                        ),
                        "count": 1,
                    })

                balances = self.account_balances(period)
                trial_difference = round(
                    sum(row["ending_debit"] for row in balances)
                    - sum(row["ending_credit"] for row in balances),
                    2,
                )
                if abs(trial_difference) >= 0.01:
                    issues.append({
                        "level": "错误",
                        "code": "TRIAL_BALANCE",
                        "message": f"科目期末借贷余额不平衡，差额{trial_difference:.2f}",
                        "count": 1,
                    })

        if not issues:
            issues.append({
                "level": "通过",
                "code": "OK",
                "message": "未发现阻断申报的基础数据问题",
                "count": 0,
            })
        return issues

    def month_end_checklist(self, period: str) -> Dict[str, Any]:
        """Return one shared close-readiness view for UI and exports."""
        _month_index(period)
        issues = self.validate(period)
        issue_codes = {row.get("code"): row for row in issues}
        vouchers = [
            row for row in self.list_vouchers() if row.get("period") == period
        ]
        bank_rows = self.list_bank_transactions(period)
        payroll_rows = self.list_payroll(period)
        depreciation_rows = [
            row for row in self.depreciation_schedule(period)
            if row.get("depreciation_amount", 0) > 0
        ]
        opening_rows = self.opening_balances_for_period(period)
        balances = self.account_balances(period)
        trial_difference = round(
            sum(row["ending_debit"] for row in balances)
            - sum(row["ending_credit"] for row in balances),
            2,
        )
        items: List[Dict[str, Any]] = []

        def add(name: str, status: str, detail: str, blocking: bool = False):
            items.append({
                "item": name,
                "status": status,
                "detail": detail,
                "blocking": bool(blocking),
            })

        if self.profile_key == "enterprise":
            company_issue = issue_codes.get("COMPANY_PROFILE")
            add(
                "企业资料",
                "待处理" if company_issue else "通过",
                company_issue["message"] if company_issue else "企业名称、信用代码和纳税人类型已填写",
                bool(company_issue),
            )
        else:
            add("企业资料", "不适用", "事业单位账套不执行企业资料检查")

        opening_issue = issue_codes.get("OPENING_UNBALANCED")
        if opening_issue:
            add("期初余额", "待处理", opening_issue["message"], True)
        elif opening_rows:
            source_period = str(opening_rows[0].get("period", ""))
            add("期初余额", "通过", f"采用 {source_period} 期初余额，借贷平衡")
        else:
            add("期初余额", "提示", "未录入期初余额；新设企业零期初时可忽略")

        voucher_issues = [
            issue_codes[code] for code in ("UNBALANCED", "UNPOSTED_VOUCHER")
            if code in issue_codes
        ]
        if voucher_issues:
            add(
                "记账凭证", "待处理",
                "；".join(issue["message"] for issue in voucher_issues), True,
            )
        elif vouchers:
            voucher_count = len({row.get("voucher_no") for row in vouchers})
            add("记账凭证", "通过", f"本期 {voucher_count} 张凭证借贷平衡")
        else:
            add("记账凭证", "提示", "本期尚无记账凭证")

        unmatched_bank = [row for row in bank_rows if not row.get("voucher_no")]
        if unmatched_bank:
            add("银行对账", "待处理", f"尚有 {len(unmatched_bank)} 条银行流水未匹配", True)
        elif bank_rows:
            add("银行对账", "通过", f"本期 {len(bank_rows)} 条银行流水已全部匹配")
        else:
            add("银行对账", "无需处理", "本期未导入银行流水")

        unposted_payroll = [row for row in payroll_rows if not row.get("voucher_no")]
        if unposted_payroll:
            add("工资社保", "待处理", f"尚有 {len(unposted_payroll)} 条工资记录未生成凭证", True)
        elif payroll_rows:
            add("工资社保", "通过", f"本期 {len(payroll_rows)} 条工资记录已入账")
        else:
            add("工资社保", "无需处理", "本期未录入工资社保")

        unposted_depreciation = [row for row in depreciation_rows if not row.get("posted")]
        if unposted_depreciation:
            add("固定资产折旧", "待处理", f"尚有 {len(unposted_depreciation)} 项折旧未生成凭证", True)
        elif depreciation_rows:
            add("固定资产折旧", "通过", f"本期 {len(depreciation_rows)} 项折旧已入账")
        else:
            add("固定资产折旧", "无需处理", "本期无应计提折旧")

        if self.profile_key == "enterprise":
            scope_issues = [
                issue_codes[code] for code in (
                    "UNSUPPORTED_TAX_SCOPE", "SMALL_PROFIT_QUALIFICATION",
                ) if code in issue_codes
            ]
            if scope_issues:
                add(
                    "税务资格与期间", "待处理",
                    "；".join(issue["message"] for issue in scope_issues), True,
                )
            else:
                tax_view = self.tax_summary(period)
                add(
                    "税务资格与期间", "通过",
                    f"增值税 {tax_view['period']['key']}，所得税 {tax_view['cit']['period']['key']}，支持范围校验通过",
                )
            accrual = self.tax_accrual_preview(period)
            if not accrual.get("can_post"):
                add("税费计提", "无需处理", "当前月份不是税务申报期末")
            elif accrual.get("posted"):
                add("税费计提", "通过", f"已生成凭证 {accrual['voucher_no']}")
            elif accrual.get("lines"):
                add("税费计提", "待处理", "申报期末尚未生成附加税费或所得税计提凭证", True)
            else:
                add("税费计提", "无需处理", "本期没有需要计提的税费")
        else:
            add("税务资格与期间", "不适用", "事业单位账套不使用企业税务引擎")
            add("税费计提", "不适用", "事业单位账套使用独立税务流程")

        if self.profile_key == "enterprise":
            cash_flow = self.cash_flow_summary(period)
            cash_issues = [
                issue_codes[code] for code in ("CASH_FLOW_REVIEW", "CASH_FLOW_TIE")
                if code in issue_codes
            ]
            if cash_issues:
                add(
                    "现金流量分类",
                    "待处理",
                    "；".join(issue["message"] for issue in cash_issues),
                    True,
                )
            else:
                add(
                    "现金流量分类",
                    "通过",
                    f"本月现金净变动 {cash_flow['month_net_change']:.2f}，与账面勾稽",
                )
        else:
            add("现金流量分类", "不适用", "事业单位账套使用独立报表口径")

        if self.profile_key == "enterprise":
            close_preview = self.profit_close_preview(period)
            if close_preview["posted"]:
                add(
                    "损益结转", "通过",
                    f"已生成凭证 {close_preview['voucher_no']}，净利润 {close_preview['net_profit']:.2f}",
                )
            elif close_preview["lines"]:
                add(
                    "损益结转", "待处理",
                    f"待结转净利润 {close_preview['net_profit']:.2f}", True,
                )
            else:
                add("损益结转", "无需处理", "本期没有待结转的损益余额")
        else:
            add("损益结转", "不适用", "事业单位账套使用独立结转流程")

        add(
            "试算平衡与报表勾稽",
            "通过" if abs(trial_difference) < 0.01 else "待处理",
            (
                "期末借贷余额平衡"
                if abs(trial_difference) < 0.01
                else f"期末借贷余额差额 {trial_difference:.2f}"
            ),
            abs(trial_difference) >= 0.01,
        )
        blocking_count = sum(1 for item in items if item["blocking"])
        return {
            "period": period,
            "items": items,
            "blocking_count": blocking_count,
            "ready": blocking_count == 0,
        }

    def list_backups(self) -> List[Dict[str, Any]]:
        backups = []
        for path in sorted(self.backup_dir.glob("*.zip"), reverse=True):
            try:
                with zipfile.ZipFile(path) as archive:
                    manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
                manifest["path"] = str(path)
                manifest["size"] = path.stat().st_size
                backups.append(manifest)
            except (OSError, KeyError, zipfile.BadZipFile, json.JSONDecodeError):
                backups.append({
                    "name": path.stem,
                    "created_at": "损坏或未知",
                    "path": str(path),
                    "size": path.stat().st_size,
                })
        return backups

    def integrity_check(self) -> Dict[str, Any]:
        """Run SQLite integrity_check and validate every mirrored JSON document."""
        problems = []
        sqlite_result = []
        journal_mode = "unknown"
        with self._lock:
            try:
                sqlite_result = self._database_integrity(self._db)
                journal_mode = str(self._db.execute("PRAGMA journal_mode").fetchone()[0]).lower()
                if sqlite_result != ["ok"]:
                    problems.append("SQLite完整性检查未通过：" + "；".join(sqlite_result[:5]))
                if journal_mode != "wal":
                    problems.append(f"SQLite日志模式异常：{journal_mode}")
                rows = self._db.execute(
                    "SELECT name, payload, checksum FROM documents ORDER BY name"
                ).fetchall()
                for name, payload_text, expected in rows:
                    actual = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
                    if actual != expected:
                        problems.append(f"数据校验码不一致：{name}")
                    try:
                        json.loads(payload_text)
                    except json.JSONDecodeError:
                        problems.append(f"SQLite数据无法解析：{name}")
            except (sqlite3.Error, AttributeError) as exc:
                problems.append(f"SQLite无法读取：{exc}")

            for name in self.DATA_FILES:
                path = self.data_dir / name
                if not path.exists():
                    problems.append(f"数据镜像缺失：{name}")
                    continue
                try:
                    with open(path, encoding="utf-8") as handle:
                        json.load(handle)
                except (OSError, json.JSONDecodeError):
                    problems.append(f"数据镜像损坏：{name}")
        return {
            "ok": not problems,
            "sqlite": sqlite_result,
            "journal_mode": journal_mode,
            "problems": problems,
        }

    def repair_data(self) -> Dict[str, Any]:
        """Repair JSON mirrors from SQLite, or rebuild SQLite from readable mirrors."""
        repaired = []
        with self._lock:
            try:
                database_ok = self._database_integrity(self._db) == ["ok"]
            except (sqlite3.Error, AttributeError):
                database_ok = False
            if not database_ok:
                self._quarantine_database(sqlite3.DatabaseError("integrity_check失败"))
                self._initialize_database()
                repaired.append("已从JSON镜像重建SQLite账套")

            for name in self.DATA_FILES:
                payload = self._read_database_document(name)
                if payload is None:
                    path = self.data_dir / name
                    try:
                        with open(path, encoding="utf-8") as handle:
                            payload = json.load(handle)
                    except (OSError, json.JSONDecodeError):
                        continue
                    self._write_database_document(name, payload)
                self._write_json_mirror(self.data_dir / name, payload)
                repaired.append(f"已修复{name}")
        result = self.integrity_check()
        result["repaired"] = repaired
        if not result["ok"]:
            raise ValueError("自动修复后仍有问题：" + "；".join(result["problems"][:5]))
        return result

    def startup_safety_check(self, keep: int = 5) -> Dict[str, Any]:
        integrity = self.integrity_check()
        if not integrity["ok"]:
            integrity = self.repair_data()
        backup = None
        if self.get_settings().get("accounting", {}).get("auto_backup", True):
            backup = self.create_backup("启动自动备份", kind="auto_startup")
            self._prune_startup_backups(keep)
        return {
            "integrity": integrity,
            "backup": str(backup) if backup else "",
            "recovery_notice": self.recovery_notice,
        }

    def _prune_startup_backups(self, keep: int):
        automatic = [
            row for row in self.list_backups()
            if row.get("kind") == "auto_startup" and row.get("files")
        ]
        for row in automatic[max(1, int(keep)):]:
            try:
                Path(row["path"]).unlink(missing_ok=True)
            except OSError:
                pass

    def create_backup(self, label: str = "手工备份", kind: str = "manual") -> Path:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safe_label = "".join(char for char in label if char not in '<>:"/\\|?*').strip()
        path = self.backup_dir / f"{timestamp}-{safe_label or '备份'}.zip"
        counter = 2
        while path.exists():
            path = self.backup_dir / f"{timestamp}-{safe_label or '备份'}-{counter}.zip"
            counter += 1
        files = [self.data_dir / name for name in self.DATA_FILES if (self.data_dir / name).exists()]
        manifest = {
            "name": safe_label or "备份",
            "created_at": _now(),
            "profile_key": self.profile_key,
            "profile_label": self.profile_label,
            "kind": kind,
            "storage": "sqlite_wal_with_json_mirror",
            "files": {},
        }
        with self._lock, tempfile.TemporaryDirectory() as temp_dir:
            snapshot_path = Path(temp_dir) / self.DATABASE_FILE
            snapshot = sqlite3.connect(str(snapshot_path))
            try:
                self._db.backup(snapshot)
            finally:
                snapshot.close()
            database_content = snapshot_path.read_bytes()
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(self.DATABASE_FILE, database_content)
                manifest["files"][self.DATABASE_FILE] = hashlib.sha256(
                    database_content
                ).hexdigest()
                for file_path in files:
                    content = file_path.read_bytes()
                    archive.writestr(file_path.name, content)
                    manifest["files"][file_path.name] = hashlib.sha256(content).hexdigest()
                archive.writestr(
                    "manifest.json",
                    json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
                )
        return path

    def validate_backup(self, path: Path) -> Dict[str, Any]:
        path = Path(path)
        with zipfile.ZipFile(path) as archive:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            if manifest.get("profile_key") != self.profile_key:
                raise ValueError("备份账套方向与当前账套不一致")
            allowed = set(self.DATA_FILES) | {self.DATABASE_FILE}
            for name, expected in manifest.get("files", {}).items():
                if name not in allowed:
                    raise ValueError(f"备份包含未知文件：{name}")
                content = archive.read(name)
                if hashlib.sha256(content).hexdigest() != expected:
                    raise ValueError(f"备份校验失败：{name}")
                if name != self.DATABASE_FILE:
                    json.loads(content.decode("utf-8"))
            if self.DATABASE_FILE in manifest.get("files", {}):
                with tempfile.TemporaryDirectory() as temp_dir:
                    database = Path(temp_dir) / self.DATABASE_FILE
                    database.write_bytes(archive.read(self.DATABASE_FILE))
                    connection = sqlite3.connect(str(database))
                    try:
                        if self._database_integrity(connection) != ["ok"]:
                            raise ValueError("备份中的SQLite账套完整性检查未通过")
                    finally:
                        connection.close()
            return manifest

    def restore_backup(self, path: Path):
        self.validate_backup(path)
        self.create_backup("恢复前自动备份")
        with zipfile.ZipFile(path) as archive:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            if self.DATABASE_FILE in manifest.get("files", {}):
                restored = self.database_path.with_name(".accounting.db.restore")
                restored.write_bytes(archive.read(self.DATABASE_FILE))
                with self._lock:
                    self.close()
                    for suffix in ("-wal", "-shm"):
                        Path(f"{self.database_path}{suffix}").unlink(missing_ok=True)
                    restored.replace(self.database_path)
                    self._initialize_database()
            for name in manifest.get("files", {}):
                if name == self.DATABASE_FILE:
                    continue
                target = self.data_dir / name
                payload = json.loads(archive.read(name).decode("utf-8"))
                self._write_json(target, payload)
        integrity = self.integrity_check()
        if not integrity["ok"]:
            raise ValueError("恢复后的账套未通过完整性检查")

    def import_backup(self, source: Path) -> Path:
        source = Path(source)
        self.validate_backup(source)
        target = self.backup_dir / source.name
        if target.resolve() != source.resolve():
            stem = target.stem
            counter = 2
            while target.exists():
                target = self.backup_dir / f"{stem}-{counter}.zip"
                counter += 1
            shutil.copy2(source, target)
        return target

    def delete_backup(self, path: Path):
        target = Path(path).resolve()
        if target.parent != self.backup_dir.resolve():
            raise ValueError("只能删除当前账套的备份")
        target.unlink(missing_ok=True)

    def close(self):
        with self._lock:
            if self._db is None:
                return
            try:
                self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                self._db.close()
            finally:
                self._db = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
