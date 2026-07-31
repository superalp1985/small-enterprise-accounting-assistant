#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Append-only audit logging with tamper-evident integrity checks."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


_LOCK = threading.RLock()


def _canonical_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(data: Any) -> str:
    return hashlib.sha256(_canonical_json(data).encode("utf-8")).hexdigest()


def _legacy_anchor(entries: list[dict[str, Any]]) -> str:
    """Anchor unsigned historical entries without rewriting them."""
    return _sha256({"legacy_entries": entries}) if entries else ""


class AuditLogger:
    """Write audit events and detect later changes to signed entries."""

    def __init__(
        self,
        log_path: Optional[Path] = None,
        default_operator: Optional[str] = None,
    ):
        if log_path is None:
            log_path = Path(__file__).parent / "operation_log.json"
        self.log_path = Path(log_path)
        self.default_operator = default_operator

    def _read_logs(self) -> list[dict[str, Any]]:
        if not self.log_path.exists():
            return []
        with self.log_path.open(encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
            raise ValueError("日志文件格式不正确")
        return data

    def _atomic_write(self, logs: list[dict[str, Any]]) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{self.log_path.name}.", suffix=".tmp", dir=self.log_path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(logs, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.log_path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    @staticmethod
    def _signed_hash(entry: dict[str, Any]) -> str:
        unsigned = {key: value for key, value in entry.items() if key != "entry_hash"}
        return _sha256(unsigned)

    @classmethod
    def verify_entries(cls, logs: list[dict[str, Any]]) -> dict[str, Any]:
        legacy_entries: list[dict[str, Any]] = []
        signed_count = 0
        previous_hash = ""
        signed_started = False

        for index, entry in enumerate(logs):
            is_signed = bool(entry.get("event_id") and entry.get("entry_hash"))
            if not is_signed:
                if signed_started:
                    return {
                        "status": "invalid",
                        "message": f"第 {index + 1} 条日志缺少签名，日志顺序可能被修改",
                        "legacy_count": len(legacy_entries),
                        "signed_count": signed_count,
                    }
                legacy_entries.append(entry)
                continue

            if not signed_started:
                signed_started = True
                previous_hash = _legacy_anchor(legacy_entries)

            if entry.get("previous_hash", "") != previous_hash:
                return {
                    "status": "invalid",
                    "message": f"第 {index + 1} 条日志的前序校验值不一致",
                    "legacy_count": len(legacy_entries),
                    "signed_count": signed_count,
                }
            expected = cls._signed_hash(entry)
            if entry.get("entry_hash") != expected:
                return {
                    "status": "invalid",
                    "message": f"第 {index + 1} 条日志内容校验失败",
                    "legacy_count": len(legacy_entries),
                    "signed_count": signed_count,
                }
            previous_hash = expected
            signed_count += 1

        if signed_count and legacy_entries:
            status = "mixed"
            message = f"新日志校验正常；{len(legacy_entries)} 条历史日志生成于签名功能启用前"
        elif signed_count:
            status = "valid"
            message = f"完整性校验正常，共 {signed_count} 条签名日志"
        elif legacy_entries:
            status = "legacy"
            message = f"共 {len(legacy_entries)} 条历史日志，生成于签名功能启用前"
        else:
            status = "empty"
            message = "暂无操作日志"
        return {
            "status": status,
            "message": message,
            "legacy_count": len(legacy_entries),
            "signed_count": signed_count,
        }

    def verify_integrity(self) -> dict[str, Any]:
        with _LOCK:
            try:
                return self.verify_entries(self._read_logs())
            except Exception as exc:
                return {
                    "status": "invalid",
                    "message": f"日志文件无法读取：{exc}",
                    "legacy_count": 0,
                    "signed_count": 0,
                }

    def log(
        self,
        action: str,
        description: str,
        before: Optional[Any] = None,
        after: Optional[Any] = None,
        operator: Optional[str] = None,
    ) -> bool:
        """Append one signed event. Existing signed history must validate first."""
        try:
            with _LOCK:
                logs = self._read_logs()
                verification = self.verify_entries(logs)
                if verification["status"] == "invalid":
                    raise ValueError(verification["message"])

                signed_entries = [row for row in logs if row.get("entry_hash")]
                previous_hash = (
                    signed_entries[-1]["entry_hash"]
                    if signed_entries
                    else _legacy_anchor(logs)
                )
                entry: dict[str, Any] = {
                    "event_id": str(uuid.uuid4()),
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                    "action": str(action),
                    "description": str(description),
                    "operator": operator or self.default_operator or "用户",
                    "previous_hash": previous_hash,
                }
                if before is not None:
                    entry["before"] = self._sanitize_data(before)
                if after is not None:
                    entry["after"] = self._sanitize_data(after)
                entry["entry_hash"] = self._signed_hash(entry)
                logs.append(entry)
                self._atomic_write(logs)
            return True
        except Exception as exc:
            print(f"Log failed: {exc!r}")
            return False

    def _sanitize_data(self, data: Any) -> Any:
        if isinstance(data, dict):
            sanitized = {}
            for key, value in data.items():
                if str(key).lower() in {"password", "pin", "code", "验证码"}:
                    sanitized[key] = "***"
                elif isinstance(value, (dict, list, tuple)):
                    sanitized[key] = self._sanitize_data(value)
                else:
                    sanitized[key] = value
            return sanitized
        if isinstance(data, (list, tuple)):
            return [self._sanitize_data(item) for item in data]
        if isinstance(data, Path):
            return str(data)
        return data

    def query(
        self,
        action: Optional[str] = None,
        operator: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        try:
            with _LOCK:
                logs = self._read_logs()
            if action:
                logs = [row for row in logs if row.get("action") == action]
            if operator:
                logs = [row for row in logs if row.get("operator") == operator]
            logs.sort(key=lambda row: row.get("timestamp", ""), reverse=True)
            return logs[:limit]
        except Exception as exc:
            print(f"Query failed: {exc}")
            return []

    def export(
        self,
        export_path: Path,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> bool:
        try:
            with _LOCK:
                logs = self._read_logs()
            if start_date or end_date:
                logs = [
                    row for row in logs
                    if (not start_date or row.get("timestamp", "")[:10] >= start_date)
                    and (not end_date or row.get("timestamp", "")[:10] <= end_date)
                ]
            Path(export_path).write_text(
                json.dumps(logs, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return True
        except Exception as exc:
            print(f"Export failed: {exc}")
            return False


_logger = AuditLogger()


def configure(log_path: Path, operator: Optional[str] = None) -> AuditLogger:
    global _logger
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _logger = AuditLogger(path, default_operator=operator)
    return _logger


def log(
    action: str,
    description: str,
    before: Optional[Any] = None,
    after: Optional[Any] = None,
    operator: Optional[str] = None,
) -> bool:
    return _logger.log(action, description, before, after, operator)


def verify_integrity(log_path: Optional[Path] = None) -> dict[str, Any]:
    return (_logger if log_path is None else AuditLogger(log_path)).verify_integrity()
