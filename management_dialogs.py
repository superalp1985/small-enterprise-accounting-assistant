#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
import shutil
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any, Callable, Dict, Optional

from legal_notice import LEGAL_NOTICE_FULL, LEGAL_NOTICE_SUMMARY, policy_snapshot_text
from account_catalog import template_labels, template_summary


BG = "#F0F0F0"
BLUE = "#0078D4"
DARK = "#003087"
WHITE = "#FFFFFF"
GREEN = "#107C10"
RED = "#D83B01"
ORANGE = "#E67E22"
FONT = ("微软雅黑", 10)
FONT_B = ("微软雅黑", 10, "bold")
FONT_T = ("微软雅黑", 14, "bold")
FONT_S = ("微软雅黑", 9)


def normalize_credit_code(value: Any) -> str:
    """Normalize a unified social credit code without accepting hidden spaces."""
    return re.sub(r"\s+", "", str(value or "")).upper()


def company_profile_errors(settings: Dict[str, Any]) -> list[str]:
    company = settings.get("company", {})
    errors = []
    if not str(company.get("name", "")).strip():
        errors.append("请填写企业名称")
    credit_code = normalize_credit_code(company.get("credit_code", ""))
    if not credit_code:
        errors.append("请填写统一社会信用代码/税号")
    elif not re.fullmatch(r"[0-9A-Z]{18}", credit_code):
        errors.append("统一社会信用代码/税号应为18位数字或大写英文字母")
    return errors


def make_btn(parent, text, command, color=BLUE, width=10):
    return tk.Button(
        parent, text=text, command=command, bg=color, fg=WHITE,
        font=FONT_B, relief="flat", padx=8, pady=4, width=width,
        activebackground=DARK, activeforeground=WHITE, cursor="hand2",
    )


def show_legal_notice(parent, settings: Optional[Dict[str, Any]] = None):
    """Show the complete free-tool positioning and responsibility boundary."""
    window = tk.Toplevel(parent)
    window.title("使用说明与责任边界")
    window.configure(bg=BG)
    window.geometry("780x620")
    window.minsize(680, 520)
    window.transient(parent)
    window.grab_set()

    header = tk.Frame(window, bg=DARK, padx=18, pady=14)
    header.pack(fill="x")
    tk.Label(
        header, text="使用说明与责任边界", font=FONT_T, bg=DARK, fg=WHITE,
    ).pack(side="left")
    tk.Label(
        header, text="免费 · 本地 · 辅助", font=FONT_S, bg=DARK, fg="#B8D9F2",
    ).pack(side="right")

    footer = tk.Frame(window, bg=BG, pady=12)
    footer.pack(side="bottom", fill="x", padx=18)
    make_btn(footer, "关闭", window.destroy, color=GREEN, width=11).pack(
        side="right"
    )
    window.bind("<Escape>", lambda _event: window.destroy())
    window.protocol("WM_DELETE_WINDOW", window.destroy)

    body = tk.Frame(window, bg=BG, padx=18, pady=14)
    body.pack(fill="both", expand=True)
    tk.Label(
        body, text=LEGAL_NOTICE_SUMMARY, font=FONT_B, bg="#FFF4CE",
        fg="#6B5200", justify="left", anchor="w", wraplength=700,
        padx=12, pady=10,
    ).pack(fill="x", pady=(0, 12))
    text = tk.Text(
        body, font=FONT, bg=WHITE, fg="#222", relief="solid", bd=1,
        wrap="word", padx=14, pady=12,
    )
    text.pack(fill="both", expand=True)
    text.insert("1.0", LEGAL_NOTICE_FULL)
    if settings:
        text.insert(
            "end",
            "\n\n当前账套政策参数快照\n" + policy_snapshot_text(settings.get("tax", {})),
        )
    text.configure(state="disabled")


class SettingsDialog:
    """Company, tax, accounting, and export settings editor."""

    def __init__(self, parent, store, on_saved: Optional[Callable] = None,
                 first_run: bool = False,
                 on_cancel: Optional[Callable] = None):
        self.parent = parent
        self.store = store
        self.on_saved = on_saved
        self.first_run = first_run
        self.on_cancel = on_cancel
        self.settings = store.get_settings()
        self.vars: Dict[str, tk.Variable] = {}
        self.widgets: Dict[str, tk.Widget] = {}
        self.window = tk.Toplevel(parent)
        self.window.title("首次设置企业资料" if first_run else "系统设置")
        self.window.configure(bg=BG)
        self.window.geometry("820x680")
        self.window.minsize(700, 560)
        self.window.transient(parent)
        self.window.grab_set()
        self._build()
        self.window.protocol("WM_DELETE_WINDOW", self._cancel)
        self.window.bind("<Control-Return>", lambda _event: self._save())

    def _build(self):
        header = tk.Frame(self.window, bg=DARK, padx=18, pady=14)
        header.pack(fill="x")
        title = "首次设置企业资料" if self.first_run else "系统设置"
        tk.Label(header, text=title, font=FONT_T, bg=DARK, fg=WHITE).pack(
            side="left"
        )
        tk.Label(
            header, text=self.store.profile_label, font=FONT_S,
            bg=DARK, fg="#B8D9F2",
        ).pack(side="right")

        # The action bar is packed first at the bottom so display scaling cannot
        # push the confirmation button outside the window.
        footer = tk.Frame(self.window, bg=BG)
        footer.pack(side="bottom", fill="x", padx=16, pady=(0, 16))
        cancel_text = "退出程序" if self.first_run else "取消"
        self.cancel_button = make_btn(
            footer, cancel_text, self._cancel, color="#666", width=9
        )
        self.cancel_button.pack(side="right", padx=(6, 0))
        save_text = "确认并进入" if self.first_run else "确认修改"
        self.save_button = make_btn(
            footer, save_text, self._save, color=GREEN, width=11
        )
        self.save_button.pack(side="right")

        notebook = ttk.Notebook(self.window)
        notebook.pack(fill="both", expand=True, padx=16, pady=14)
        company_tab = tk.Frame(notebook, bg=BG)
        tax_tab = tk.Frame(notebook, bg=BG)
        accounting_tab = tk.Frame(notebook, bg=BG)
        export_tab = tk.Frame(notebook, bg=BG)
        policy_tab = tk.Frame(notebook, bg=BG)
        notebook.add(company_tab, text="企业资料")
        if not self.first_run:
            notebook.add(tax_tab, text="税务参数")
            notebook.add(accounting_tab, text="核算参数")
            notebook.add(export_tab, text="导出设置")
            notebook.add(policy_tab, text="政策与边界")

        company_fields = [
            ("company.name", "企业/单位名称", None),
            ("company.credit_code", "统一社会信用代码", None),
            ("company.taxpayer_type", "纳税人类型", "fixed"),
            ("company.industry", "所属行业", None),
            ("company.legal_representative", "法定代表人/负责人", None),
            ("company.finance_contact", "财务联系人", None),
            ("company.phone", "联系电话", None),
            ("company.registered_address", "注册地址", None),
            ("company.bank_name", "开户银行", None),
            ("company.bank_account", "银行账号", None),
            ("company.currency", "记账本位币", "fixed"),
        ]
        for row, (key, label, options) in enumerate(company_fields):
            self._add_field(company_tab, row, key, label, options)
        company_notice = (
            "请填写企业名称和统一社会信用代码，然后点击右下角“确认并进入”。"
            if self.first_run else
            "企业名称和税号修改后，请点击右下角“确认修改”；保存后会同步刷新当前账套。"
        )
        tk.Label(
            company_tab,
            text=company_notice + "\n本版本固定适用于小规模纳税人、小型微利企业和人民币记账。",
            font=FONT_S, bg="#EAF4FB", fg="#163A5F", anchor="w",
            justify="left", wraplength=700, padx=10, pady=8,
        ).grid(
            row=len(company_fields), column=0, columnspan=2,
            sticky="ew", padx=16, pady=(10, 6),
        )

        tax_fields = [
            ("tax.vat_filing_frequency", "增值税申报频率", ["按月", "按季"]),
            ("tax.vat_rate", "增值税测算税率（%）", "percent"),
            ("tax.default_price_tax_mode", "默认价税口径", ["含税", "不含税"]),
            ("tax.surcharge_rate", "附加税费综合比例（%）", "percent"),
            ("tax.cit_filing_frequency", "企业所得税预缴频率", ["按月", "按季"]),
            ("tax.cit_rate", "所得税测算有效税率（%）", "percent"),
            ("tax.stamp_duty_filing_frequency", "印花税申报频率", ["按月", "按季"]),
            ("tax.stamp_duty_relief_rate", "印花税减征后比例（%）", "percent"),
            ("tax.small_low_profit", "产品适用范围：小型微利企业", "fixed"),
            ("tax.invoice_required", "成本费用需要合规扣除凭证", "fixed"),
            ("tax.input_vat_deductible", "小规模纳税人不抵扣进项税额", "fixed"),
        ]
        for row, (key, label, kind) in enumerate(tax_fields):
            self._add_field(tax_tab, row, key, label, kind)
        tk.Label(
            tax_tab,
            text=(
                "本版本仅支持“小规模纳税人 + 小型微利企业”。税率仅用于申报前测算，"
                "应按当前资格、所属地区和电子税务局口径维护。"
            ),
            font=FONT_S, bg="#FFF4CE", fg="#6B5200", anchor="w", padx=10, pady=8,
        ).grid(row=len(tax_fields), column=0, columnspan=2, sticky="ew", padx=16, pady=12)

        policy_fields = [
            ("tax.policy_reference_date", "政策参数复核日（YYYY-MM-DD）", "date"),
            ("tax.policy_effective_through", "当前优惠政策截止日（YYYY-MM-DD）", "date"),
            ("tax.vat_monthly_exemption_threshold", "增值税月免税销售额阈值（元）", "amount"),
            ("tax.vat_quarterly_exemption_threshold", "增值税季免税销售额阈值（元）", "amount"),
            ("tax.cit_taxable_income_limit", "小型微利企业所得额上限（元）", "amount"),
            ("tax.cit_employee_limit", "小型微利企业从业人数上限（人）", "integer"),
            ("tax.cit_asset_limit", "小型微利企业资产总额上限（元）", "amount"),
            ("tax.average_employees", "全年季度平均从业人数（人）", "integer"),
            ("tax.average_assets", "全年季度平均资产总额（元）", "amount"),
            ("tax.restricted_industry", "属于限制或禁止行业", "bool"),
            ("tax.iit_monthly_deduction", "个税每月基本减除费用（元）", "amount"),
        ]
        for row, (key, label, kind) in enumerate(policy_fields):
            self._add_field(policy_tab, row, key, label, kind)
        tk.Label(
            policy_tab,
            text=(
                "以上是申报复核参数，不是软件对优惠资格的保证。政策发生变化时，"
                "请依据财政部、税务总局、电子税务局或主管机关口径自行更新。"
            ),
            font=FONT_S, bg="#FFF4CE", fg="#6B5200", anchor="w",
            justify="left", wraplength=700, padx=10, pady=8,
        ).grid(row=len(policy_fields), column=0, columnspan=2, sticky="ew", padx=16, pady=(12, 6))
        make_btn(
            policy_tab, "查看完整责任边界",
            lambda: show_legal_notice(self.window, self.store.get_settings()),
            color="#666", width=16,
        ).grid(row=len(policy_fields) + 1, column=1, sticky="e", padx=12, pady=8)

        accounting_fields = [
            ("accounting.standard", "会计准则", "fixed"),
            ("accounting.account_template", "科目启用模板", template_labels(self.store.account_catalog)),
            ("accounting.opening_date", "开账日期（YYYY-MM-DD）", None),
            ("accounting.fiscal_year_start", "会计年度起始（月-日）", "fixed"),
            ("accounting.default_cash_subject", "默认结算科目", None),
            ("accounting.default_payable_subject", "默认应付科目", None),
            ("accounting.auto_backup", "每次启动自动备份（保留最近5份）", "fixed"),
        ]
        for row, (key, label, kind) in enumerate(accounting_fields):
            self._add_field(accounting_tab, row, key, label, kind)
        self.template_help = tk.Label(
            accounting_tab, font=FONT_S, bg="#EAF4FB", fg="#163A5F",
            anchor="w", justify="left", wraplength=700, padx=12, pady=10,
        )
        self.template_help.grid(
            row=len(accounting_fields), column=0, columnspan=2,
            sticky="ew", padx=16, pady=(10, 6),
        )
        self.vars["accounting.account_template"].trace_add(
            "write", lambda *_args: self._refresh_template_help()
        )
        self._refresh_template_help()

        self._add_field(export_tab, 0, "export.default_dir", "默认导出目录", None)
        export_entry = self.widgets["export.default_dir"]
        make_btn(
            export_tab, "选择目录",
            lambda: self._choose_directory("export.default_dir"), width=9,
        ).grid(row=0, column=2, padx=(0, 12), pady=7)
        self._add_field(
            export_tab, 1, "export.include_policy_basis", "导出政策依据工作表", "bool"
        )
        export_entry.configure(width=48)

    def _get_value(self, key: str):
        section, field = key.split(".", 1)
        return self.settings.get(section, {}).get(field, "")

    def _add_field(self, parent, row: int, key: str, label: str, kind: Any):
        tk.Label(parent, text=label, font=FONT_B, bg=BG, anchor="w").grid(
            row=row, column=0, sticky="w", padx=(18, 12), pady=7
        )
        value = self._get_value(key)
        if kind == "fixed":
            display = "是（固定）" if value is True else "否（固定）" if value is False else f"{value}（固定）"
            var = tk.StringVar(value=display)
            widget = ttk.Entry(parent, textvariable=var, state="readonly", font=FONT)
        elif kind == "bool":
            var = tk.BooleanVar(value=bool(value))
            widget = ttk.Checkbutton(parent, variable=var, text="启用")
        else:
            display = value * 100 if kind == "percent" else value
            var = tk.StringVar(value=str(display))
            if isinstance(kind, list):
                widget = ttk.Combobox(
                    parent, textvariable=var, values=kind, state="readonly", font=FONT,
                )
            else:
                widget = tk.Entry(parent, textvariable=var, font=FONT, relief="solid", bd=1)
        widget.grid(row=row, column=1, sticky="ew", padx=(0, 12), pady=7)
        parent.columnconfigure(1, weight=1)
        if kind != "fixed":
            self.vars[key] = var
        self.widgets[key] = widget

    def _choose_directory(self, key: str):
        initial = self.vars[key].get() or str(self.store.data_dir)
        selected = filedialog.askdirectory(parent=self.window, initialdir=initial)
        if selected:
            self.vars[key].set(selected)

    def _refresh_template_help(self):
        template = str(self.vars["accounting.account_template"].get()).strip()
        self.template_help.configure(
            text=template_summary(template, self.store.account_catalog)
        )

    def _save(self):
        updated = self.store.get_settings()
        for key, var in self.vars.items():
            section, field = key.split(".", 1)
            value: Any = var.get()
            if key in {
                "tax.vat_rate", "tax.surcharge_rate", "tax.cit_rate",
                "tax.stamp_duty_relief_rate",
            }:
                try:
                    value = float(value) / 100
                except ValueError:
                    messagebox.showwarning(
                        "无法保存", f"{self._label_for(key)}必须是数字", parent=self.window
                    )
                    return
                if not 0 <= value <= 1:
                    messagebox.showwarning(
                        "无法保存", f"{self._label_for(key)}应在0%至100%之间", parent=self.window
                    )
                    return
            elif key in {
                "tax.vat_monthly_exemption_threshold",
                "tax.vat_quarterly_exemption_threshold",
                "tax.cit_taxable_income_limit",
                "tax.cit_asset_limit",
                "tax.average_assets",
                "tax.iit_monthly_deduction",
            }:
                try:
                    value = float(value)
                except ValueError:
                    messagebox.showwarning(
                        "无法保存", f"{self._label_for(key)}必须是数字", parent=self.window
                    )
                    return
                if value < 0:
                    messagebox.showwarning(
                        "无法保存", f"{self._label_for(key)}不能小于0", parent=self.window
                    )
                    return
            elif key in {"tax.cit_employee_limit", "tax.average_employees"}:
                try:
                    value = int(value)
                except ValueError:
                    messagebox.showwarning(
                        "无法保存", f"{self._label_for(key)}必须是整数", parent=self.window
                    )
                    return
                if value < 0:
                    messagebox.showwarning(
                        "无法保存", f"{self._label_for(key)}不能小于0", parent=self.window
                    )
                    return
            elif key in {"tax.policy_reference_date", "tax.policy_effective_through"}:
                try:
                    datetime.strptime(str(value).strip(), "%Y-%m-%d")
                except ValueError:
                    messagebox.showwarning(
                        "无法保存", f"{self._label_for(key)}应为 YYYY-MM-DD 格式",
                        parent=self.window,
                    )
                    return
            updated.setdefault(section, {})[field] = value

        updated["company"]["name"] = str(
            updated["company"].get("name", "")
        ).strip()
        updated["company"]["credit_code"] = normalize_credit_code(
            updated["company"].get("credit_code", "")
        )
        profile_errors = company_profile_errors(updated)
        if profile_errors:
            messagebox.showwarning(
                "企业资料未完成", "\n".join(profile_errors), parent=self.window
            )
            return
        try:
            self.store.save_settings(updated)
        except Exception as exc:
            messagebox.showerror("无法保存", str(exc), parent=self.window)
            return
        persisted = self.store.get_settings()
        self.window.destroy()
        message = "企业资料已保存并同步到当前账套" if self.first_run else "系统设置已保存并同步到当前账套"
        messagebox.showinfo("保存成功", message, parent=self.parent)
        if self.on_saved:
            self.on_saved(persisted)

    def _cancel(self):
        self.window.destroy()
        if self.on_cancel:
            self.on_cancel()

    @staticmethod
    def _label_for(key: str) -> str:
        return {
            "tax.vat_rate": "增值税测算税率",
            "tax.surcharge_rate": "附加税费综合比例",
            "tax.cit_rate": "所得税测算有效税率",
            "tax.stamp_duty_relief_rate": "印花税减征后比例",
            "tax.vat_monthly_exemption_threshold": "增值税月免税销售额阈值",
            "tax.vat_quarterly_exemption_threshold": "增值税季免税销售额阈值",
            "tax.cit_taxable_income_limit": "小型微利企业所得额上限",
            "tax.cit_asset_limit": "小型微利企业资产总额上限",
            "tax.average_employees": "全年季度平均从业人数",
            "tax.average_assets": "全年季度平均资产总额",
            "tax.iit_monthly_deduction": "个税每月基本减除费用",
            "tax.policy_reference_date": "政策参数复核日",
            "tax.policy_effective_through": "优惠政策截止日",
        }.get(key, key)


class ArchiveManagerDialog:
    """Validated ZIP backup manager for the active accounting profile."""

    def __init__(self, parent, store, on_restored: Optional[Callable] = None):
        self.parent = parent
        self.store = store
        self.on_restored = on_restored
        self.backups = []
        self.window = tk.Toplevel(parent)
        self.window.title("存档管理")
        self.window.configure(bg=BG)
        self.window.geometry("850x540")
        self.window.minsize(700, 460)
        self.window.transient(parent)
        self.window.grab_set()
        self._build()
        self._refresh()

    def _build(self):
        header = tk.Frame(self.window, bg=DARK, padx=18, pady=14)
        header.pack(fill="x")
        tk.Label(header, text="存档管理", font=FONT_T, bg=DARK, fg=WHITE).pack(side="left")
        tk.Label(
            header, text=f"当前账套：{self.store.profile_label}", font=FONT_S,
            bg=DARK, fg="#B8D9F2",
        ).pack(side="right")

        toolbar = tk.Frame(self.window, bg=BG, pady=10)
        toolbar.pack(fill="x", padx=14)
        make_btn(toolbar, "创建备份", self._create, color=GREEN).pack(side="left", padx=3)
        make_btn(toolbar, "恢复", self._restore, color=BLUE, width=8).pack(side="left", padx=3)
        make_btn(toolbar, "导入ZIP", self._import, color=BLUE).pack(side="left", padx=3)
        make_btn(toolbar, "导出ZIP", self._export, color=BLUE).pack(side="left", padx=3)
        make_btn(toolbar, "检查账套", self._check_integrity, color="#666").pack(
            side="left", padx=3
        )
        make_btn(toolbar, "自动修复", self._repair, color=ORANGE).pack(side="left", padx=3)
        make_btn(toolbar, "删除", self._delete, color=RED, width=8).pack(side="left", padx=3)
        make_btn(toolbar, "刷新", self._refresh, color="#666", width=8).pack(side="right", padx=3)

        columns = ("名称", "创建时间", "文件数", "大小", "校验")
        body = tk.Frame(self.window, bg=BG)
        body.pack(fill="both", expand=True, padx=14, pady=(0, 10))
        self.tree = ttk.Treeview(body, columns=columns, show="headings", height=16)
        widths = (200, 170, 80, 100, 110)
        for name, width in zip(columns, widths):
            self.tree.heading(name, text=name)
            self.tree.column(name, width=width, anchor="center")
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        footer = tk.Frame(self.window, bg=BG)
        footer.pack(fill="x", padx=14, pady=(0, 14))
        tk.Label(
            footer,
            text="SQLite已启用WAL和完整性检查；恢复、修复和反过账前都会自动备份。",
            font=FONT_S, bg=BG, fg="#666",
        ).pack(side="left")
        make_btn(footer, "关闭", self.window.destroy, color="#666", width=8).pack(side="right")

    def _refresh(self):
        self.backups = self.store.list_backups()
        for item in self.tree.get_children():
            self.tree.delete(item)
        for index, backup in enumerate(self.backups):
            size = int(backup.get("size", 0))
            files = len(backup.get("files", {}))
            valid = "可恢复" if backup.get("files") else "需检查"
            self.tree.insert(
                "", "end", iid=str(index), values=(
                    backup.get("name", Path(backup.get("path", "")).stem),
                    backup.get("created_at", ""), files,
                    f"{size / 1024:.1f} KB", valid,
                )
            )

    def _selected(self) -> Optional[Dict[str, Any]]:
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("提示", "请先选择一个存档", parent=self.window)
            return None
        return self.backups[int(selected[0])]

    def _create(self):
        label = simpledialog.askstring(
            "创建备份", "备份名称：", parent=self.window, initialvalue="手工备份"
        )
        if label is None:
            return
        try:
            path = self.store.create_backup(label)
            self._refresh()
            messagebox.showinfo("备份完成", f"已创建：{path.name}", parent=self.window)
        except Exception as exc:
            messagebox.showerror("备份失败", str(exc), parent=self.window)

    def _check_integrity(self):
        result = self.store.integrity_check()
        if result["ok"]:
            messagebox.showinfo(
                "账套检查通过",
                "SQLite完整性：通过\n日志模式：WAL\nJSON数据镜像：通过",
                parent=self.window,
            )
            return
        messagebox.showwarning(
            "发现账套问题",
            "\n".join(result["problems"][:10]) + "\n\n可点击“自动修复”处理。",
            parent=self.window,
        )

    def _repair(self):
        if not messagebox.askyesno(
            "确认自动修复",
            "系统将先创建当前备份，再修复SQLite账套和JSON数据镜像。继续吗？",
            parent=self.window,
        ):
            return
        try:
            self.store.create_backup("自动修复前备份")
            result = self.store.repair_data()
            self._refresh()
            if self.on_restored:
                self.on_restored()
            messagebox.showinfo(
                "修复完成",
                f"账套完整性检查已通过，共处理 {len(result.get('repaired', []))} 项。",
                parent=self.window,
            )
        except Exception as exc:
            messagebox.showerror(
                "自动修复未完成",
                f"系统没有强行覆盖账套。请从最近可恢复备份中恢复。\n\n原因：{exc}",
                parent=self.window,
            )

    def _restore(self):
        backup = self._selected()
        if not backup:
            return
        if not messagebox.askyesno(
            "确认恢复", "恢复将覆盖当前账套数据，系统会先自动备份当前数据。继续吗？",
            parent=self.window,
        ):
            return
        try:
            self.store.restore_backup(Path(backup["path"]))
            if self.on_restored:
                self.on_restored()
            self._refresh()
            messagebox.showinfo("恢复完成", "账套数据已恢复", parent=self.window)
        except Exception as exc:
            messagebox.showerror("恢复失败", str(exc), parent=self.window)

    def _import(self):
        source = filedialog.askopenfilename(
            parent=self.window, title="导入存档", filetypes=[("ZIP存档", "*.zip")]
        )
        if not source:
            return
        try:
            self.store.import_backup(Path(source))
            self._refresh()
            messagebox.showinfo("导入完成", "存档已通过校验并导入", parent=self.window)
        except Exception as exc:
            messagebox.showerror("导入失败", str(exc), parent=self.window)

    def _export(self):
        backup = self._selected()
        if not backup:
            return
        source = Path(backup["path"])
        target = filedialog.asksaveasfilename(
            parent=self.window, title="导出存档", initialfile=source.name,
            defaultextension=".zip", filetypes=[("ZIP存档", "*.zip")],
        )
        if not target:
            return
        try:
            shutil.copy2(source, target)
            messagebox.showinfo("导出完成", f"已导出到：{target}", parent=self.window)
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc), parent=self.window)

    def _delete(self):
        backup = self._selected()
        if not backup:
            return
        if not messagebox.askyesno("确认删除", "删除选中的存档？", parent=self.window):
            return
        try:
            self.store.delete_backup(Path(backup["path"]))
            self._refresh()
        except Exception as exc:
            messagebox.showerror("删除失败", str(exc), parent=self.window)


def show_settings(parent, store, on_saved: Optional[Callable] = None,
                  first_run: bool = False,
                  on_cancel: Optional[Callable] = None):
    return SettingsDialog(parent, store, on_saved, first_run, on_cancel)


def show_archive_manager(parent, store, on_restored: Optional[Callable] = None):
    return ArchiveManagerDialog(parent, store, on_restored)
