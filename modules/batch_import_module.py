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
import threading
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


def make_btn(parent, text, cmd, color=BLUE, width=12):
    return tk.Button(parent, text=text, command=cmd,
                     bg=color, fg=WHITE, font=FONT_B,
                     relief="flat", padx=8, pady=4,
                     activebackground=DARK, activeforeground=WHITE,
                     cursor="hand2", width=width)


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

    def _load_vocab(self) -> List[Dict]:
        """加载词库"""
        return load_vocab(
            self.config.vocab_path,
            getattr(self.config, "account_catalog_path", None),
        )

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

        make_btn(row, "选择文件", self._select_files, width=10).pack(side="left", padx=6)
        make_btn(row, "清空", self._clear_files, width=8).pack(side="left", padx=4)

        # 进度条
        self.progress_var = tk.DoubleVar(value=0)
        progress = ttk.Progressbar(file_frame, variable=self.progress_var,
                                   maximum=100, length=300)
        progress.pack(padx=10, pady=(0, 10), anchor="w")

        self.progress_text_var = tk.StringVar(value="")
        tk.Label(file_frame, textvariable=self.progress_text_var, font=FONT_S,
                 bg=BG, fg="#666").pack(anchor="w", padx=10, pady=(0, 6))

        # 操作按钮
        btn_row = tk.Frame(f, bg=BG)
        btn_row.pack(fill="x", padx=12, pady=6)

        self.start_btn = make_btn(btn_row, "开始识别", self._start_recognition, color=GREEN, width=12)
        self.start_btn.pack(side="left", padx=4)
        self.pause_btn = make_btn(btn_row, "暂停", self._pause_recognition, color=ORANGE, width=8)
        self.pause_btn.pack(side="left", padx=4)
        make_btn(btn_row, "取消", self._cancel_recognition, color=RED, width=8).pack(side="left", padx=4)

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

        cols = ("状态", "文件名", "摘要", "金额", "科目")
        self.tree = ttk.Treeview(left_frame, columns=cols, show="headings", height=18)

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

        # 右侧：确认编辑区
        right_frame = tk.LabelFrame(split_frame, text=" 确认编辑 ", font=FONT_B,
                                     bg=BG, fg=DARK, bd=1, relief="groove")
        right_frame.pack(side="right", fill="both", expand=True, padx=(6, 0), ipadx=6)

        self._build_confirm_ui(right_frame)

        # 底部：已确认列表
        bottom_frame = tk.LabelFrame(f, text=" 已确认凭证（本次会话） ", font=FONT_B,
                                     bg=BG, fg=DARK, bd=1, relief="groove")
        bottom_frame.pack(fill="x", padx=12, pady=6)

        confirm_cols = ("序号", "时间", "摘要", "科目", "金额", "方向", "操作")
        self.confirmed_tree = ttk.Treeview(bottom_frame, columns=confirm_cols,
                                           show="headings", height=6)

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
        make_btn(btn_row, "暂存", self._save_pending, color=ORANGE, width=8).pack(side="left", padx=4)
        make_btn(btn_row, "✓ 确认", self._confirm_item, color=GREEN, width=10).pack(side="left", padx=4)
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
                ("图片文件", "*.png *.jpg *.jpeg *.bmp *.tiff"),
                ("PDF文件", "*.pdf"),
                ("所有文件", "*.*")
            ]
        )
        if files:
            self.selected_files = list(files)
            self.file_list_var.set(f"已选择 {len(files)} 个文件")
            self.status_var.set(f"已选择 {len(files)} 个文件")

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

    def _start_recognition(self):
        """开始识别"""
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
        self.progress_var.set(0)
        files = list(self.selected_files)
        self._recognition_failures = []

        def worker():
            failed = 0
            processed = 0
            total = len(files)
            for index, filepath in enumerate(files, start=1):
                while self._pause_event.is_set() and not self._cancel_event.is_set():
                    self._cancel_event.wait(0.1)
                if self._cancel_event.is_set():
                    break
                file_name = Path(filepath).name
                try:
                    item = self.ocr_service.recognize_invoice(Path(filepath))
                    self._apply_automatic_match(item)
                    error = None
                except Exception as exc:
                    item = None
                    error = exc
                    failed += 1
                processed += 1
                try:
                    self.after(
                        0, lambda i=index, t=total, fp=filepath, it=item, err=error:
                        self._accept_recognition_result(i, t, fp, it, err)
                    )
                except tk.TclError:
                    return
            cancelled = self._cancel_event.is_set()
            try:
                self.after(
                    0, lambda: self._finish_recognition(processed, failed, len(files), cancelled)
                )
            except tk.TclError:
                pass

        self._recognition_thread = threading.Thread(
            target=worker, name="batch-ocr-recognition", daemon=True
        )
        self._recognition_thread.start()

    def _accept_recognition_result(self, index, total, filepath, item, error):
        file_name = Path(filepath).name
        self.progress_var.set(index / total * 100)
        self.progress_text_var.set(f"已识别：{file_name} ({index}/{total})")
        if error:
            friendly = (
                f"第{index}张票据“{file_name}”识别失败：{error}。"
                "请确认图片清晰、方向正确，或改用手动录入。"
            )
            self._recognition_failures.append(friendly)
            self.imported_items.append({
                "file_name": file_name, "filepath": filepath, "status": "识别失败",
                "amount": 0.0, "description": friendly, "invoice_date": "",
                "invoice_no": "", "matched_subject": "", "match_score": 0.0,
                "warnings": [friendly],
            })
        else:
            self.imported_items.append(item)
            self.pending_items.append(item)
        self._refresh_tree()
        self._update_stats()

    def _finish_recognition(self, processed, failed, total, cancelled):
        self.start_btn.configure(state="normal")
        self._pause_event.clear()
        self.pause_btn.configure(text="暂停")
        if cancelled:
            self.progress_text_var.set(f"已取消：完成 {processed}/{total}，失败 {failed}")
            self.status_var.set("识别已取消")
        else:
            self.progress_text_var.set(f"识别完成：成功 {processed - failed}，失败 {failed}")
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
        if self.pending_items:
            self._load_item(min(self.current_index, len(self.pending_items) - 1))

    def _apply_automatic_match(self, item: Dict):
        """Use deterministic vocabulary matching without starting the model server."""
        if not self.semantic_matcher or not item.get("description"):
            return
        matches = self.semantic_matcher.match_rules(item["description"])
        if not matches:
            return
        best = matches[0]
        record = best.get("record", {})
        item["matched_subject"] = record.get("subject", "")
        item["match_score"] = float(best.get("score", 0))
        item["match_type"] = best.get("match_type", "")
        item["law"] = record.get("law", "")
        item["match_details"] = MR.format_match_details(best)

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
        for item in self.tree.get_children():
            self.tree.delete(item)

        for i, item in enumerate(self.imported_items):
            status_mark = "○" if item["status"] == "待处理" else ("✓" if item["status"] == "已确认" else "?")
            self.tree.insert("", tk.END, values=(
                status_mark,
                item["file_name"][:15],
                item["description"][:20],
                f"¥{item['amount']:.2f}",
                item.get("matched_subject", "")[:15]
            ))

    def _update_stats(self):
        """更新统计信息"""
        imported = len(self.imported_items)
        pending = sum(1 for i in self.imported_items if i["status"] == "待处理")
        confirmed = len(self.confirmed_items)
        pending_saved = sum(1 for i in self.imported_items if i["status"] == "待定")

        self.stats_var.set(f"导入：{imported}  待处理：{pending}  已确认：{confirmed}  待定：{pending_saved}")

    def _on_tree_click(self, event):
        """列表点击"""
        item = self.tree.identify_row(event.y)
        if not item:
            return

        items = self.tree.get_children()
        idx = items.index(item)
        self._load_item(idx)

    def _on_tree_double_click(self, event):
        """列表双击"""
        self._re_match()

    def _load_item(self, idx: int):
        """加载指定索引的项目"""
        if 0 <= idx < len(self.pending_items):
            self.current_index = idx
            item = self.pending_items[idx]

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

    def _save_pending(self):
        """暂存"""
        if self.current_index >= len(self.pending_items):
            return

        item = self.pending_items[self.current_index]
        item["description"] = self.confirm_desc_var.get().strip()
        item["matched_subject"] = self.confirm_subject_var.get().strip()
        item["counter_subject"] = self.confirm_counter_subject_var.get().strip()
        item["direction"] = self.confirm_dir_var.get()
        try:
            item["amount"] = float(self.confirm_amount_var.get())
        except ValueError:
            messagebox.showwarning("提示", "金额格式不正确")
            return
        item["status"] = "待定"
        if self.store:
            self.store.add_draft({"type": "batch", **item})
        self._refresh_tree()
        self._update_stats()
        self.status_var.set(f"已暂存：{item['description'][:20]}")

    def _confirm_item(self):
        """确认项目 - 强制预览确认"""
        if self.current_index >= len(self.pending_items):
            return

        item = self.pending_items[self.current_index]

        try:
            edited_amount = float(self.confirm_amount_var.get())
        except ValueError:
            messagebox.showwarning("提示", "金额格式不正确")
            return
        edited_desc = self.confirm_desc_var.get().strip()
        edited_subject = self.confirm_subject_var.get().strip()
        counter_subject = self.confirm_counter_subject_var.get().strip()
        if not edited_desc or not edited_subject or not counter_subject:
            messagebox.showwarning("提示", "摘要、科目和对方科目不能为空")
            return
        if edited_subject == counter_subject:
            messagebox.showwarning("提示", "会计科目和对方科目不能相同")
            return

        # 构建预览数据
        preview_data = {
            "file_name": item.get("file_name", ""),
            "invoice_no": item.get("invoice_no", ""),
            "invoice_date": item.get("invoice_date", ""),
            "amount": edited_amount,
            "seller": item.get("seller", ""),
            "buyer": item.get("buyer", ""),
            "description": edited_desc,
            "matched_subject": edited_subject,
            "law": item.get("law", ""),
            "confidence": item.get("confidence", 0.0)
        }

        # 显示预览确认对话框（强制确认）
        confirmed_data = show_invoice_preview(self, preview_data, self.vocab)

        if confirmed_data:
            # 用户确认后，更新item状态
            item["description"] = confirmed_data["description"]
            item["amount"] = confirmed_data["amount"]
            item["matched_subject"] = confirmed_data["subject"]
            item["direction"] = self.confirm_dir_var.get()
            item["status"] = "已确认"

            voucher_no = ""
            if self.store:
                voucher_date = confirmed_data.get("invoice_date") or datetime.now().strftime("%Y-%m-%d")
                try:
                    datetime.strptime(voucher_date, "%Y-%m-%d")
                except ValueError:
                    messagebox.showwarning("日期格式", "开票日期应为 YYYY-MM-DD 格式")
                    return
                direction = item["direction"]
                amount = float(confirmed_data["amount"])
                if amount <= 0:
                    messagebox.showwarning("金额错误", "入账金额必须大于0")
                    return
                debit = amount if direction == "借方" else 0.0
                credit = amount if direction == "贷方" else 0.0
                common = {
                    "摘要": confirmed_data["description"], "date": voucher_date,
                    "状态": "已记账", "source": "batch",
                    "counterparty": confirmed_data.get("seller", ""),
                    "attachment": item.get("filepath", ""),
                }
                tax_amount = float(item.get("tax_amount", 0) or 0)
                lines = [
                    {
                        **common, "科目": confirmed_data["subject"],
                        "借方": debit, "贷方": credit,
                        "invoice_no": confirmed_data.get("invoice_no", ""),
                        "invoice_code": item.get("invoice_code", ""),
                        "tax_amount": tax_amount,
                    },
                    {
                        **common, "科目": counter_subject,
                        "借方": credit, "贷方": debit,
                    },
                ]
                try:
                    added = self.store.add_voucher_lines(lines, voucher_date=voucher_date)
                    voucher_no = added[0]["voucher_no"]
                    settings = self.store.get_settings()
                    is_revenue = "收入" in confirmed_data["subject"] and direction == "贷方"
                    if confirmed_data.get("invoice_no") or item.get("invoice_code"):
                        self.store.upsert_invoice({
                            "invoice_code": item.get("invoice_code", ""),
                            "invoice_no": confirmed_data.get("invoice_no", ""),
                            "invoice_date": voucher_date,
                            "invoice_type": "销项" if is_revenue else "进项",
                            "seller": confirmed_data.get("seller", ""),
                            "buyer": confirmed_data.get("buyer", ""),
                            "amount": max(0.0, amount - tax_amount),
                            "tax_amount": tax_amount,
                            "total_amount": amount,
                            "deductible": bool(
                                settings["tax"].get("input_vat_deductible") and not is_revenue
                            ),
                            "status": "已确认", "source": "batch",
                            "file_path": item.get("filepath", ""),
                        })
                except Exception as exc:
                    messagebox.showerror("入账失败", str(exc))
                    return

            # 添加到已确认列表
            voucher = {
                "序号": len(self.confirmed_items) + 1,
                "时间": datetime.now().strftime("%H:%M:%S"),
                "摘要": confirmed_data["description"],
                "科目": confirmed_data["subject"],
                "金额": confirmed_data["amount"],
                "方向": item["direction"],
                "凭证号": voucher_no,
            }
            if self.store:
                self.reload_from_store()
            else:
                self.confirmed_items.append(voucher)

            # 从待处理列表移除
            self.pending_items.pop(self.current_index)

            self._refresh_tree()
            self._refresh_confirmed_tree()
            self._update_stats()

            # 记录审计日志
            L.log("批量导入确认", f"{confirmed_data['description']}/{confirmed_data['subject']}/{item['direction']} {confirmed_data['amount']}",
                  after={"文件": item["file_name"], "金额": confirmed_data['amount'], "凭证号": voucher_no})

            self.status_var.set(f"已确认：{confirmed_data['description'][:20]}")

            # 加载下一个
            if self.current_index >= len(self.pending_items):
                self.current_index = 0

            if self.pending_items:
                self._load_item(self.current_index)

    def _skip_item(self):
        """跳过项目"""
        if self.current_index >= len(self.pending_items):
            return

        item = self.pending_items[self.current_index]
        item["status"] = "跳过"

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
