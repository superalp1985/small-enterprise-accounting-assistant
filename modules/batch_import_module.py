#!/usr/bin/env python3
import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
# -*- coding: utf-8 -*-
"""
batch_import_module.py - 批量导入模块
提供批量票据导入、OCR识别、智能匹配、逐条确认功能
"""

import json
import queue
import threading
import traceback
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional
import logger as L


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

# 导入冲突对话框和预览确认对话框
from modules import conflict_dialog
import model_runner as MR
from modules.preview_dialog import show_invoice_preview
from ocr_service import OcrRecognitionError, OcrService
from modules.loading_dialog import ApproxProgressDialog
from modules.vocabulary_module import load_vocab
from invoice_excel_import import InvoiceExcelImportError, read_tax_invoice_workbook
from platform_order_excel_import import (
    PlatformOrderExcelImportError,
    read_platform_order_workbook,
)


def make_btn(parent, text, cmd, color=BLUE, width=12):
    return tk.Button(parent, text=text, command=cmd,
                     bg=color, fg=WHITE, font=FONT_B,
                     relief="flat", padx=8, pady=4,
                     activebackground=DARK, activeforeground=WHITE,
                     cursor="hand2", width=width)


def _invoice_identity(item: Dict[str, Any]) -> Optional[tuple[str, str]]:
    code = str(item.get("invoice_code", "")).strip().upper()
    number = str(item.get("invoice_no", "")).strip().upper()
    return (code, number) if code or number else None


def _register_invoice_identity(
    item: Dict[str, Any], known_keys: set[tuple[str, str]],
) -> tuple[Optional[tuple[str, str]], bool]:
    key = _invoice_identity(item)
    if key is None:
        return None, False
    if key in known_keys:
        return key, True
    known_keys.add(key)
    return key, False


def review_snapshot(item: Dict[str, Any]) -> Dict[str, Any]:
    """Return the business fields that must be visible in the audit trail."""
    return {
        "文件名": str(item.get("file_name", "")),
        "来源标识": str(item.get("source_reference", "")),
        "发票代码": str(item.get("invoice_code", "")),
        "发票号码": str(item.get("invoice_no", "")),
        "开票日期": str(item.get("invoice_date", "")),
        "销售方": str(item.get("seller", "")),
        "购买方": str(item.get("buyer", "")),
        "价税合计": round(float(item.get("amount", 0) or 0), 2),
        "税额": round(float(item.get("tax_amount", 0) or 0), 2),
        "不含税金额": round(float(item.get("net_amount", 0) or 0), 2),
        "业务摘要": str(item.get("description", "")),
        "会计科目": str(item.get("matched_subject", "")),
        "对方科目": str(item.get("counter_subject", "")),
        "票据方向": str(item.get("invoice_type", "")),
        "借贷方向": str(item.get("direction", "")),
        "状态": str(item.get("status", "")),
    }


def changed_review_fields(before: Dict[str, Any], after: Dict[str, Any]) -> List[str]:
    return [key for key in after if before.get(key) != after.get(key)]


def apply_confirmed_review_data(item: Dict[str, Any], data: Dict[str, Any]) -> None:
    mappings = {
        "invoice_code": "invoice_code",
        "invoice_no": "invoice_no",
        "invoice_date": "invoice_date",
        "seller": "seller",
        "buyer": "buyer",
        "description": "description",
        "subject": "matched_subject",
        "amount": "amount",
        "total_amount": "total_amount",
        "tax_amount": "tax_amount",
        "net_amount": "net_amount",
    }
    for source, target in mappings.items():
        if source in data:
            item[target] = data[source]


def restore_review_snapshot(item: Dict[str, Any], snapshot: Dict[str, Any]) -> None:
    mappings = {
        "文件名": "file_name",
        "来源标识": "source_reference",
        "发票代码": "invoice_code",
        "发票号码": "invoice_no",
        "开票日期": "invoice_date",
        "销售方": "seller",
        "购买方": "buyer",
        "价税合计": "amount",
        "税额": "tax_amount",
        "不含税金额": "net_amount",
        "业务摘要": "description",
        "会计科目": "matched_subject",
        "对方科目": "counter_subject",
        "票据方向": "invoice_type",
        "借贷方向": "direction",
        "状态": "status",
    }
    for source, target in mappings.items():
        if source in snapshot:
            item[target] = snapshot[source]
    if "价税合计" in snapshot:
        item["total_amount"] = snapshot["价税合计"]


def apply_bulk_review_updates(
    items: List[Dict[str, Any]], updates: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Apply only explicitly selected bulk fields and return changed items."""
    changed = []
    for item in items:
        before = review_snapshot(item)
        for key, value in updates.items():
            item[key] = value
            if key == "invoice_type" and value in {"进项", "销项"}:
                item["direction"] = "贷方" if value == "销项" else "借方"
        item["needs_review"] = True
        item["match_type"] = "manual_override"
        item["status"] = "待定"
        if changed_review_fields(before, review_snapshot(item)):
            changed.append(item)
    return changed


class BatchImportModule(tk.Frame):
    """批量导入模块"""

    def __init__(self, parent, config, semantic_matcher, authenticated=False, store=None):
        super().__init__(parent, bg=BG)
        self.parent = parent
        self.config = config
        self.semantic_matcher = semantic_matcher
        self.authenticated = authenticated
        self.store = store

        # 数据存储
        self.imported_items: List[Dict] = []
        self.confirmed_items: List[Dict] = []
        self.pending_items: List[Dict] = []
        self.current_index = 0
        self.ocr_service = OcrService(config)
        self._match_in_progress = False
        self._match_loading = None
        self._recognition_thread = None
        self._recognition_events = queue.Queue()
        self._recognition_pump_id = None
        self._recognition_ui_active = False
        self._recognition_failures: List[str] = []
        self._recognition_duplicates: List[str] = []
        self._subject_match_cache: Dict[tuple, Optional[Dict[str, Any]]] = {}
        self._loaded_item: Optional[Dict[str, Any]] = None
        self._tree_item_map: Dict[str, Dict[str, Any]] = {}
        self._pause_event = threading.Event()
        self._cancel_event = threading.Event()

        # 加载词库
        self.vocab = self._load_vocab()
        enabled_codes = set(self.store.enabled_account_codes()) if self.store else set()
        catalog_subjects = [
            f"{row.get('code', '')} {row.get('name', '')}".strip()
            for row in (self.store.enabled_accounts() if self.store else [])
        ]
        detail_subjects = [
            row["subject"] for row in self.vocab
            if row.get("subject") and (
                not enabled_codes or str(row.get("subject_code", "")) in enabled_codes
            )
        ]
        self.subject_options = list(dict.fromkeys(catalog_subjects + detail_subjects))

        self._build_ui()
        self.reload_from_store()
        self._restore_review_drafts()

    def _load_vocab(self) -> List[Dict]:
        """加载词库"""
        return load_vocab(
            self.config.vocab_path,
            getattr(self.config, "account_catalog_path", None),
        )

    def _restore_review_drafts(self):
        if not self.store:
            return
        restored = 0
        posted_keys = {
            key for row in self.store.list_invoices()
            if (key := _invoice_identity(row)) is not None
        }
        stale_draft_ids = []
        for draft in self.store.list_drafts():
            if draft.get("type") != "batch":
                continue
            draft_key = _invoice_identity(draft)
            if draft_key is not None and draft_key in posted_keys:
                stale_draft_ids.append(str(draft.get("id", "")))
                continue
            item = dict(draft)
            item["_draft_id"] = item.get("id", "")
            item["status"] = "待定"
            item.setdefault("file_name", item.get("source_reference", "已保存票据"))
            item.setdefault("filepath", "")
            item["_audit_snapshot"] = review_snapshot(item)
            self.imported_items.append(item)
            self.pending_items.append(item)
            restored += 1
        if stale_draft_ids:
            self.store.delete_drafts(stale_draft_ids)
        if restored:
            self.list_filter_var.set("待复核")
            self._refresh_tree()
            self._update_stats()
            self._load_item(0)
            self.next_step_var.set(
                f"已恢复 {restored} 张上次保存的复核草稿；核对后可批量修改或入账"
            )
            self.status_var.set(f"已恢复 {restored} 张复核草稿")

    def _persist_review_draft(self, item: Dict[str, Any]) -> None:
        self._persist_review_drafts([item])

    def _persist_review_drafts(self, items: List[Dict[str, Any]]) -> None:
        if not self.store:
            return
        payloads = []
        for item in items:
            payload = {"type": "batch", **item}
            payload.pop("_audit_snapshot", None)
            if item.get("_draft_id"):
                payload["id"] = item["_draft_id"]
            payloads.append(payload)
        saved_rows = self.store.add_drafts(payloads)
        for item, saved in zip(items, saved_rows):
            item["_draft_id"] = saved["id"]

    def _audit_review_change(
        self,
        item: Dict[str, Any],
        action: str,
        before: Optional[Dict[str, Any]] = None,
    ) -> bool:
        before = dict(before or item.get("_audit_snapshot") or review_snapshot(item))
        after = review_snapshot(item)
        fields = changed_review_fields(before, after)
        if not fields:
            item["_audit_snapshot"] = after
            return False
        reference = item.get("invoice_no") or item.get("source_reference") or item.get("file_name")
        success = L.log(
            action,
            f"{reference}；修改字段：{'、'.join(fields)}",
            before=before,
            after=after,
        )
        if not success:
            restore_review_snapshot(item, before)
            raise RuntimeError("操作日志写入失败，修改已撤回；请先到操作日志检查完整性")
        item["_audit_snapshot"] = after
        return True

    def _audit_bulk_review_changes(
        self,
        items: List[Dict[str, Any]],
        action: str,
        before_by_id: Dict[int, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        changes = []
        for item in items:
            before = dict(before_by_id[id(item)])
            after = review_snapshot(item)
            fields = changed_review_fields(before, after)
            if fields:
                changes.append({
                    "reference": (
                        item.get("invoice_no")
                        or item.get("source_reference")
                        or item.get("file_name")
                    ),
                    "fields": fields,
                    "before": before,
                    "after": after,
                    "item": item,
                })
        if not changes:
            return []
        success = L.log(
            action,
            f"批量处理 {len(changes)} 张票据；每张票据的修改字段和前后值见详情",
            before=[
                {
                    "reference": change["reference"],
                    "fields": change["fields"],
                    "values": change["before"],
                }
                for change in changes
            ],
            after=[
                {
                    "reference": change["reference"],
                    "fields": change["fields"],
                    "values": change["after"],
                }
                for change in changes
            ],
        )
        if not success:
            for change in changes:
                restore_review_snapshot(change["item"], change["before"])
            raise RuntimeError("操作日志写入失败，批量修改已撤回；请先检查日志完整性")
        changed_items = []
        for change in changes:
            change["item"]["_audit_snapshot"] = change["after"]
            changed_items.append(change["item"])
        return changed_items

    def _ensure_audit_writable(self) -> bool:
        verification = L.verify_integrity()
        if verification.get("status") == "invalid":
            messagebox.showerror(
                "操作日志校验失败",
                f"为避免产生无日志修改，当前保存和入账操作已停止。\n\n"
                f"{verification.get('message', '')}\n\n请保留日志文件并联系维护人员处理。",
                parent=self,
            )
            return False
        return True

    def _build_ui(self):
        """构建UI"""
        f = tk.LabelFrame(self, text=" 批量导入模式 ", font=FONT_T,
                          bg=BG, fg=DARK, bd=1, relief="groove")
        f.pack(fill="both", expand=True, pady=6)

        # 工具栏
        tool = tk.Frame(f, bg=BG, pady=6)
        tool.pack(fill="x", padx=12)

        self.status_var = tk.StringVar(value="就绪")
        tk.Label(tool, textvariable=self.status_var, font=FONT_S, bg=BG,
                 fg="#666").pack(side="left")

        # 文件选择区
        file_frame = tk.LabelFrame(f, text=" 文件选择 ", font=FONT_B,
                                    bg=BG, fg=DARK, bd=1, relief="groove")
        file_frame.pack(fill="x", padx=12, pady=8)

        row = tk.Frame(file_frame, bg=BG)
        row.pack(fill="x", padx=10, pady=10)

        self.file_list_var = tk.StringVar(value="未选择文件")
        tk.Label(row, textvariable=self.file_list_var, font=FONT_S, bg=BG,
                 fg="#666", anchor="w", relief="solid", bd=1, padx=10, pady=8,
                 width=50).pack(side="left", fill="x", expand=True)

        make_btn(row, "1 选择票据/Excel", self._select_files, width=15).pack(side="left", padx=6)
        make_btn(row, "清空", self._clear_files, width=8).pack(side="left", padx=4)

        # 进度条
        self.progress_var = tk.DoubleVar(value=0)
        progress = ttk.Progressbar(file_frame, variable=self.progress_var,
                                   maximum=100, length=300)
        progress.pack(padx=10, pady=(0, 10), anchor="w")

        self.progress_text_var = tk.StringVar(value="")
        tk.Label(file_frame, textvariable=self.progress_text_var, font=FONT_S,
                 bg=BG, fg="#666").pack(anchor="w", padx=10, pady=(0, 6))

        self.next_step_var = tk.StringVar(value="下一步：选择图片、PDF或税务系统Excel导出文件")
        tk.Label(
            file_frame, textvariable=self.next_step_var, font=FONT_B,
            bg="#E8F3FC", fg=DARK, anchor="w", padx=10, pady=7,
        ).pack(fill="x", padx=10, pady=(0, 8))

        # 操作按钮
        btn_row = tk.Frame(f, bg=BG)
        btn_row.pack(fill="x", padx=12, pady=6)

        self.start_btn = make_btn(
            btn_row, "2 全部识别并推荐", self._start_recognition, color=GREEN, width=16
        )
        self.start_btn.pack(side="left", padx=4)
        self.bulk_post_btn = make_btn(
            btn_row, "3 全部确认入账", self._confirm_all_items, color=DARK, width=15
        )
        self.bulk_post_btn.pack(side="left", padx=4)
        self.bulk_post_btn.configure(state="disabled")
        self.selected_post_btn = make_btn(
            btn_row, "入账选中", self._confirm_selected_items, color=BLUE, width=10
        )
        self.selected_post_btn.pack(side="left", padx=4)
        self.selected_post_btn.configure(state="disabled")
        self.pause_btn = make_btn(btn_row, "暂停", self._pause_recognition, color=ORANGE, width=8)
        self.pause_btn.pack(side="left", padx=4)
        make_btn(btn_row, "取消", self._cancel_recognition, color=RED, width=8).pack(side="left", padx=4)
        tk.Label(btn_row, text="批量票据方向：", font=FONT_S, bg=BG).pack(
            side="left", padx=(14, 4)
        )
        self.batch_invoice_type_var = tk.StringVar(value="自动识别")
        self.batch_invoice_type_combo = ttk.Combobox(
            btn_row,
            textvariable=self.batch_invoice_type_var,
            values=["自动识别", "进项", "销项"],
            state="readonly",
            width=10,
            font=FONT,
        )
        self.batch_invoice_type_combo.pack(side="left", padx=4)

        # 统计信息
        stats_frame = tk.Frame(f, bg=YELLOW, relief="solid", bd=1)
        stats_frame.pack(fill="x", padx=12, pady=6)

        self.stats_var = tk.StringVar(value="导入：0  待处理：0  已确认：0  待定：0")
        tk.Label(stats_frame, textvariable=self.stats_var, font=FONT_B, bg=YELLOW,
                 fg="#333").pack(padx=12, pady=6)

        # 分隔区：左侧列表，右侧详情
        split_frame = tk.Frame(f, bg=BG)
        split_frame.pack(fill="both", expand=True, padx=12, pady=6)

        # 左侧：待处理列表
        left_frame = tk.LabelFrame(split_frame, text=" 待处理列表 ", font=FONT_B,
                                    bg=BG, fg=DARK, bd=1, relief="groove")
        left_frame.pack(side="left", fill="both", expand=True, padx=(0, 6))

        list_tools = tk.Frame(left_frame, bg=BG)
        list_tools.pack(fill="x", padx=6, pady=6)
        make_btn(list_tools, "全选待复核", self._select_all_pending, width=10).pack(
            side="left", padx=2
        )
        make_btn(list_tools, "清除选择", self._clear_tree_selection, color="#666", width=9).pack(
            side="left", padx=2
        )
        make_btn(list_tools, "批量修改", self._bulk_edit_selected, color=ORANGE, width=9).pack(
            side="left", padx=2
        )
        make_btn(list_tools, "补充票据信息", self._edit_current_invoice_details, width=12).pack(
            side="left", padx=2
        )

        self.list_filter_var = tk.StringVar(value="待复核")
        list_filter = ttk.Combobox(
            list_tools,
            textvariable=self.list_filter_var,
            values=["全部", "待复核", "已入账", "已拦截", "进项", "销项"],
            state="readonly",
            width=8,
            font=FONT_S,
        )
        list_filter.pack(side="right", padx=2)
        list_filter.bind("<<ComboboxSelected>>", lambda _event: self._refresh_tree())
        self.list_search_var = tk.StringVar()
        list_search = tk.Entry(
            list_tools, textvariable=self.list_search_var, font=FONT_S, width=16
        )
        list_search.pack(side="right", padx=4)
        list_search.bind("<KeyRelease>", lambda _event: self._refresh_tree())
        tk.Label(list_tools, text="搜索/筛选：", font=FONT_S, bg=BG).pack(side="right")

        cols = ("状态", "文件名", "摘要", "金额", "科目")
        self.tree = ttk.Treeview(
            left_frame, columns=cols, show="headings", height=18, selectmode="extended"
        )

        self.tree.heading("状态", text="状态")
        self.tree.column("状态", width=60, anchor="center")
        self.tree.heading("文件名", text="文件名")
        self.tree.column("文件名", width=150, anchor="w")
        self.tree.heading("摘要", text="摘要")
        self.tree.column("摘要", width=180, anchor="w")
        self.tree.heading("金额", text="金额")
        self.tree.column("金额", width=80, anchor="center")
        self.tree.heading("科目", text="科目")
        self.tree.column("科目", width=120, anchor="w")

        sb = ttk.Scrollbar(left_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self.tree.bind("<ButtonRelease-1>", self._on_tree_click)
        self.tree.bind("<Double-1>", self._on_tree_double_click)
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self._update_selection_state())

        # 右侧：确认编辑区
        right_frame = tk.LabelFrame(split_frame, text=" 确认编辑 ", font=FONT_B,
                                     bg=BG, fg=DARK, bd=1, relief="groove")
        right_frame.pack(side="right", fill="both", expand=True, padx=(6, 0), ipadx=6)
        confirm_canvas = tk.Canvas(right_frame, bg=BG, highlightthickness=0)
        confirm_scrollbar = ttk.Scrollbar(
            right_frame, orient="vertical", command=confirm_canvas.yview
        )
        confirm_canvas.configure(yscrollcommand=confirm_scrollbar.set)
        confirm_canvas.pack(side="left", fill="both", expand=True)
        confirm_scrollbar.pack(side="right", fill="y")
        confirm_content = tk.Frame(confirm_canvas, bg=BG)
        confirm_window = confirm_canvas.create_window(
            (0, 0), window=confirm_content, anchor="nw"
        )
        confirm_content.bind(
            "<Configure>",
            lambda _event: confirm_canvas.configure(
                scrollregion=confirm_canvas.bbox("all")
            ),
        )
        confirm_canvas.bind(
            "<Configure>",
            lambda event: confirm_canvas.itemconfigure(confirm_window, width=event.width),
        )
        confirm_canvas.bind(
            "<MouseWheel>",
            lambda event: confirm_canvas.yview_scroll(int(-event.delta / 120), "units"),
        )
        self._build_confirm_ui(confirm_content)

        # 底部：已确认列表
        bottom_frame = tk.LabelFrame(f, text=" 已确认凭证（本次会话） ", font=FONT_B,
                                     bg=BG, fg=DARK, bd=1, relief="groove")
        bottom_frame.pack(fill="x", padx=12, pady=6)

        confirm_cols = ("序号", "时间", "摘要", "科目", "金额", "方向", "操作")
        self.confirmed_tree = ttk.Treeview(bottom_frame, columns=confirm_cols,
                                           show="headings", height=4)

        for c, w in zip(confirm_cols, (50, 70, 150, 120, 80, 60, 100)):
            self.confirmed_tree.heading(c, text=c)
            self.confirmed_tree.column(c, width=w, anchor="center")

        c_sb = ttk.Scrollbar(bottom_frame, orient="vertical",
                             command=self.confirmed_tree.yview)
        self.confirmed_tree.configure(yscrollcommand=c_sb.set)
        self.confirmed_tree.pack(side="left", fill="both", expand=True)
        c_sb.pack(side="right", fill="y")

    def _build_confirm_ui(self, parent):
        """构建确认编辑UI"""
        # 当前索引显示
        self.index_var = tk.StringVar(value="当前：- / -")
        tk.Label(parent, textvariable=self.index_var, font=FONT_B, bg=BG, fg=BLUE).pack(
            pady=(8, 4), padx=8)

        # 文件名
        self.confirm_file_var = tk.StringVar(value="")
        tk.Label(parent, text="文件：", font=FONT_B, bg=BG).pack(anchor="w", padx=8)
        tk.Label(parent, textvariable=self.confirm_file_var, font=FONT_S, bg=BG,
                 fg="#666", wraplength=300).pack(anchor="w", padx=24, pady=(0, 6))

        # 摘要
        tk.Label(parent, text="业务摘要：", font=FONT_B, bg=BG).pack(anchor="w", padx=8)
        self.confirm_desc_var = tk.StringVar()
        self.confirm_desc_entry = tk.Entry(parent, textvariable=self.confirm_desc_var,
                                          font=FONT, width=40, relief="solid", bd=1)
        self.confirm_desc_entry.pack(fill="x", padx=8, pady=(0, 6))

        # 金额
        tk.Label(parent, text="金额（元）：", font=FONT_B, bg=BG).pack(anchor="w", padx=8)
        self.confirm_amount_var = tk.StringVar()
        self.confirm_amount_entry = tk.Entry(parent, textvariable=self.confirm_amount_var,
                                            font=FONT, width=20, relief="solid", bd=1)
        self.confirm_amount_entry.pack(anchor="w", padx=8, pady=(0, 6))

        # 匹配结果
        tk.Label(parent, text="匹配结果：", font=FONT_B, bg=BG).pack(anchor="w", padx=8)
        self.match_result_text = tk.Text(parent, font=FONT_S, width=40, height=9,
                                       wrap="word", relief="solid", bd=1, bg=WHITE)
        self.match_result_text.pack(fill="x", padx=8, pady=(0, 6))
        self.match_result_text.configure(state="disabled")

        # 科目选择
        tk.Label(parent, text="选择科目：", font=FONT_B, bg=BG).pack(anchor="w", padx=8)
        self.confirm_subject_var = tk.StringVar()
        self.confirm_subject_combo = ttk.Combobox(parent, textvariable=self.confirm_subject_var,
                                                 values=self.subject_options, font=FONT,
                                                 width=35, state="readonly")
        self.confirm_subject_combo.pack(fill="x", padx=8, pady=(0, 6))
        self.confirm_subject_combo.bind("<<ComboboxSelected>>", self._on_editor_changed)

        tk.Label(parent, text="对方科目：", font=FONT_B, bg=BG).pack(anchor="w", padx=8)
        self.confirm_counter_subject_var = tk.StringVar()
        self.confirm_counter_subject_combo = ttk.Combobox(
            parent, textvariable=self.confirm_counter_subject_var,
            values=self.subject_options, font=FONT, width=35, state="readonly"
        )
        self.confirm_counter_subject_combo.pack(fill="x", padx=8, pady=(0, 6))
        if self.store:
            default_counter = self.store.get_settings()["accounting"].get(
                "default_cash_subject", ""
            )
            if default_counter in self.subject_options:
                self.confirm_counter_subject_var.set(default_counter)

        tk.Label(parent, text="票据方向（进项/销项）：", font=FONT_B, bg=BG).pack(
            anchor="w", padx=8
        )
        self.confirm_invoice_type_var = tk.StringVar(value="进项")
        self.confirm_invoice_type_combo = ttk.Combobox(
            parent,
            textvariable=self.confirm_invoice_type_var,
            values=["进项", "销项"],
            font=FONT,
            width=35,
            state="readonly",
        )
        self.confirm_invoice_type_combo.pack(fill="x", padx=8, pady=(0, 6))
        self.confirm_invoice_type_combo.bind("<<ComboboxSelected>>", self._on_editor_changed)

        # 借贷方向
        tk.Label(parent, text="借贷方向：", font=FONT_B, bg=BG).pack(anchor="w", padx=8)
        self.confirm_dir_var = tk.StringVar(value="借方")
        dir_row = tk.Frame(parent, bg=BG)
        dir_row.pack(fill="x", padx=8, pady=(0, 6))
        ttk.Radiobutton(dir_row, text="借方", variable=self.confirm_dir_var, value="借方").pack(side="left")
        ttk.Radiobutton(dir_row, text="贷方", variable=self.confirm_dir_var, value="贷方").pack(side="left", padx=20)

        # 按钮行
        btn_row = tk.Frame(parent, bg=BG)
        btn_row.pack(fill="x", padx=8, pady=8)

        make_btn(btn_row, "重新匹配", self._re_match, color=BLUE, width=10).pack(side="left", padx=4)
        make_btn(btn_row, "查看依据", self._view_law, width=10).pack(side="left", padx=4)
        make_btn(btn_row, "保存修改", self._save_pending, color=ORANGE, width=9).pack(side="left", padx=4)
        make_btn(btn_row, "当前确认入账", self._confirm_item, color=GREEN, width=12).pack(side="left", padx=4)
        make_btn(btn_row, "跳过", self._skip_item, color="#777", width=8).pack(side="left", padx=4)

        # 导航按钮
        nav_row = tk.Frame(parent, bg=BG)
        nav_row.pack(fill="x", padx=8, pady=(4, 8))
        make_btn(nav_row, "◄ 上一条", self._prev_item, width=12).pack(side="left", padx=4)
        make_btn(nav_row, "下一条 ►", self._next_item, width=12).pack(side="right", padx=4)

        self.selected_files: List[str] = []

    def _select_files(self):
        """选择文件"""
        files = filedialog.askopenfilenames(
            title="选择票据文件",
            filetypes=[
                ("税务系统Excel", "*.xls *.xlsx *.xlsm"),
                ("图片文件", "*.png *.jpg *.jpeg *.bmp *.tiff"),
                ("PDF文件", "*.pdf"),
                ("所有文件", "*.*")
            ]
        )
        if files:
            self.selected_files = list(files)
            self.file_list_var.set(f"已选择 {len(files)} 个文件")
            self.status_var.set(f"已选择 {len(files)} 个文件")
            self.next_step_var.set("下一步：点击“2 全部识别并推荐”，等待进度完成")

    def _clear_files(self):
        """清空文件"""
        if self._recognition_thread and self._recognition_thread.is_alive():
            messagebox.showwarning("提示", "请先取消当前识别任务")
            return
        self.selected_files.clear()
        self.file_list_var.set("未选择文件")
        self.imported_items.clear()
        self.pending_items.clear()
        self._refresh_tree()
        self._update_stats()
        self.status_var.set("已清空")
        self.next_step_var.set("下一步：选择图片、PDF或税务系统Excel导出文件")

    def _start_recognition(self):
        """Extract every selected source and build a single review queue."""
        if not self.selected_files:
            messagebox.showwarning("提示", "请先选择文件")
            return
        if self._recognition_thread and self._recognition_thread.is_alive():
            messagebox.showinfo("提示", "识别任务正在运行")
            return

        self._pause_event.clear()
        self._cancel_event.clear()
        self.pause_btn.configure(text="暂停")
        self.start_btn.configure(state="disabled")
        self.status_var.set("正在识别...")
        self.next_step_var.set("正在处理：请等待全部票据识别并生成推荐科目")
        self.progress_var.set(0)
        files = list(self.selected_files)
        self._recognition_failures = []
        self._recognition_duplicates = []
        known_invoice_keys = set()
        if self.store:
            known_invoice_keys = {
                key for row in self.store.list_invoices()
                if (key := _invoice_identity(row)) is not None
            }
        known_invoice_keys.update(
            key for row in self.imported_items
            if not row.get("duplicate_invoice")
            if (key := _invoice_identity(row)) is not None
        )
        settings = self.store.get_settings() if self.store else {}
        company = settings.get("company", {})
        company_tax_id = str(company.get("credit_code", ""))
        company_industry = str(company.get("industry", ""))
        default_counter_subject = str(
            settings.get("accounting", {}).get("default_cash_subject", "")
        )
        batch_invoice_type = self.batch_invoice_type_var.get().strip()

        def worker():
            failed = 0
            processed = 0
            total = len(files)
            for index, filepath in enumerate(files, start=1):
                while self._pause_event.is_set() and not self._cancel_event.is_set():
                    self._cancel_event.wait(0.1)
                if self._cancel_event.is_set():
                    break
                try:
                    source = Path(filepath)
                    if source.suffix.lower() in {".xls", ".xlsx", ".xlsm"}:
                        try:
                            items = read_tax_invoice_workbook(
                                source,
                                company_tax_id=company_tax_id,
                                company_industry=company_industry,
                                progress_callback=lambda current, count, stage, name=source.name:
                                self._queue_import_progress(current, count, stage, name),
                            )
                        except InvoiceExcelImportError as invoice_error:
                            try:
                                items = read_platform_order_workbook(
                                    source,
                                    company_name=str(company.get("name", "")),
                                    company_industry=company_industry,
                                    progress_callback=lambda current, count, stage, name=source.name:
                                    self._queue_import_progress(current, count, stage, name),
                                )
                            except PlatformOrderExcelImportError:
                                raise invoice_error
                    else:
                        self._queue_import_progress(0, 1, "正在进行 OCR 识别", source.name)
                        items = [self.ocr_service.recognize_invoice(source)]

                    item_total = len(items)
                    for item_index, item in enumerate(items, start=1):
                        if self._cancel_event.is_set():
                            break
                        item.setdefault("counter_subject", default_counter_subject)
                        item.setdefault("invoice_type", "进项")
                        item.setdefault("direction", "借方")
                        if batch_invoice_type in {"进项", "销项"}:
                            item["invoice_type"] = batch_invoice_type
                            item["direction"] = "贷方" if batch_invoice_type == "销项" else "借方"
                            item["needs_review"] = True
                            item["direction_override"] = True
                        invoice_key, is_duplicate = _register_invoice_identity(
                            item, known_invoice_keys,
                        )
                        if is_duplicate:
                            item["status"] = "重复票据"
                            item["duplicate_invoice"] = True
                            item["needs_review"] = True
                            item.setdefault("warnings", []).append(
                                f"发票号码 {invoice_key[1]} 已在当前账套或本批次中出现，已阻止重复入账"
                            )
                        elif not item.get("non_postable"):
                            self._apply_automatic_match(item)
                        if item_total > 1 and (item_index == 1 or item_index % 10 == 0):
                            self._recognition_events.put(
                                ("source_progress", item_index, item_total, source.name)
                            )
                    error = None
                except Exception as exc:
                    items = []
                    error = exc
                    failed += 1
                processed += 1
                self._recognition_events.put(
                    ("file_result", index, total, filepath, items, error)
                )
            cancelled = self._cancel_event.is_set()
            self._recognition_events.put(
                ("finished", processed, failed, len(files), cancelled)
            )

        self._recognition_thread = threading.Thread(
            target=worker, name="batch-ocr-recognition", daemon=True
        )
        self._recognition_ui_active = True
        self._start_recognition_ui_pump()
        self._recognition_thread.start()

    def _show_source_progress(self, current: int, total: int, file_name: str):
        self.progress_var.set(min(95.0, 35.0 + (current / max(total, 1) * 60.0)))
        self.progress_text_var.set(
            f"正在生成推荐科目：{file_name}（{current}/{total}张发票）"
        )

    def _queue_import_progress(self, current: int, total: int, stage: str, file_name: str):
        """Queue parser progress; only the Tk thread may touch widgets."""
        self._recognition_events.put(
            ("import_progress", current, total, stage, file_name)
        )

    def _start_recognition_ui_pump(self):
        if self._recognition_pump_id is not None:
            return
        try:
            self._recognition_pump_id = self.after(50, self._drain_recognition_events)
        except tk.TclError:
            self._recognition_ui_active = False

    def _drain_recognition_events(self):
        """Apply worker results on the Tk thread and bound work per UI tick."""
        self._recognition_pump_id = None
        handled = 0
        while handled < 100:
            try:
                event = self._recognition_events.get_nowait()
            except queue.Empty:
                break
            handled += 1
            kind = event[0]
            try:
                if kind == "import_progress":
                    self._show_import_progress(*event[1:])
                elif kind == "source_progress":
                    self._show_source_progress(*event[1:])
                elif kind == "file_result":
                    self._accept_recognition_results(*event[1:])
                elif kind == "finished":
                    self._finish_recognition(*event[1:])
            except Exception as exc:
                self._record_batch_thread_error(exc)

        if self._recognition_ui_active or not self._recognition_events.empty():
            self._start_recognition_ui_pump()

    def _record_batch_thread_error(self, exc: Exception):
        detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        crash_log = Path(getattr(self.config, "data_dir", Path.cwd())) / "crash.log"
        try:
            crash_log.parent.mkdir(parents=True, exist_ok=True)
            with crash_log.open("a", encoding="utf-8") as handle:
                handle.write(f"\n[批量导入界面线程异常]\n{detail}")
        except OSError:
            pass
        try:
            messagebox.showerror(
                "批量导入没有完成",
                f"批量导入结果显示失败，原始文件没有自动入账。\n\n原因：{exc}\n\n诊断信息：{crash_log}",
                parent=self,
            )
        except tk.TclError:
            pass

    def _show_import_progress(self, current: int, total: int, stage: str, file_name: str):
        ratio = min(max(float(current) / max(total, 1), 0.0), 1.0)
        self.progress_var.set(min(35.0, 3.0 + ratio * 32.0))
        self.progress_text_var.set(
            f"{stage}：{file_name}（已读取 {current}/{total} 行）"
        )

    def _accept_recognition_results(self, index, total, filepath, items, error):
        file_name = Path(filepath).name
        self.progress_var.set(max(self.progress_var.get(), index / total * 100))
        self.progress_text_var.set(
            f"已处理：{file_name}，导入 {len(items)} 张发票（文件 {index}/{total}）"
        )
        if error:
            friendly = (
                f"第{index}个文件“{file_name}”处理失败：{error}。"
                "图片/PDF请确认清晰度；Excel请确认来自税务系统全量发票查询导出。"
            )
            self._recognition_failures.append(friendly)
            self.imported_items.append({
                "file_name": file_name, "filepath": filepath, "status": "识别失败",
                "amount": 0.0, "description": friendly, "invoice_date": "",
                "invoice_no": "", "matched_subject": "", "match_score": 0.0,
                "warnings": [friendly],
            })
        else:
            for item in items:
                item["_audit_snapshot"] = review_snapshot(item)
                self.imported_items.append(item)
                if item.get("duplicate_invoice"):
                    self._recognition_duplicates.append(
                        f"“{item.get('file_name', file_name)}”："
                        f"{item.get('warnings', ['重复票据'])[-1]}"
                    )
                elif not item.get("non_postable"):
                    self.pending_items.append(item)
        self._refresh_tree()
        self._update_stats()

    def _finish_recognition(self, processed, failed, total, cancelled):
        self._recognition_ui_active = False
        self.start_btn.configure(state="normal")
        self._pause_event.clear()
        self.pause_btn.configure(text="暂停")
        if cancelled:
            self.progress_text_var.set(f"已取消：完成 {processed}/{total}，失败 {failed}")
            self.status_var.set("识别已取消")
        else:
            self.progress_text_var.set(
                f"处理完成：{len(self.pending_items)} 张待复核，失败文件 {failed} 个"
            )
            if self._recognition_duplicates:
                self.status_var.set(f"识别完成，已拦截 {len(self._recognition_duplicates)} 张重复票据")
            else:
                self.status_var.set("识别完成" if failed == 0 else "识别完成，存在失败文件")
        if failed and self._recognition_failures:
            details = "\n".join(self._recognition_failures[:6])
            remaining = len(self._recognition_failures) - 6
            if remaining > 0:
                details += f"\n另有 {remaining} 张票据识别失败，请在列表中逐张处理。"
            messagebox.showwarning(
                "部分票据需要手动处理",
                f"{details}\n\n失败票据没有自动入账，其他已识别票据不受影响。",
                parent=self,
            )
        if self._recognition_duplicates:
            details = "\n".join(self._recognition_duplicates[:6])
            remaining = len(self._recognition_duplicates) - 6
            if remaining > 0:
                details += f"\n另有 {remaining} 张重复票据已拦截。"
            messagebox.showwarning(
                "发现重复票据",
                f"{details}\n\n重复票据保留在识别列表中，但不会进入待确认和记账队列。",
                parent=self,
            )
        if self.pending_items:
            self._load_item(min(self.current_index, len(self.pending_items) - 1))
            self.next_step_var.set(
                "下一步：检查推荐结果；科目不准可双击该行修改，确认后点击“3 全部确认入账”"
            )
        elif not cancelled:
            self.next_step_var.set("本批次没有可入账票据，请检查失败、作废或重复状态")

    def _apply_automatic_match(self, item: Dict):
        """Apply exact rules, then local-model semantics, then a review fallback."""
        if item.get("source_type") == "platform_excel":
            self._apply_platform_order_recommendation(item)
            return
        description = str(item.get("description", "")).strip()
        matches = []
        if self.semantic_matcher and description:
            matches = self.semantic_matcher.match_rules(description)
        if not matches and self.semantic_matcher and description:
            tax_categories = tuple(item.get("tax_categories") or [])
            industry = str(item.get("company_industry", "")).strip()
            invoice_type = str(item.get("invoice_type", "进项"))
            if tax_categories:
                cache_key = ("tax-category", industry, invoice_type, tax_categories)
                example = str((item.get("item_descriptions") or [description])[0])[:100]
                query = (
                    f"公司行业：{industry or '未设置'}；{invoice_type}发票；"
                    f"税收分类：{'、'.join(tax_categories)}；商品服务示例：{example}"
                )
            else:
                cache_key = ("description", invoice_type, description.casefold())
                query = description
            if cache_key in self._subject_match_cache:
                cached = self._subject_match_cache[cache_key]
                matches = [cached] if cached else []
            else:
                ai_matches = self.semantic_matcher.match_with_ai(query)
                cached = ai_matches[0] if ai_matches else None
                self._subject_match_cache[cache_key] = cached
                matches = [cached] if cached else []
        if not matches:
            self._apply_fallback_recommendation(item)
            return

        best = matches[0]
        record = best.get("record", {})
        item["matched_subject"] = record.get("subject", "")
        item["match_score"] = float(best.get("score", 0))
        item["match_type"] = best.get("match_type", "")
        item["law"] = record.get("law", "")
        item["rule_category"] = best.get("rule_category", record.get("rule_category", ""))
        item["rule_basis"] = best.get("rule_basis", record.get("rule_basis", ""))
        item["recommendation_reason"] = best.get(
            "recommendation_reason", record.get("recommendation_reason", "")
        )
        item["manual_review_required"] = bool(best.get("manual_review_required"))
        item["match_details"] = MR.format_match_details(best)

    def _apply_platform_order_recommendation(self, item: Dict):
        subject = next(
            (
                option for option in self.subject_options
                if option == "5001 主营业务收入" or option.startswith("5001 主营业务收入-")
            ),
            self.subject_options[0] if self.subject_options else "5001 主营业务收入",
        )
        item["matched_subject"] = subject
        item["match_score"] = 1.0
        item["match_type"] = "platform_order_default"
        item["rule_category"] = "平台销售订单"
        item["rule_basis"] = "购物平台交易成功订单按销项进入复核，货款先计入平台待结算款"
        item["recommendation_reason"] = "销售商品订单默认推荐主营业务收入，仍需核对退款、优惠和结算单"
        item["needs_review"] = True
        warning = "平台订单已按销售收入生成待复核推荐，入账前请核对退款、优惠和平台结算单"
        if warning not in item.setdefault("warnings", []):
            item["warnings"].append(warning)
        item["match_details"] = (
            f"推荐科目：{subject}\n匹配方式：平台订单默认映射\n"
            f"规则依据：{item['rule_basis']}\n推荐理由：{item['recommendation_reason']}"
        )

    def _apply_fallback_recommendation(self, item: Dict):
        invoice_type = str(item.get("invoice_type", "进项"))
        preferred = (
            ("5001 主营业务收入", "主营业务收入")
            if invoice_type == "销项"
            else ("5602 管理费用-其他", "5602 管理费用")
        )
        subject = next(
            (
                option for candidate in preferred for option in self.subject_options
                if option == candidate or option.startswith(candidate + "-")
            ),
            self.subject_options[0] if self.subject_options else "",
        )
        item["matched_subject"] = subject
        item["match_score"] = 0.0
        item["match_type"] = "review_fallback"
        item["rule_category"] = "未确定业务"
        item["rule_basis"] = "规则词库和本地模型均未形成有效结果"
        item["recommendation_reason"] = "仅作待复核占位科目，不代表最终会计判断"
        item["needs_review"] = True
        item.setdefault("warnings", []).append(
            "未能确定业务科目，已给出待复核占位推荐；入账前必须人工修改或确认"
        )
        item["match_details"] = (
            f"推荐科目：{subject}\n匹配方式：待复核占位\n"
            f"规则依据：{item['rule_basis']}\n推荐理由：{item['recommendation_reason']}"
        )

    def _pause_recognition(self):
        """暂停识别"""
        if not self._recognition_thread or not self._recognition_thread.is_alive():
            messagebox.showinfo("提示", "当前没有正在运行的识别任务")
            return
        if self._pause_event.is_set():
            self._pause_event.clear()
            self.pause_btn.configure(text="暂停")
            self.status_var.set("正在识别...")
        else:
            self._pause_event.set()
            self.pause_btn.configure(text="继续")
            self.status_var.set("识别已暂停")

    def _cancel_recognition(self):
        """取消识别"""
        if messagebox.askyesno("确认取消", "取消当前识别？"):
            self._cancel_event.set()
            self._pause_event.clear()
            self.status_var.set("正在取消识别任务...")

    def _refresh_tree(self):
        """刷新待处理列表"""
        selected_ids = {
            id(self._tree_item_map[iid])
            for iid in self.tree.selection()
            if iid in self._tree_item_map
        }
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self._tree_item_map.clear()

        filter_value = self.list_filter_var.get() if hasattr(self, "list_filter_var") else "全部"
        query = self.list_search_var.get().strip().casefold() if hasattr(self, "list_search_var") else ""
        pending_ids = {id(item) for item in self.pending_items}

        def visible(item: Dict[str, Any]) -> bool:
            status = str(item.get("status", ""))
            if filter_value == "待复核" and id(item) not in pending_ids:
                return False
            if filter_value == "已入账" and status != "已确认":
                return False
            if filter_value == "已拦截" and status not in {
                "重复票据", "不可入账", "识别失败", "跳过"
            }:
                return False
            if filter_value in {"进项", "销项"} and item.get("invoice_type") != filter_value:
                return False
            if query:
                searchable = " ".join(
                    str(item.get(key, ""))
                    for key in (
                        "file_name", "invoice_code", "invoice_no", "description",
                        "seller", "buyer", "matched_subject", "counter_subject",
                        "source_reference",
                    )
                ).casefold()
                if query not in searchable:
                    return False
            return True

        restored_selection = []
        for item in self.imported_items:
            if not visible(item):
                continue
            status_mark = (
                "○" if item["status"] == "待处理"
                else "✓" if item["status"] == "已确认"
                else "!" if item["status"] == "重复票据"
                else "×" if item["status"] in {"不可入账", "识别失败"}
                else "?"
            )
            iid = f"invoice-{id(item)}"
            self._tree_item_map[iid] = item
            self.tree.insert("", tk.END, iid=iid, values=(
                status_mark,
                str(item.get("file_name", ""))[:15],
                str(item.get("description", ""))[:20],
                f"¥{float(item.get('amount', 0) or 0):.2f}",
                str(item.get("matched_subject") or "")[:15]
            ))
            if id(item) in selected_ids:
                restored_selection.append(iid)
        if restored_selection:
            self.tree.selection_set(restored_selection)
        self._update_selection_state()

    def _selected_review_items(self) -> List[Dict[str, Any]]:
        pending_ids = {id(item) for item in self.pending_items}
        return [
            self._tree_item_map[iid]
            for iid in self.tree.selection()
            if iid in self._tree_item_map and id(self._tree_item_map[iid]) in pending_ids
        ]

    def _update_selection_state(self):
        if not hasattr(self, "selected_post_btn"):
            return
        count = len(self._selected_review_items())
        self.selected_post_btn.configure(state="normal" if count else "disabled")
        if count > 1:
            self.status_var.set(f"已选择 {count} 张待复核票据，可批量修改或入账")

    def _select_all_pending(self):
        pending_ids = {id(item) for item in self.pending_items}
        iids = [
            iid for iid, item in self._tree_item_map.items() if id(item) in pending_ids
        ]
        self.tree.selection_set(iids)
        self._update_selection_state()

    def _clear_tree_selection(self):
        self.tree.selection_remove(self.tree.selection())
        self._update_selection_state()

    def _bulk_edit_selected(self):
        if not self._ensure_audit_writable():
            return
        items = self._selected_review_items()
        if not items:
            messagebox.showinfo("请选择票据", "请先在左侧列表中选择一张或多张待复核票据")
            return

        dialog = tk.Toplevel(self)
        dialog.title(f"批量修改 {len(items)} 张票据")
        dialog.configure(bg=BG)
        dialog.transient(self.winfo_toplevel())
        dialog.grab_set()
        dialog.geometry("700x590")

        tk.Label(
            dialog,
            text=f"已选择 {len(items)} 张票据",
            font=FONT_T,
            bg=BG,
            fg=DARK,
        ).pack(anchor="w", padx=18, pady=(16, 4))
        tk.Label(
            dialog,
            text="仅勾选的字段会应用到全部所选票据；未勾选字段保持原值。",
            font=FONT_S,
            bg="#E8F3FC",
            fg=DARK,
            padx=8,
            pady=6,
        ).pack(fill="x", padx=18, pady=(0, 10))

        body = tk.Frame(dialog, bg=BG)
        body.pack(fill="both", expand=True, padx=18)
        controls: Dict[str, tuple[tk.BooleanVar, tk.StringVar]] = {}

        def add_row(key: str, label: str, values=None):
            row = tk.Frame(body, bg=BG)
            row.pack(fill="x", pady=5)
            enabled = tk.BooleanVar(value=False)
            value = tk.StringVar()
            ttk.Checkbutton(row, variable=enabled).pack(side="left")
            tk.Label(row, text=label, font=FONT_B, bg=BG, width=13, anchor="e").pack(
                side="left", padx=(2, 8)
            )
            if values is None:
                widget = tk.Entry(row, textvariable=value, font=FONT, relief="solid", bd=1)
            else:
                widget = ttk.Combobox(
                    row, textvariable=value, values=values, state="readonly", font=FONT
                )
            widget.pack(side="left", fill="x", expand=True, ipady=3)
            controls[key] = (enabled, value)

        add_row("matched_subject", "会计科目", self.subject_options)
        add_row("counter_subject", "对方科目", self.subject_options)
        add_row("invoice_type", "票据方向", ["进项", "销项"])
        add_row("description", "业务摘要")
        add_row("invoice_date", "开票日期")
        add_row("seller", "销售方")
        add_row("buyer", "购买方")

        def confirm():
            updates = {
                key: value.get().strip()
                for key, (enabled, value) in controls.items()
                if enabled.get()
            }
            if not updates:
                messagebox.showwarning("未选择字段", "请至少勾选一个要批量修改的字段", parent=dialog)
                return
            empty_required = [
                key for key in ("matched_subject", "counter_subject", "invoice_type", "description")
                if key in updates and not updates[key]
            ]
            if empty_required:
                messagebox.showwarning("内容不能为空", "已勾选的科目、方向或摘要不能为空", parent=dialog)
                return
            if "invoice_date" in updates:
                try:
                    datetime.strptime(updates["invoice_date"], "%Y-%m-%d")
                except ValueError:
                    messagebox.showwarning(
                        "日期格式", "开票日期请填写为 YYYY-MM-DD，例如 2026-07-29", parent=dialog
                    )
                    return
            before_by_id = {id(item): review_snapshot(item) for item in items}
            changed = apply_bulk_review_updates(items, updates)
            if not changed:
                messagebox.showinfo("无需修改", "所选票据的字段已经是目标值", parent=dialog)
                return
            for item in changed:
                if "matched_subject" in updates:
                    item["recommendation_reason"] = "用户批量复核后指定会计科目"
                    item["match_details"] = (
                        f"科目：{item['matched_subject']}\n匹配类型：批量人工复核\n"
                        "推荐理由：用户根据真实业务统一指定"
                    )
            try:
                audited = self._audit_bulk_review_changes(
                    changed, "批量复核修改", before_by_id
                )
                self._persist_review_drafts(audited)
            except Exception as exc:
                for changed_item in changed:
                    restore_review_snapshot(changed_item, before_by_id[id(changed_item)])
                messagebox.showerror("修改未保存", str(exc), parent=dialog)
                return
            self._refresh_tree()
            self._update_stats()
            if self._loaded_item in changed:
                self._load_item(self.current_index)
            self.status_var.set(f"已批量修改并保存 {len(changed)} 张票据")
            self.next_step_var.set("批量修改已保存；继续复核，确认无误后可入账选中或全部入账")
            dialog.destroy()

        buttons = tk.Frame(dialog, bg=BG)
        buttons.pack(fill="x", padx=18, pady=14)
        make_btn(buttons, "应用并保存", confirm, color=GREEN, width=13).pack(side="right", padx=4)
        make_btn(buttons, "取消", dialog.destroy, color="#666", width=9).pack(side="right", padx=4)

    def _edit_current_invoice_details(self):
        if not self._ensure_audit_writable():
            return
        selected = self._selected_review_items()
        item = selected[0] if len(selected) == 1 else self._loaded_item
        if item is None or item not in self.pending_items:
            messagebox.showinfo("请选择票据", "请先选择一张待复核票据，再补充票据信息")
            return
        pending_index = next(
            (index for index, pending in enumerate(self.pending_items) if pending is item), None
        )
        if pending_index is not None:
            self._load_item(pending_index)
        if not self._sync_editor_to_item(item):
            return

        before = review_snapshot(item)
        preview_data = {
            "file_name": item.get("file_name", ""),
            "invoice_code": item.get("invoice_code", ""),
            "invoice_no": item.get("invoice_no", ""),
            "invoice_date": item.get("invoice_date", ""),
            "amount": item.get("amount", 0),
            "total_amount": item.get("amount", 0),
            "tax_amount": item.get("tax_amount", 0),
            "seller": item.get("seller", ""),
            "buyer": item.get("buyer", ""),
            "description": item.get("description", ""),
            "matched_subject": item.get("matched_subject", ""),
            "law": item.get("law", ""),
            "confidence": item.get("confidence", 0.0),
        }
        confirmed = show_invoice_preview(
            self,
            preview_data,
            self.vocab,
            confirm_label="保存修改",
            title="补充和修改票据信息",
        )
        if not confirmed:
            return

        new_key = (
            str(confirmed.get("invoice_code", "")).strip().upper(),
            str(confirmed.get("invoice_no", "")).strip().upper(),
        )
        old_key = _invoice_identity(item)
        if any(new_key) and new_key != old_key:
            existing_keys = {
                key for other in self.imported_items if other is not item
                if (key := _invoice_identity(other)) is not None
            }
            if self.store:
                existing_keys.update(
                    key for row in self.store.list_invoices()
                    if (key := _invoice_identity(row)) is not None
                )
            if new_key in existing_keys:
                messagebox.showwarning(
                    "发票号码重复",
                    "修改后的发票代码和号码已存在，未保存本次修改。",
                    parent=self,
                )
                return

        apply_confirmed_review_data(item, confirmed)
        item["status"] = "待定"
        item["needs_review"] = True
        item["match_type"] = "manual_override"
        try:
            changed = self._audit_review_change(item, "票据信息补充修改", before=before)
            self._persist_review_draft(item)
        except Exception as exc:
            messagebox.showerror("修改未保存", str(exc), parent=self)
            return
        self._load_item(self.current_index)
        self._refresh_tree()
        self._update_stats()
        self.status_var.set("票据信息已保存" if changed else "票据信息没有变化")

    def _update_stats(self):
        """更新统计信息"""
        imported = len(self.imported_items)
        pending = sum(1 for i in self.imported_items if i["status"] == "待处理")
        confirmed = len(self.confirmed_items)
        pending_saved = sum(1 for i in self.imported_items if i["status"] == "待定")
        duplicates = sum(1 for i in self.imported_items if i["status"] == "重复票据")
        failed = sum(
            1 for i in self.imported_items
            if i["status"] in {"不可入账", "识别失败"}
        )

        self.stats_var.set(
            f"导入：{imported}  待处理：{pending}  已确认：{confirmed}  "
            f"待定：{pending_saved}  重复：{duplicates}  不可入账/失败：{failed}"
        )
        if hasattr(self, "bulk_post_btn"):
            self.bulk_post_btn.configure(
                state="normal" if self.pending_items else "disabled"
            )

    def _on_tree_click(self, event):
        """列表点击"""
        item = self.tree.identify_row(event.y)
        if not item:
            return

        selected = self._tree_item_map.get(item)
        if selected is None:
            return
        pending_idx = next(
            (
                pending_index
                for pending_index, pending_item in enumerate(self.pending_items)
                if pending_item is selected
            ),
            None,
        )
        if pending_idx is None:
            warnings = selected.get("warnings") or []
            self.status_var.set(
                warnings[-1] if warnings else f"该票据当前状态：{selected.get('status', '')}"
            )
            return
        self._load_item(pending_idx)

    def _on_tree_double_click(self, event):
        """Select the row and open a direct subject chooser."""
        row_id = self.tree.identify_row(event.y)
        if not row_id:
            return
        selected = self._tree_item_map.get(row_id)
        if selected is None:
            return
        pending_index = next(
            (index for index, item in enumerate(self.pending_items) if item is selected),
            None,
        )
        if pending_index is None:
            self.status_var.set(
                "该票据不能修改科目：重复、作废或识别失败的票据不会进入记账队列"
            )
            return
        self._load_item(pending_index)
        self._show_subject_chooser(selected)

    def _show_subject_chooser(self, item: Dict):
        dialog = tk.Toplevel(self)
        dialog.title("修改推荐科目")
        dialog.configure(bg=BG)
        dialog.transient(self.winfo_toplevel())
        dialog.grab_set()
        dialog.geometry("620x430")

        tk.Label(
            dialog, text="双击票据修改科目", font=FONT_T, bg=BG, fg=DARK,
        ).pack(anchor="w", padx=18, pady=(16, 4))
        tk.Label(
            dialog, text=str(item.get("description", ""))[:180], font=FONT_S,
            bg=BG, fg="#555", wraplength=580, justify="left",
        ).pack(anchor="w", padx=18, pady=(0, 10))

        search_var = tk.StringVar()
        tk.Entry(dialog, textvariable=search_var, font=FONT, relief="solid", bd=1).pack(
            fill="x", padx=18, pady=(0, 8), ipady=4
        )
        listbox = tk.Listbox(dialog, font=FONT, selectmode="browse")
        scrollbar = ttk.Scrollbar(dialog, orient="vertical", command=listbox.yview)
        listbox.configure(yscrollcommand=scrollbar.set)
        listbox.pack(side="left", fill="both", expand=True, padx=(18, 0), pady=(0, 54))
        scrollbar.pack(side="left", fill="y", pady=(0, 54))

        visible_options = []

        def refresh(*_args):
            query = search_var.get().strip().casefold()
            visible_options[:] = [
                option for option in self.subject_options
                if not query or query in option.casefold()
            ]
            listbox.delete(0, "end")
            for option in visible_options:
                listbox.insert("end", option)
            current = str(item.get("matched_subject", ""))
            if current in visible_options:
                index = visible_options.index(current)
                listbox.selection_set(index)
                listbox.see(index)

        def confirm(event=None):
            if not self._ensure_audit_writable():
                return
            selection = listbox.curselection()
            if not selection:
                messagebox.showwarning("请选择科目", "请先选择一个会计科目", parent=dialog)
                return
            subject = visible_options[selection[0]]
            before = review_snapshot(item)
            item["matched_subject"] = subject
            item["match_type"] = "manual_override"
            item["match_score"] = 100.0
            item["recommendation_reason"] = "用户在批量复核中手工修改"
            item["match_details"] = (
                f"科目：{subject}\n匹配类型：人工复核修改\n"
                "推荐理由：用户已根据真实业务人工确认"
            )
            item["status"] = "待定"
            try:
                self._audit_review_change(item, "单张科目修改", before=before)
                self._persist_review_draft(item)
            except Exception as exc:
                messagebox.showerror("修改未保存", str(exc), parent=dialog)
                return
            self.confirm_subject_var.set(subject)
            self._refresh_tree()
            self._update_stats()
            self.status_var.set(f"已修改推荐科目：{subject}")
            dialog.destroy()

        search_var.trace_add("write", refresh)
        listbox.bind("<Double-1>", confirm)
        button_row = tk.Frame(dialog, bg=BG)
        button_row.place(relx=0, rely=1, relwidth=1, y=-50, height=50)
        make_btn(button_row, "确认修改", confirm, color=GREEN, width=12).pack(
            side="right", padx=(6, 18), pady=8
        )
        make_btn(button_row, "取消", dialog.destroy, color="#666", width=8).pack(
            side="right", pady=8
        )
        refresh()
        dialog.bind("<Return>", confirm)

    def _on_editor_changed(self, _event=None):
        if self.current_index >= len(self.pending_items):
            return
        item = self.pending_items[self.current_index]
        item["matched_subject"] = self.confirm_subject_var.get().strip()
        invoice_type = self.confirm_invoice_type_var.get().strip()
        if invoice_type in {"进项", "销项"}:
            item["invoice_type"] = invoice_type
            if _event is not None and getattr(_event, "widget", None) is self.confirm_invoice_type_combo:
                direction = "贷方" if invoice_type == "销项" else "借方"
                item["direction"] = direction
                self.confirm_dir_var.set(direction)
        item["needs_review"] = True
        item["match_type"] = "manual_override"
        item["status"] = "待定"
        self._refresh_tree()
        self._update_stats()

    def _load_item(self, idx: int):
        """加载指定索引的项目"""
        if 0 <= idx < len(self.pending_items):
            next_item = self.pending_items[idx]
            if self._loaded_item is not None and self._loaded_item is not next_item:
                self._sync_editor_to_item(self._loaded_item, show_errors=False)
            self.current_index = idx
            item = next_item
            self._loaded_item = item

            self.index_var.set(f"当前：{idx+1} / {len(self.pending_items)}")
            self.confirm_file_var.set(item["file_name"])
            self.confirm_desc_var.set(item["description"])
            self.confirm_amount_var.set(str(item["amount"]))
            self.confirm_subject_var.set(item.get("matched_subject", ""))
            counter_subject = item.get("counter_subject", "")
            if not counter_subject and self.store:
                counter_subject = self.store.get_settings()["accounting"].get(
                    "default_cash_subject", ""
                )
            if counter_subject in self.subject_options:
                self.confirm_counter_subject_var.set(counter_subject)
            else:
                self.confirm_counter_subject_var.set("")
            self.confirm_invoice_type_var.set(item.get("invoice_type", "进项"))
            self.confirm_dir_var.set(item.get("direction", "借方"))

            self.match_result_text.configure(state="normal")
            self.match_result_text.delete("1.0", "end")
            if item.get("matched_subject"):
                details = item.get("match_details") or (
                    f"科目：{item['matched_subject']}\n"
                    f"置信度：{item.get('match_score', 0):.1f}\n"
                    f"匹配类型：{item.get('match_type', '')}"
                )
                self.match_result_text.insert("1.0", details)
            else:
                self.match_result_text.insert("1.0", "尚未匹配，请点击「重新匹配」")
            self.match_result_text.configure(state="disabled")

    def _re_match(self):
        """重新匹配 - 增强模糊引导"""
        if not self.semantic_matcher:
            messagebox.showerror("错误", "模型尚未就绪")
            return

        if self.current_index >= len(self.pending_items):
            return

        item = self.pending_items[self.current_index]
        desc = item["description"]

        completed = getattr(self, "_completed_model_match", None)
        if completed is not None:
            matches = completed
            self._completed_model_match = None
        else:
            if self._match_in_progress:
                return
            try:
                matches = self.semantic_matcher.match_rules(desc)
            except Exception as exc:
                self._show_match_error(exc)
                return
            if not matches:
                self._begin_model_match(desc)
                return

        self.status_var.set("正在匹配...")

        try:
            if not matches:
                # 无匹配结果时显示引导提示
                self._show_no_match_guidance(desc, item)
                self.status_var.set("就绪")
                return

            # 检查冲突
            conflict_records = []
            for match in matches:
                record = match.get("record", {})
                if not record:
                    continue
                subject = record.get("subject", "")
                if not any(r.get("record", {}).get("subject") == subject for r in conflict_records):
                    conflict_records.append(match)

            # 检查词汇冲突
            matched_words = set(m.get("matched_word", "") for m in matches if m.get("matched_word"))
            word_to_subjects = {}
            for word in matched_words:
                for m in matches:
                    subject = m.get("record", {}).get("subject", "")
                    if subject:
                        if word not in word_to_subjects:
                            word_to_subjects[word] = []
                        word_to_subjects[word].append(m.get("record", {}))

            real_conflicts = []
            for word, records in word_to_subjects.items():
                subjects = [r.get("subject") for r in records if r.get("subject")]
                subjects = [s for s in subjects if s]
                if len(set(subjects)) > 1:
                    real_conflicts.append({
                        "word": word,
                        "records": records,
                        "query": desc
                    })

            if real_conflicts:
                def on_conflict_selected(selected):
                    if selected:
                        self._apply_match(selected, item)
                        self.match_result_text.configure(state="normal")
                        self.match_result_text.delete("1.0", "end")
                        details = MR.format_match_details(selected)
                        item["match_details"] = details
                        self.match_result_text.insert("1.0", details)
                        self.match_result_text.configure(state="disabled")
                        self.status_var.set("就绪")

                conflict_dialog.show_conflict_selection(
                    self, desc, real_conflicts[0]["records"], on_conflict_selected
                )
            elif len(conflict_records) == 1:
                record = conflict_records[0].get("record", {})
                if record:
                    self._apply_match(record, item)
                    self.match_result_text.configure(state="normal")
                    self.match_result_text.delete("1.0", "end")
                    details = MR.format_match_details(conflict_records[0])
                    item["match_details"] = details
                    self.match_result_text.insert("1.0", details)
                    self.match_result_text.configure(state="disabled")
                    self.status_var.set("就绪")
            else:
                self._show_multi_match_dialog(desc, conflict_records, item)

        except Exception as e:
            self._show_match_error(e)

    def _begin_model_match(self, desc: str):
        self._match_in_progress = True
        self.status_var.set("模型正在处理模糊语义...")
        self._match_loading = ApproxProgressDialog(
            self.winfo_toplevel(),
            "正在处理模糊语义",
            [
                "核对全部规则词库分类",
                "分析票据摘要与分类规则",
                "生成候选会计科目",
                "整理规则依据和推荐理由",
            ],
            expected_seconds=2.0,
        )

        def run_model_match():
            try:
                matches = self.semantic_matcher.match_with_ai(desc)
                error = None
            except Exception as exc:
                matches = []
                error = exc
            try:
                self.after(0, lambda: self._finish_model_match(matches, error))
            except tk.TclError:
                pass

        threading.Thread(
            target=run_model_match, name="batch-semantic-match", daemon=True
        ).start()

    def _finish_model_match(self, matches: List[Dict], error):
        self._match_in_progress = False
        dialog = self._match_loading
        self._match_loading = None

        def apply_result():
            if error:
                self._show_match_error(error)
                return
            self._completed_model_match = matches
            self._re_match()

        if error:
            dialog.fail("模型查询失败", callback=apply_result)
        else:
            dialog.complete("语义分析完成", callback=apply_result)

    def _show_match_error(self, error):
        self.match_result_text.configure(state="normal")
        self.match_result_text.delete("1.0", "end")
        self.match_result_text.insert("1.0", f"匹配失败：{error}")
        self.match_result_text.configure(state="disabled")
        self.status_var.set("就绪")

    def _show_no_match_guidance(self, query: str, item: Dict):
        """显示无匹配时的引导提示"""
        self.match_result_text.configure(state="normal")
        self.match_result_text.delete("1.0", "end")

        guidance = f"⚠ 未找到精确匹配的科目\n\n"
        guidance += f"查询：「{query}」\n\n"
        guidance += f"💡 建议：\n"
        guidance += f"1. 检查业务描述是否准确\n"
        guidance += f"2. 尝试使用更专业的会计术语\n"
        guidance += f"3. 点击「查看依据」查看科目列表\n"
        guidance += f"4. 手动选择最相关的科目\n\n"
        guidance += f"常见科目参考：\n"
        guidance += f"- 办公用品 → 办公费 (30201)\n"
        guidance += f"- 差旅住宿 → 差旅费 (30211)\n"
        guidance += f"- 培训学习 → 培训费 (30216)\n"
        guidance += f"- 车辆加油 → 公务用车运行维护费 (30225)"

        self.match_result_text.insert("1.0", guidance)
        self.match_result_text.configure(state="disabled")

    def _show_multi_match_dialog(self, query: str, matches: List[Dict], item: Dict):
        """显示多匹配选择对话框 - 增强版，显示区分规则"""
        d = tk.Toplevel(self)
        d.title("多个科目匹配 - 请选择")
        d.configure(bg=BG)
        d.grab_set()

        tk.Label(d, text=f"查询：「{query}」匹配到 {len(matches)} 个科目",
                 font=FONT_T, bg=BG, fg=BLUE).pack(pady=(14, 4))

        var = tk.IntVar(value=-1)

        canvas = tk.Canvas(d, bg=BG, highlightthickness=0, width=800)
        csb = ttk.Scrollbar(d, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=csb.set)

        canvas.pack(side="left", fill="both", expand=True, padx=(20, 0), pady=8)
        csb.pack(side="right", fill="y", padx=(0, 20))

        sf = tk.Frame(canvas, bg=BG)
        canvas.create_window((0, 0), window=sf, anchor="nw")

        for i, match in enumerate(matches):
            record = match.get("record", {})
            if not record:
                continue

            subject = record.get("subject", "")
            code = record.get("code", "")
            score = match.get("score", 0)
            match_type = match.get("match_type", "")
            matched_word = match.get("matched_word", "")
            distinction_rule = record.get("distinction_rule", "")
            rule_category = match.get("rule_category", "")
            rule_basis = match.get("rule_basis", "")
            recommendation_reason = match.get("recommendation_reason", "")

            card = tk.Frame(sf, bg=WHITE, relief="solid", bd=1)
            card.pack(fill="x", pady=4, padx=(0, 10))

            # 单选按钮和科目名称
            header = tk.Frame(card, bg=WHITE)
            header.pack(fill="x", padx=8, pady=(6, 2))
            tk.Radiobutton(header, text=f"{code} {subject}", variable=var, value=i,
                          font=FONT_B, bg=WHITE, fg=DARK, anchor="w",
                          activebackground=WHITE).pack(side="left", fill="x", expand=True)

            # 匹配信息
            info = f"匹配词：{matched_word} | 类型：{match_type} | 置信度：{score:.1f}"
            tk.Label(card, text=info, font=FONT_S, bg=WHITE, fg="#666").pack(
                anchor="w", padx=24, pady=(0, 2))

            if match_type == "ai_suggested":
                evidence = (
                    f"规则词库分类：{rule_category}\n"
                    f"规则依据：{rule_basis}\n"
                    f"模型推荐理由：{recommendation_reason}"
                )
                tk.Label(card, text=evidence, font=FONT_S, bg=WHITE, fg="#333",
                         wraplength=720, justify="left").pack(
                    anchor="w", padx=24, pady=(2, 6)
                )

            # 区分规则（如果有冲突）
            if distinction_rule and "区分" in distinction_rule:
                rule_frame = tk.Frame(card, bg="#F8F9FA", relief="solid", bd=1)
                rule_frame.pack(fill="x", padx=8, pady=(2, 6))
                tk.Label(rule_frame, text="区分规则：", font=FONT_S, bg="#F8F9FA",
                        fg="#666", anchor="w").pack(fill="x", padx=6, pady=(4, 2))
                rule_text = tk.Text(rule_frame, font=FONT_S, height=2, wrap="word",
                                  relief="flat", bg="#F8F9FA", fg="#333")
                rule_text.pack(fill="x", padx=6, pady=(0, 4))
                rule_text.insert("1.0", distinction_rule[:150] + "..." if len(distinction_rule) > 150 else distinction_rule)
                rule_text.configure(state="disabled")

        def _on_cfg(e):
            canvas.configure(scrollregion=canvas.bbox("all"))
        sf.bind("<Configure>", _on_cfg)

        d.after(50, lambda: canvas.configure(height=min(sf.winfo_reqheight(), 500)))

        btn_row = tk.Frame(d, bg=BG)
        btn_row.pack(pady=(12, 16))

        def confirm():
            idx = var.get()
            if idx < 0:
                messagebox.showwarning("提示", "请先选择一个科目", parent=d)
                return
            record = matches[idx].get("record")
            d.destroy()
            if record:
                self._apply_match(record, item)
                self.match_result_text.configure(state="normal")
                self.match_result_text.delete("1.0", "end")
                details = MR.format_match_details(matches[idx])
                item["match_details"] = details
                self.match_result_text.insert("1.0", details)
                self.match_result_text.configure(state="disabled")
                self.status_var.set("就绪")

        def cancel():
            d.destroy()
            self.status_var.set("就绪")

        make_btn(btn_row, "✓ 确认选择", confirm, color=GREEN, width=14).pack(side="left", padx=6)
        make_btn(btn_row, "✗ 取消", cancel, color=RED, width=10).pack(side="left", padx=6)

        d.update_idletasks()
        x = self.parent.winfo_rootx() + (self.parent.winfo_width() - d.winfo_width()) // 2
        y = self.parent.winfo_rooty() + 80
        d.geometry(f"+{x}+{y}")

    def _apply_match(self, record: Dict, item: Dict):
        """应用匹配结果"""
        subject = record.get("subject", "")
        item["matched_subject"] = subject
        item["match_score"] = record.get("score", 0)
        item["match_type"] = record.get("match_type", "")
        item["law"] = record.get("law", "")
        item["rule_category"] = record.get("rule_category", "")
        item["rule_basis"] = record.get("rule_basis", "")
        item["recommendation_reason"] = record.get("recommendation_reason", "")
        item["match_details"] = MR.format_match_details(record)

        self.confirm_subject_var.set(subject)
        self._refresh_tree()

    def _view_law(self):
        """查看法律依据"""
        subject = self.confirm_subject_var.get()
        if not subject:
            messagebox.showwarning("提示", "请先选择科目")
            return

        # 查找对应科目的法律依据
        record = next((r for r in self.vocab if r.get("subject") == subject), None)
        if record:
            law = record.get("law", "")

            d = tk.Toplevel(self)
            d.title("法律依据")
            d.configure(bg=BG)
            d.grab_set()

            tk.Label(d, text=f"科目：{subject}", font=FONT_B, bg=BG, fg=DARK).pack(
                pady=(14, 8), padx=20)

            tk.Label(d, text="法律依据：", font=FONT_B, bg=BG).pack(anchor="w", padx=20)

            text_area = tk.Text(d, font=FONT_S, width=60, height=15, wrap="word",
                              relief="solid", bd=1, bg=WHITE)
            text_area.pack(fill="both", expand=True, padx=20, pady=(0, 12))
            text_area.insert("1.0", law or "暂无具体条款")
            text_area.configure(state="disabled")

            make_btn(d, "关闭", d.destroy, width=8).pack(pady=(0, 12))

            d.update_idletasks()
            x = self.parent.winfo_rootx() + (self.parent.winfo_width() - d.winfo_width()) // 2
            y = self.parent.winfo_rooty() + 100
            d.geometry(f"+{x}+{y}")
        else:
            messagebox.showinfo("法律依据", "未找到该科目的法律依据")

    def _sync_editor_to_item(self, item: Dict, show_errors: bool = True) -> bool:
        if item is not self._loaded_item:
            return True
        description = self.confirm_desc_var.get().strip()
        subject = self.confirm_subject_var.get().strip()
        counter_subject = self.confirm_counter_subject_var.get().strip()
        invoice_type = self.confirm_invoice_type_var.get().strip()
        try:
            amount = float(self.confirm_amount_var.get())
        except ValueError:
            if show_errors:
                messagebox.showwarning("金额格式", "金额应填写为数字，例如 128.50")
            return False
        if show_errors and (not description or not subject or not counter_subject):
            messagebox.showwarning("信息不完整", "摘要、会计科目和对方科目不能为空")
            return False
        if show_errors and subject == counter_subject:
            messagebox.showwarning("科目错误", "会计科目和对方科目不能相同")
            return False
        if show_errors and amount <= 0:
            messagebox.showwarning("金额错误", "入账金额必须大于0")
            return False
        item["description"] = description
        item["matched_subject"] = subject
        item["counter_subject"] = counter_subject
        item["invoice_type"] = invoice_type if invoice_type in {"进项", "销项"} else "进项"
        item["direction"] = self.confirm_dir_var.get()
        item["amount"] = amount
        return True

    def _save_pending(self):
        """Save review edits as a draft without posting a voucher."""
        if not self._ensure_audit_writable():
            return
        if self.current_index >= len(self.pending_items):
            return
        item = self.pending_items[self.current_index]
        before = dict(item.get("_audit_snapshot") or review_snapshot(item))
        if not self._sync_editor_to_item(item):
            return
        item["status"] = "待定"
        try:
            changed = self._audit_review_change(item, "单张复核保存", before=before)
            self._persist_review_draft(item)
        except Exception as exc:
            messagebox.showerror("修改未保存", str(exc), parent=self)
            return
        self._refresh_tree()
        self._update_stats()
        self.status_var.set(
            f"修改已保存：{str(item.get('description') or '')[:20]}"
            if changed else "复核草稿已保存，字段未变化"
        )
        self.next_step_var.set("修改已保存；继续复核其他票据，完成后点击“3 全部确认入账”")

    def _build_posting_entry(self, item: Dict) -> Dict[str, Any]:
        amount = round(float(item.get("amount", 0) or 0), 2)
        tax_amount = min(amount, round(abs(float(item.get("tax_amount", 0) or 0)), 2))
        net_amount = round(amount - tax_amount, 2)
        direction = str(item.get("direction", "借方"))
        is_red = item.get("document_type") == "红字发票"
        if is_red:
            direction = "贷方" if direction == "借方" else "借方"
        debit = amount if direction == "借方" else 0.0
        credit = amount if direction == "贷方" else 0.0
        voucher_date = str(item.get("invoice_date", "")).strip()
        datetime.strptime(voucher_date, "%Y-%m-%d")
        subject = str(item.get("matched_subject", "")).strip()
        counter_subject = str(item.get("counter_subject", "")).strip()
        source_type = str(item.get("source_type", ""))
        common = {
            "摘要": str(item.get("description", "")).strip(),
            "date": voucher_date,
            "状态": "已记账",
            "source": "batch",
            "counterparty": item.get("seller", ""),
            "attachment": item.get("filepath", ""),
        }
        if source_type == "platform_excel":
            common["source"] = "batch_platform"
        common["counterparty"] = (
            item.get("counterparty") or item.get("buyer") or item.get("seller", "")
        )
        invoice_type = str(item.get("invoice_type") or (
            "销项" if "收入" in subject and direction == "贷方" else "进项"
        ))
        settings = self.store.get_settings() if self.store else {"tax": {}}
        split_output_tax = invoice_type == "销项" and tax_amount > 0
        if split_output_tax:
            subject_debit = net_amount if direction == "借方" else 0.0
            subject_credit = net_amount if direction == "贷方" else 0.0
            tax_debit = tax_amount if direction == "借方" else 0.0
            tax_credit = tax_amount if direction == "贷方" else 0.0
            counter_debit = 0.0 if direction == "借方" else amount
            counter_credit = amount if direction == "借方" else 0.0
            lines = [
                {
                    **common, "科目": subject, "借方": subject_debit,
                    "贷方": subject_credit, "invoice_no": item.get("invoice_no", ""),
                    "invoice_code": item.get("invoice_code", ""),
                    "tax_amount": tax_amount,
                },
                {
                    **common, "科目": "2221 应交税费-应交增值税",
                    "借方": tax_debit, "贷方": tax_credit,
                },
                {
                    **common, "科目": counter_subject,
                    "借方": counter_debit, "贷方": counter_credit,
                },
            ]
        else:
            lines = [
                {
                    **common, "科目": subject, "借方": debit, "贷方": credit,
                    "invoice_no": item.get("invoice_no", ""),
                    "invoice_code": item.get("invoice_code", ""),
                    "tax_amount": tax_amount,
                },
                {
                    **common, "科目": counter_subject,
                    "借方": credit, "贷方": debit,
                },
            ]

        invoice_source = {
            "tax_excel": "batch_excel",
            "platform_excel": "platform_excel",
        }.get(source_type, "batch")
        return {
            "voucher_date": voucher_date,
            "lines": lines,
            "invoice": {
                "invoice_code": item.get("invoice_code", ""),
                "invoice_no": item.get("invoice_no", ""),
                "invoice_date": voucher_date,
                "invoice_type": invoice_type,
                "document_type": item.get("document_type", "正常发票"),
                "invoice_form": item.get("invoice_form", "普通发票"),
                "seller": item.get("seller", ""),
                "buyer": item.get("buyer", ""),
                "amount": abs(float(item.get("net_amount", net_amount) or net_amount)),
                "tax_amount": tax_amount,
                "total_amount": amount,
                "deductible": bool(
                    settings.get("tax", {}).get("input_vat_deductible")
                    and invoice_type == "进项"
                ),
                "status": "已确认",
                "source": invoice_source,
                "file_path": item.get("filepath", ""),
                "source_reference": item.get("source_reference", ""),
                "order_status": item.get("invoice_status", ""),
                "risk_level": item.get("risk_level", ""),
                "review_note": "；".join(item.get("warnings") or []),
            },
        }

    def _posting_errors(self, items: List[Dict]) -> List[str]:
        errors = []
        for index, item in enumerate(items, start=1):
            missing = []
            if not str(item.get("description", "")).strip():
                missing.append("摘要")
            if not str(item.get("matched_subject", "")).strip():
                missing.append("会计科目")
            if not str(item.get("counter_subject", "")).strip():
                missing.append("对方科目")
            if str(item.get("invoice_type", "")).strip() not in {"进项", "销项"}:
                missing.append("票据方向")
            try:
                if float(item.get("amount", 0) or 0) <= 0:
                    missing.append("有效金额")
            except (TypeError, ValueError):
                missing.append("有效金额")
            try:
                datetime.strptime(str(item.get("invoice_date", "")), "%Y-%m-%d")
            except ValueError:
                missing.append("有效开票日期")
            if item.get("matched_subject") == item.get("counter_subject"):
                missing.append("不同的借贷科目")
            if missing:
                errors.append(
                    f"第{index}张 {item.get('invoice_no') or item.get('file_name', '')}："
                    + "、".join(missing)
                )
        return errors

    def _post_items(self, items: List[Dict]) -> List[Dict[str, Any]]:
        if not self.store:
            raise RuntimeError("账套存储尚未就绪")
        entries = [self._build_posting_entry(item) for item in items]
        return self.store.post_invoice_vouchers(entries)

    def _confirm_item(self):
        """Preview and atomically post the current invoice."""
        if not self._ensure_audit_writable():
            return
        if self.current_index >= len(self.pending_items):
            return

        item = self.pending_items[self.current_index]
        review_before = dict(item.get("_audit_snapshot") or review_snapshot(item))
        if not self._sync_editor_to_item(item):
            return
        errors = self._posting_errors([item])
        if errors:
            messagebox.showwarning("不能入账", errors[0], parent=self)
            return

        preview_data = {
            "file_name": item.get("file_name", ""),
            "invoice_code": item.get("invoice_code", ""),
            "invoice_no": item.get("invoice_no", ""),
            "invoice_date": item.get("invoice_date", ""),
            "amount": item["amount"],
            "total_amount": item["amount"],
            "tax_amount": item.get("tax_amount", 0),
            "seller": item.get("seller", ""),
            "buyer": item.get("buyer", ""),
            "description": item["description"],
            "matched_subject": item["matched_subject"],
            "law": item.get("law", ""),
            "confidence": item.get("confidence", 0.0)
        }
        confirmed_data = show_invoice_preview(self, preview_data, self.vocab)
        if not confirmed_data:
            return
        apply_confirmed_review_data(item, confirmed_data)
        try:
            self._audit_review_change(item, "入账前单张复核修改", before=review_before)
            self._persist_review_draft(item)
        except Exception as exc:
            messagebox.showerror("入账已停止", str(exc), parent=self)
            return
        if not L.log(
            "票据入账确认",
            f"用户确认写入票据 {item.get('invoice_no') or item.get('file_name', '')}",
            before=review_snapshot(item),
            after={"待执行": "生成借贷平衡凭证并登记票据"},
        ):
            messagebox.showerror(
                "入账已停止", "入账确认日志未能写入，账目没有发生变化。", parent=self
            )
            return
        try:
            result = self._post_items([item])[0]
        except Exception as exc:
            L.log(
                "票据入账失败",
                f"{item.get('invoice_no') or item.get('file_name', '')} 未写入账目：{exc}",
                before=review_snapshot(item),
                after={"结果": "未入账"},
            )
            messagebox.showerror(
                "入账失败",
                f"票据没有写入账目，原复核数据仍保留。\n\n原因：{exc}",
                parent=self,
            )
            return
        posting_before = review_snapshot(item)
        item["status"] = "已确认"
        self.pending_items.pop(self.current_index)
        if item.get("_draft_id"):
            self.store.delete_draft(item["_draft_id"])
        self._loaded_item = None
        self.reload_from_store()
        self._refresh_tree()
        self._update_stats()
        logged = L.log(
            "批量导入确认",
            f"{item['description']}/{item['matched_subject']}/{item['direction']} {item['amount']}",
            before=posting_before,
            after={**review_snapshot(item), "凭证号": result["voucher_no"]},
        )
        if not logged:
            messagebox.showerror(
                "入账日志写入失败",
                f"凭证 {result['voucher_no']} 已入账，但结果日志未能写入。"
                "请立即停止继续操作并联系维护人员。",
                parent=self,
            )
        self.status_var.set(f"已入账：{result['voucher_no']}")
        if self.current_index >= len(self.pending_items):
            self.current_index = 0
        if self.pending_items:
            self._load_item(self.current_index)
        else:
            self.next_step_var.set("本批次已全部入账，可到“操作日志”核对处理记录")

    def _confirm_all_items(self):
        """Validate the complete review queue and post it atomically."""
        if not self.pending_items:
            messagebox.showinfo("没有待入账票据", "当前复核队列为空", parent=self)
            return
        if self._loaded_item is not None and not self._sync_editor_to_item(self._loaded_item):
            return
        self._confirm_review_items(list(self.pending_items), "全部确认入账")

    def _confirm_selected_items(self):
        items = self._selected_review_items()
        if not items:
            messagebox.showinfo("请选择票据", "请先选择要入账的待复核票据", parent=self)
            return
        if self._loaded_item is not None and any(item is self._loaded_item for item in items):
            if not self._sync_editor_to_item(self._loaded_item):
                return
        self._confirm_review_items(items, "确认选中入账")

    def _confirm_review_items(self, items: List[Dict[str, Any]], title: str):
        if not self._ensure_audit_writable():
            return
        errors = self._posting_errors(items)
        if errors:
            details = "\n".join(errors[:10])
            if len(errors) > 10:
                details += f"\n另有 {len(errors) - 10} 张票据信息不完整。"
            messagebox.showwarning(
                "入账前检查未通过",
                f"请先修正以下票据；双击列表行可修改科目：\n\n{details}",
                parent=self,
            )
            return
        fallback_count = sum(
            item.get("match_type") == "review_fallback" for item in items
        )
        note = (
            f"其中 {fallback_count} 张使用待复核占位科目，请确认已经人工检查。\n\n"
            if fallback_count else ""
        )
        if not messagebox.askyesno(
            title,
            f"即将一次写入 {len(items)} 张票据及其借贷凭证。\n\n"
            f"{note}请确认票据方向、科目和金额均已复核。是否继续？",
            parent=self,
        ):
            return
        review_before = {
            id(item): dict(item.get("_audit_snapshot") or review_snapshot(item))
            for item in items
        }
        try:
            audited = self._audit_bulk_review_changes(
                items, "入账前批量复核修改", review_before
            )
            self._persist_review_drafts(audited or items)
        except Exception as exc:
            self._refresh_tree()
            self._update_stats()
            if self._loaded_item in items:
                self._load_item(self.current_index)
            messagebox.showerror("批量入账已停止", str(exc), parent=self)
            return
        if not L.log(
            "批量票据入账确认",
            f"用户确认批量写入 {len(items)} 张票据",
            before=[review_snapshot(item) for item in items],
            after={"待执行": "原子生成借贷平衡凭证并登记全部所选票据"},
        ):
            messagebox.showerror(
                "批量入账已停止", "批量入账确认日志未能写入，账目没有发生变化。", parent=self
            )
            return
        self.bulk_post_btn.configure(state="disabled")
        self.selected_post_btn.configure(state="disabled")
        self.next_step_var.set("正在原子写入票据和凭证，请稍候...")
        try:
            results = self._post_items(items)
        except Exception as exc:
            L.log(
                "批量票据入账失败",
                f"{len(items)} 张票据均未写入：{exc}",
                before=[review_snapshot(item) for item in items],
                after={"结果": "本批次原子回滚，未写入票据或凭证"},
            )
            self.bulk_post_btn.configure(state="normal")
            messagebox.showerror(
                "批量入账失败",
                f"本批次没有写入任何票据或凭证，复核队列已保留。\n\n原因：{exc}",
                parent=self,
            )
            self.next_step_var.set("入账失败：请修正提示的问题后再次点击“3 全部确认入账”")
            return
        result_details = []
        draft_ids = []
        for item, result in zip(items, results):
            posting_before = review_snapshot(item)
            item["status"] = "已确认"
            if item.get("_draft_id"):
                draft_ids.append(item["_draft_id"])
            result_details.append({
                "reference": (
                    item.get("invoice_no")
                    or item.get("source_reference")
                    or item.get("file_name")
                ),
                "before": posting_before,
                "after": {**review_snapshot(item), "凭证号": result["voucher_no"]},
            })
        draft_cleanup_error = ""
        try:
            self.store.delete_drafts(draft_ids)
        except Exception as exc:
            draft_cleanup_error = str(exc)
        logged = L.log(
            "批量票据入账",
            f"已写入 {len(results)} 张票据及其借贷平衡凭证",
            before=[detail["before"] for detail in result_details],
            after=[{
                "reference": detail["reference"],
                **detail["after"],
            } for detail in result_details],
        )
        log_failed = not logged
        posted_ids = {id(item) for item in items}
        self.pending_items[:] = [
            item for item in self.pending_items if id(item) not in posted_ids
        ]
        self._loaded_item = None
        self.current_index = 0
        self.reload_from_store()
        self._refresh_tree()
        self._update_stats()
        self.status_var.set(
            "批量凭证已入账，但结果日志写入失败，请停止操作"
            if log_failed else (
                f"已成功入账 {len(results)} 张票据；草稿清理需下次启动自动处理"
                if draft_cleanup_error else f"已成功入账 {len(results)} 张票据"
            )
        )
        if self.pending_items:
            self._load_item(0)
            self.next_step_var.set(
                f"已入账 {len(results)} 张，仍有 {len(self.pending_items)} 张待复核"
            )
        else:
            self.next_step_var.set("本批次已全部入账，可到“操作日志”核对处理记录")
        messagebox.showinfo(
            "批量入账完成" if not log_failed else "入账完成但日志异常",
            (
                f"已写入 {len(results)} 张票据和 {len(results)} 组借贷平衡凭证。"
                if not log_failed and not draft_cleanup_error else
                f"已写入 {len(results)} 张票据；草稿清理未完成，下次启动会自动处理。\n\n"
                f"原因：{draft_cleanup_error}"
                if draft_cleanup_error and not log_failed else
                f"已写入 {len(results)} 张票据，但结果日志未能写入。请停止操作并联系维护人员。"
            ),
            parent=self,
        )

    def _skip_item(self):
        """跳过项目"""
        if not self._ensure_audit_writable():
            return
        if self.current_index >= len(self.pending_items):
            return

        item = self.pending_items[self.current_index]
        before = review_snapshot(item)
        item["status"] = "跳过"
        try:
            self._audit_review_change(item, "跳过本次入账", before=before)
            if item.get("_draft_id") and self.store:
                self.store.delete_draft(item["_draft_id"])
        except Exception as exc:
            messagebox.showerror("跳过失败", str(exc), parent=self)
            return

        self.pending_items.pop(self.current_index)
        self._refresh_tree()
        self._update_stats()

        if self.current_index >= len(self.pending_items):
            self.current_index = 0

        if self.pending_items:
            self._load_item(self.current_index)

    def _prev_item(self):
        """上一条"""
        if self.current_index > 0:
            self._load_item(self.current_index - 1)

    def _next_item(self):
        """下一条"""
        if self.current_index < len(self.pending_items) - 1:
            self._load_item(self.current_index + 1)

    def reload_from_store(self):
        """Reload persisted batch vouchers after startup or archive restore."""
        if not self.store:
            return
        records = self.store.list_vouchers(source="batch")
        groups = {}
        for record in records:
            groups.setdefault(record.get("voucher_no", ""), []).append(record)
        confirmed = []
        for voucher_no, lines in sorted(groups.items()):
            lines.sort(key=lambda row: row.get("line_no", 0))
            if not lines:
                continue
            primary = lines[0]
            confirmed.append({
                "序号": len(confirmed) + 1,
                "时间": str(primary.get("created_at", ""))[11:19],
                "摘要": primary.get("description", ""),
                "科目": primary.get("subject", ""),
                "金额": max(float(primary.get("debit", 0)), float(primary.get("credit", 0))),
                "方向": primary.get("direction", ""),
                "凭证号": voucher_no,
            })
        self.confirmed_items = confirmed
        if hasattr(self, "confirmed_tree"):
            self._refresh_confirmed_tree()
            self._update_stats()

    def _refresh_confirmed_tree(self):
        """刷新已确认列表"""
        for item in self.confirmed_tree.get_children():
            self.confirmed_tree.delete(item)

        for v in self.confirmed_items:
            self.confirmed_tree.insert("", tk.END, values=(
                v["序号"],
                v["时间"],
                v["摘要"][:15],
                v["科目"][:15],
                f"¥{v['金额']:.2f}",
                v["方向"],
                v.get("凭证号", "")
            ))

    def pack_forget(self):
        """隐藏模块"""
        super().pack_forget()

    def pack(self, **kwargs):
        """显示模块"""
        super().pack(**kwargs)

    def set_authenticated(self, active: bool, operator: str = ""):
        """更新当前登录会话状态。"""
        self.authenticated = active
