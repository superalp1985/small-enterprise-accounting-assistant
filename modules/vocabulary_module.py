#!/usr/bin/env python3
import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
# -*- coding: utf-8 -*-
"""
vocabulary_module.py - 词库管理模块
提供词库加载、查询、编辑等功能
"""

import json
import tkinter as tk
from pathlib import Path
from typing import List, Dict, Any, Optional
from tkinter import ttk, messagebox
import re

from account_catalog import (
    account_label,
    enrich_vocab_records,
    load_account_catalog,
    semantic_payload,
)


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


def _default_profile_path(field: str, fallback: str) -> Path:
    project_root = Path(__file__).parent.parent
    config_path = project_root / "config.json"
    try:
        with open(config_path, encoding="utf-8") as handle:
            accounting = json.load(handle).get("accounting", {})
        profiles = accounting.get("profiles", {})
        profile_key = accounting.get("defaultProfile", next(iter(profiles), ""))
        configured = profiles.get(profile_key, {}).get(field)
        if configured:
            return project_root / configured
    except (OSError, ValueError, TypeError):
        pass
    return project_root / fallback


def load_vocab(path: Optional[Path] = None,
               catalog_path: Optional[Path] = None) -> List[Dict]:
    """加载词库"""
    if path is None:
        path = _default_profile_path("vocabPath", "vocab_library.json")

    if path.exists():
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            data = data.get("科目", [])
        records = [r for r in data if r.get("id") and str(r["id"]).isdigit()]
        return enrich_vocab_records(records, load_account_catalog(catalog_path))
    return []


def load_semantic_categories(path: Optional[Path] = None) -> Dict:
    """加载语义分类"""
    if path is None:
        path = _default_profile_path(
            "semanticCategoriesPath", "semantic_categories.json"
        )

    if path.exists():
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    return {}


def find_conflicts(vocab: List[Dict]) -> Dict[str, List[Dict]]:
    """查找词库冲突"""
    # 构建词汇到科目的映射
    vocab_map: Dict[str, List[Dict]] = {}

    for record in vocab:
        # 检查所有层级的词
        words = set()

        if record.get("input"):
            words.add(record["input"].lower())

        if record.get("layer2"):
            for w in record["layer2"].split("、"):
                words.add(w.strip().lower())

        layer3_raw = record.get("layer3", "")
        if layer3_raw:
            layer3 = layer3_raw.split("||")[0]  # 只取||前面的词
            for w in layer3.split("、"):
                w = w.strip().lower()
                if len(w) >= 2:
                    words.add(w)

        # 添加到映射
        subject = record.get("subject", "")
        for word in words:
            if word not in vocab_map:
                vocab_map[word] = []
            vocab_map[word].append(record)

    # 找出冲突（一个词对应多个科目）
    conflicts = {word: records for word, records in vocab_map.items() if len(records) > 1}

    return conflicts


def save_vocab(vocab: List[Dict], path: Optional[Path] = None):
    """保存词库"""
    if path is None:
        path = _default_profile_path("vocabPath", "vocab_library.json")

    stripped = semantic_payload(vocab)
    payload: Any = stripped
    if path.exists():
        with open(path, encoding='utf-8') as f:
            existing = json.load(f)
        if isinstance(existing, dict) and "科目" in existing:
            existing["科目"] = stripped
            payload = existing

    temp_path = path.with_name(f".{path.name}.tmp")
    with open(temp_path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    temp_path.replace(path)


def parse_terms(value: str) -> List[str]:
    """Parse newline and common delimiter separated vocabulary terms."""
    terms = []
    seen = set()
    for raw in re.split(r"[、,，;；\r\n]+", value or ""):
        term = raw.strip()
        key = term.casefold()
        if term and key not in seen:
            terms.append(term)
            seen.add(key)
    return terms


def split_layer3(value: str):
    """Separate editable colloquial terms from preserved conflict metadata."""
    editable, marker, metadata = (value or "").partition("||")
    suffix = f"{marker}{metadata}" if marker else ""
    return parse_terms(editable), suffix


class VocabModule(tk.Frame):
    """词库管理模块"""

    def __init__(self, parent, config, authenticated=False, semantic_matcher=None):
        super().__init__(parent, bg=BG)
        self.parent = parent
        self.config = config
        self.authenticated = authenticated
        self.semantic_matcher = semantic_matcher

        self.catalog = load_account_catalog(config.account_catalog_path)
        self.vocab = load_vocab(config.vocab_path, config.account_catalog_path)
        self.semantic_categories = load_semantic_categories(config.semantic_categories_path)
        self.conflicts = find_conflicts(self.vocab)

        self._build_ui()

    def _build_ui(self):
        """构建UI"""
        f = tk.LabelFrame(self, text=" 词库管理 ", font=FONT_T,
                          bg=BG, fg=DARK, bd=1, relief="groove")
        f.pack(fill="both", expand=True, pady=6)

        # 工具栏
        tool = tk.Frame(f, bg=BG, pady=8)
        tool.pack(fill="x", padx=12)

        tk.Label(tool, text="当前词库统计：", font=FONT_B, bg=BG).pack(side="left")

        self.stats_var = tk.StringVar()
        self._update_stats()
        tk.Label(tool, textvariable=self.stats_var, font=FONT_S, bg=BG,
                 fg=BLUE).pack(side="left", padx=8)

        make_btn(tool, "刷新词库", self._refresh, width=10).pack(side="right", padx=4)

        # Tab页
        notebook = ttk.Notebook(f)
        notebook.pack(fill="both", expand=True, padx=12, pady=8)

        # 词库浏览Tab
        self.vocab_tab = tk.Frame(notebook, bg=BG)
        notebook.add(self.vocab_tab, text="词库浏览")

        # 冲突处理Tab
        self.conflict_tab = tk.Frame(notebook, bg=BG)
        notebook.add(self.conflict_tab, text="冲突处理")

        # 语义分类Tab
        self.semantic_tab = tk.Frame(notebook, bg=BG)
        notebook.add(self.semantic_tab, text="语义分类")

        self._build_vocab_tab(self.vocab_tab)
        self._build_conflict_tab(self.conflict_tab)
        self._build_semantic_tab(self.semantic_tab)

    def _build_vocab_tab(self, parent):
        """构建词库浏览Tab"""
        # 搜索框
        search_frame = tk.Frame(parent, bg=BG)
        search_frame.pack(fill="x", padx=12, pady=(12, 6))

        tk.Label(search_frame, text="搜索科目：", font=FONT_B, bg=BG).pack(side="left")
        self.search_var = tk.StringVar()
        search_entry = tk.Entry(search_frame, textvariable=self.search_var,
                                  font=FONT, width=30, relief="solid", bd=1)
        search_entry.pack(side="left", padx=6)
        search_entry.bind("<KeyRelease>", self._on_search)

        make_btn(search_frame, "新增语义映射", self._add_subject, color=GREEN, width=12).pack(side="right", padx=4)

        # 列表
        cols = ("ID", "科目名称", "精确词", "同义词数", "口语词数", "冲突", "操作")
        self.tree = ttk.Treeview(parent, columns=cols, show="headings", height=20)

        self.tree.heading("ID", text="ID")
        self.tree.column("ID", width=50, anchor="center")
        self.tree.heading("科目名称", text="科目名称")
        self.tree.column("科目名称", width=180, anchor="w")
        self.tree.heading("精确词", text="精确词")
        self.tree.column("精确词", width=120, anchor="w")
        self.tree.heading("同义词数", text="同义词")
        self.tree.column("同义词数", width=80, anchor="center")
        self.tree.heading("口语词数", text="口语词")
        self.tree.column("口语词数", width=80, anchor="center")
        self.tree.heading("冲突", text="冲突")
        self.tree.column("冲突", width=60, anchor="center")
        self.tree.heading("操作", text="操作")
        self.tree.column("操作", width=120, anchor="center")

        sb = ttk.Scrollbar(parent, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)

        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self.tree.bind("<Double-1>", self._on_double_click)

        self._refresh_vocab_list()

    def _build_conflict_tab(self, parent):
        """构建冲突处理Tab"""
        if not self.conflicts:
            tk.Label(parent, text="✓ 当前词库无冲突", font=FONT_T, bg=BG, fg=GREEN).pack(
                pady=40)
            return

        tk.Label(parent, text=f"发现 {len(self.conflicts)} 个词汇冲突，需要处理",
                 font=FONT_B, bg=BG, fg=ORANGE).pack(pady=12)

        # 冲突列表
        self.conflict_tree = ttk.Treeview(parent, columns=("词汇", "科目数", "操作"),
                                            show="headings", height=20)

        self.conflict_tree.heading("词汇", text="冲突词汇")
        self.conflict_tree.column("词汇", width=150, anchor="w")
        self.conflict_tree.heading("科目数", text="涉及科目")
        self.conflict_tree.column("科目数", width=100, anchor="center")
        self.conflict_tree.heading("操作", text="操作")
        self.conflict_tree.column("操作", width=150, anchor="center")

        sb = ttk.Scrollbar(parent, orient="vertical", command=self.conflict_tree.yview)
        self.conflict_tree.configure(yscrollcommand=sb.set)

        self.conflict_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self.conflict_tree.bind("<Double-1>", self._on_conflict_double_click)

        for word, records in self.conflicts.items():
            self.conflict_tree.insert("", tk.END, values=(word, len(records), "查看"))

    def _build_semantic_tab(self, parent):
        """构建语义分类Tab"""
        if not self.semantic_categories:
            tk.Label(parent, text="无语义分类数据", font=FONT_T, bg=BG).pack(pady=40)
            return

        categories = self.semantic_categories.get("categories", {})
        tk.Label(parent, text=f"共 {len(categories)} 个分类",
                 font=FONT_B, bg=BG).pack(pady=8)

        # 分类列表
        for cat_name, cat_info in categories.items():
            frame = tk.Frame(parent, bg=WHITE, relief="solid", bd=1)
            frame.pack(fill="x", padx=12, pady=4)

            # 分类名称和标签
            tk.Label(frame, text=f"{cat_name}", font=FONT_B, bg=WHITE).pack(
                anchor="w", padx=12, pady=8)

            tags = cat_info.get("tags", [])
            if tags:
                tag_text = " | ".join(tags[:10])
                tk.Label(frame, text=f"标签：{tag_text}", font=FONT_S,
                         bg=WHITE, fg="#666").pack(anchor="w", padx=24, pady=(0, 8))

            # 关联科目
            subjects = cat_info.get("subjects", [])
            if subjects:
                tk.Label(frame, text=f"关联科目：{len(subjects)} 个",
                         font=FONT_S, bg=WHITE, fg="#666").pack(anchor="w", padx=24, pady=(0, 8))

    def _refresh(self):
        """刷新词库"""
        self.vocab = load_vocab(self.config.vocab_path)
        self.semantic_categories = load_semantic_categories(self.config.semantic_categories_path)
        self.conflicts = find_conflicts(self.vocab)
        self._sync_matcher()
        self._update_stats()
        self._refresh_vocab_list()
        self._rebuild_conflict_tab()

    def _update_stats(self):
        mapped_codes = len({str(row.get("subject_code", "")) for row in self.vocab})
        self.stats_var.set(
            f"{mapped_codes}/66 个科目 | {len(self.vocab)} 条语义映射 | "
            f"{len(self.conflicts)} 个词汇冲突"
        )

    def _sync_matcher(self):
        if self.semantic_matcher is not None:
            self.semantic_matcher.vocab_library = [dict(record) for record in self.vocab]

    def _rebuild_conflict_tab(self):
        for child in self.conflict_tab.winfo_children():
            child.destroy()
        self._build_conflict_tab(self.conflict_tab)

    def _refresh_vocab_list(self, filter_text: str = ""):
        """刷新词库列表"""
        for item in self.tree.get_children():
            self.tree.delete(item)

        for record in self.vocab:
            subject = record.get("subject", "")

            # 搜索过滤
            if filter_text:
                searchable = " ".join(
                    str(record.get(key, ""))
                    for key in ("code", "subject", "input", "layer2", "layer3")
                ).lower()
                if filter_text.lower() not in searchable:
                    continue

            # 检查冲突
            input_word = record.get("input", "").lower()
            is_conflicted = input_word in self.conflicts

            layer2_count = len(record.get("layer2", "").split("、")) if record.get("layer2") else 0

            layer3_raw = record.get("layer3", "")
            layer3 = layer3_raw.split("||")[0] if layer3_raw else ""
            layer3_count = len(layer3.split("、")) if layer3 else 0

            conflict_mark = "⚠" if is_conflicted else ""

            self.tree.insert("", tk.END, values=(
                record.get("id", ""),
                subject[:25],
                record.get("input", "")[:20],
                layer2_count,
                layer3_count,
                conflict_mark,
                "编辑"
            ))

    def _on_search(self, event):
        """搜索事件"""
        filter_text = self.search_var.get()
        self._refresh_vocab_list(filter_text)

    def _on_double_click(self, event):
        """双击编辑"""
        item = self.tree.selection()
        if not item:
            return

        values = self.tree.item(item, "values")
        record_id = values[0]
        self._edit_subject(record_id)

    def _on_conflict_double_click(self, event):
        """冲突双击查看"""
        item = self.conflict_tree.selection()
        if not item:
            return

        values = self.conflict_tree.item(item, "values")
        word = values[0]
        self._view_conflict(word)

    def _add_subject(self):
        """新增科目"""
        d = tk.Toplevel(self)
        d.title("新增语义映射")
        d.configure(bg=BG)
        d.geometry("760x620")
        d.transient(self.winfo_toplevel())
        d.grab_set()
        subject_var = tk.StringVar()
        detail_var = tk.StringVar()
        exact_var = tk.StringVar()
        for row, (label, var) in enumerate((
            ("官方科目", subject_var), ("明细名称（可选）", detail_var), ("精确词", exact_var),
        )):
            tk.Label(d, text=label, font=FONT_B, bg=BG).grid(
                row=row, column=0, sticky="w", padx=18, pady=8
            )
            if row == 0:
                values = [account_label(row) for row in self.catalog.get("accounts", [])]
                widget = ttk.Combobox(
                    d, textvariable=var, values=values, state="readonly", font=FONT,
                )
                if values:
                    var.set(values[0])
            else:
                widget = tk.Entry(d, textvariable=var, font=FONT, relief="solid", bd=1)
            widget.grid(row=row, column=1, sticky="ew", padx=(0, 18), pady=8)
        d.columnconfigure(1, weight=1)

        editors = {}
        for row, (key, label, height) in enumerate((
            ("layer2", "同义词（每行一个或用顿号分隔）", 5),
            ("layer3", "口语词（每行一个或用顿号分隔）", 5),
            ("logic", "语义区分规则", 8),
        ), start=3):
            tk.Label(d, text=label, font=FONT_B, bg=BG).grid(
                row=row, column=0, sticky="nw", padx=18, pady=8
            )
            text_widget = tk.Text(d, font=FONT, height=height, wrap="word",
                                  relief="solid", bd=1)
            text_widget.grid(row=row, column=1, sticky="nsew", padx=(0, 18), pady=8)
            editors[key] = text_widget
            d.rowconfigure(row, weight=1)

        def save_new():
            subject = subject_var.get().strip()
            code = subject.split(" ", 1)[0]
            detail = detail_var.get().strip().lstrip("-")
            exact = exact_var.get().strip()
            known_codes = {
                str(account.get("code", "")) for account in self.catalog.get("accounts", [])
            }
            if code not in known_codes or not exact:
                messagebox.showwarning(
                    "无法保存", "请选择官方66科目并填写精确词", parent=d
                )
                return
            if any(str(row.get("input", "")).casefold() == exact.casefold() for row in self.vocab):
                messagebox.showwarning("无法保存", "该精确词已经存在", parent=d)
                return
            numeric_ids = [int(row["id"]) for row in self.vocab if str(row.get("id", "")).isdigit()]
            record = {
                "id": max(numeric_ids, default=0) + 1,
                "input": exact,
                "subject_code": code,
                "subject_detail": detail,
                "layer2": "、".join(parse_terms(editors["layer2"].get("1.0", "end"))),
                "layer3": "、".join(parse_terms(editors["layer3"].get("1.0", "end"))),
                "logic": editors["logic"].get("1.0", "end").strip() or "用户新增语义映射",
                "overlap_risk": "待复核",
            }
            enriched = enrich_vocab_records([record], self.catalog)[0]
            self.vocab.append(enriched)
            try:
                save_vocab(self.vocab, self.config.vocab_path)
            except Exception as exc:
                self.vocab.remove(enriched)
                messagebox.showerror("保存失败", str(exc), parent=d)
                return
            self.conflicts = find_conflicts(self.vocab)
            self._sync_matcher()
            self._update_stats()
            self._refresh_vocab_list(self.search_var.get())
            self._rebuild_conflict_tab()
            d.destroy()
            messagebox.showinfo("新增成功", f"已新增语义映射：{enriched['subject']}", parent=self)

        footer = tk.Frame(d, bg=BG)
        footer.grid(row=6, column=0, columnspan=2, sticky="e", padx=18, pady=14)
        make_btn(footer, "保存", save_new, color=GREEN, width=10).pack(side="left", padx=4)
        make_btn(footer, "取消", d.destroy, color="#666", width=9).pack(side="left", padx=4)

    def _edit_subject(self, record_id):
        """Edit the exact, synonym, and colloquial terms for one subject."""
        record = next(
            (item for item in self.vocab if str(item.get("id")) == str(record_id)),
            None,
        )
        if not record:
            messagebox.showerror("编辑失败", "未找到对应的词库记录")
            return

        d = tk.Toplevel(self)
        self._editor_window = d
        d.title("编辑词库词条")
        d.configure(bg=BG)
        d.geometry("940x650")
        d.minsize(780, 560)
        d.transient(self.winfo_toplevel())
        d.grab_set()

        header = tk.Frame(d, bg=DARK, padx=18, pady=14)
        header.pack(fill="x")
        code = str(record.get("code", "")).strip()
        subject = record.get("subject", "")
        tk.Label(header, text=subject, font=FONT_T, bg=DARK,
                 fg=WHITE).pack(anchor="w")
        tk.Label(header, text=f"词条 ID：{record.get('id', '')}", font=FONT_S,
                 bg=DARK, fg="#B8D9F2").pack(anchor="w", pady=(3, 0))

        exact_frame = tk.Frame(d, bg=BG)
        exact_frame.pack(fill="x", padx=18, pady=(16, 8))
        tk.Label(exact_frame, text="精确词", font=FONT_B, bg=BG,
                 width=10, anchor="w").pack(side="left")
        exact_var = tk.StringVar(value=record.get("input", ""))
        exact_entry = tk.Entry(exact_frame, textvariable=exact_var, font=FONT,
                               relief="solid", bd=1)
        exact_entry.pack(side="left", fill="x", expand=True)

        editor_row = tk.Frame(d, bg=BG)
        editor_row.pack(fill="both", expand=True, padx=18, pady=8)

        synonym_frame = tk.LabelFrame(editor_row, text=" 同义词 ", font=FONT_B,
                                      bg=BG, fg=DARK, bd=1, relief="groove")
        synonym_frame.pack(side="left", fill="both", expand=True, padx=(0, 7))
        synonym_text = tk.Text(synonym_frame, font=FONT, wrap="word",
                               relief="solid", bd=1, undo=True)
        synonym_scroll = ttk.Scrollbar(synonym_frame, orient="vertical",
                                       command=synonym_text.yview)
        synonym_text.configure(yscrollcommand=synonym_scroll.set)
        synonym_text.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=10)
        synonym_scroll.pack(side="right", fill="y", padx=(0, 10), pady=10)
        synonym_text.insert("1.0", "\n".join(parse_terms(record.get("layer2", ""))))

        colloquial_terms, layer3_suffix = split_layer3(record.get("layer3", ""))
        colloquial_frame = tk.LabelFrame(editor_row, text=" 口语词 ", font=FONT_B,
                                         bg=BG, fg=DARK, bd=1, relief="groove")
        colloquial_frame.pack(side="right", fill="both", expand=True, padx=(7, 0))
        colloquial_text = tk.Text(colloquial_frame, font=FONT, wrap="word",
                                  relief="solid", bd=1, undo=True)
        colloquial_scroll = ttk.Scrollbar(colloquial_frame, orient="vertical",
                                          command=colloquial_text.yview)
        colloquial_text.configure(yscrollcommand=colloquial_scroll.set)
        colloquial_text.pack(side="left", fill="both", expand=True,
                             padx=(10, 0), pady=10)
        colloquial_scroll.pack(side="right", fill="y", padx=(0, 10), pady=10)
        colloquial_text.insert("1.0", "\n".join(colloquial_terms))

        self._synonym_text = synonym_text
        self._colloquial_text = colloquial_text

        footer = tk.Frame(d, bg=BG)
        footer.pack(fill="x", padx=18, pady=(4, 16))

        def save_changes(event=None):
            exact_word = exact_var.get().strip()
            if not exact_word:
                messagebox.showwarning("无法保存", "精确词不能为空", parent=d)
                exact_entry.focus_set()
                return "break"

            synonyms = parse_terms(synonym_text.get("1.0", "end"))
            colloquial = parse_terms(colloquial_text.get("1.0", "end"))
            record["input"] = exact_word
            record["layer2"] = "、".join(synonyms)
            record["layer3"] = "、".join(colloquial) + layer3_suffix

            try:
                save_vocab(self.vocab, self.config.vocab_path)
            except Exception as exc:
                messagebox.showerror("保存失败", str(exc), parent=d)
                return "break"

            self.conflicts = find_conflicts(self.vocab)
            self._sync_matcher()
            self._update_stats()
            self._refresh_vocab_list(self.search_var.get())
            self._rebuild_conflict_tab()
            d.destroy()
            messagebox.showinfo("保存成功", f"已更新：{subject}", parent=self)
            return "break"

        make_btn(footer, "取消", d.destroy, color="#666", width=9).pack(
            side="right", padx=(6, 0)
        )
        self._save_edit_button = make_btn(
            footer, "保存修改", save_changes, color=GREEN, width=11
        )
        self._save_edit_button.pack(side="right")

        d.bind("<Control-s>", save_changes)
        d.bind("<Escape>", lambda event: d.destroy())
        exact_entry.focus_set()

    def _view_conflict(self, word: str):
        """查看冲突详情"""
        records = self.conflicts.get(word, [])

        if not records:
            return

        d = tk.Toplevel(self.parent)
        d.title(f"冲突详情：{word}")
        d.configure(bg=BG)
        d.grab_set()

        tk.Label(d, text=f"词汇「{word}」对应 {len(records)} 个科目",
                 font=FONT_T, bg=BG, fg=ORANGE).pack(pady=(16, 8))

        # 显示所有科目的法律依据
        for i, record in enumerate(records):
            frame = tk.Frame(d, bg=WHITE, relief="solid", bd=1)
            frame.pack(fill="x", padx=12, pady=6)

            subject = record.get("subject", "")
            law = record.get("law", "")

            tk.Label(frame, text=f"{i+1}. {subject}", font=FONT_B, bg=WHITE, fg=DARK).pack(
                anchor="w", padx=12, pady=(8, 4))

            tk.Label(frame, text=f"法律依据：", font=FONT_S, bg=WHITE, fg="#666").pack(anchor="w", padx=12)
            tk.Label(frame, text=law[:150] + "..." if len(law) > 150 else law,
                     font=FONT_S, bg=WHITE, wraplength=500).pack(anchor="w", padx=24, pady=(0, 8))

        tk.Label(d, text="请在实际使用时根据业务场景选择正确的科目",
                 font=FONT_S, bg=BG, fg=ORANGE).pack(pady=(8, 16))

        make_btn(d, "关闭", d.destroy, width=8).pack(pady=(0, 16))

        d.update_idletasks()
        x = self.parent.winfo_rootx() + (self.parent.winfo_width() - d.winfo_width()) // 2
        d.geometry(f"+{x}+200")

    def pack_forget(self):
        """隐藏模块"""
        super().pack_forget()

    def pack(self, **kwargs):
        """显示模块"""
        super().pack(**kwargs)

    def set_authenticated(self, active: bool, operator: str = ""):
        """更新当前登录会话状态。"""
        self.authenticated = active


def make_btn(parent, text, cmd, color=BLUE, width=12):
    """创建标准按钮"""
    return tk.Button(parent, text=text, command=cmd,
                     bg=color, fg=WHITE, font=FONT_B,
                     relief="flat", padx=8, pady=4,
                     activebackground=DARK, activeforeground=WHITE,
                     cursor="hand2", width=width)
