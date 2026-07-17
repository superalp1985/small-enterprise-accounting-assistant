#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import math
import time
import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional, Sequence


BG = "#F4F6F8"
DARK = "#203040"
BLUE = "#0078D4"
WHITE = "#FFFFFF"
FONT = ("微软雅黑", 10)
FONT_B = ("微软雅黑", 11, "bold")
FONT_S = ("微软雅黑", 9)


class ApproxProgressDialog:
    """Modal progress feedback for work whose exact completion is unavailable."""

    def __init__(self, parent, title: str, stages: Sequence[str],
                 expected_seconds: float = 4.0):
        self.parent = parent
        self.stages = list(stages) or ["正在处理"]
        self.expected_seconds = max(0.5, float(expected_seconds))
        self.started_at = time.monotonic()
        self.running = True
        self._after_id = None

        self.window = tk.Toplevel(parent)
        self.window.title(title)
        self.window.configure(bg=BG)
        self.window.resizable(False, False)
        self.window.transient(parent)
        self.window.protocol("WM_DELETE_WINDOW", lambda: None)

        body = tk.Frame(self.window, bg=BG, padx=24, pady=20)
        body.pack(fill="both", expand=True)

        tk.Label(body, text=title, font=FONT_B, bg=BG, fg=DARK).pack(anchor="w")
        self.detail_var = tk.StringVar(value=self.stages[0])
        tk.Label(body, textvariable=self.detail_var, font=FONT, bg=BG, fg="#4B5A67",
                 width=52, anchor="w").pack(fill="x", pady=(10, 8))

        self.progress_var = tk.DoubleVar(value=6)
        ttk.Progressbar(body, variable=self.progress_var, maximum=100,
                        mode="determinate", length=430).pack(fill="x")

        self.percent_var = tk.StringVar(value="6%")
        tk.Label(body, textvariable=self.percent_var, font=FONT_S, bg=BG,
                 fg="#667582").pack(anchor="e", pady=(5, 0))

        self.window.update_idletasks()
        self._center()
        try:
            self.window.grab_set()
        except tk.TclError:
            pass
        self._tick()

    def _center(self):
        width = self.window.winfo_reqwidth()
        height = self.window.winfo_reqheight()
        x = self.parent.winfo_rootx() + max(0, (self.parent.winfo_width() - width) // 2)
        y = self.parent.winfo_rooty() + max(0, (self.parent.winfo_height() - height) // 2)
        self.window.geometry(f"+{x}+{y}")

    def _tick(self):
        if not self.running or not self.window.winfo_exists():
            return
        elapsed = time.monotonic() - self.started_at
        ratio = 1.0 - math.exp(-elapsed / self.expected_seconds)
        percent = min(92.0, 6.0 + 88.0 * ratio)
        stage_index = min(len(self.stages) - 1, int(ratio * len(self.stages)))
        self.progress_var.set(percent)
        self.percent_var.set(f"{int(percent)}%")
        self.detail_var.set(f"{self.stages[stage_index]}  已等待 {elapsed:.1f} 秒")
        self._after_id = self.window.after(120, self._tick)

    def complete(self, message: str = "处理完成",
                 callback: Optional[Callable] = None, delay_ms: int = 220):
        self._finish(100, message, callback, delay_ms)

    def fail(self, message: str, callback: Optional[Callable] = None,
             delay_ms: int = 900):
        self._finish(max(1, self.progress_var.get()), message, callback, delay_ms)

    def _finish(self, percent: float, message: str,
                callback: Optional[Callable], delay_ms: int):
        if not self.running:
            return
        self.running = False
        if self._after_id:
            try:
                self.window.after_cancel(self._after_id)
            except tk.TclError:
                pass
        self.progress_var.set(percent)
        self.percent_var.set("100%" if percent >= 100 else "处理失败")
        self.detail_var.set(message)
        self.window.after(delay_ms, lambda: self._close_then(callback))

    def _close_then(self, callback: Optional[Callable]):
        try:
            self.window.grab_release()
        except tk.TclError:
            pass
        try:
            self.window.destroy()
        except tk.TclError:
            pass
        if callback:
            callback()

    def close(self):
        if self.running:
            self.running = False
        self._close_then(None)
