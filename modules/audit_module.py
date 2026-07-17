#!/usr/bin/env python3
import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
# -*- coding: utf-8 -*-
"""
audit_module.py - 审核管理模块
提供操作日志查看、凭证审核、权限管理等功能
"""

import json
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog, simpledialog
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional
import os
from modules.vocabulary_module import load_vocab


BG = "#F0F0F0"
BLUE = "#0078D4"
DARK = "#003087"
WHITE = "#FFFFFF"
GREEN = "#107C10"
RED = "#D83B01"
YELLOW = "#FFF4CE"
GRAY = "#D0D0D0"
ORANGE = "#E67E22"

FONT = ("微软雅黑", 10)
FONT_B = ("微软雅黑", 10, "bold")
FONT_T = ("微软雅黑", 14, "bold")
FONT_S = ("微软雅黑", 9)


def make_btn(parent, text, cmd, color=BLUE, width=12):
    return tk.Button(parent, text=text, command=cmd,
                     bg=color, fg=WHITE, font=FONT_B,
                     relief="flat", padx=8, pady=4,
                     activebackground=DARK, activeforeground=WHITE,
                     cursor="hand2", width=width)


class AuditModule(tk.Frame):
    """审核管理模块"""

    def __init__(self, parent, config, authenticated=False):
        super().__init__(parent, bg=BG)
        self.parent = parent
        self.config = config
        self.authenticated = authenticated

        # 加载操作日志
        self.logs = self._load_logs()

        self._build_ui()
        self._refresh_log_list()

    def _load_logs(self) -> List[Dict]:
        """加载操作日志"""
        log_path = self.config.audit_log_path
        if log_path.exists():
            with open(log_path, encoding='utf-8') as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        return []

    def _build_ui(self):
        """构建UI"""
        f = tk.LabelFrame(self, text=" 审核管理模式 ", font=FONT_T,
                          bg=BG, fg=DARK, bd=1, relief="groove")
        f.pack(fill="both", expand=True, pady=6)

        # 工具栏
        tool = tk.Frame(f, bg=BG, pady=6)
        tool.pack(fill="x", padx=12)

        self.status_var = tk.StringVar(value=f"共 {len(self.logs)} 条日志")
        tk.Label(tool, textvariable=self.status_var, font=FONT_S, bg=BG,
                 fg="#666").pack(side="left")

        # 操作按钮
        btn_row = tk.Frame(f, bg=BG)
        btn_row.pack(fill="x", padx=12, pady=6)

        make_btn(btn_row, "刷新日志", self._refresh, width=10).pack(side="left", padx=4)
        make_btn(btn_row, "导出日志", self._export, width=10).pack(side="left", padx=4)
        make_btn(btn_row, "清空日志", self._clear_logs, color=RED, width=10).pack(side="left", padx=4)

        # 分隔
        tk.Frame(btn_row, bg=BG, width=20).pack(side="left")

        self.filter_var = tk.StringVar(value="全部")
        ttk.Combobox(btn_row, textvariable=self.filter_var,
                    values=["全部", "手工入账", "批量导入", "删除凭证", "账户登录"],
                    width=12, state="readonly").pack(side="left", padx=4)
        self.filter_var.trace_add("write", self._on_filter_change)

        # 搜索框
        search_frame = tk.Frame(f, bg=BG)
        search_frame.pack(fill="x", padx=12, pady=(0, 6))
        tk.Label(search_frame, text="搜索：", font=FONT_B, bg=BG).pack(side="left")
        self.search_var = tk.StringVar()
        search_entry = tk.Entry(search_frame, textvariable=self.search_var,
                                font=FONT, width=30, relief="solid", bd=1)
        search_entry.pack(side="left", padx=6)
        search_entry.bind("<KeyRelease>", self._on_search)

        # 分区显示
        split_frame = tk.Frame(f, bg=BG)
        split_frame.pack(fill="both", expand=True, padx=12, pady=6)

        # 左侧：日志列表
        left_frame = tk.LabelFrame(split_frame, text=" 操作日志列表 ", font=FONT_B,
                                    bg=BG, fg=DARK, bd=1, relief="groove")
        left_frame.pack(side="left", fill="both", expand=True, padx=(0, 6))

        cols = ("时间", "操作", "详情", "操作员", "状态")
        self.tree = ttk.Treeview(left_frame, columns=cols, show="headings", height=20)

        self.tree.heading("时间", text="时间")
        self.tree.column("时间", width=100, anchor="center")
        self.tree.heading("操作", text="操作类型")
        self.tree.column("操作", width=100, anchor="center")
        self.tree.heading("详情", text="操作详情")
        self.tree.column("详情", width=200, anchor="w")
        self.tree.heading("操作员", text="操作员")
        self.tree.column("操作员", width=100, anchor="center")
        self.tree.heading("状态", text="状态")
        self.tree.column("状态", width=80, anchor="center")

        sb = ttk.Scrollbar(left_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self.tree.bind("<ButtonRelease-1>", self._on_tree_click)
        self.tree.bind("<Double-1>", self._on_tree_double_click)

        # 右侧：详情显示
        right_frame = tk.LabelFrame(split_frame, text=" 日志详情 ", font=FONT_B,
                                     bg=BG, fg=DARK, bd=1, relief="groove")
        right_frame.pack(side="right", fill="both", expand=True, padx=(6, 0), ipadx=6)

        self._build_detail_ui(right_frame)

        # 统计区
        stats_frame = tk.Frame(f, bg=YELLOW, relief="solid", bd=1)
        stats_frame.pack(fill="x", padx=12, pady=6)

        self.stats_var = tk.StringVar(value="统计信息加载中...")
        tk.Label(stats_frame, textvariable=self.stats_var, font=FONT_B, bg=YELLOW,
                 fg="#333").pack(padx=12, pady=6)

        self._update_stats()

    def _build_detail_ui(self, parent):
        """构建详情显示UI"""
        # 时间戳
        self.detail_time_var = tk.StringVar(value="")
        tk.Label(parent, text="时间：", font=FONT_B, bg=BG).pack(anchor="w", padx=8)
        tk.Label(parent, textvariable=self.detail_time_var, font=FONT_S, bg=BG,
                 fg="#666").pack(anchor="w", padx=24, pady=(0, 6))

        # 操作类型
        self.detail_op_var = tk.StringVar(value="")
        tk.Label(parent, text="操作类型：", font=FONT_B, bg=BG).pack(anchor="w", padx=8)
        tk.Label(parent, textvariable=self.detail_op_var, font=FONT_S, bg=BG,
                 fg="#666").pack(anchor="w", padx=24, pady=(0, 6))

        # 操作员
        self.detail_operator_var = tk.StringVar(value="")
        tk.Label(parent, text="操作员：", font=FONT_B, bg=BG).pack(anchor="w", padx=8)
        tk.Label(parent, textvariable=self.detail_operator_var, font=FONT_S, bg=BG,
                 fg="#666").pack(anchor="w", padx=24, pady=(0, 6))

        # 操作前
        tk.Label(parent, text="操作前状态：", font=FONT_B, bg=BG).pack(anchor="w", padx=8)
        self.before_text = scrolledtext.ScrolledText(parent, font=FONT_S, width=35, height=6,
                                                    wrap="word", relief="solid", bd=1, bg=WHITE)
        self.before_text.pack(fill="x", padx=8, pady=(0, 6))
        self.before_text.configure(state="disabled")

        # 操作后
        tk.Label(parent, text="操作后状态：", font=FONT_B, bg=BG).pack(anchor="w", padx=8)
        self.after_text = scrolledtext.ScrolledText(parent, font=FONT_S, width=35, height=6,
                                                   wrap="word", relief="solid", bd=1, bg=WHITE)
        self.after_text.pack(fill="x", padx=8, pady=(0, 6))
        self.after_text.configure(state="disabled")

        # 备注
        tk.Label(parent, text="备注：", font=FONT_B, bg=BG).pack(anchor="w", padx=8)
        self.remark_var = tk.StringVar(value="")
        tk.Label(parent, textvariable=self.remark_var, font=FONT_S, bg=BG,
                 fg="#666", wraplength=300).pack(anchor="w", padx=24, pady=(0, 8))

        # 审核按钮
        if self.authenticated:
            btn_row = tk.Frame(parent, bg=BG)
            btn_row.pack(fill="x", padx=8, pady=(8, 12))

            make_btn(btn_row, "通过审核", self._approve, color=GREEN, width=10).pack(side="left", padx=4)
            make_btn(btn_row, "退回", self._reject, color=ORANGE, width=10).pack(side="left", padx=4)
            make_btn(btn_row, "备注", self._add_remark, width=10).pack(side="left", padx=4)
        else:
            tk.Label(parent, text="请先登录账号再进行审核操作", font=FONT_S,
                     bg=BG, fg="#888").pack(pady=(8, 12))

    def _refresh_log_list(self):
        """刷新日志列表"""
        for item in self.tree.get_children():
            self.tree.delete(item)

        filter_text = self.search_var.get().lower()
        filter_op = self.filter_var.get()

        for log in self.logs:
            # 类型过滤
            if filter_op != "全部" and log.get("operation", "") != filter_op:
                continue

            # 搜索过滤
            if filter_text:
                details = str(log.get("details", "")) + " " + str(log.get("operation", ""))
                if filter_text not in details.lower():
                    continue

            self.tree.insert("", tk.END, values=(
                log.get("timestamp", ""),
                log.get("operation", ""),
                str(log.get("details", ""))[:30],
                log.get("operator", ""),
                log.get("status", "")
            ))

        self.status_var.set(f"共 {len(self.tree.get_children())} 条日志")

    def _update_stats(self):
        """更新统计信息"""
        total = len(self.logs)
        operations = {}
        for log in self.logs:
            op = log.get("operation", "未知")
            operations[op] = operations.get(op, 0) + 1

        stats = f"总日志：{total}  |  "
        stats += "  ".join([f"{k}：{v}" for k, v in sorted(operations.items())[:4]])

        self.stats_var.set(stats)

    def _on_tree_click(self, event):
        """日志列表点击"""
        item = self.tree.identify_row(event.y)
        if not item:
            return

        items = self.tree.get_children()
        idx = items.index(item)
        self._load_log_detail(idx)

    def _on_tree_double_click(self, event):
        """双击显示完整详情"""
        self._show_full_detail()

    def _load_log_detail(self, idx: int):
        """加载日志详情"""
        # 根据显示顺序找到对应的日志
        filter_op = self.filter_var.get()
        filter_text = self.search_var.get().lower()

        filtered_logs = []
        for log in self.logs:
            if filter_op != "全部" and log.get("operation", "") != filter_op:
                continue
            if filter_text:
                details = str(log.get("details", "")) + " " + str(log.get("operation", ""))
                if filter_text not in details.lower():
                    continue
            filtered_logs.append(log)

        if 0 <= idx < len(filtered_logs):
            log = filtered_logs[idx]
            self.current_log = log

            self.detail_time_var.set(log.get("timestamp", ""))
            self.detail_op_var.set(log.get("operation", ""))
            self.detail_operator_var.set(log.get("operator", "系统"))
            self.remark_var.set(log.get("remark", ""))

            self.before_text.configure(state="normal")
            self.before_text.delete("1.0", "end")
            self.before_text.insert("1.0", json.dumps(log.get("before", {}), ensure_ascii=False, indent=2))
            self.before_text.configure(state="disabled")

            self.after_text.configure(state="normal")
            self.after_text.delete("1.0", "end")
            self.after_text.insert("1.0", json.dumps(log.get("after", {}), ensure_ascii=False, indent=2))
            self.after_text.configure(state="disabled")

    def _show_full_detail(self):
        """显示完整详情对话框"""
        if not hasattr(self, "current_log"):
            return

        d = tk.Toplevel(self)
        d.title("完整日志详情")
        d.configure(bg=BG)
        d.geometry("900x650")

        log = self.current_log
        details = log.get('details', {})

        # 查找法律依据
        law_text = "无"
        subject = ""
        if isinstance(details, dict):
            subject = details.get('科目', '')
            if subject:
                # 加载词库查找法律依据
                subjects = load_vocab(
                    self.config.vocab_path,
                    getattr(self.config, "account_catalog_path", None),
                )
                record = next(
                    (item for item in subjects if item.get("subject", "") == subject), None
                )
                if record:
                    law_text = record.get("law", "无")

        # 内容区域
        content_frame = tk.Frame(d, bg=BG)
        content_frame.pack(fill="both", expand=True, padx=12, pady=12)

        # 使用ScrolledText
        text = scrolledtext.ScrolledText(content_frame, font=FONT_S, wrap="word")
        text.pack(fill="both", expand=True)

        # 构建内容
        content_lines = []
        content_lines.append("=" * 60)
        content_lines.append("操作日志详情")
        content_lines.append("=" * 60)
        content_lines.append(f"\n时间：{log.get('timestamp', '')}")
        content_lines.append(f"操作：{log.get('operation', '')}")
        content_lines.append(f"操作员：{log.get('operator', '')}")
        content_lines.append(f"状态：{log.get('status', '')}")
        content_lines.append("\n" + "-" * 40)

        # 详细信息
        if isinstance(details, dict):
            content_lines.append("\n操作详情：")
            content_lines.append(json.dumps(details, ensure_ascii=False, indent=2))

        # 法律依据
        content_lines.append("\n" + "-" * 40)
        content_lines.append(f"\n相关科目：{subject}")
        content_lines.append(f"\n法律依据：")
        content_lines.append(law_text)

        content = "\n".join(content_lines)
        text.insert("1.0", content)
        text.configure(state="disabled")

        # 底部提示
        note_frame = tk.Frame(d, bg=BG)
        note_frame.pack(fill="x", padx=12, pady=(0, 12))

        tk.Label(note_frame, text="💡 双击日志列表中的条目可查看完整详情", font=FONT_S,
                 bg=BG, fg="#999").pack(side="left")

        make_btn(d, "关闭", d.destroy, width=10).pack(side="right")

    def _on_filter_change(self, *args):
        """过滤器变化"""
        self._refresh_log_list()

    def _on_search(self, event):
        """搜索事件"""
        self._refresh_log_list()

    def _refresh(self):
        """刷新日志"""
        self.logs = self._load_logs()
        self._refresh_log_list()
        self._update_stats()
        messagebox.showinfo("刷新完成", f"已加载 {len(self.logs)} 条日志")

    def _export(self):
        """导出日志"""
        log_path = self.config.audit_log_path
        if log_path.exists():
            export_path = filedialog.asksaveasfilename(
                title="导出日志",
                defaultextension=".json",
                filetypes=[
                    ("JSON文件", "*.json"),
                    ("文本文件", "*.txt"),
                    ("所有文件", "*.*")
                ]
            )
            if export_path:
                if export_path.endswith(".txt"):
                    # 导出为文本
                    with open(export_path, 'w', encoding='utf-8') as f:
                        f.write("操作日志导出\n")
                        f.write("=" * 60 + "\n\n")
                        for log in self.logs:
                            f.write(f"时间：{log.get('timestamp', '')}\n")
                            f.write(f"操作：{log.get('operation', '')}\n")
                            f.write(f"详情：{log.get('details', '')}\n")
                            f.write(f"操作员：{log.get('operator', '')}\n")
                            f.write("-" * 40 + "\n")
                else:
                    # 导出为JSON
                    with open(export_path, 'w', encoding='utf-8') as f:
                        json.dump(self.logs, f, ensure_ascii=False, indent=2)

                messagebox.showinfo("导出成功", f"日志已导出到：{export_path}")
        else:
            messagebox.showinfo("提示", "暂无日志可导出")

    def _clear_logs(self):
        """清空日志"""
        if messagebox.askyesno("确认清空", "确定清空所有操作日志？此操作不可恢复。"):
            log_path = self.config.audit_log_path
            if log_path.exists():
                log_path.unlink()
            self.logs.clear()
            self._refresh_log_list()
            self._update_stats()
            messagebox.showinfo("清空完成", "日志已清空")

    def _approve(self):
        """通过审核"""
        if not self.authenticated:
            messagebox.showwarning("提示", "请先登录账号再进行审核")
            return

        if not hasattr(self, "current_log"):
            return

        # 更新日志状态
        for log in self.logs:
            if log.get("timestamp") == self.current_log.get("timestamp"):
                log["status"] = "已审核"
                log["review_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                break

        self._save_logs()
        self._refresh_log_list()
        messagebox.showinfo("审核通过", "该操作已通过审核")

    def _reject(self):
        """退回"""
        if not self.authenticated:
            messagebox.showwarning("提示", "请先登录账号再进行审核")
            return

        if not hasattr(self, "current_log"):
            return

        reason = simpledialog.askstring("退回原因", "请输入退回原因：")
        if reason:
            for log in self.logs:
                if log.get("timestamp") == self.current_log.get("timestamp"):
                    log["status"] = "已退回"
                    log["reject_reason"] = reason
                    log["review_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    break

            self._save_logs()
            self._refresh_log_list()
            messagebox.showinfo("退回成功", "该操作已退回")

    def _add_remark(self):
        """添加备注"""
        if not self.authenticated:
            messagebox.showwarning("提示", "请先登录账号再添加备注")
            return

        if not hasattr(self, "current_log"):
            return

        remark = simpledialog.askstring("添加备注", "请输入备注：", initialvalue=self.current_log.get("remark", ""))
        if remark is not None:
            for log in self.logs:
                if log.get("timestamp") == self.current_log.get("timestamp"):
                    log["remark"] = remark
                    break

            self._save_logs()
            self.remark_var.set(remark)

    def _save_logs(self):
        """保存日志"""
        log_path = self.config.audit_log_path
        with open(log_path, 'w', encoding='utf-8') as f:
            json.dump(self.logs, f, ensure_ascii=False, indent=2)

    def pack_forget(self):
        """隐藏模块"""
        super().pack_forget()

    def pack(self, **kwargs):
        """显示模块"""
        super().pack(**kwargs)

    def set_authenticated(self, active: bool, operator: str = ""):
        """更新当前登录会话状态。"""
        self.authenticated = active
        # 重建详情UI以显示/隐藏审核按钮
        # 实际应用中可以用更优雅的方式处理
