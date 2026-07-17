#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
logger.py - 操作日志记录器
从AccountingDemo移植，记录所有操作用于审计
"""

import json
import os
from pathlib import Path
from datetime import datetime
from typing import Any, Optional


class AuditLogger:
    """审计日志记录器"""

    def __init__(self, log_path: Optional[Path] = None,
                 default_operator: Optional[str] = None):
        """
        Args:
            log_path: 日志文件路径，默认为程序目录下的operation_log.json
        """
        if log_path is None:
            log_path = Path(__file__).parent / "operation_log.json"

        self.log_path = Path(log_path)
        self.default_operator = default_operator

    def log(self, action: str, description: str,
            before: Optional[Any] = None,
            after: Optional[Any] = None,
            operator: Optional[str] = None) -> bool:
        """
        记录操作日志

        Args:
            action: 操作类型
            description: 操作描述
            before: 操作前状态（可选）
            after: 操作后状态（可选）
            operator: 操作员；未传入时使用当前登录账号

        Returns:
            是否记录成功
        """
        try:
            # 加载现有日志
            logs = []
            if self.log_path.exists():
                with open(self.log_path, encoding='utf-8') as f:
                    try:
                        logs = json.load(f)
                    except json.JSONDecodeError:
                        logs = []

            # 添加新日志
            entry = {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                "action": action,
                "description": description,
                "operator": operator or self.default_operator or "用户"
            }

            if before is not None:
                entry["before"] = self._sanitize_data(before)

            if after is not None:
                entry["after"] = self._sanitize_data(after)

            logs.append(entry)

            # 保存日志
            with open(self.log_path, 'w', encoding='utf-8') as f:
                json.dump(logs, f, ensure_ascii=False, indent=2)

            return True
        except Exception as e:
            print(f"Log failed: {e}")
            return False

    def _sanitize_data(self, data: Any) -> Any:
        """
        清理敏感数据

        Args:
            data: 待清理的数据

        Returns:
            清理后的数据
        """
        if isinstance(data, dict):
            sanitized = {}
            for key, value in data.items():
                # 过滤敏感字段
                if key.lower() in ["password", "pin", "code", "验证码"]:
                    sanitized[key] = "***"
                elif isinstance(value, (dict, list)):
                    sanitized[key] = self._sanitize_data(value)
                else:
                    sanitized[key] = value
            return sanitized
        elif isinstance(data, list):
            return [self._sanitize_data(item) for item in data]
        else:
            return data

    def query(self, action: Optional[str] = None,
              operator: Optional[str] = None,
              limit: int = 100) -> list:
        """
        查询操作日志

        Args:
            action: 操作类型过滤
            operator: 操作员过滤
            limit: 返回数量限制

        Returns:
            匹配的日志列表
        """
        try:
            if not self.log_path.exists():
                return []

            with open(self.log_path, encoding='utf-8') as f:
                logs = json.load(f)

            # 过滤
            if action:
                logs = [l for l in logs if l.get("action") == action]

            if operator:
                logs = [l for l in logs if l.get("operator") == operator]

            # 按时间倒序并限制数量
            logs = sorted(logs, key=lambda x: x["timestamp"], reverse=True)
            return logs[:limit]

        except Exception as e:
            print(f"Query failed: {e}")
            return []

    def export(self, export_path: Path,
               start_date: Optional[str] = None,
               end_date: Optional[str] = None) -> bool:
        """
        导出日志

        Args:
            export_path: 导出文件路径
            start_date: 开始日期（YYYY-MM-DD）
            end_date: 结束日期（YYYY-MM-DD）

        Returns:
            是否导出成功
        """
        try:
            all_logs = []

            if self.log_path.exists():
                with open(self.log_path, encoding='utf-8') as f:
                    all_logs = json.load(f)

            # 日期过滤
            if start_date or end_date:
                filtered = []
                for log in all_logs:
                    log_date = log["timestamp"][:10]  # YYYY-MM-DD

                    if start_date and log_date < start_date:
                        continue
                    if end_date and log_date > end_date:
                        continue

                    filtered.append(log)
                all_logs = filtered

            # 导出
            with open(export_path, 'w', encoding='utf-8') as f:
                json.dump(all_logs, f, ensure_ascii=False, indent=2)

            return True
        except Exception as e:
            print(f"Export failed: {e}")
            return False


# 全局日志实例
_logger = AuditLogger()


def configure(log_path: Path, operator: Optional[str] = None) -> AuditLogger:
    """Route audit events to the active accounting profile."""
    global _logger
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _logger = AuditLogger(path, default_operator=operator)
    return _logger


def log(action: str, description: str,
         before: Optional[Any] = None,
         after: Optional[Any] = None,
         operator: Optional[str] = None) -> bool:
    """
    便捷的日志记录函数

    Args:
        action: 操作类型
        description: 操作描述
        before: 操作前状态
        after: 操作后状态
        operator: 操作员

    Returns:
        是否记录成功
    """
    return _logger.log(action, description, before, after, operator)
