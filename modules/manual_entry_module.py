#!/usr/bin/env python3
import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
# -*- coding: utf-8 -*-
"""
manual_entry_module.py - 手工入账模块
提供完整的手工入账功能，包括智能匹配、科目确认、借贷平衡检查
"""

import sys
# 添加项目根目录到路径
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
import json
import threading
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
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

# 导入冲突对话框
from modules import conflict_dialog
import model_runner as MR
from modules.loading_dialog import ApproxProgressDialog
from modules.vocabulary_module import load_vocab


def make_btn(parent, text, cmd, color=BLUE, width=12):
    return tk.Button(parent, text=text, command=cmd,
                     bg=color, fg=WHITE, font=FONT_B,
                     relief="flat", padx=8, pady=4,
                     activebackground=DARK, activeforeground=WHITE,
                     cursor="hand2", width=width)


def show_law_dialog(parent, subject, law):
    """显示法律依据对话框"""
    d = tk.Toplevel(parent)
    d.title("法律依据")
    d.configure(bg=BG)
    d.grab_set()
    d.resizable(False, False)

    tk.Label(d, text=f"科目：{subject}", font=FONT_B, bg=BG, fg=DARK).pack(
        pady=(14, 4), padx=20)
    tk.Label(d, text="适用法规/准则：", font=FONT_B, bg=BG).pack(
        anchor="w", padx=20)
    tf = tk.Frame(d, bg=BG)
    tf.pack(padx=20, pady=6, fill="both")
    txt = tk.Text(tf, font=FONT_S, width=60, height=12, wrap="word",
                  relief="solid", bd=1, bg=WHITE)
    sb = ttk.Scrollbar(tf, orient="vertical", command=txt.yview)
    txt.configure(yscrollcommand=sb.set)
    txt.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")
    txt.insert("1.0", law or "暂无具体条款")
    txt.configure(state="disabled")
    make_btn(d, "确认", d.destroy, width=10).pack(pady=(4, 14))
    d.update_idletasks()
    x = parent.winfo_rootx() + (parent.winfo_width() - d.winfo_width()) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - d.winfo_height()) // 2
    d.geometry(f"+{x}+{y}")


class ManualEntryModule(tk.Frame):
    """手工入账模块"""

    def __init__(self, parent, config, semantic_matcher, authenticated=False, store=None):
        super().__init__(parent, bg=BG)
        self.parent = parent
        self.config = config
        self.semantic_matcher = semantic_matcher
        self.authenticated = authenticated
        self.store = store

        # 凭证数据
        self.vouchers: List[Dict] = []
        self._pair_seq = 0
        self._match_in_progress = False
        self._match_loading = None

        # 加载科目列表
        self.vocab = self._load_vocab()
        enabled_codes = set(self.store.enabled_account_codes()) if self.store else set()
        detail_subjects = [
            row["subject"] for row in self.vocab
            if row.get("subject") and (
                not enabled_codes or str(row.get("subject_code", "")) in enabled_codes
            )
        ]
        catalog_subjects = [
            f"{row.get('code', '')} {row.get('name', '')}".strip()
            for row in (self.store.enabled_accounts() if self.store else [])
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
        f = tk.LabelFrame(self, text=" 手工入账模式 ", font=FONT_T,
                          bg=BG, fg=DARK, bd=1, relief="groove")
        f.pack(fill="both", expand=True, pady=6)

        # 工具栏
        tool = tk.Frame(f, bg=BG, pady=6)
        tool.pack(fill="x", padx=12)

        self.status_var = tk.StringVar(value="就绪")
        tk.Label(tool, textvariable=self.status_var, font=FONT_S, bg=BG,
                 fg="#666").pack(side="left")

        # 平衡状态
        self.balance_var = tk.StringVar(value="借贷平衡：待录入")
        self.balance_lbl = tk.Label(tool, textvariable=self.balance_var,
                                    font=FONT_S, bg=BG, fg="#888")
        self.balance_lbl.pack(side="right")

        # 摘要输入区
        input_frame = tk.LabelFrame(f, text=" 业务摘要录入 ", font=FONT_B,
                                     bg=BG, fg=DARK, bd=1, relief="groove")
        input_frame.pack(fill="x", padx=12, pady=8)

        tk.Label(input_frame, text="业务摘要：", font=FONT_B, bg=BG).grid(
            row=0, column=0, sticky="w", padx=10, pady=10)
        self.entry_desc = tk.Entry(input_frame, font=FONT, width=35, relief="solid", bd=1)
        self.entry_desc.grid(row=0, column=1, columnspan=2, sticky="ew", padx=6)
        self.entry_desc.bind("<Return>", lambda e: self._on_match())
        make_btn(input_frame, "智能匹配", self._on_match, width=12).grid(
            row=0, column=3, padx=6)

        # 匹配结果
        self.match_result_var = tk.StringVar(value="请输入摘要后点击智能匹配")
        result_label = tk.Label(input_frame, textvariable=self.match_result_var,
                                 font=FONT_S, bg=YELLOW, fg="#333",
                                 wraplength=500, justify="left", anchor="w",
                                 relief="solid", bd=1, padx=10, pady=8)
        result_label.grid(row=1, column=0, columnspan=4, sticky="ew", padx=10, pady=6)

        # 科目选择
        tk.Label(input_frame, text="会计科目：", font=FONT_B, bg=BG).grid(
            row=2, column=0, sticky="w", padx=10, pady=8)
        self.subject_var = tk.StringVar()
        self.entry_subject = ttk.Combobox(input_frame, textvariable=self.subject_var,
                                         values=self.subject_options, font=FONT,
                                         width=35, state="readonly")
        self.entry_subject.grid(row=2, column=1, sticky="ew", padx=6)

        tk.Label(input_frame, text="对方科目：", font=FONT_B, bg=BG).grid(
            row=2, column=2, sticky="w", padx=10, pady=8)
        self.counter_subject_var = tk.StringVar()
        self.entry_counter_subject = ttk.Combobox(
            input_frame, textvariable=self.counter_subject_var,
            values=self.subject_options, font=FONT, width=30, state="readonly"
        )
        self.entry_counter_subject.grid(row=2, column=3, sticky="ew", padx=6)
        default_counter = ""
        if self.store:
            default_counter = self.store.get_settings()["accounting"].get(
                "default_cash_subject", ""
            )
        if default_counter in self.subject_options:
            self.counter_subject_var.set(default_counter)

        # 金额和方向
        tk.Label(input_frame, text="金额（元）：", font=FONT_B, bg=BG).grid(
            row=3, column=0, sticky="w", padx=10, pady=8)
        self.entry_amount = tk.Entry(input_frame, font=FONT, width=20, relief="solid", bd=1)
        self.entry_amount.grid(row=3, column=1, sticky="w", padx=6)

        tk.Label(input_frame, text="借贷方向：", font=FONT_B, bg=BG).grid(
            row=3, column=2, sticky="w", padx=10)
        self.entry_dir = ttk.Combobox(input_frame, values=["借方", "贷方"],
                                        font=FONT, width=12, state="readonly")
        self.entry_dir.current(0)
        self.entry_dir.grid(row=3, column=3, sticky="w", padx=6)

        tk.Label(input_frame, text="凭证日期：", font=FONT_B, bg=BG).grid(
            row=4, column=0, sticky="w", padx=10, pady=8)
        self.voucher_date_var = tk.StringVar(value=datetime.now().strftime("%Y-%m-%d"))
        tk.Entry(input_frame, textvariable=self.voucher_date_var, font=FONT,
                 relief="solid", bd=1).grid(row=4, column=1, sticky="ew", padx=6)
        tk.Label(input_frame, text="发票号码：", font=FONT_B, bg=BG).grid(
            row=4, column=2, sticky="w", padx=10, pady=8)
        self.invoice_no_var = tk.StringVar()
        tk.Entry(input_frame, textvariable=self.invoice_no_var, font=FONT,
                 relief="solid", bd=1).grid(row=4, column=3, sticky="ew", padx=6)

        tk.Label(input_frame, text="往来单位：", font=FONT_B, bg=BG).grid(
            row=5, column=0, sticky="w", padx=10, pady=8)
        self.counterparty_var = tk.StringVar()
        tk.Entry(input_frame, textvariable=self.counterparty_var, font=FONT,
                 relief="solid", bd=1).grid(row=5, column=1, sticky="ew", padx=6)
        tk.Label(input_frame, text="其中税额：", font=FONT_B, bg=BG).grid(
            row=5, column=2, sticky="w", padx=10, pady=8)
        self.tax_amount_var = tk.StringVar(value="0")
        tk.Entry(input_frame, textvariable=self.tax_amount_var, font=FONT,
                 relief="solid", bd=1).grid(row=5, column=3, sticky="ew", padx=6)

        # 按钮行
        btn_row = tk.Frame(input_frame, bg=BG)
        btn_row.grid(row=6, column=0, columnspan=4, sticky="e", padx=10, pady=10)

        make_btn(btn_row, "清空", self._clear, color="#777", width=8).pack(side="left", padx=4)
        make_btn(btn_row, "校验", self._validate, width=8).pack(side="left", padx=4)
        make_btn(btn_row, "确认入账", self._confirm, color=GREEN, width=12).pack(side="left", padx=4)
        make_btn(btn_row, "暂存", self._save_temp, color=ORANGE, width=8).pack(side="left", padx=4)
        make_btn(btn_row, "草稿箱", self._open_drafts, color="#666", width=8).pack(side="left", padx=4)

        input_frame.columnconfigure(1, weight=1)
        input_frame.columnconfigure(3, weight=1)

        invoice_direction_frame = tk.Frame(f, bg=BG)
        invoice_direction_frame.pack(fill="x", padx=20, pady=(0, 4))
        tk.Label(
            invoice_direction_frame,
            text="票据方向（进项/销项）：",
            font=FONT_B,
            bg=BG,
        ).pack(side="left")
        self.invoice_type_var = tk.StringVar(value="进项")
        self.entry_invoice_type = ttk.Combobox(
            invoice_direction_frame,
            textvariable=self.invoice_type_var,
            values=["进项", "销项"],
            font=FONT,
            width=12,
            state="readonly",
        )
        self.entry_invoice_type.pack(side="left", padx=(6, 16))
        self.entry_invoice_type.bind("<<ComboboxSelected>>", self._sync_invoice_direction)
        tk.Label(
            invoice_direction_frame,
            text="进项=购进取得；销项=销售开具或未开票销售收入",
            font=FONT_S,
            bg=BG,
            fg="#666",
        ).pack(side="left")

        # 凭证列表
        list_frame = tk.LabelFrame(f, text=" 已保存凭证分录 ", font=FONT_B,
                                    bg=BG, fg=DARK, bd=1, relief="groove")
        list_frame.pack(fill="both", expand=True, padx=12, pady=8)

        # 列表工具栏
        list_tool = tk.Frame(list_frame, bg=BG, pady=4)
        list_tool.pack(fill="x", padx=8)

        make_btn(list_tool, "编辑", self._edit_voucher, color=BLUE, width=6).pack(side="left", padx=2)
        make_btn(list_tool, "反过账", self._unpost_voucher, color=ORANGE, width=8).pack(
            side="left", padx=2
        )
        make_btn(list_tool, "删除", self._delete_voucher, color=RED, width=6).pack(side="left", padx=2)
        make_btn(list_tool, "全选", self._select_all, color="#555", width=6).pack(side="left", padx=2)
        make_btn(list_tool, "清空手工", self._clear_all, color="#777", width=8).pack(
            side="right", padx=2
        )

        # 凭证列表
        cols = ("标记", "凭证号", "日期", "摘要", "科目", "借方", "贷方", "状态")
        self.tree = ttk.Treeview(list_frame, columns=cols, show="headings", height=18)
        for c, w in zip(cols, (50, 125, 95, 170, 180, 90, 90, 70)):
            self.tree.heading(c, text=c)
            self.tree.column(c, width=w, anchor="center")
        sb = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self.tree.bind("<Double-1>", self._edit_voucher)
        self.tree.bind("<ButtonRelease-1>", self._on_tree_click)

        self._checked_ids = set()

    def _sync_invoice_direction(self, _event=None):
        invoice_type = self.entry_invoice_type.get().strip()
        if invoice_type in {"进项", "销项"}:
            self.entry_dir.set("贷方" if invoice_type == "销项" else "借方")

    def _on_match(self):
        """执行智能匹配"""
        desc = self.entry_desc.get().strip()
        if not desc:
            messagebox.showwarning("提示", "请先输入业务摘要")
            return

        if not self.semantic_matcher:
            messagebox.showerror("错误", "模型尚未就绪")
            return

        if self._match_in_progress:
            return

        try:
            matches = self.semantic_matcher.match_rules(desc)
        except Exception as exc:
            self.match_result_var.set(f"匹配失败：{exc}")
            return

        if matches:
            self._process_match_results(desc, matches)
            return

        self._match_in_progress = True
        self.status_var.set("模型正在处理模糊语义...")
        self._match_loading = ApproxProgressDialog(
            self.winfo_toplevel(),
            "正在处理模糊语义",
            [
                "核对全部规则词库分类",
                "分析摘要与分类规则的关联",
                "生成候选会计科目",
                "整理规则依据和推荐理由",
            ],
            expected_seconds=2.0,
        )

        def run_model_match():
            try:
                result = self.semantic_matcher.match_with_ai(desc)
                error = None
            except Exception as exc:
                result = []
                error = exc
            try:
                self.after(0, lambda: self._finish_model_match(desc, result, error))
            except tk.TclError:
                pass

        threading.Thread(
            target=run_model_match, name="manual-semantic-match", daemon=True
        ).start()

    def _finish_model_match(self, desc: str, matches: List[Dict], error):
        self._match_in_progress = False
        dialog = self._match_loading
        self._match_loading = None

        def apply_result():
            if error:
                self.match_result_var.set(f"匹配失败：{error}")
                self.status_var.set("就绪")
                return
            self._process_match_results(desc, matches)

        if error:
            dialog.fail("模型查询失败", callback=apply_result)
        else:
            dialog.complete("语义分析完成", callback=apply_result)

    def _process_match_results(self, desc: str, matches: List[Dict]):
        """Render rule or AI match results on Tk's UI thread."""
        try:

            if not matches:
                self.match_result_var.set("⚠ 未找到匹配的科目，请检查词库或手工选择科目")
                self.status_var.set("就绪")
                return

            # 检查是否有冲突
            conflict_records = []
            for match in matches:
                record = match.get("record", {})
                if not record:
                    continue
                subject = record.get("subject", "")
                # 简单检查：同一科目只保留一个
                if not any(r.get("record", {}).get("subject") == subject for r in conflict_records):
                    conflict_records.append(match)

            # 检查是否有同一个词汇对应多个科目
            matched_words = set(m.get("matched_word", "") for m in matches if m.get("matched_word"))
            word_to_subjects = {}
            for word in matched_words:
                for m in matches:
                    subject = m.get("record", {}).get("subject", "")
                    if subject:
                        if word not in word_to_subjects:
                            word_to_subjects[word] = []
                        word_to_subjects[word].append(m.get("record", {}))

            # 找出真正的冲突
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
                # 有冲突，显示冲突选择对话框
                def on_conflict_selected(selected):
                    if selected:
                        self._apply_match(selected)
                        self.match_result_var.set(MR.format_match_details(selected))
                        self.status_var.set("就绪")
                    else:
                        self.match_result_var.set("用户取消了科目选择")
                        self.status_var.set("就绪")

                conflict_dialog.show_conflict_selection(
                    self, desc, real_conflicts[0]["records"], on_conflict_selected
                )
            elif len(conflict_records) == 1:
                # 唯一匹配，直接应用
                record = conflict_records[0].get("record", {})
                if record:
                    self._apply_match(record)
                    self.match_result_var.set(MR.format_match_details(conflict_records[0]))
                    self.status_var.set("就绪")
            else:
                # 多个科目但无词汇冲突，显示选择列表
                self._show_multi_match_dialog(desc, conflict_records)

        except Exception as e:
            self.match_result_var.set(f"❌ 匹配失败：{e}")
            self.status_var.set("就绪")

    def _show_multi_match_dialog(self, query: str, matches: List[Dict]):
        """显示多匹配选择对话框"""
        d = tk.Toplevel(self)
        d.title("多个科目匹配 - 请选择")
        d.configure(bg=BG)
        d.grab_set()

        tk.Label(d, text=f"查询：「{query}」匹配到 {len(matches)} 个科目",
                 font=FONT_T, bg=BG, fg=BLUE).pack(pady=(14, 4))

        var = tk.IntVar(value=-1)

        canvas = tk.Canvas(d, bg=BG, highlightthickness=0, width=650)
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
            score = match.get("score", 0)
            match_type = match.get("match_type", "")
            matched_word = match.get("matched_word", "")
            rule_category = match.get("rule_category", "")
            rule_basis = match.get("rule_basis", "")
            recommendation_reason = match.get("recommendation_reason", "")
            review_message = match.get("review_message", "")

            card = tk.Frame(sf, bg=WHITE, relief="solid", bd=1)
            card.pack(fill="x", pady=4, padx=(0, 10))

            tk.Radiobutton(card, text=subject, variable=var, value=i,
                          font=FONT_B, bg=WHITE, fg=DARK, anchor="w",
                          activebackground=WHITE).pack(anchor="w", padx=8, pady=(6, 2))

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
                if match.get("manual_review_required") and review_message:
                    evidence += f"\n人工拆分复核：{review_message}"
                tk.Label(card, text=evidence, font=FONT_S, bg=WHITE, fg="#333",
                         wraplength=590, justify="left").pack(
                    anchor="w", padx=24, pady=(2, 6)
                )

        def _on_cfg(e):
            canvas.configure(scrollregion=canvas.bbox("all"))
        sf.bind("<Configure>", _on_cfg)

        d.after(50, lambda: canvas.configure(height=min(sf.winfo_reqheight(), 400)))

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
                self._apply_match(record)
                self.match_result_var.set(MR.format_match_details(matches[idx]))
                self.status_var.set("就绪")

        def cancel():
            d.destroy()
            self.match_result_var.set("用户取消了科目选择")
            self.status_var.set("就绪")

        make_btn(btn_row, "✓ 确认选择", confirm, color=GREEN, width=14).pack(side="left", padx=6)
        make_btn(btn_row, "✗ 取消", cancel, color=RED, width=10).pack(side="left", padx=6)

        d.update_idletasks()
        x = self.parent.winfo_rootx() + (self.parent.winfo_width() - d.winfo_width()) // 2
        y = self.parent.winfo_rooty() + 120
        d.geometry(f"+{x}+{y}")

    def _apply_match(self, record: Dict):
        """应用匹配结果"""
        subject = record.get("subject", "")
        law = record.get("law", "")
        self.subject_var.set(subject)

        # 显示法律依据预览
        self.match_result_var.set(f"科目：{subject}\n依据：{law[:100]}..." if len(law) > 100 else law)

    def _validate(self, show_success=True):
        """校验输入"""
        desc = self.entry_desc.get().strip()
        subject = self.subject_var.get().strip()
        counter_subject = self.counter_subject_var.get().strip()
        amount_str = self.entry_amount.get().strip()
        voucher_date = self.voucher_date_var.get().strip()
        tax_amount_str = self.tax_amount_var.get().strip() or "0"

        errors = []

        if not desc:
            errors.append("摘要不能为空")
        if not subject:
            errors.append("请选择科目")
        if not counter_subject:
            errors.append("请选择对方科目")
        if subject and counter_subject and subject == counter_subject:
            errors.append("会计科目和对方科目不能相同")
        if not amount_str:
            errors.append("请输入金额")

        try:
            amount = float(amount_str)
            if amount <= 0:
                errors.append("金额必须大于0")
        except ValueError:
            errors.append("金额格式不正确")

        try:
            tax_amount = float(tax_amount_str)
            if tax_amount < 0:
                errors.append("税额不能小于0")
            elif amount_str and tax_amount > float(amount_str):
                errors.append("税额不能大于凭证金额")
        except ValueError:
            errors.append("税额格式不正确")

        try:
            datetime.strptime(voucher_date, "%Y-%m-%d")
        except ValueError:
            errors.append("凭证日期应为 YYYY-MM-DD 格式")

        if not errors:
            # 检查科目是否在词库中
            if subject not in self.subject_options:
                errors.append("科目不在词库中，请检查")
            if counter_subject not in self.subject_options:
                errors.append("对方科目不在词库中，请检查")

        if errors:
            messagebox.showwarning("校验失败", "\n".join(errors))
            return False

        if show_success:
            messagebox.showinfo("校验通过", "字段完整，借贷分录将自动保持平衡")
        return True

    def _confirm(self):
        """确认入账"""
        desc = self.entry_desc.get().strip()
        subject = self.subject_var.get().strip()
        counter_subject = self.counter_subject_var.get().strip()
        amount_str = self.entry_amount.get().strip()
        direction = self.entry_dir.get()
        invoice_type = self.entry_invoice_type.get().strip()

        if not desc or not subject or not counter_subject or not amount_str:
            messagebox.showwarning("提示", "请填写摘要、科目、对方科目和金额")
            return

        try:
            amount = float(amount_str)
        except ValueError:
            messagebox.showerror("错误", "金额格式不正确")
            return

        # 必须先校验
        if not self._validate(show_success=False):
            return

        # 显示法律依据确认对话框
        # 先查找对应科目的法律依据
        record = next((r for r in self.vocab if r.get("subject") == subject), None)
        if record:
            law = record.get("law", "")

            def proceed():
                self._add_voucher(
                    desc, subject, counter_subject, amount, direction,
                    invoice_type,
                    self.voucher_date_var.get().strip(),
                    self.invoice_no_var.get().strip(),
                    self.counterparty_var.get().strip(),
                    float(self.tax_amount_var.get().strip() or 0),
                )
                self._clear()

            # 显示法律依据并确认
            self._show_confirm_law_dialog(subject, law, proceed)
        else:
            # 没有找到法律依据，直接添加
            self._add_voucher(
                desc, subject, counter_subject, amount, direction,
                invoice_type,
                self.voucher_date_var.get().strip(),
                self.invoice_no_var.get().strip(),
                self.counterparty_var.get().strip(),
                float(self.tax_amount_var.get().strip() or 0),
            )
            self._clear()

    def _show_confirm_law_dialog(self, subject: str, law: str, callback):
        """显示法律依据确认对话框"""
        d = tk.Toplevel(self)
        d.title("法律依据确认")
        d.configure(bg=BG)
        d.grab_set()

        tk.Label(d, text="请确认法律依据", font=FONT_T, bg=BG, fg=DARK).pack(
            pady=(14, 8))

        tk.Label(d, text=f"科目：{subject}", font=FONT_B, bg=BG, fg=DARK).pack(
            padx=20)

        tk.Label(d, text="法律依据：", font=FONT_B, bg=BG).pack(anchor="w", padx=20)

        text_area = tk.Text(d, font=FONT_S, width=60, height=15, wrap="word",
                          relief="solid", bd=1, bg=WHITE)
        sb = ttk.Scrollbar(d, orient="vertical", command=text_area.yview)
        text_area.configure(yscrollcommand=sb.set)
        text_area.pack(fill="both", expand=True, padx=20, pady=(0, 12))
        text_area.insert("1.0", law or "暂无具体条款")
        text_area.configure(state="disabled")
        sb.pack(side="right", fill="y", padx=(0, 20), pady=(0, 12))

        tk.Label(d, text="确认以上法律依据并添加凭证？", font=FONT_B, bg=BG).pack()

        btn_row = tk.Frame(d, bg=BG)
        btn_row.pack(pady=(8, 16))

        make_btn(btn_row, "确认添加", lambda: [callback(), d.destroy()], color=GREEN, width=12).pack(side="left", padx=6)
        make_btn(btn_row, "取消", d.destroy, color="#777", width=8).pack(side="left", padx=6)

        d.update_idletasks()
        x = self.parent.winfo_rootx() + (self.parent.winfo_width() - d.winfo_width()) // 2
        y = self.parent.winfo_rooty() + 150
        d.geometry(f"+{x}+{y}")

    def _add_voucher(self, desc: str, subject: str, counter_subject: str,
                     amount: float, direction: str, invoice_type: str,
                     voucher_date: str,
                     invoice_no: str = "", counterparty: str = "",
                     tax_amount: float = 0.0):
        """Persist a balanced voucher, separating output VAT when supplied."""
        primary_debit = amount if direction == "借方" else 0.0
        primary_credit = amount if direction == "贷方" else 0.0
        code = subject.split(" ", 1)[0]
        is_revenue = code in {"5001", "5051", "5111", "5301"}
        invoice_type = invoice_type if invoice_type in {"进项", "销项"} else "进项"
        is_output_invoice = invoice_type == "销项"
        common = {
            "摘要": desc,
            "date": voucher_date,
            "状态": "已记账",
            "source": "manual",
            "counterparty": counterparty,
        }
        if is_revenue and tax_amount > 0:
            net_amount = round(amount - tax_amount, 2)
            lines = [
                {
                    **common, "科目": subject,
                    "借方": net_amount if direction == "借方" else 0.0,
                    "贷方": net_amount if direction == "贷方" else 0.0,
                    "invoice_no": invoice_no, "tax_amount": tax_amount,
                },
                {
                    **common, "科目": counter_subject,
                    "借方": amount if direction == "贷方" else 0.0,
                    "贷方": amount if direction == "借方" else 0.0,
                },
                {
                    **common, "科目": "2221 应交税费-应交增值税",
                    "借方": tax_amount if direction == "借方" else 0.0,
                    "贷方": tax_amount if direction == "贷方" else 0.0,
                },
            ]
        else:
            lines = [
                {
                    **common, "科目": subject, "借方": primary_debit,
                    "贷方": primary_credit, "invoice_no": invoice_no,
                    "tax_amount": tax_amount,
                },
                {
                    **common, "科目": counter_subject, "借方": primary_credit,
                    "贷方": primary_debit,
                },
            ]
        try:
            if self.store:
                added = self.store.add_voucher_lines(lines, voucher_date=voucher_date)
                document_type = (
                    "未开票收入" if is_output_invoice and "未开票" in desc else
                    "红字发票" if is_output_invoice and any(word in desc for word in ("红字", "红票", "冲红")) else
                    "正常发票"
                )
                if invoice_no or document_type == "未开票收入":
                    settings = self.store.get_settings()
                    self.store.upsert_invoice({
                        "invoice_no": invoice_no,
                        "invoice_date": voucher_date,
                        "invoice_type": invoice_type,
                        "document_type": document_type,
                        "invoice_form": (
                            "增值税专用发票" if "专票" in desc else
                            "无票" if document_type == "未开票收入" else "普通发票"
                        ),
                        "price_tax_mode": "含税",
                        "seller": settings["company"].get("name", "") if is_output_invoice else counterparty,
                        "buyer": counterparty if is_output_invoice else settings["company"].get("name", ""),
                        "amount": max(0.0, amount - tax_amount),
                        "tax_amount": tax_amount,
                        "total_amount": amount,
                        "deductible": bool(settings["tax"].get("input_vat_deductible") and not is_output_invoice),
                        "status": "已确认",
                        "source": "manual",
                    })
                voucher_no = added[0]["voucher_no"]
                self.reload_from_store()
            else:
                voucher_no = f"TEMP-{len(self.vouchers) // 2 + 1:04d}"
                for line_no, line in enumerate(lines, start=1):
                    self.vouchers.append(self._display_record({
                        **line, "voucher_no": voucher_no, "line_no": line_no,
                        "id": f"{voucher_no}-{line_no}", "subject": line["科目"],
                        "description": desc, "debit": line.get("借方", 0),
                        "credit": line.get("贷方", 0), "status": "已记账",
                    }))
                self._refresh_list()
        except Exception as exc:
            messagebox.showerror("入账失败", str(exc), parent=self)
            return

        self.status_var.set(f"已保存凭证 {voucher_no}：{desc}")
        L.log(
            "手工入账", f"{desc}/{subject}/{counter_subject} {amount}",
            after={"凭证号": voucher_no, "借方": amount, "贷方": amount},
        )

    def _save_temp(self):
        """暂存"""
        if not self.store:
            messagebox.showwarning("暂存失败", "当前账套未启用持久化存储")
            return
        payload = {
            "type": "manual",
            "description": self.entry_desc.get().strip(),
            "subject": self.subject_var.get().strip(),
            "counter_subject": self.counter_subject_var.get().strip(),
            "amount": self.entry_amount.get().strip(),
            "direction": self.entry_dir.get(),
            "invoice_type": self.entry_invoice_type.get().strip(),
            "voucher_date": self.voucher_date_var.get().strip(),
            "invoice_no": self.invoice_no_var.get().strip(),
            "counterparty": self.counterparty_var.get().strip(),
            "tax_amount": self.tax_amount_var.get().strip(),
        }
        if not any(str(value).strip() for key, value in payload.items() if key != "type"):
            messagebox.showwarning("提示", "当前没有可暂存的录入内容")
            return
        draft = self.store.add_draft(payload)
        self.status_var.set(f"草稿已保存：{draft['saved_at']}")
        messagebox.showinfo("暂存成功", "当前录入内容已保存到草稿箱")

    def _open_drafts(self):
        if not self.store:
            return
        drafts = [row for row in self.store.list_drafts() if row.get("type") == "manual"]
        if not drafts:
            messagebox.showinfo("草稿箱", "暂无手工录入草稿")
            return
        d = tk.Toplevel(self)
        d.title("手工录入草稿箱")
        d.configure(bg=BG)
        d.geometry("720x400")
        d.transient(self.winfo_toplevel())
        d.grab_set()
        columns = ("保存时间", "摘要", "科目", "金额")
        tree = ttk.Treeview(d, columns=columns, show="headings", height=13)
        for name, width in zip(columns, (150, 260, 190, 90)):
            tree.heading(name, text=name)
            tree.column(name, width=width, anchor="w")
        for index, draft in enumerate(drafts):
            tree.insert("", "end", iid=str(index), values=(
                draft.get("saved_at", ""), draft.get("description", ""),
                draft.get("subject", ""), draft.get("amount", ""),
            ))
        tree.pack(fill="both", expand=True, padx=12, pady=12)

        def selected_draft():
            selected = tree.selection()
            if not selected:
                messagebox.showwarning("提示", "请先选择草稿", parent=d)
                return None
            return drafts[int(selected[0])]

        def load_draft(event=None):
            draft = selected_draft()
            if not draft:
                return
            self._load_draft_values(draft)
            d.destroy()

        def delete_draft():
            draft = selected_draft()
            if not draft:
                return
            self.store.delete_draft(draft["id"])
            d.destroy()
            self._open_drafts()

        buttons = tk.Frame(d, bg=BG)
        buttons.pack(fill="x", padx=12, pady=(0, 12))
        make_btn(buttons, "载入", load_draft, color=GREEN, width=9).pack(side="right", padx=4)
        make_btn(buttons, "删除", delete_draft, color=RED, width=9).pack(side="right", padx=4)
        make_btn(buttons, "关闭", d.destroy, color="#666", width=9).pack(side="right", padx=4)
        tree.bind("<Double-1>", load_draft)

    def _load_draft_values(self, draft):
        self.entry_desc.delete(0, "end")
        self.entry_desc.insert(0, draft.get("description", ""))
        self.subject_var.set(draft.get("subject", ""))
        self.counter_subject_var.set(draft.get("counter_subject", ""))
        self.entry_amount.delete(0, "end")
        self.entry_amount.insert(0, draft.get("amount", ""))
        self.entry_dir.set(draft.get("direction", "借方"))
        self.entry_invoice_type.set(draft.get("invoice_type", "进项"))
        self.voucher_date_var.set(draft.get("voucher_date", datetime.now().strftime("%Y-%m-%d")))
        self.invoice_no_var.set(draft.get("invoice_no", ""))
        self.counterparty_var.set(draft.get("counterparty", ""))
        self.tax_amount_var.set(draft.get("tax_amount", "0"))
        self.status_var.set("已载入草稿，可继续编辑")

    def _clear(self):
        """清空输入"""
        self.entry_desc.delete(0, "end")
        self.entry_amount.delete(0, "end")
        self.subject_var.set("")
        self.entry_dir.current(0)
        self.entry_invoice_type.set("进项")
        self.invoice_no_var.set("")
        self.counterparty_var.set("")
        self.tax_amount_var.set("0")
        self.match_result_var.set("请输入摘要后点击智能匹配")

    @staticmethod
    def _display_record(record: Dict) -> Dict:
        return {
            "id": record.get("id", ""),
            "voucher_no": record.get("voucher_no", ""),
            "line_no": record.get("line_no", 0),
            "日期": record.get("date", record.get("日期", "")),
            "摘要": record.get("description", record.get("摘要", "")),
            "科目": record.get("subject", record.get("科目", "")),
            "金额": max(float(record.get("debit", record.get("借方", 0)) or 0),
                          float(record.get("credit", record.get("贷方", 0)) or 0)),
            "方向": "借方" if float(record.get("debit", record.get("借方", 0)) or 0) else "贷方",
            "借方": float(record.get("debit", record.get("借方", 0)) or 0),
            "贷方": float(record.get("credit", record.get("贷方", 0)) or 0),
            "状态": record.get("status", record.get("状态", "已记账")),
            "invoice_no": record.get("invoice_no", ""),
            "counterparty": record.get("counterparty", ""),
            "tax_amount": float(record.get("tax_amount", 0) or 0),
            "source": record.get("source", "manual"),
        }

    def reload_from_store(self):
        if self.store:
            records = self.store.list_vouchers(include_unposted=True)
            self.vouchers = [self._display_record(row) for row in records]
        if hasattr(self, "tree"):
            self._refresh_list()

    def _refresh_list(self):
        """刷新凭证列表"""
        for item in self.tree.get_children():
            self.tree.delete(item)

        self._checked_ids = set()

        for i, v in enumerate(self.vouchers):
            self.tree.insert("", tk.END, iid=str(i), values=(
                "○", v.get("voucher_no", ""), v.get("日期", ""),
                v["摘要"][:24], v["科目"][:24],
                f"¥{v['借方']:.2f}" if v["借方"] else "-",
                f"¥{v['贷方']:.2f}" if v["贷方"] else "-",
                v["状态"],
            ))

        self._check_balance()

    def _check_balance(self):
        """检查借贷平衡"""
        debit_total = sum(v["借方"] for v in self.vouchers)
        credit_total = sum(v["贷方"] for v in self.vouchers)

        if abs(debit_total - credit_total) < 0.01:
            self.balance_var.set(f"借贷平衡  借方¥{debit_total:.2f}  贷方¥{credit_total:.2f}")
            self.balance_lbl.configure(fg=GREEN)
        else:
            self.balance_var.set(f"借贷不平衡！借¥{debit_total:.2f}  贷¥{credit_total:.2f}  差¥{abs(debit_total-credit_total):.2f}")
            self.balance_lbl.configure(fg=RED)

    def _on_tree_click(self, event):
        """凭证列表点击"""
        item = self.tree.identify_row(event.y)
        if not item:
            return
        idx = int(item)
        if idx in self._checked_ids:
            self._checked_ids.discard(idx)
        else:
            self._checked_ids.add(idx)
        self._update_marks()

    def _update_marks(self):
        """更新标记"""
        for item in self.tree.get_children():
            idx = int(item)
            mark = "●" if idx in self._checked_ids else "○"
            vals = list(self.tree.item(item, "values"))
            vals[0] = mark
            self.tree.item(item, values=vals)

    def _edit_voucher(self, event=None):
        """编辑凭证"""
        if not self._checked_ids:
            messagebox.showwarning("提示", "请先点击○选中要修改的凭证")
            return
        selected = self.vouchers[min(self._checked_ids)]
        voucher_no = selected.get("voucher_no", "")
        group = [row for row in self.vouchers if row.get("voucher_no") == voucher_no]
        group.sort(key=lambda row: row.get("line_no", 0))
        if len(group) != 2:
            messagebox.showerror(
                "这里不能直接编辑",
                "该凭证不是两条分录。请先反过账，再回到工资、折旧或原业务模块重新生成。",
            )
            return
        if group[0].get("source") in {"payroll", "depreciation", "period_close", "tax_accrual"}:
            messagebox.showerror(
                "这里不能直接编辑",
                "系统生成凭证应先反过账，再回到对应业务模块修改并重新生成。",
            )
            return
        primary, counter = group[0], group[1]

        d = tk.Toplevel(self)
        d.title(f"编辑凭证 {voucher_no}")
        d.configure(bg=BG)
        d.geometry("650x520")
        d.transient(self.winfo_toplevel())
        d.grab_set()
        vars_map = {
            "date": tk.StringVar(value=primary.get("日期", "")),
            "description": tk.StringVar(value=primary.get("摘要", "")),
            "subject": tk.StringVar(value=primary.get("科目", "")),
            "counter_subject": tk.StringVar(value=counter.get("科目", "")),
            "amount": tk.StringVar(value=str(primary.get("金额", 0))),
            "direction": tk.StringVar(value=primary.get("方向", "借方")),
            "invoice_no": tk.StringVar(value=primary.get("invoice_no", "")),
            "counterparty": tk.StringVar(value=primary.get("counterparty", "")),
            "tax_amount": tk.StringVar(value=str(primary.get("tax_amount", 0))),
        }
        fields = [
            ("凭证日期", "date", None), ("摘要", "description", None),
            ("会计科目", "subject", self.subject_options),
            ("对方科目", "counter_subject", self.subject_options),
            ("金额", "amount", None), ("借贷方向", "direction", ["借方", "贷方"]),
            ("发票号码", "invoice_no", None), ("往来单位", "counterparty", None),
            ("其中税额", "tax_amount", None),
        ]
        for row, (label, key, options) in enumerate(fields):
            tk.Label(d, text=label, font=FONT_B, bg=BG).grid(
                row=row, column=0, sticky="w", padx=18, pady=7
            )
            if options:
                widget = ttk.Combobox(
                    d, textvariable=vars_map[key], values=options,
                    state="readonly", font=FONT,
                )
            else:
                widget = tk.Entry(d, textvariable=vars_map[key], font=FONT, relief="solid", bd=1)
            widget.grid(row=row, column=1, sticky="ew", padx=(0, 18), pady=7)
        d.columnconfigure(1, weight=1)

        def save_edit():
            try:
                edit_amount = float(vars_map["amount"].get())
                edit_tax = float(vars_map["tax_amount"].get() or 0)
                datetime.strptime(vars_map["date"].get(), "%Y-%m-%d")
            except ValueError:
                messagebox.showwarning("无法保存", "请检查日期、金额和税额格式", parent=d)
                return
            subject = vars_map["subject"].get().strip()
            counter_subject = vars_map["counter_subject"].get().strip()
            if not vars_map["description"].get().strip() or not subject or not counter_subject:
                messagebox.showwarning("无法保存", "摘要和两个科目不能为空", parent=d)
                return
            if subject == counter_subject or edit_amount <= 0 or edit_tax < 0 or edit_tax > edit_amount:
                messagebox.showwarning("无法保存", "请检查科目、金额和税额", parent=d)
                return
            direction = vars_map["direction"].get()
            primary_debit = edit_amount if direction == "借方" else 0.0
            primary_credit = edit_amount if direction == "贷方" else 0.0
            common = {
                "摘要": vars_map["description"].get().strip(),
                "date": vars_map["date"].get(), "状态": "已记账",
                "source": "manual", "counterparty": vars_map["counterparty"].get().strip(),
            }
            lines = [
                {
                    **common, "科目": subject, "借方": primary_debit, "贷方": primary_credit,
                    "invoice_no": vars_map["invoice_no"].get().strip(), "tax_amount": edit_tax,
                },
                {
                    **common, "科目": counter_subject, "借方": primary_credit, "贷方": primary_debit,
                },
            ]
            try:
                self.store.replace_voucher_group(voucher_no, lines, vars_map["date"].get())
            except Exception as exc:
                messagebox.showerror("保存失败", str(exc), parent=d)
                return
            L.log("编辑凭证", voucher_no, before=group, after=lines)
            d.destroy()
            self.reload_from_store()
            self.status_var.set(f"已更新凭证 {voucher_no}")

        footer = tk.Frame(d, bg=BG)
        footer.grid(row=len(fields), column=0, columnspan=2, sticky="e", padx=18, pady=16)
        make_btn(footer, "保存修改", save_edit, color=GREEN, width=11).pack(side="left", padx=4)
        make_btn(footer, "取消", d.destroy, color="#666", width=9).pack(side="left", padx=4)

    def _delete_voucher(self):
        """删除凭证"""
        if not self._checked_ids:
            messagebox.showwarning("提示", "请先点击○选中要删除的凭证")
            return

        if messagebox.askyesno("确认删除", f"确认删除选中的 {len(self._checked_ids)} 条凭证？"):
            voucher_numbers = {
                self.vouchers[idx].get("voucher_no", "") for idx in self._checked_ids
            }
            before = [
                row for row in self.vouchers if row.get("voucher_no") in voucher_numbers
            ]
            generated = [
                row for row in before
                if row.get("source") in {"payroll", "depreciation", "period_close", "tax_accrual"}
            ]
            if generated:
                messagebox.showwarning(
                    "不能直接删除",
                    "选中项包含系统生成凭证。请使用反过账或撤销结转，以便同步恢复业务状态。",
                    parent=self,
                )
                return
            if self.store:
                self.store.delete_voucher_numbers(voucher_numbers)
                self.reload_from_store()
            else:
                self.vouchers = [
                    row for row in self.vouchers if row.get("voucher_no") not in voucher_numbers
                ]
                self._refresh_list()
            L.log("删除凭证", "、".join(sorted(voucher_numbers)), before=before)
            self.status_var.set(f"已删除 {len(voucher_numbers)} 张凭证")

    def _unpost_voucher(self):
        """Keep the original voucher visible while removing it from posted books."""
        if not self._checked_ids:
            messagebox.showwarning("提示", "请先点击○选中要反过账的凭证")
            return
        voucher_numbers = sorted({
            self.vouchers[index].get("voucher_no", "") for index in self._checked_ids
        })
        if not messagebox.askyesno(
            "确认反过账",
            (
                f"将反过账 {len(voucher_numbers)} 张凭证。\n\n"
                "反过账后不再计入账簿和报表，但原凭证仍保留；"
                "双击编辑并保存即可重新入账。"
            ),
            parent=self,
        ):
            return
        completed = []
        try:
            for voucher_no in voucher_numbers:
                self.store.unpost_voucher(voucher_no)
                completed.append(voucher_no)
        except Exception as exc:
            messagebox.showerror(
                "反过账未完成",
                f"已处理 {len(completed)} 张，后续操作已停止。\n\n原因：{exc}",
                parent=self,
            )
        self.reload_from_store()
        if completed:
            L.log("凭证反过账", "、".join(completed))
            self.status_var.set(f"已反过账 {len(completed)} 张凭证，可双击修改后重新入账")

    def _select_all(self):
        """全选"""
        self._checked_ids = set(range(len(self.vouchers)))
        self._update_marks()

    def _clear_all(self):
        """清空全部"""
        if messagebox.askyesno("确认清空", "清空当前账套全部手工录入凭证？"):
            voucher_numbers = {
                row.get("voucher_no", "") for row in self.vouchers
                if row.get("source") == "manual"
            }
            if not voucher_numbers:
                messagebox.showinfo("无需清空", "当前没有手工录入凭证", parent=self)
                return
            if self.store:
                self.store.delete_voucher_numbers(voucher_numbers)
                self.reload_from_store()
            else:
                self.vouchers.clear()
                self._refresh_list()
            self.status_var.set("已清空全部手工录入凭证")

    def pack_forget(self):
        """隐藏模块"""
        super().pack_forget()

    def pack(self, **kwargs):
        """显示模块"""
        super().pack(**kwargs)

    def set_authenticated(self, active: bool, operator: str = ""):
        """更新当前登录会话状态。"""
        self.authenticated = active
