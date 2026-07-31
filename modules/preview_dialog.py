#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
preview_dialog.py - 票据预览确认对话框
提供OCR结果预览、模糊引导、强制确认功能
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime
import json

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

class InvoicePreviewDialog:
    """票据预览确认对话框"""

    def __init__(
        self,
        parent,
        invoice_data: Dict,
        vocab: List[Dict],
        confirm_label: str = "确认入账",
        title: str = "票据信息确认",
    ):
        self.parent = parent
        self.invoice_data = invoice_data
        self.vocab = vocab
        self.confirmed_data = {}
        self.editing = False
        self.field_entries = {}
        self.confirm_label = confirm_label
        self.title = title

        self._build_ui()

    def _build_ui(self):
        """构建UI"""
        self.dlg = tk.Toplevel(self.parent)
        self.dlg.title(self.title)
        self.dlg.configure(bg=BG)
        self.dlg.grab_set()
        self.dlg.geometry("980x650")
        self.dlg.minsize(900, 600)

        # 标题
        tk.Label(self.dlg, text="请确认票据识别信息", font=FONT_T, bg=BG, fg=BLUE).pack(
            pady=(14, 8), padx=20)

        # 内容区域
        content = tk.Frame(self.dlg, bg=BG)
        content.pack(fill="both", expand=True, padx=20, pady=0)

        # 左侧：识别结果
        left = tk.Frame(content, bg=WHITE, relief="solid", bd=1)
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))

        tk.Label(left, text="OCR识别结果", font=FONT_B, bg=BG, fg=DARK).pack(
            pady=(8, 4), padx=12)

        # 识别字段
        self.fields = {
            "file_name": tk.StringVar(value=self.invoice_data.get("file_name", "")),
            "invoice_code": tk.StringVar(value=self.invoice_data.get("invoice_code", "")),
            "invoice_no": tk.StringVar(value=self.invoice_data.get("invoice_no", "")),
            "invoice_date": tk.StringVar(value=self.invoice_data.get("invoice_date", "")),
            "amount": tk.StringVar(value=str(
                self.invoice_data.get("total_amount", self.invoice_data.get("amount", 0))
            )),
            "tax_amount": tk.StringVar(value=str(self.invoice_data.get("tax_amount", 0))),
            "seller": tk.StringVar(value=self.invoice_data.get("seller", "")),
            "buyer": tk.StringVar(value=self.invoice_data.get("buyer", ""))
        }

        field_labels = {
            "file_name": "文件名",
            "invoice_code": "发票代码",
            "invoice_no": "发票号码",
            "invoice_date": "开票日期",
            "amount": "价税合计（元）",
            "tax_amount": "税额（元）",
            "seller": "销售方",
            "buyer": "购买方"
        }

        for key, label in field_labels.items():
            row = tk.Frame(left, bg=WHITE)
            row.pack(fill="x", padx=12, pady=4)
            tk.Label(row, text=label, font=FONT, bg=WHITE, width=12, anchor="e").pack(side="left", padx=(0, 12))
            entry = tk.Entry(row, textvariable=self.fields[key], font=FONT, width=35,
                             state="readonly", readonlybackground="#F7F7F7")
            entry.pack(side="left", fill="x", expand=True, padx=(0, 12))
            self.field_entries[key] = entry

        # 置信度显示
        self.confidence_var = tk.StringVar(value="识别置信度：--")
        if "confidence" in self.invoice_data:
            conf = self.invoice_data["confidence"] * 100
            self.confidence_var.set(f"识别置信度：{conf:.1f}%")

        # 置信度颜色
        conf_value = self.invoice_data.get("confidence", 0)
        conf_color = GREEN if conf_value >= 0.8 else (ORANGE if conf_value >= 0.5 else RED)
        self.confidence_label = tk.Label(left, textvariable=self.confidence_var,
                                          font=FONT_B, bg=WHITE, fg=conf_color)
        self.confidence_label.pack(pady=(8, 4), padx=12)

        # 警告提示
        warning_text = "⚠ 请仔细核对识别结果，所有信息必须正确确认"
        tk.Label(left, text=warning_text, font=FONT_S, bg="#FFF3CD", fg="#856404",
                 pady=4).pack(fill="x", padx=12, pady=(0, 8))

        # 右侧：科目选择
        right = tk.Frame(content, bg=WHITE, relief="solid", bd=1)
        right.pack(side="right", fill="both", expand=True, padx=(8, 0))

        tk.Label(right, text="科目选择", font=FONT_B, bg=BG, fg=DARK).pack(
            pady=(8, 4), padx=12)

        # 摘要输入
        tk.Label(right, text="业务摘要", font=FONT_B, bg=WHITE).pack(anchor="w", padx=12, pady=(8, 0))
        self.desc_var = tk.StringVar(value=self.invoice_data.get("description", ""))
        tk.Entry(right, textvariable=self.desc_var, font=FONT, width=35).pack(
            padx=12, pady=(0, 8))

        # 科目匹配结果
        tk.Label(right, text="智能匹配科目", font=FONT_B, bg=WHITE).pack(anchor="w", padx=12, pady=(8, 0))

        self.match_result_var = tk.StringVar(value=self.invoice_data.get("matched_subject", ""))
        self.match_combo = ttk.Combobox(right, textvariable=self.match_result_var,
                                    values=[r["subject"] for r in self.vocab], font=FONT,
                                    width=35, state="readonly")
        self.match_combo.pack(padx=12, pady=(0, 8))
        self.match_combo.bind("<<ComboboxSelected>>", lambda event: self._show_distinction_rule())

        # 匹配提示
        self.match_hint_var = tk.StringVar(value="点击「重新匹配」可智能匹配科目")
        self.match_hint_label = tk.Label(right, textvariable=self.match_hint_var, font=FONT_S, bg=WHITE,
                                          fg="#666")
        self.match_hint_label.pack(anchor="w", padx=12, pady=(0, 4))

        # 区分规则显示
        tk.Label(right, text="区分规则", font=FONT_B, bg=BG, fg=DARK).pack(anchor="w", padx=12, pady=(8, 4))

        self.distinction_text = tk.Text(right, font=FONT_S, width=40, height=8,
                                       wrap="word", relief="solid", bd=1, bg=WHITE)
        self.distinction_text.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        self.distinction_text.configure(state="disabled")

        self._show_distinction_rule()

        # 法律依据
        tk.Label(right, text="法律依据", font=FONT_B, bg=BG, fg=DARK).pack(anchor="w", padx=12, pady=(8, 4))

        law_var = tk.StringVar(value=self.invoice_data.get("law", "点击「查看依据」查看"))
        tk.Entry(right, textvariable=law_var, font=FONT_S, width=35, state="readonly",
               relief="flat", bg="#E8E8E8").pack(padx=12, pady=(0, 8))

        # 确认按钮区
        btn_row = tk.Frame(self.dlg, bg=BG)
        btn_row.pack(fill="x", padx=20, pady=(12, 16))

        make_btn(btn_row, "重新匹配", self._re_match, BLUE, 12).pack(side="left", padx=4)
        make_btn(btn_row, "查看依据", self._view_law, BLUE, 12).pack(side="left", padx=4)
        self.edit_button = make_btn(btn_row, "修改信息", self._edit_fields, ORANGE, 12)
        self.edit_button.pack(side="left", padx=4)
        make_btn(btn_row, self.confirm_label, self._confirm, GREEN, 12).pack(side="right", padx=4)
        make_btn(btn_row, "✗ 取消", self.dlg.destroy, RED, 10).pack(side="right", padx=4)

        self.parent.update_idletasks()

    def _show_distinction_rule(self):
        """显示区分规则"""
        subject = self.match_result_var.get()
        self.distinction_text.configure(state="normal")
        self.distinction_text.delete("1.0", "end")
        if not subject:
            self.distinction_text.insert("1.0", "请先选择科目")
            self.distinction_text.configure(state="disabled")
            return

        record = next((r for r in self.vocab if r.get("subject") == subject), None)
        if record:
            rule = record.get("distinction_rule", "")
            self.distinction_text.insert("1.0", rule or "暂无区分规则")
        else:
            self.distinction_text.insert("1.0", "未找到该科目的规则记录")
        self.distinction_text.configure(state="disabled")

    def _re_match(self):
        """重新匹配"""
        # 触发重新匹配，需要调用semantic_matcher
        self.match_result_var.set("")
        self.match_hint_label.configure(fg="blue")
        self.match_hint_label.after(2000, lambda: self.match_hint_label.configure(fg="#666"))
        # 这里应该调用parent的re_match方法
        messagebox.showinfo("提示", "请使用上方界面的「重新匹配」按钮")

    def _view_law(self):
        """查看法律依据"""
        subject = self.match_result_var.get()
        if not subject:
            messagebox.showwarning("提示", "请先选择科目")
            return

        record = next((r for r in self.vocab if r.get("subject") == subject), None)
        if record:
            law = record.get("law", "")

            d = tk.Toplevel(self.dlg)
            d.title("法律依据")
            d.configure(bg=BG)
            d.grab_set()

            tk.Label(d, text=f"科目：{subject}", font=FONT_B, bg=BG, fg=DARK).pack(
                pady=(14, 8), padx=20)

            text_area = tk.Text(d, font=FONT_S, width=60, height=15, wrap="word",
                              relief="solid", bd=1, bg=WHITE)
            text_area.pack(fill="both", expand=True, padx=20, pady=(0, 12))
            text_area.insert("1.0", law or "暂无具体条款")
            text_area.configure(state="disabled")

            make_btn(d, "关闭", d.destroy, width=8).pack(pady=(0, 12))

            x = self.dlg.winfo_rootx() + (self.dlg.winfo_width() - d.winfo_width()) // 2
            y = self.dlg.winfo_rooty() + 100
            d.geometry(f"+{x}+{y}")

    def _edit_fields(self):
        """修改字段"""
        self.editing = not self.editing
        for key, entry in self.field_entries.items():
            if key == "file_name":
                entry.configure(state="readonly")
            else:
                entry.configure(state="normal" if self.editing else "readonly")
        self.edit_button.configure(text="锁定信息" if self.editing else "修改信息")
        if self.editing:
            self.field_entries["invoice_no"].focus_set()

    def _confirm(self):
        """确认入账"""
        # 验证必填字段
        subject = self.match_result_var.get().strip()
        desc = self.desc_var.get().strip()
        amount_str = self.fields["amount"].get().strip()
        tax_amount_str = self.fields["tax_amount"].get().strip() or "0"
        invoice_date = self.fields["invoice_date"].get().strip()

        if not subject:
            messagebox.showwarning("提示", "必须选择科目")
            return

        if not desc:
            messagebox.showwarning("提示", "必须填写业务摘要")
            return

        if not amount_str:
            messagebox.showwarning("提示", "必须填写金额")
            return

        try:
            amount = float(amount_str)
        except ValueError:
            messagebox.showerror("错误", "金额格式不正确")
            return
        if amount <= 0:
            messagebox.showerror("错误", "金额必须大于0")
            return
        try:
            tax_amount = float(tax_amount_str)
        except ValueError:
            messagebox.showerror("错误", "税额格式不正确")
            return
        if tax_amount < 0 or tax_amount > amount:
            messagebox.showerror("错误", "税额应大于等于0，且不能超过价税合计")
            return
        try:
            datetime.strptime(invoice_date, "%Y-%m-%d")
        except ValueError:
            messagebox.showerror("错误", "开票日期请填写为 YYYY-MM-DD，例如 2026-07-29")
            return

        # 收集确认数据
        self.confirmed_data = {
            "file_name": self.fields["file_name"].get(),
            "invoice_code": self.fields["invoice_code"].get().strip(),
            "invoice_no": self.fields["invoice_no"].get(),
            "invoice_date": invoice_date,
            "amount": amount,
            "total_amount": amount,
            "tax_amount": tax_amount,
            "net_amount": round(amount - tax_amount, 2),
            "seller": self.fields["seller"].get(),
            "buyer": self.fields["buyer"].get(),
            "subject": subject,
            "description": desc,
            "original_data": self.invoice_data
        }

        self.dlg.destroy()

    def show(self) -> Optional[Dict]:
        """显示对话框，返回确认数据"""
        self.dlg.wait_window()
        return self.confirmed_data if self.confirmed_data else None

def show_invoice_preview(
    parent,
    invoice_data: Dict,
    vocab: List[Dict],
    confirm_label: str = "确认入账",
    title: str = "票据信息确认",
) -> Optional[Dict]:
    """显示票据预览确认对话框"""
    dialog = InvoicePreviewDialog(
        parent,
        invoice_data,
        vocab,
        confirm_label=confirm_label,
        title=title,
    )
    return dialog.show()
