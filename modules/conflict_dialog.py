#!/usr/bin/env python3
import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
# -*- coding: utf-8 -*-
"""
conflict_dialog.py - 科目冲突选择对话框
当相同词汇对应多个科目时，显示所有科目的法律依据让用户选择
"""

import tkinter as tk
from tkinter import ttk
from typing import List, Dict, Optional


BG = "#F0F0F0"
BLUE = "#0078D4"
DARK = "#003087"
WHITE = "#FFFFFF"
GREEN = "#107C10"
RED = "#D83B01"
YELLOW = "#FFF4CE"
GRAY = "#D0D0D0"

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


def show_conflict_selection(parent, query: str, conflicts: List[Dict],
                                callback) -> Optional[Dict]:
    """
    显示科目冲突选择对话框

    Args:
        parent: 父窗口
        query: 用户查询文本
        conflicts: 冲突的科目列表
        callback: 用户选择后的回调函数

    Returns:
        用户选择的科目记录，如果取消则返回None
    """
    d = tk.Toplevel(parent)
    d.title("科目冲突 - 请选择")
    d.configure(bg=BG)
    d.grab_set()
    d.resizable(False, False)

    # 标题
    tk.Label(d, text=f"⚠ 检测到词汇冲突", font=FONT_T, bg=BG, fg=RED).pack(
        pady=(16, 4))
    tk.Label(d, text=f"查询：「{query}」", font=FONT_B, bg=BG).pack(
        anchor="w", padx=20, pady=(4, 2))
    tk.Label(d, text=f"该词汇对应 {len(conflicts)} 个科目，请根据业务场景选择：",
             font=FONT_B, bg=BG).pack(anchor="w", padx=20, pady=(2, 8))

    # 科目选择区域
    var = tk.IntVar(value=-1)

    # 可滚动区域
    canvas = tk.Canvas(d, bg=BG, highlightthickness=0, width=700)
    csb = ttk.Scrollbar(d, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=csb.set)

    canvas.pack(side="left", fill="both", expand=True, padx=(20, 0), pady=4)
    csb.pack(side="right", fill="y", padx=(0, 20))

    sf = tk.Frame(canvas, bg=BG)
    canvas.create_window((0, 0), window=sf, anchor="nw")

    # 构建科目卡片
    for i, record in enumerate(conflicts):
        subject = record.get("subject", "")
        law = record.get("law", "")
        distinction_rule = record.get("distinction_rule", "")
        match_type = record.get("match_type", "")
        matched_word = record.get("matched_word", "")
        score = record.get("score", 0)
        rule_category = record.get("rule_category", "")
        rule_basis = record.get("rule_basis", "")
        recommendation_reason = record.get("recommendation_reason", "")

        # 功能和经济分类
        func_class = record.get("functional_class", "")
        econ_class = record.get("economic_class", "")
        class_info = f"功能分类：{func_class} | 经济分类：{econ_class}"

        card = tk.Frame(sf, bg=WHITE, relief="solid", bd=2)
        card.pack(fill="x", pady=4, padx=(0, 10))

        # 选择按钮
        btn_frame = tk.Frame(card, bg=WHITE)
        btn_frame.pack(anchor="w", padx=8, pady=(6, 2))

        tk.Radiobutton(btn_frame, text=subject, variable=var, value=i,
                      font=FONT_B, bg=WHITE, fg=DARK, anchor="w",
                      activebackground=WHITE, indicatoron=0).pack(anchor="w", padx=8)

        # 匹配信息
        if matched_word:
            tk.Label(card, text=f"匹配词：{matched_word}",
                     font=FONT_S, bg=WHITE, fg="#666").pack(anchor="w", padx=24, pady=(0, 2))

        if match_type:
            tk.Label(card, text=f"匹配类型：{match_type} | 置信度：{score:.1f}",
                     font=FONT_S, bg=WHITE, fg="#666").pack(anchor="w", padx=24, pady=(0, 2))

        if match_type == "ai_suggested":
            evidence = (
                f"规则词库分类：{rule_category}\n"
                f"规则依据：{rule_basis}\n"
                f"模型推荐理由：{recommendation_reason}"
            )
            tk.Label(card, text=evidence, font=FONT_S, bg="#E8F4FD", fg="#333",
                     wraplength=650, justify="left", relief="solid", bd=1,
                     padx=8, pady=6).pack(fill="x", padx=8, pady=(4, 6))

        # 分类信息
        if func_class or econ_class:
            tk.Label(card, text=class_info, font=FONT_S, bg=BG, fg="#666",
                     wraplength=650, justify="left").pack(anchor="w", padx=8, pady=(0, 4))

        # 区分规则（重要！）
        if distinction_rule:
            rule_frame = tk.Frame(card, bg="#E8F4FD", relief="solid", bd=1)
            rule_frame.pack(fill="x", padx=8, pady=(6, 0))

            tk.Label(rule_frame, text="💡 区分规则", font=FONT_B, bg="#E8F4FD",
                     fg="#0056B3").pack(anchor="w", padx=8, pady=(6, 2))

            tk.Label(rule_frame, text=distinction_rule, font=FONT_S, bg="#E8F4FD",
                     fg="#333", wraplength=650, justify="left",
                     padx=8, pady=(0, 6)).pack(anchor="w", padx=8)

        # 法律依据
        if law:
            tk.Label(card, text="法律依据：", font=FONT_B, bg=WHITE,
                     fg=DARK).pack(anchor="w", padx=8, pady=(6, 2))

            # 简短显示法律依据
            law_short = law[:200] + "..." if len(law) > 200 else law
            law_label = tk.Label(card, text=law_short, font=FONT_S, bg=YELLOW,
                                fg="#333", wraplength=650, justify="left",
                                relief="solid", bd=1, padx=8, pady=8)
            law_label.pack(anchor="w", padx=8, pady=(0, 6))

            # 查看完整法律依据按钮
            def show_full_law(subj=subject, full_law=law):
                dlg = tk.Toplevel(d)
                dlg.title("完整法律依据")
                dlg.configure(bg=BG)
                dlg.geometry("700x400")

                tk.Label(dlg, text=f"科目：{subj}", font=FONT_B, bg=BG, fg=DARK).pack(
                    pady=(12, 8), padx=20)

                tk.Label(dlg, text="法律依据：", font=FONT_B, bg=BG).pack(
                    anchor="w", padx=20)

                text_area = tk.Text(dlg, font=FONT_S, width=75, height=18,
                                  wrap="word", relief="solid", bd=1, bg=WHITE)
                text_area.pack(fill="both", expand=True, padx=20, pady=(0, 12))
                text_area.insert("1.0", full_law)
                text_area.configure(state="disabled")

                make_btn(dlg, "关闭", dlg.destroy, width=8).pack(pady=(0, 12))
                dlg.update_idletasks()
                x = d.winfo_rootx() + (d.winfo_width() - 700) // 2
                y = d.winfo_rooty() + 100
                dlg.geometry(f"+{x}+{y}")

            tk.Button(card, text="查看完整法律依据", font=FONT_S, bg="#F0F0",
                     relief="flat", pady=2,
                     command=lambda: show_full_law(subject, law)).pack(anchor="w", padx=8, pady=(0, 6))

    def _on_frame_cfg(e):
        canvas.configure(scrollregion=canvas.bbox("all"))
    sf.bind("<Configure>", _on_frame_cfg)

    d.after(50, lambda: canvas.configure(height=min(sf.winfo_reqheight(), 500)))

    # 按钮行
    btn_row = tk.Frame(d, bg=BG)
    btn_row.pack(pady=(12, 16))

    result = {"selected": None}

    def confirm():
        idx = var.get()
        if idx < 0:
            messagebox.showwarning("提示", "请先选择一个科目", parent=d)
            return

        result["selected"] = conflicts[idx]
        d.destroy()

        if callback:
            callback(result["selected"])

    def cancel():
        result["selected"] = None
        d.destroy()

        if callback:
            callback(None)

    make_btn(btn_row, "✓ 确认选择", confirm, color=GREEN, width=14).pack(side="left", padx=6)
    make_btn(btn_row, "✗ 取消", cancel, color=RED, width=10).pack(side="left", padx=6)

    d.update_idletasks()
    x = parent.winfo_rootx() + (parent.winfo_width() - d.winfo_width()) // 2
    y = parent.winfo_rooty() + 100
    d.geometry(f"+{x}+{y}")

    # 等待对话框关闭
    d.wait_window()

    return result["selected"]
