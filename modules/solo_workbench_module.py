#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import threading
import tkinter as tk
from datetime import date
from tkinter import messagebox, ttk
from typing import Any, Callable, Dict, List, Optional

import logger as L
import model_runner as MR
from natural_entry import (
    build_voucher_plan,
    extract_transaction_facts,
    post_voucher_plan,
    semantic_business_text,
)
from modules.loading_dialog import ApproxProgressDialog


BG = "#F3F4F6"
WHITE = "#FFFFFF"
DARK = "#17365D"
BLUE = "#1F4E78"
TEAL = "#0F6B78"
GREEN = "#107C10"
RED = "#C42B1C"
ORANGE = "#C55A11"
GRAY = "#E5E7EB"
TEXT = "#202020"
MUTED = "#666666"
YELLOW = "#FFF2CC"

FONT = ("微软雅黑", 10)
FONT_B = ("微软雅黑", 10, "bold")
FONT_T = ("微软雅黑", 15, "bold")
FONT_S = ("微软雅黑", 9)


def make_btn(parent, text, command, color=BLUE, width=12):
    return tk.Button(
        parent, text=text, command=command, bg=color, fg=WHITE,
        activebackground=DARK, activeforeground=WHITE, relief="flat",
        font=FONT_B, padx=8, pady=5, width=width, cursor="hand2",
    )


class SoloWorkbenchModule(tk.Frame):
    """Default workspace for a non-accountant completing the monthly workflow."""

    def __init__(self, parent, config, semantic_matcher, store,
                 navigate: Callable[[str], None], open_settings: Callable[[], None],
                 on_data_changed: Optional[Callable[[], None]] = None):
        super().__init__(parent, bg=BG)
        self.config = config
        self.semantic_matcher = semantic_matcher
        self.store = store
        self.navigate = navigate
        self.open_settings = open_settings
        self.on_data_changed = on_data_changed
        self.period_var = tk.StringVar(value=date.today().strftime("%Y-%m"))
        self.readiness_var = tk.StringVar()
        self.company_var = tk.StringVar()
        self.metric_vars = {
            key: tk.StringVar(value="¥0.00")
            for key in ("revenue", "expenses", "profit", "vat_payable", "cit_payable")
        }
        self.quick_status_var = tk.StringVar(value="输入一句业务描述后开始解析")
        self.candidate_var = tk.StringVar()
        self._candidate_matches: Dict[str, Dict[str, Any]] = {}
        self._current_plan: Optional[Dict[str, Any]] = None
        self._analysis_in_progress = False
        self._analysis_loading = None
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        header = tk.Frame(self, bg=DARK, padx=18, pady=12)
        header.pack(fill="x")
        tk.Label(
            header, text="本月工作台", bg=DARK, fg=WHITE, font=FONT_T,
        ).pack(side="left")
        tk.Label(
            header, text="期间", bg=DARK, fg="#D9EAF7", font=FONT_S,
        ).pack(side="right", padx=(8, 4))
        self.period_combo = ttk.Combobox(
            header, textvariable=self.period_var, width=9, font=FONT,
        )
        self.period_combo.pack(side="right")
        self.period_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh())
        self.period_combo.bind("<Return>", lambda _event: self.refresh())

        status_band = tk.Frame(self, bg=WHITE, padx=16, pady=10, bd=1, relief="solid")
        status_band.pack(fill="x", pady=(8, 6))
        tk.Label(
            status_band, textvariable=self.readiness_var, bg=WHITE,
            fg=DARK, font=FONT_B, anchor="w",
        ).pack(side="left", fill="x", expand=True)
        tk.Label(
            status_band, textvariable=self.company_var, bg=WHITE,
            fg=MUTED, font=FONT_S,
        ).pack(side="right", padx=10)
        make_btn(status_band, "刷新", self.refresh, color=TEAL, width=7).pack(side="right")

        steps = tk.Frame(self, bg=BG)
        steps.pack(fill="x", pady=4)
        self.step_labels = {}
        step_defs = [
            ("资料", "1  企业资料", self.open_settings),
            ("票据", "2  录票据流水", lambda: self.navigate("batch")),
            ("记账", "3  对账与月结", lambda: self.navigate("basic")),
            ("申报", "4  导出报税资料", lambda: self.navigate("tax")),
        ]
        for key, text, command in step_defs:
            frame = tk.Frame(steps, bg=WHITE, bd=1, relief="solid", padx=12, pady=9)
            frame.pack(side="left", fill="x", expand=True, padx=3)
            tk.Button(
                frame, text=text, command=command, bg=WHITE, fg=DARK,
                activebackground="#EEF5FA", relief="flat", font=FONT_B,
                cursor="hand2",
            ).pack(anchor="w")
            label = tk.Label(frame, bg=WHITE, fg=MUTED, font=FONT_S, anchor="w")
            label.pack(anchor="w", pady=(2, 0))
            self.step_labels[key] = label

        metrics = tk.Frame(self, bg=BG)
        metrics.pack(fill="x", pady=6)
        metric_defs = [
            ("revenue", "营业收入"), ("expenses", "成本费用"),
            ("profit", "本期利润"), ("vat_payable", "预计增值税"),
            ("cit_payable", "预计所得税"),
        ]
        for key, title in metric_defs:
            frame = tk.Frame(metrics, bg=WHITE, bd=1, relief="solid", padx=12, pady=8)
            frame.pack(side="left", fill="x", expand=True, padx=3)
            tk.Label(frame, text=title, bg=WHITE, fg=MUTED, font=FONT_S).pack(anchor="w")
            tk.Label(
                frame, textvariable=self.metric_vars[key], bg=WHITE,
                fg=DARK, font=("微软雅黑", 13, "bold"),
            ).pack(anchor="w", pady=(2, 0))

        work = tk.Frame(self, bg=BG)
        work.pack(fill="both", expand=True, pady=(2, 6))

        quick = tk.LabelFrame(
            work, text=" 一句话记账 ", bg=WHITE, fg=DARK, font=FONT_B,
            bd=1, relief="solid",
        )
        quick.pack(side="left", fill="both", expand=True, padx=(3, 4))
        tk.Label(
            quick,
            text="业务原话",
            bg=WHITE, fg=MUTED, font=FONT_S, anchor="w",
        ).pack(fill="x", padx=12, pady=(10, 4))
        self.quick_text = tk.Text(
            quick, height=3, font=FONT, wrap="word", relief="solid", bd=1,
        )
        self.quick_text.pack(fill="x", padx=12)
        self.quick_text.insert("1.0", "今天老板垫付299元购买办公软件会员")

        quick_bar = tk.Frame(quick, bg=WHITE)
        quick_bar.pack(fill="x", padx=12, pady=8)
        make_btn(quick_bar, "解析这笔业务", self._analyze, color=TEAL, width=13).pack(side="left")
        tk.Label(
            quick_bar, textvariable=self.quick_status_var, bg=WHITE,
            fg=MUTED, font=FONT_S,
        ).pack(side="left", padx=10)

        candidate_row = tk.Frame(quick, bg=WHITE)
        candidate_row.pack(fill="x", padx=12, pady=(0, 6))
        tk.Label(candidate_row, text="候选科目", bg=WHITE, fg=TEXT, font=FONT_B).pack(side="left")
        self.candidate_combo = ttk.Combobox(
            candidate_row, textvariable=self.candidate_var,
            state="readonly", font=FONT,
        )
        self.candidate_combo.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self.candidate_combo.bind("<<ComboboxSelected>>", self._candidate_selected)

        confirm_bar = tk.Frame(quick, bg=WHITE)
        confirm_bar.pack(fill="x", side="bottom", padx=12, pady=(4, 10))
        self.confirm_btn = make_btn(
            confirm_bar, "确认入账", self._confirm_plan, color=GREEN, width=11,
        )
        self.confirm_btn.configure(state="disabled")
        self.confirm_btn.pack(side="right")
        make_btn(
            confirm_bar, "打开完整手工录入", lambda: self.navigate("manual"),
            color=BLUE, width=16,
        ).pack(side="right", padx=6)
        preview_frame = tk.Frame(quick, bg=WHITE)
        preview_frame.pack(fill="both", expand=True, padx=12, pady=(0, 4))
        self.preview_text = tk.Text(
            preview_frame, height=7, font=FONT_S, wrap="word", relief="solid", bd=1,
            bg="#F8FAFC",
        )
        preview_scroll = ttk.Scrollbar(
            preview_frame, orient="vertical", command=self.preview_text.yview,
        )
        self.preview_text.configure(yscrollcommand=preview_scroll.set)
        self.preview_text.pack(side="left", fill="both", expand=True)
        preview_scroll.pack(side="right", fill="y")
        self.preview_text.configure(state="disabled")

        close = tk.LabelFrame(
            work, text=" 本月关账清单 ", bg=WHITE, fg=DARK, font=FONT_B,
            bd=1, relief="solid", width=540,
        )
        close.pack(side="right", fill="both", padx=(4, 3))
        close.pack_propagate(False)
        close_bar = tk.Frame(close, bg=WHITE)
        close_bar.pack(fill="x", side="bottom", padx=10, pady=(4, 10))
        make_btn(
            close_bar, "进入月末关账", lambda: self.navigate("tax"),
            color=BLUE, width=13,
        ).pack(side="right")

        columns = ("状态", "检查项", "说明")
        self.close_tree = ttk.Treeview(close, columns=columns, show="headings", height=14)
        for name, width in zip(columns, (78, 135, 300)):
            self.close_tree.heading(name, text=name)
            self.close_tree.column(name, width=width, anchor="w" if name == "说明" else "center")
        scroll = ttk.Scrollbar(close, orient="vertical", command=self.close_tree.yview)
        self.close_tree.configure(yscrollcommand=scroll.set)
        self.close_tree.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
        scroll.pack(side="right", fill="y", padx=(0, 8), pady=8)
        for tag, color in (("通过", GREEN), ("待处理", RED), ("提示", ORANGE)):
            self.close_tree.tag_configure(tag, foreground=color)

    def _valid_period(self) -> Optional[str]:
        value = self.period_var.get().strip()
        try:
            date.fromisoformat(f"{value}-01")
        except ValueError:
            messagebox.showwarning("期间格式", "请输入 YYYY-MM 格式的期间")
            return None
        return value

    def _period_values(self) -> List[str]:
        values = {date.today().strftime("%Y-%m")}
        values.update(str(row.get("period", "")) for row in self.store.list_vouchers())
        values.update(str(row.get("invoice_date", ""))[:7] for row in self.store.list_invoices())
        return sorted((value for value in values if len(value) == 7), reverse=True)

    def refresh(self):
        period = self._valid_period()
        if not period:
            return
        self.period_combo.configure(values=self._period_values())
        settings = self.store.get_settings()
        company = settings["company"]
        missing_company = [
            label for key, label in (
                ("name", "企业名称"), ("credit_code", "统一社会信用代码"),
                ("taxpayer_type", "纳税人类型"),
            ) if not str(company.get(key, "")).strip()
        ]
        summary = self.store.tax_summary(period)
        for key, var in self.metric_vars.items():
            var.set(f"¥{summary.get(key, 0):,.2f}")
        checklist = self.store.month_end_checklist(period)
        issues = self.store.validate(period)
        error_count = sum(1 for issue in issues if issue.get("level") == "错误")
        warning_count = sum(1 for issue in issues if issue.get("level") == "警告")
        if not checklist["ready"]:
            readiness = f"{period}  还有 {checklist['blocking_count']} 项关账事项需要处理"
        elif error_count:
            readiness = f"{period}  申报校验还有 {error_count} 项错误"
        elif warning_count:
            readiness = f"{period}  关账完成，还有 {warning_count} 项申报警告待复核"
        else:
            readiness = f"{period}  可以导出报税准备资料"
        self.readiness_var.set(readiness)
        self.company_var.set(
            company.get("name", "") or "尚未设置企业资料"
        )

        voucher_count = len({
            row.get("voucher_no") for row in self.store.list_vouchers()
            if row.get("period") == period
        })
        invoice_count = len([
            row for row in self.store.list_invoices()
            if str(row.get("invoice_date", ""))[:7] == period
        ])
        unmatched_bank = len([
            row for row in self.store.list_bank_transactions(period)
            if not row.get("voucher_no")
        ])
        self.step_labels["资料"].configure(
            text="待补：" + "、".join(missing_company) if missing_company else "企业资料已完整",
            fg=RED if missing_company else GREEN,
        )
        self.step_labels["票据"].configure(
            text=f"{invoice_count} 张发票 · {voucher_count} 张凭证",
            fg=GREEN if voucher_count else ORANGE,
        )
        self.step_labels["记账"].configure(
            text=f"{unmatched_bank} 条流水待对账" if unmatched_bank else "对账和关账状态可复核",
            fg=RED if unmatched_bank else GREEN,
        )
        self.step_labels["申报"].configure(
            text=(
                "可导出" if checklist["ready"] and not error_count and not warning_count
                else f"{warning_count} 项警告待复核" if checklist["ready"] and warning_count
                else f"{error_count} 项错误待处理" if checklist["ready"] and error_count
                else f"待完成 {checklist['blocking_count']} 项"
            ),
            fg=(
                GREEN if checklist["ready"] and not error_count and not warning_count
                else RED if error_count else ORANGE
            ),
        )
        for item in self.close_tree.get_children():
            self.close_tree.delete(item)
        for row in checklist["items"]:
            status = row.get("status", "")
            self.close_tree.insert(
                "", "end", values=(status, row.get("item", ""), row.get("detail", "")),
                tags=(status,),
            )

    def _analyze(self):
        if self._analysis_in_progress:
            return
        text = self.quick_text.get("1.0", "end").strip()
        if not text:
            messagebox.showwarning("缺少业务描述", "请先写清楚这笔业务")
            return
        try:
            facts = extract_transaction_facts(text)
        except ValueError as exc:
            messagebox.showwarning("无法识别", str(exc))
            return
        if facts["amount"] <= 0:
            messagebox.showwarning("缺少金额", "请在描述中写明金额，例如“299元”或“2万元”")
            return
        if not self.semantic_matcher:
            messagebox.showerror("语义服务未就绪", "请等待本地模型初始化完成")
            return

        semantic_text = semantic_business_text(text)

        try:
            matches = self.semantic_matcher.match_rules(semantic_text)
        except Exception as exc:
            messagebox.showerror("规则匹配失败", str(exc))
            return
        if matches:
            self._apply_matches(text, matches)
            return

        self._analysis_in_progress = True
        self.quick_status_var.set("本地模型正在判断模糊业务语义")
        self._analysis_loading = ApproxProgressDialog(
            self.winfo_toplevel(), "正在理解这笔业务",
            [
                "提取金额和交易日期", "对照小企业会计词库分类",
                "判断业务实质和候选科目", "生成推荐理由与凭证预览",
            ],
            expected_seconds=2.0,
        )

        def run():
            try:
                result = self.semantic_matcher.match_with_ai(semantic_text)
                error = None
            except Exception as exc:
                result, error = [], exc
            try:
                self.after(0, lambda: self._finish_analysis(text, result, error))
            except tk.TclError:
                pass

        threading.Thread(target=run, name="solo-natural-entry", daemon=True).start()

    def _finish_analysis(self, text: str, matches: List[Dict[str, Any]], error):
        self._analysis_in_progress = False
        dialog = self._analysis_loading
        self._analysis_loading = None

        def finish():
            if error:
                self.quick_status_var.set(f"模型查询失败：{error}")
                return
            self._apply_matches(text, matches)

        if error:
            dialog.fail("语义分析失败", callback=finish)
        else:
            dialog.complete("业务解析完成", callback=finish)

    def _apply_matches(self, text: str, matches: List[Dict[str, Any]]):
        candidates = {}
        for match in matches:
            subject = str(match.get("record", {}).get("subject", "")).strip()
            if subject and subject not in candidates:
                candidates[subject] = match
        if not candidates:
            self.quick_status_var.set("没有得到可用科目，请改写描述或进入完整手工录入")
            self.confirm_btn.configure(state="disabled")
            return
        business_words = (
            "购买", "采购", "买了", "费用", "服务", "会员", "订阅", "办公",
            "差旅", "招待", "培训", "会议", "销售", "收入", "成本", "资产",
        )
        settlement_codes = ("1001", "1002", "1122", "1221", "2202", "2203", "2241")
        if any(word in text for word in business_words):
            ordered = sorted(
                candidates.items(),
                key=lambda item: (
                    str(item[0]).startswith(settlement_codes),
                    list(candidates).index(item[0]),
                ),
            )
            candidates = dict(ordered)
        self._candidate_matches = candidates
        values = list(candidates)
        self.candidate_combo.configure(values=values)
        self.candidate_var.set(values[0])
        self.quick_status_var.set(
            "规则已确定科目" if matches[0].get("match_type") != "ai_suggested"
            else "模型已给出候选，请核对推荐理由"
        )
        self._build_current_plan(text, candidates[values[0]])

    def _candidate_selected(self, _event=None):
        subject = self.candidate_var.get()
        match = self._candidate_matches.get(subject)
        if match:
            self._build_current_plan(self.quick_text.get("1.0", "end").strip(), match)

    def _build_current_plan(self, text: str, match: Dict[str, Any]):
        try:
            plan = build_voucher_plan(text, match)
        except Exception as exc:
            self._current_plan = None
            self.confirm_btn.configure(state="disabled")
            self._set_preview(f"无法生成凭证：{exc}")
            return
        self._current_plan = plan
        debit = next(line for line in plan["lines"] if line["debit"] > 0)
        credit = next(line for line in plan["lines"] if line["credit"] > 0)
        evidence = MR.format_match_details(match)
        preview = (
            f"日期：{plan['date']}    金额：¥{plan['amount']:,.2f}\n"
            f"借：{debit['subject']}    ¥{debit['debit']:,.2f}\n"
            f"贷：{credit['subject']}    ¥{credit['credit']:,.2f}\n\n"
            f"{evidence}\n\n"
            f"对方科目依据：{plan['counter_basis']}\n"
            f"客观字段：日期和金额从原句提取，凭证借贷自动平衡"
        )
        self._set_preview(preview)
        self.confirm_btn.configure(state="normal")

    def _set_preview(self, text: str):
        self.preview_text.configure(state="normal")
        self.preview_text.delete("1.0", "end")
        self.preview_text.insert("1.0", text)
        self.preview_text.configure(state="disabled")

    def _confirm_plan(self):
        plan = self._current_plan
        if not plan:
            return
        debit = next(line for line in plan["lines"] if line["debit"] > 0)
        credit = next(line for line in plan["lines"] if line["credit"] > 0)
        if not messagebox.askyesno(
            "确认入账",
            (
                f"日期：{plan['date']}\n"
                f"借：{debit['subject']}  ¥{debit['debit']:,.2f}\n"
                f"贷：{credit['subject']}  ¥{credit['credit']:,.2f}\n\n"
                "确认保存这张凭证？"
            ),
            parent=self,
        ):
            return
        try:
            added = post_voucher_plan(self.store, plan)
        except Exception as exc:
            messagebox.showerror("入账失败", str(exc), parent=self)
            return
        voucher_no = added[0]["voucher_no"]
        L.log(
            "自然语言入账", plan["description"],
            after={"凭证号": voucher_no, "金额": plan["amount"]},
        )
        self.quick_text.delete("1.0", "end")
        self.candidate_var.set("")
        self.candidate_combo.configure(values=[])
        self._candidate_matches = {}
        self._current_plan = None
        self.confirm_btn.configure(state="disabled")
        self.quick_status_var.set(f"已保存凭证 {voucher_no}")
        self._set_preview("")
        if self.on_data_changed:
            self.on_data_changed()
        self.refresh()
        messagebox.showinfo("入账完成", f"已保存凭证 {voucher_no}", parent=self)

    def set_authenticated(self, active: bool, operator: str = ""):
        return None

    def pack(self, **kwargs):
        super().pack(**kwargs)
        self.refresh()
