#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import threading
import tkinter as tk
from datetime import date
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from finance_exporter import export_finance_workbook
from modules.loading_dialog import ApproxProgressDialog


BG = "#F0F0F0"
BLUE = "#0078D4"
DARK = "#003087"
WHITE = "#FFFFFF"
GREEN = "#107C10"
RED = "#D83B01"
ORANGE = "#E67E22"
GRAY = "#D0D0D0"
FONT = ("微软雅黑", 10)
FONT_B = ("微软雅黑", 10, "bold")
FONT_T = ("微软雅黑", 14, "bold")
FONT_S = ("微软雅黑", 9)


def make_btn(parent, text, command, color=BLUE, width=10):
    return tk.Button(
        parent, text=text, command=command, bg=color, fg=WHITE,
        font=FONT_B, relief="flat", padx=8, pady=4, width=width,
        activebackground=DARK, activeforeground=WHITE, cursor="hand2",
    )


class TaxWorkbenchModule(tk.Frame):
    """Period review, filing checks, and accounting/tax workbook export."""

    def __init__(self, parent, config, store, authenticated=False):
        super().__init__(parent, bg=BG)
        self.parent = parent
        self.config = config
        self.store = store
        self.authenticated = authenticated
        self._export_in_progress = False
        self._last_export_path = None
        self.period_var = tk.StringVar(value=date.today().strftime("%Y-%m"))
        self.status_var = tk.StringVar(value="未标记")
        self.note_var = tk.StringVar()
        self.policy_notice_var = tk.StringVar()
        self.metric_vars = {}
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        outer = tk.LabelFrame(
            self, text=" 财税工作台 ", font=FONT_T, bg=BG, fg=DARK,
            bd=1, relief="groove",
        )
        outer.pack(fill="both", expand=True, pady=6)

        toolbar = tk.Frame(outer, bg=BG, pady=8)
        toolbar.pack(fill="x", padx=12)
        tk.Label(toolbar, text="核算期间：", font=FONT_B, bg=BG).pack(side="left")
        self.period_combo = ttk.Combobox(
            toolbar, textvariable=self.period_var, font=FONT, width=12,
        )
        self.period_combo.pack(side="left", padx=(0, 8))
        self.period_combo.bind("<<ComboboxSelected>>", lambda event: self.refresh())
        self.period_combo.bind("<Return>", lambda event: self.refresh())
        make_btn(toolbar, "刷新", self.refresh, width=8).pack(side="left", padx=3)
        make_btn(
            toolbar, "执行关账检查", self._run_close_check,
            color=BLUE, width=13,
        ).pack(side="left", padx=3)
        make_btn(
            toolbar, "结转损益", self._post_profit_close,
            color=GREEN, width=10,
        ).pack(side="left", padx=3)
        make_btn(
            toolbar, "撤销结转", self._unpost_profit_close,
            color=ORANGE, width=10,
        ).pack(side="left", padx=3)
        make_btn(toolbar, "导出财税Excel", self._export, color=GREEN, width=14).pack(
            side="left", padx=3
        )
        make_btn(toolbar, "打开导出目录", self._open_export_dir, color="#666", width=13).pack(
            side="left", padx=3
        )
        tk.Label(
            toolbar, text="免费辅助测算，不是电子税务局直传模板",
            font=FONT_S, bg=BG, fg="#7A4E00",
        ).pack(side="right")

        tk.Label(
            outer, textvariable=self.policy_notice_var, font=FONT_S,
            bg="#FFF4CE", fg="#6B5200", anchor="w", padx=10, pady=7,
        ).pack(fill="x", padx=12, pady=(0, 8))

        metrics = tk.Frame(outer, bg=WHITE, relief="solid", bd=1)
        metrics.pack(fill="x", padx=12, pady=(0, 10))
        labels = [
            ("revenue", "收入"), ("expenses", "成本费用"), ("profit", "利润"),
            ("vat_payable", "增值税测算"), ("surcharge", "附加税费测算"),
            ("cit_payable", "所得税测算"),
        ]
        for column, (key, label) in enumerate(labels):
            frame = tk.Frame(metrics, bg=WHITE, padx=12, pady=10)
            frame.grid(row=0, column=column, sticky="nsew")
            metrics.columnconfigure(column, weight=1)
            tk.Label(frame, text=label, font=FONT_S, bg=WHITE, fg="#666").pack()
            var = tk.StringVar(value="¥0.00")
            self.metric_vars[key] = var
            tk.Label(frame, textvariable=var, font=FONT_B, bg=WHITE, fg=DARK).pack(pady=(4, 0))

        body = tk.Frame(outer, bg=BG)
        body.pack(fill="both", expand=True, padx=12, pady=(0, 10))

        self.review_notebook = ttk.Notebook(body)
        self.review_notebook.pack(side="left", fill="both", expand=True, padx=(0, 6))
        issue_tab = tk.Frame(self.review_notebook, bg=BG)
        close_tab = tk.Frame(self.review_notebook, bg=BG)
        tax_tab = tk.Frame(self.review_notebook, bg=BG)
        adjustment_tab = tk.Frame(self.review_notebook, bg=BG)
        invoice_tab = tk.Frame(self.review_notebook, bg=BG)
        other_tax_tab = tk.Frame(self.review_notebook, bg=BG)
        self.review_notebook.add(issue_tab, text="申报前校验")
        self.review_notebook.add(close_tab, text="月末关账")
        self.review_notebook.add(tax_tab, text="税务期间")
        self.review_notebook.add(adjustment_tab, text="纳税调整")
        self.review_notebook.add(invoice_tab, text="红字/未开票")
        self.review_notebook.add(other_tax_tab, text="个税/印花税")
        self.close_tab = close_tab

        columns = ("级别", "代码", "问题", "数量")
        self.issue_tree = ttk.Treeview(issue_tab, columns=columns, show="headings", height=16)
        widths = (70, 150, 470, 60)
        for name, width in zip(columns, widths):
            self.issue_tree.heading(name, text=name)
            self.issue_tree.column(name, width=width, anchor="center" if name != "问题" else "w")
        scroll = ttk.Scrollbar(issue_tab, orient="vertical", command=self.issue_tree.yview)
        self.issue_tree.configure(yscrollcommand=scroll.set)
        self.issue_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.issue_tree.tag_configure("错误", foreground=RED)
        self.issue_tree.tag_configure("警告", foreground=ORANGE)
        self.issue_tree.tag_configure("通过", foreground=GREEN)

        close_columns = ("状态", "检查项", "检查说明")
        self.close_tree = ttk.Treeview(
            close_tab, columns=close_columns, show="headings", height=16,
        )
        close_widths = (90, 180, 500)
        for name, width in zip(close_columns, close_widths):
            self.close_tree.heading(name, text=name)
            self.close_tree.column(
                name, width=width,
                anchor="w" if name == "检查说明" else "center",
            )
        close_scroll = ttk.Scrollbar(
            close_tab, orient="vertical", command=self.close_tree.yview,
        )
        self.close_tree.configure(yscrollcommand=close_scroll.set)
        self.close_tree.pack(side="left", fill="both", expand=True)
        close_scroll.pack(side="right", fill="y")
        self.close_tree.tag_configure("待处理", foreground=RED)
        self.close_tree.tag_configure("提示", foreground=ORANGE)
        self.close_tree.tag_configure("通过", foreground=GREEN)
        self.close_tree.tag_configure("无需处理", foreground="#666")
        self.close_tree.tag_configure("不适用", foreground="#666")

        self._build_tax_period_tab(tax_tab)
        self._build_adjustment_tab(adjustment_tab)
        self._build_special_invoice_tab(invoice_tab)
        self._build_other_tax_tab(other_tax_tab)

        right = tk.LabelFrame(
            body, text=" 期间状态 ", font=FONT_B, bg=BG, fg=DARK,
            bd=1, relief="groove", width=300,
        )
        right.pack(side="right", fill="y", padx=(6, 0))
        right.pack_propagate(False)
        tk.Label(right, text="处理状态", font=FONT_B, bg=BG).pack(anchor="w", padx=12, pady=(14, 4))
        self.status_combo = ttk.Combobox(
            right, textvariable=self.status_var,
            values=["未标记", "整理中", "待复核", "已复核", "已申报", "已归档"],
            state="readonly", font=FONT,
        )
        self.status_combo.pack(fill="x", padx=12)
        tk.Label(right, text="备注", font=FONT_B, bg=BG).pack(anchor="w", padx=12, pady=(12, 4))
        self.note_entry = tk.Entry(right, textvariable=self.note_var, font=FONT, relief="solid", bd=1)
        self.note_entry.pack(fill="x", padx=12)
        make_btn(right, "保存期间状态", self._save_period_status, color=BLUE, width=13).pack(
            pady=14
        )
        make_btn(
            right, "重新打开归档期", self._reopen_archived_period,
            color=ORANGE, width=13,
        ).pack(pady=(0, 12))
        make_btn(
            right, "生成税费计提凭证", self._post_tax_accrual,
            color=GREEN, width=15,
        ).pack(pady=(0, 8))
        make_btn(
            right, "撤销税费计提凭证", self._unpost_tax_accrual,
            color=ORANGE, width=15,
        ).pack(pady=(0, 12))

        self.summary_var = tk.StringVar()
        tk.Label(
            right, textvariable=self.summary_var, font=FONT_S, bg="#FFF4CE",
            fg="#5A4500", wraplength=250, justify="left", padx=10, pady=10,
        ).pack(fill="x", padx=12, pady=(4, 10))

        self.footer_var = tk.StringVar(value="就绪")
        tk.Label(
            outer, textvariable=self.footer_var, font=FONT_S, bg=GRAY,
            fg="#444", anchor="w", padx=10, pady=5,
        ).pack(fill="x", padx=12, pady=(0, 10))

    def _tree(self, parent, columns, widths, height=14):
        frame = tk.Frame(parent, bg=BG)
        frame.pack(fill="both", expand=True, padx=8, pady=8)
        tree = ttk.Treeview(frame, columns=columns, show="headings", height=height)
        for name, width in zip(columns, widths):
            tree.heading(name, text=name)
            tree.column(name, width=width, anchor="e" if "金额" in name or "税额" in name else "w")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        return tree

    def _build_tax_period_tab(self, parent):
        self.tax_period_tree = self._tree(
            parent, ("项目", "值", "说明"), (210, 180, 420), height=16,
        )

    def _build_adjustment_tab(self, parent):
        toolbar = tk.Frame(parent, bg=BG)
        toolbar.pack(fill="x", padx=8, pady=(8, 0))
        make_btn(toolbar, "新增调整", self._add_adjustment, color=GREEN, width=10).pack(side="left", padx=3)
        make_btn(toolbar, "删除调整", self._delete_adjustment, color=RED, width=10).pack(side="left", padx=3)
        self.adjustment_tree = self._tree(
            parent, ("期间", "方向", "类别", "金额", "依据/备注"),
            (90, 120, 170, 110, 320), height=14,
        )

    def _build_special_invoice_tab(self, parent):
        toolbar = tk.Frame(parent, bg=BG)
        toolbar.pack(fill="x", padx=8, pady=(8, 0))
        make_btn(toolbar, "新增未开票收入", lambda: self._add_special_invoice("未开票收入"), color=GREEN, width=14).pack(side="left", padx=3)
        make_btn(toolbar, "新增红字发票", lambda: self._add_special_invoice("红字发票"), color=ORANGE, width=12).pack(side="left", padx=3)
        make_btn(toolbar, "删除记录", self._delete_special_invoice, color=RED, width=10).pack(side="left", padx=3)
        self.special_invoice_tree = self._tree(
            parent, ("日期", "类型", "发票号码", "原发票号", "不含税金额", "税额", "价税合计", "税务处理"),
            (95, 100, 110, 110, 110, 90, 110, 100), height=14,
        )

    def _build_other_tax_tab(self, parent):
        toolbar = tk.Frame(parent, bg=BG)
        toolbar.pack(fill="x", padx=8, pady=(8, 0))
        make_btn(toolbar, "新增印花税项目", self._add_stamp_duty, color=GREEN, width=13).pack(side="left", padx=3)
        make_btn(toolbar, "删除印花税项目", self._delete_stamp_duty, color=RED, width=13).pack(side="left", padx=3)
        self.iit_stamp_tree = self._tree(
            parent, ("税种/人员", "计税金额", "税率", "本期测算", "已录金额", "差额/说明"),
            (170, 120, 90, 120, 120, 250), height=14,
        )

    def _valid_period(self):
        value = self.period_var.get().strip()
        try:
            date.fromisoformat(f"{value}-01")
        except ValueError:
            messagebox.showwarning("期间格式", "请输入 YYYY-MM 格式的核算期间")
            return None
        return value

    def _period_values(self):
        values = {date.today().strftime("%Y-%m")}
        values.update(str(row.get("period", "")) for row in self.store.list_vouchers())
        values.update(str(row.get("invoice_date", ""))[:7] for row in self.store.list_invoices())
        return sorted((value for value in values if len(value) == 7), reverse=True)

    def refresh(self):
        period = self._valid_period()
        if not period:
            return
        tax = self.store.get_settings().get("tax", {})
        self.policy_notice_var.set(
            "仅支持：小规模纳税人 + 小型微利企业 | 政策参数可在系统设置修改 | "
            f"预设复核日：{tax.get('policy_reference_date', '未设置')} | "
            f"优惠截止：{tax.get('policy_effective_through', '未设置')} | "
            "最终以现行政策、电子税务局和主管机关口径为准"
        )
        self.period_combo.configure(values=self._period_values())
        summary = self.store.tax_summary(period)
        for key, var in self.metric_vars.items():
            var.set(f"¥{summary.get(key, 0):,.2f}")
        for item in self.issue_tree.get_children():
            self.issue_tree.delete(item)
        issues = self.store.validate(period)
        for issue in issues:
            level = issue.get("level", "")
            self.issue_tree.insert(
                "", "end", values=(
                    level, issue.get("code", ""), issue.get("message", ""), issue.get("count", 0),
                ), tags=(level,),
            )
        checklist = self.store.month_end_checklist(period)
        self._last_checklist = checklist
        for item in self.close_tree.get_children():
            self.close_tree.delete(item)
        for row in checklist["items"]:
            status = row.get("status", "")
            self.close_tree.insert(
                "", "end", values=(
                    status, row.get("item", ""), row.get("detail", ""),
                ), tags=(status,),
            )
        period_data = self.store.get_tax_periods().get(period, {})
        self.status_var.set(period_data.get("status", "未标记"))
        self.note_var.set(period_data.get("note", ""))
        voucher_count = len([row for row in self.store.list_vouchers() if row.get("period") == period])
        invoice_count = len([
            row for row in self.store.list_invoices()
            if str(row.get("invoice_date", ""))[:7] == period
        ])
        close_status = (
            "可以归档" if checklist["ready"]
            else f"待处理 {checklist['blocking_count']} 项"
        )
        self.summary_var.set(
            f"本期凭证分录：{voucher_count}\n本期发票：{invoice_count}\n"
            f"校验项目：{len(issues)}\n"
            f"关账状态：{close_status}\n"
            f"当前状态：{self.status_var.get()}"
        )
        self._refresh_tax_tabs(period, summary)
        self.footer_var.set(f"已刷新 {period} 数据")

    @staticmethod
    def _clear_tree(tree):
        for item in tree.get_children():
            tree.delete(item)

    def _refresh_tax_tabs(self, period, summary):
        self._clear_tree(self.tax_period_tree)
        vat = summary.get("vat", {})
        cit = summary.get("cit", {})
        scope = summary.get("scope", {})
        period_info = summary.get("period", {})
        rows = [
            ("支持范围", "通过" if scope.get("supported") else "超出范围", scope.get("message", "")),
            ("增值税期间", period_info.get("key", ""), f"{period_info.get('start_month', '')} 至 {period_info.get('end_month', '')}"),
            ("价税分离后销售额", f"¥{vat.get('sales', 0):,.2f}", "销项发票、红字发票和未开票收入合计"),
            ("免税销售额阈值", f"¥{vat.get('threshold', 0):,.2f}", "系统设置可修改"),
            ("是否达到阈值免税条件", "是" if vat.get("threshold_eligible") else "否", "专票或标记不得免税的销售额仍单独计税"),
            ("不得免税销售额", f"¥{vat.get('non_exempt_sales', 0):,.2f}", "包括增值税专用发票等人工标记项目"),
            ("免税销售额", f"¥{vat.get('exempt_sales', 0):,.2f}", "含阈值免税和明确免税项目"),
            ("增值税测算", f"¥{summary.get('vat_payable', 0):,.2f}", "最终以电子税务局为准"),
            ("附加税费测算", f"¥{summary.get('surcharge', 0):,.2f}", "按用户维护的综合比例"),
            ("所得税累计会计利润", f"¥{cit.get('accounting_profit', 0):,.2f}", "本年截至申报期累计"),
            ("纳税调增/调减", f"¥{cit.get('tax_increase', 0):,.2f} / ¥{cit.get('tax_decrease', 0):,.2f}", "来自纳税调整台账"),
            ("小型微利资格", "通过" if cit.get("supported") else "未通过", cit.get("eligibility", {}).get("message", "")),
            ("企业所得税本期应补", f"¥{summary.get('cit_payable', 0):,.2f}", "已扣除纳税调整台账中的已预缴所得税"),
        ]
        for row in rows:
            self.tax_period_tree.insert("", "end", values=row)

        self._clear_tree(self.adjustment_tree)
        for row in summary.get("adjustments", []):
            self.adjustment_tree.insert("", "end", iid=str(row.get("id")), values=(
                row.get("period", ""), row.get("direction", ""), row.get("category", ""),
                f"¥{row.get('amount', 0):,.2f}", row.get("basis") or row.get("note", ""),
            ))

        self._clear_tree(self.special_invoice_tree)
        months = set(period_info.get("months", [period]))
        for row in self.store.list_invoices():
            if str(row.get("invoice_date", ""))[:7] not in months:
                continue
            if row.get("document_type") not in {"红字发票", "未开票收入"}:
                continue
            self.special_invoice_tree.insert("", "end", iid=str(row.get("id")), values=(
                row.get("invoice_date", ""), row.get("document_type", ""),
                row.get("invoice_no", ""), row.get("original_invoice_no", ""),
                f"¥{row.get('amount', 0):,.2f}", f"¥{row.get('tax_amount', 0):,.2f}",
                f"¥{row.get('total_amount', 0):,.2f}", row.get("tax_treatment", "自动判断"),
            ))

        self._clear_tree(self.iit_stamp_tree)
        iit = self.store.individual_income_tax_summary(period)
        for row in iit.get("rows", []):
            self.iit_stamp_tree.insert("", "end", values=(
                f"个税-{row.get('employee_name', '')}",
                f"¥{row.get('cumulative_taxable_income', 0):,.2f}",
                f"{row.get('rate', 0):.2%}", f"¥{row.get('current_withholding', 0):,.2f}",
                f"¥{row.get('current_recorded', 0):,.2f}", f"差额 ¥{row.get('difference', 0):,.2f}",
            ))
        stamp = self.store.stamp_duty_summary(period)
        for row in stamp.get("items", []):
            self.iit_stamp_tree.insert("", "end", iid=f"stamp:{row.get('id')}", values=(
                f"印花税-{row.get('item', '')}", f"¥{row.get('taxable_amount', 0):,.2f}",
                f"{row.get('rate', 0):.4%}", f"¥{row.get('payable', 0):,.2f}", "",
                f"减征后比例 {stamp.get('relief_rate', 1):.0%}",
            ))

    def _add_adjustment(self):
        period = self._valid_period()
        if not period:
            return
        direction = simpledialog.askstring(
            "新增纳税调整", "方向：调增 / 调减 / 弥补以前年度亏损 / 已预缴所得税",
            parent=self, initialvalue="调增",
        )
        if direction is None:
            return
        category = simpledialog.askstring(
            "新增纳税调整", "调整类别：", parent=self, initialvalue="无票支出"
        )
        if category is None:
            return
        amount = simpledialog.askfloat("新增纳税调整", "金额（元）：", parent=self, minvalue=0.01)
        if amount is None:
            return
        basis = simpledialog.askstring(
            "新增纳税调整", "依据或备注：", parent=self, initialvalue="人工复核录入"
        )
        try:
            self.store.upsert_tax_adjustment({
                "period": period, "direction": direction.strip(), "category": category.strip(),
                "amount": amount, "basis": (basis or "").strip(),
            })
            self.refresh()
        except Exception as exc:
            messagebox.showerror("新增失败", str(exc), parent=self)

    def _delete_adjustment(self):
        selected = self.adjustment_tree.selection()
        if not selected:
            messagebox.showwarning("提示", "请先选择一条纳税调整", parent=self)
            return
        if not messagebox.askyesno("确认删除", "删除选中的纳税调整？", parent=self):
            return
        try:
            self.store.delete_tax_adjustment(selected[0])
            self.refresh()
        except Exception as exc:
            messagebox.showerror("删除失败", str(exc), parent=self)

    def _add_special_invoice(self, document_type):
        period = self._valid_period()
        if not period:
            return
        invoice_date = simpledialog.askstring(
            f"新增{document_type}", "业务日期（YYYY-MM-DD）：", parent=self,
            initialvalue=f"{period}-01",
        )
        if not invoice_date:
            return
        total = simpledialog.askfloat(
            f"新增{document_type}", "价税合计（填写正数）：", parent=self, minvalue=0.01
        )
        if total is None:
            return
        invoice_no = ""
        original_no = ""
        if document_type == "红字发票":
            invoice_no = simpledialog.askstring(
                "新增红字发票", "红字发票号码：", parent=self
            ) or ""
            original_no = simpledialog.askstring(
                "新增红字发票", "原蓝字发票号码（必填）：", parent=self
            ) or ""
            if not original_no.strip():
                messagebox.showwarning("无法保存", "红字发票必须填写原蓝字发票号码", parent=self)
                return
        invoice_form = "无票" if document_type == "未开票收入" else (
            simpledialog.askstring(
                "发票类型", "普通发票 / 增值税专用发票：", parent=self,
                initialvalue="普通发票",
            ) or "普通发票"
        )
        treatment = simpledialog.askstring(
            "税务处理", "自动判断 / 不得免税 / 免税项目 / 不征税：", parent=self,
            initialvalue="自动判断",
        ) or "自动判断"
        try:
            self.store.upsert_invoice({
                "invoice_date": invoice_date.strip(), "invoice_type": "销项",
                "document_type": document_type, "invoice_form": invoice_form.strip(),
                "invoice_no": invoice_no.strip(), "original_invoice_no": original_no.strip(),
                "price_tax_mode": "含税", "total_amount": total,
                "tax_treatment": treatment.strip(), "source": "tax_workbench",
            })
            self.refresh()
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc), parent=self)

    def _delete_special_invoice(self):
        selected = self.special_invoice_tree.selection()
        if not selected:
            messagebox.showwarning("提示", "请先选择一条红字或未开票记录", parent=self)
            return
        if not messagebox.askyesno("确认删除", "删除选中的销项记录？", parent=self):
            return
        try:
            self.store.delete_invoice(selected[0])
            self.refresh()
        except Exception as exc:
            messagebox.showerror("删除失败", str(exc), parent=self)

    def _add_stamp_duty(self):
        period = self._valid_period()
        if not period:
            return
        item = simpledialog.askstring(
            "新增印花税项目", "税目：", parent=self, initialvalue="买卖合同"
        )
        if item is None:
            return
        amount = simpledialog.askfloat(
            "新增印花税项目", "计税金额（元）：", parent=self, minvalue=0.01
        )
        if amount is None:
            return
        rate_percent = simpledialog.askfloat(
            "新增印花税项目", "税率（%，买卖合同通常填0.03）：", parent=self,
            initialvalue=0.03, minvalue=0.0001,
        )
        if rate_percent is None:
            return
        try:
            self.store.upsert_stamp_duty_item({
                "period": period, "item": item.strip(), "taxable_amount": amount,
                "rate": rate_percent / 100,
            })
            self.refresh()
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc), parent=self)

    def _delete_stamp_duty(self):
        selected = self.iit_stamp_tree.selection()
        if not selected or not selected[0].startswith("stamp:"):
            messagebox.showwarning("提示", "请先选择一条印花税项目", parent=self)
            return
        if not messagebox.askyesno("确认删除", "删除选中的印花税项目？", parent=self):
            return
        try:
            self.store.delete_stamp_duty_item(selected[0].split(":", 1)[1])
            self.refresh()
        except Exception as exc:
            messagebox.showerror("删除失败", str(exc), parent=self)

    def _post_tax_accrual(self):
        period = self._valid_period()
        if not period:
            return
        try:
            preview = self.store.tax_accrual_preview(period)
            if preview["posted"]:
                messagebox.showinfo("已计提", f"已生成凭证：{preview['voucher_no']}", parent=self)
                return
            if not preview.get("can_post"):
                messagebox.showinfo(
                    "尚未到申报期末",
                    "当前月份只显示累计进度。默认按季时，请在3、6、9、12月生成计提凭证。",
                    parent=self,
                )
                return
            if not preview["lines"]:
                messagebox.showinfo("无需计提", "本期没有需要计提的附加税费或企业所得税", parent=self)
                return
            total = sum(row.get("debit", 0) for row in preview["lines"])
            if not messagebox.askyesno(
                "确认计提税费",
                f"税务期间：{preview['period_key']}\n计提金额：¥{total:,.2f}\n\n确认生成凭证？",
                parent=self,
            ):
                return
            voucher_no = self.store.post_tax_accrual_voucher(period)
            self.refresh()
            messagebox.showinfo("计提完成", f"已生成凭证：{voucher_no}", parent=self)
        except Exception as exc:
            messagebox.showerror("计提失败", str(exc), parent=self)

    def _unpost_tax_accrual(self):
        period = self._valid_period()
        if not period:
            return
        try:
            preview = self.store.tax_accrual_preview(period)
            if not preview["posted"]:
                messagebox.showinfo("无需撤销", "本期尚未生成税费计提凭证", parent=self)
                return
            if not messagebox.askyesno(
                "确认撤销", f"撤销税费计提凭证 {preview['voucher_no']}？", parent=self
            ):
                return
            voucher_no = self.store.unpost_tax_accrual_voucher(period)
            self.refresh()
            messagebox.showinfo("撤销完成", f"已删除凭证：{voucher_no}", parent=self)
        except Exception as exc:
            messagebox.showerror("撤销失败", str(exc), parent=self)

    def _run_close_check(self):
        period = self._valid_period()
        if not period:
            return
        self.refresh()
        self.review_notebook.select(self.close_tab)
        checklist = self._last_checklist
        if checklist["ready"]:
            messagebox.showinfo(
                "关账检查通过",
                f"{period} 未发现阻断关账的问题，可以复核后归档。",
            )
        else:
            messagebox.showwarning(
                "关账检查未通过",
                f"{period} 仍有 {checklist['blocking_count']} 项需要处理，详情见“月末关账”页签。",
            )

    def _post_profit_close(self):
        period = self._valid_period()
        if not period:
            return
        try:
            preview = self.store.profit_close_preview(period)
        except Exception as exc:
            messagebox.showerror("无法结转", str(exc))
            return
        if preview["posted"]:
            messagebox.showinfo(
                "已完成结转",
                f"{period} 已生成损益结转凭证：{preview['voucher_no']}",
            )
            return
        if not preview["lines"]:
            messagebox.showinfo("无需结转", f"{period} 没有待结转的损益余额")
            return
        net_label = "净利润" if preview["net_profit"] >= 0 else "净亏损"
        confirmed = messagebox.askyesno(
            "确认结转损益",
            (
                f"期间：{period}\n"
                f"收入合计：¥{preview['income_total']:,.2f}\n"
                f"费用合计：¥{preview['expense_total']:,.2f}\n"
                f"{net_label}：¥{abs(preview['net_profit']):,.2f}\n\n"
                "将生成一张损益结转凭证，是否继续？"
            ),
        )
        if not confirmed:
            return
        try:
            voucher_no = self.store.post_profit_close_voucher(period)
            self.refresh()
            self.review_notebook.select(self.close_tab)
            messagebox.showinfo("结转完成", f"已生成凭证：{voucher_no}")
        except Exception as exc:
            messagebox.showerror("结转失败", str(exc))

    def _unpost_profit_close(self):
        period = self._valid_period()
        if not period:
            return
        try:
            preview = self.store.profit_close_preview(period)
        except Exception as exc:
            messagebox.showerror("无法撤销", str(exc))
            return
        if not preview["posted"]:
            messagebox.showinfo("无需撤销", f"{period} 尚未生成损益结转凭证")
            return
        if not messagebox.askyesno(
            "确认撤销结转",
            f"撤销损益结转凭证 {preview['voucher_no']}？\n撤销后需重新执行关账检查。",
        ):
            return
        try:
            voucher_no = self.store.unpost_profit_close_voucher(period)
            self.refresh()
            self.review_notebook.select(self.close_tab)
            messagebox.showinfo("撤销完成", f"已删除凭证：{voucher_no}")
        except Exception as exc:
            messagebox.showerror("撤销失败", str(exc))

    def _save_period_status(self):
        period = self._valid_period()
        if not period:
            return
        current_status = self.store.get_tax_periods().get(period, {}).get("status", "未标记")
        if current_status == "已归档" and self.status_var.get() != "已归档":
            self.status_var.set("已归档")
            messagebox.showwarning(
                "归档期已锁定",
                "不能通过下拉框直接解除归档。确需更正历史数据时，请点击“重新打开归档期”。",
                parent=self,
            )
            return
        if self.status_var.get() == "已归档":
            if not self._forced_balance_wizard(period):
                return
            checklist = self.store.month_end_checklist(period)
            if not checklist["ready"]:
                self._last_checklist = checklist
                self.refresh()
                self.review_notebook.select(self.close_tab)
                messagebox.showwarning(
                    "暂不能归档",
                    f"尚有 {checklist['blocking_count']} 项关账检查未通过，请处理后再归档。",
                )
                return
        try:
            self.store.set_period_status(
                period, self.status_var.get(), self.note_var.get().strip()
            )
        except Exception as exc:
            messagebox.showerror("期间状态未保存", str(exc), parent=self)
            return
        self.footer_var.set(f"已保存 {period} 期间状态")
        self.refresh()

    def _forced_balance_wizard(self, period: str) -> bool:
        """Run a visible voucher-level balance gate before archival."""
        issues = self.store.voucher_balance_issues(period)
        if not issues:
            posted = {
                row.get("voucher_no") for row in self.store.list_vouchers()
                if row.get("period") == period
            }
            return messagebox.askyesno(
                "强制平衡检查通过",
                (
                    f"{period} 共检查 {len(posted)} 张已过账凭证，借贷均平衡。\n\n"
                    "归档后该月份所有会计数据将锁定。确认继续归档吗？"
                ),
                parent=self,
            )

        dialog = tk.Toplevel(self)
        dialog.title("强制平衡检查未通过")
        dialog.geometry("820x440")
        dialog.configure(bg=BG)
        dialog.transient(self.winfo_toplevel())
        dialog.grab_set()
        tk.Label(
            dialog,
            text=f"{period} 有 {len(issues)} 张凭证借贷不平，系统已阻止归档",
            font=FONT_T, bg="#FDE7E9", fg=RED, padx=14, pady=12, anchor="w",
        ).pack(fill="x")
        columns = ("凭证号", "日期", "摘要", "借方", "贷方", "差额")
        tree = ttk.Treeview(dialog, columns=columns, show="headings", height=13)
        for name, width in zip(columns, (130, 100, 220, 100, 100, 100)):
            tree.heading(name, text=name)
            tree.column(name, width=width, anchor="w" if name == "摘要" else "center")
        tree.tag_configure("error", foreground=RED)
        for row in issues:
            tree.insert("", "end", values=(
                row["voucher_no"], row["date"], row["description"],
                f"¥{row['debit']:,.2f}", f"¥{row['credit']:,.2f}",
                f"¥{row['difference']:,.2f}",
            ), tags=("error",))
        tree.pack(fill="both", expand=True, padx=14, pady=14)
        tk.Label(
            dialog,
            text="请回到“完整手工录入”修正红色凭证，再重新执行归档。",
            font=FONT_S, bg=BG, fg="#555",
        ).pack(anchor="w", padx=14)
        make_btn(dialog, "关闭并处理", dialog.destroy, color=BLUE, width=12).pack(
            side="right", padx=14, pady=14
        )
        dialog.wait_window()
        return False

    def _reopen_archived_period(self):
        period = self._valid_period()
        if not period:
            return
        current = self.store.get_tax_periods().get(period, {}).get("status", "未标记")
        if current != "已归档":
            messagebox.showinfo("无需重新打开", f"{period} 当前不是已归档状态", parent=self)
            return
        if not messagebox.askyesno(
            "确认重新打开归档期",
            (
                f"重新打开 {period} 后将允许修改历史数据。\n"
                "系统会先自动备份，修改完成后必须重新检查并归档。\n\n确认继续吗？"
            ),
            parent=self,
        ):
            return
        try:
            self.store.reopen_archived_period(period)
            self.refresh()
            messagebox.showinfo(
                "归档期已重新打开",
                f"{period} 已变为“待复核”，修改完成后请重新执行关账检查。",
                parent=self,
            )
        except Exception as exc:
            messagebox.showerror("无法重新打开", str(exc), parent=self)

    def _export(self):
        if self._export_in_progress:
            return
        period = self._valid_period()
        if not period:
            return
        checklist = self.store.month_end_checklist(period)
        issues = self.store.validate(period)
        errors = [issue for issue in issues if issue.get("level") == "错误"]
        warnings = [issue for issue in issues if issue.get("level") == "警告"]
        if not checklist["ready"] or errors:
            if not messagebox.askyesno(
                "仍有未完成事项",
                (
                    f"{period} 还有 {checklist['blocking_count']} 项关账事项和 "
                    f"{len(errors)} 项申报错误。\n\n"
                    "仍然导出时，工作簿会标记为待处理稿。是否继续？"
                ),
                parent=self,
            ):
                return
        elif warnings:
            if not messagebox.askyesno(
                "申报警告待复核",
                f"当前有 {len(warnings)} 项警告。是否导出工作簿继续人工复核？",
                parent=self,
            ):
                return
        settings = self.store.get_settings()
        default_dir = self.store.resolve_export_dir(
            settings["export"].get("default_dir")
        )
        default_dir.mkdir(parents=True, exist_ok=True)
        company = settings["company"].get("name") or "账套"
        safe_company = "".join(char for char in company if char not in '<>:"/\\|?*')
        target = filedialog.asksaveasfilename(
            parent=self, title="导出小企业月度报税准备工作簿",
            initialdir=default_dir,
            initialfile=f"{safe_company}-{period}-月度报税准备.xlsx",
            defaultextension=".xlsx", filetypes=[("Excel工作簿", "*.xlsx")],
        )
        if not target:
            return
        self._export_in_progress = True
        self.footer_var.set("正在生成财税工作簿...")
        dialog = ApproxProgressDialog(
            self.winfo_toplevel(), "正在生成月度报税准备工作簿",
            ["整理凭证和发票", "计算科目余额", "生成税费测算", "执行申报前校验", "写入Excel文件"],
            expected_seconds=2.0,
        )

        def run():
            try:
                result = export_finance_workbook(self.store, Path(target), period)
                error = None
            except Exception as exc:
                result = None
                error = exc
            self.after(0, lambda: self._finish_export(dialog, result, error))

        threading.Thread(target=run, name="finance-excel-export", daemon=True).start()

    def _finish_export(self, dialog, result, error):
        self._export_in_progress = False
        if error:
            self.footer_var.set(f"导出失败：{error}")
            dialog.fail("导出失败", callback=lambda: messagebox.showerror("导出失败", str(error)))
            return
        self._last_export_path = Path(result)
        self.footer_var.set(f"已导出：{result}")
        dialog.complete(
            "财税工作簿已生成",
            callback=lambda: messagebox.showinfo("导出完成", f"已保存到：\n{result}"),
        )

    def _open_export_dir(self):
        if self._last_export_path:
            directory = self._last_export_path.parent
        else:
            settings = self.store.get_settings()
            directory = self.store.resolve_export_dir(
                settings["export"].get("default_dir")
            )
        directory.mkdir(parents=True, exist_ok=True)
        os.startfile(directory)

    def set_authenticated(self, active: bool, operator: str = ""):
        self.authenticated = active

    def pack(self, **kwargs):
        super().pack(**kwargs)
        self.refresh()
