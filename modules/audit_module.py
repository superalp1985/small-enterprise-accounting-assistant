#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only operation-log viewer."""

from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Any, Dict, List

import logger as L


BG = "#F0F0F0"
BLUE = "#0078D4"
DARK = "#003087"
WHITE = "#FFFFFF"
GREEN = "#107C10"
RED = "#D83B01"
YELLOW = "#FFF4CE"
FONT = ("微软雅黑", 10)
FONT_B = ("微软雅黑", 10, "bold")
FONT_T = ("微软雅黑", 14, "bold")
FONT_S = ("微软雅黑", 9)


def make_btn(parent, text, cmd, color=BLUE, width=12):
    return tk.Button(
        parent,
        text=text,
        command=cmd,
        bg=color,
        fg=WHITE,
        font=FONT_B,
        relief="flat",
        padx=8,
        pady=4,
        activebackground=DARK,
        activeforeground=WHITE,
        cursor="hand2",
        width=width,
    )


def _action(log: Dict[str, Any]) -> str:
    return str(log.get("action") or log.get("operation") or "未知操作")


def _description(log: Dict[str, Any]) -> str:
    value = log.get("description", log.get("details", ""))
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


class AuditModule(tk.Frame):
    """Display and export audit logs without any mutation controls."""

    def __init__(self, parent, config, authenticated=False):
        super().__init__(parent, bg=BG)
        self.parent = parent
        self.config = config
        self.authenticated = authenticated
        self.logs: List[Dict[str, Any]] = []
        self.visible_logs: List[Dict[str, Any]] = []
        self.current_log: Dict[str, Any] | None = None
        self._build_ui()
        self._refresh(show_message=False)

    def _load_logs(self) -> List[Dict[str, Any]]:
        path = Path(self.config.audit_log_path)
        if not path.exists():
            return []
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, list):
            raise ValueError("日志文件不是有效的日志列表")
        return [row for row in data if isinstance(row, dict)]

    def _build_ui(self):
        frame = tk.LabelFrame(
            self,
            text=" 操作日志（只读） ",
            font=FONT_T,
            bg=BG,
            fg=DARK,
            bd=1,
            relief="groove",
        )
        frame.pack(fill="both", expand=True, pady=6)

        toolbar = tk.Frame(frame, bg=BG, pady=6)
        toolbar.pack(fill="x", padx=12)
        make_btn(toolbar, "刷新日志", self._refresh, width=10).pack(side="left", padx=4)
        make_btn(toolbar, "导出日志", self._export, width=10).pack(side="left", padx=4)

        tk.Label(toolbar, text="类型：", font=FONT_S, bg=BG).pack(side="left", padx=(18, 4))
        self.filter_var = tk.StringVar(value="全部")
        self.filter_combo = ttk.Combobox(
            toolbar,
            textvariable=self.filter_var,
            values=["全部"],
            width=18,
            state="readonly",
            font=FONT,
        )
        self.filter_combo.pack(side="left", padx=4)
        self.filter_combo.bind("<<ComboboxSelected>>", lambda _event: self._refresh_log_list())

        tk.Label(toolbar, text="搜索：", font=FONT_S, bg=BG).pack(side="left", padx=(18, 4))
        self.search_var = tk.StringVar()
        search = tk.Entry(toolbar, textvariable=self.search_var, width=28, font=FONT)
        search.pack(side="left", padx=4)
        search.bind("<KeyRelease>", lambda _event: self._refresh_log_list())

        self.status_var = tk.StringVar(value="正在加载日志...")
        tk.Label(toolbar, textvariable=self.status_var, font=FONT_S, bg=BG, fg="#666").pack(
            side="right", padx=4
        )

        self.integrity_var = tk.StringVar(value="完整性校验中...")
        self.integrity_label = tk.Label(
            frame,
            textvariable=self.integrity_var,
            font=FONT_B,
            bg=YELLOW,
            fg="#6A5200",
            anchor="w",
            padx=10,
            pady=6,
        )
        self.integrity_label.pack(fill="x", padx=12, pady=(0, 6))

        tk.Label(
            frame,
            text="日志在软件内只能查看和导出；外部修改签名日志会触发完整性校验失败。",
            font=FONT_S,
            bg="#E8F3FC",
            fg=DARK,
            anchor="w",
            padx=10,
            pady=6,
        ).pack(fill="x", padx=12, pady=(0, 6))

        split = tk.PanedWindow(frame, orient="horizontal", sashwidth=6, bg=BG)
        split.pack(fill="both", expand=True, padx=12, pady=6)

        list_frame = tk.LabelFrame(split, text=" 日志列表 ", font=FONT_B, bg=BG, fg=DARK)
        detail_frame = tk.LabelFrame(split, text=" 日志详情 ", font=FONT_B, bg=BG, fg=DARK)
        split.add(list_frame, minsize=540)
        split.add(detail_frame, minsize=360)

        columns = ("时间", "操作", "说明", "操作员", "事件编号")
        self.tree = ttk.Treeview(
            list_frame, columns=columns, show="headings", height=22, selectmode="browse"
        )
        widths = (155, 130, 280, 90, 100)
        for column, width in zip(columns, widths):
            self.tree.heading(column, text=column)
            self.tree.column(column, width=width, anchor="w" if column == "说明" else "center")
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Double-1>", lambda _event: self._show_full_detail())

        self.detail_header_var = tk.StringVar(value="请选择一条日志")
        tk.Label(
            detail_frame,
            textvariable=self.detail_header_var,
            font=FONT_B,
            bg=BG,
            fg=DARK,
            justify="left",
            anchor="w",
            wraplength=420,
        ).pack(fill="x", padx=8, pady=8)

        self.detail_text = scrolledtext.ScrolledText(
            detail_frame, font=FONT_S, wrap="word", relief="solid", bd=1, bg=WHITE
        )
        self.detail_text.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.detail_text.configure(state="disabled")

    def _filtered_logs(self) -> List[Dict[str, Any]]:
        selected_action = self.filter_var.get()
        query = self.search_var.get().strip().casefold()
        result = []
        for log in self.logs:
            if selected_action != "全部" and _action(log) != selected_action:
                continue
            searchable = " ".join(
                [
                    _action(log),
                    _description(log),
                    str(log.get("operator", "")),
                    json.dumps(log.get("before", {}), ensure_ascii=False),
                    json.dumps(log.get("after", {}), ensure_ascii=False),
                ]
            ).casefold()
            if query and query not in searchable:
                continue
            result.append(log)
        return list(reversed(result))

    def _refresh_log_list(self):
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self.visible_logs = self._filtered_logs()
        for index, log in enumerate(self.visible_logs):
            event_id = str(log.get("event_id", "历史日志"))
            self.tree.insert(
                "",
                "end",
                iid=f"log-{index}",
                values=(
                    log.get("timestamp", ""),
                    _action(log),
                    _description(log)[:100],
                    log.get("operator", "系统"),
                    event_id[:8],
                ),
            )
        self.status_var.set(f"显示 {len(self.visible_logs)} / {len(self.logs)} 条")
        self.current_log = None
        self.detail_header_var.set("请选择一条日志")
        self._set_detail_text("")

    def _set_detail_text(self, content: str):
        self.detail_text.configure(state="normal")
        self.detail_text.delete("1.0", "end")
        self.detail_text.insert("1.0", content)
        self.detail_text.configure(state="disabled")

    def _on_tree_select(self, _event=None):
        selection = self.tree.selection()
        if not selection:
            return
        try:
            index = int(selection[0].split("-", 1)[1])
            log = self.visible_logs[index]
        except (IndexError, ValueError):
            return
        self.current_log = log
        self.detail_header_var.set(
            f"{log.get('timestamp', '')}  |  {_action(log)}\n"
            f"操作员：{log.get('operator', '系统')}  事件：{log.get('event_id', '历史未签名日志')}"
        )
        content = [
            "操作说明",
            _description(log) or "（无）",
            "",
            "修改前",
            json.dumps(log.get("before", {}), ensure_ascii=False, indent=2),
            "",
            "修改后",
            json.dumps(log.get("after", {}), ensure_ascii=False, indent=2),
            "",
            "完整性字段",
            f"前序哈希：{log.get('previous_hash', '历史日志未签名')}",
            f"本条哈希：{log.get('entry_hash', '历史日志未签名')}",
        ]
        self._set_detail_text("\n".join(content))

    def _show_full_detail(self):
        if not self.current_log:
            return
        dialog = tk.Toplevel(self)
        dialog.title("完整操作日志")
        dialog.configure(bg=BG)
        dialog.geometry("900x650")
        dialog.transient(self.winfo_toplevel())
        text = scrolledtext.ScrolledText(dialog, font=FONT, wrap="word")
        text.pack(fill="both", expand=True, padx=12, pady=12)
        text.insert("1.0", json.dumps(self.current_log, ensure_ascii=False, indent=2))
        text.configure(state="disabled")
        make_btn(dialog, "关闭", dialog.destroy, width=10).pack(pady=(0, 12))

    def _refresh(self, show_message=True):
        try:
            self.logs = self._load_logs()
        except Exception as exc:
            self.logs = []
            messagebox.showerror("日志读取失败", f"日志文件无法读取，请保留文件并联系维护人员。\n\n{exc}")

        actions = ["全部"] + sorted({_action(log) for log in self.logs})
        self.filter_combo.configure(values=actions)
        if self.filter_var.get() not in actions:
            self.filter_var.set("全部")

        verification = L.verify_integrity(Path(self.config.audit_log_path))
        status = verification["status"]
        self.integrity_var.set(verification["message"])
        if status == "invalid":
            self.integrity_label.configure(bg="#FDE7E9", fg=RED)
        elif status in {"legacy", "mixed"}:
            self.integrity_label.configure(bg=YELLOW, fg="#6A5200")
        else:
            self.integrity_label.configure(bg="#DFF6DD", fg=GREEN)
        self._refresh_log_list()
        if show_message:
            messagebox.showinfo("刷新完成", f"已加载 {len(self.logs)} 条日志")

    def _export(self):
        if not self.logs:
            messagebox.showinfo("提示", "暂无日志可导出")
            return
        export_path = filedialog.asksaveasfilename(
            title="导出只读操作日志",
            defaultextension=".json",
            filetypes=[("JSON 文件", "*.json"), ("文本文件", "*.txt")],
        )
        if not export_path:
            return
        try:
            path = Path(export_path)
            if path.suffix.lower() == ".txt":
                lines = []
                for log in self.logs:
                    lines.extend(
                        [
                            f"时间：{log.get('timestamp', '')}",
                            f"操作：{_action(log)}",
                            f"操作员：{log.get('operator', '系统')}",
                            f"说明：{_description(log)}",
                            "修改前：" + json.dumps(log.get("before", {}), ensure_ascii=False),
                            "修改后：" + json.dumps(log.get("after", {}), ensure_ascii=False),
                            "-" * 60,
                        ]
                    )
                path.write_text("\n".join(lines), encoding="utf-8")
            else:
                path.write_text(json.dumps(self.logs, ensure_ascii=False, indent=2), encoding="utf-8")
            messagebox.showinfo("导出成功", f"日志已导出到：\n{path}")
        except Exception as exc:
            messagebox.showerror("导出失败", f"无法导出日志。\n\n{exc}")

    def pack_forget(self):
        super().pack_forget()

    def pack(self, **kwargs):
        super().pack(**kwargs)

    def set_authenticated(self, active: bool, operator: str = ""):
        self.authenticated = active
