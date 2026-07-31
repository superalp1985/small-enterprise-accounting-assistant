#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Global Chinese context menu for editable Tk text controls."""

from __future__ import annotations

import tkinter as tk


_SUPPORTED_CLASSES = ("Entry", "TEntry", "Text", "TCombobox", "Spinbox", "TSpinbox")


def _widget_state(widget: tk.Misc) -> str:
    try:
        return str(widget.cget("state"))
    except tk.TclError:
        return "normal"


def _has_selection(widget: tk.Misc) -> bool:
    try:
        if widget.winfo_class() == "Text":
            return bool(widget.tag_ranges("sel"))
        return bool(widget.selection_present())
    except (tk.TclError, AttributeError):
        return False


def _select_all(widget: tk.Misc) -> None:
    try:
        if widget.winfo_class() == "Text":
            widget.tag_add("sel", "1.0", "end-1c")
            widget.mark_set("insert", "1.0")
            widget.see("insert")
        else:
            widget.selection_range(0, "end")
            widget.icursor("end")
    except (tk.TclError, AttributeError):
        pass


def install_text_context_menu(root: tk.Misc) -> None:
    """Install one right-click menu for current and future controls in this Tk app."""
    menu = tk.Menu(root, tearoff=False)
    root._text_context_menu = menu

    def show(event):
        widget = event.widget
        state = _widget_state(widget)
        readonly = state in {"readonly", "disabled"}
        selected = _has_selection(widget)
        menu.delete(0, "end")
        menu.add_command(
            label="剪切", state="normal" if selected and not readonly else "disabled",
            command=lambda: widget.event_generate("<<Cut>>"),
        )
        menu.add_command(
            label="复制", state="normal" if selected else "disabled",
            command=lambda: widget.event_generate("<<Copy>>"),
        )
        menu.add_command(
            label="粘贴", state="normal" if not readonly else "disabled",
            command=lambda: widget.event_generate("<<Paste>>"),
        )
        menu.add_separator()
        menu.add_command(label="全选", command=lambda: _select_all(widget))
        try:
            widget.focus_set()
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    for class_name in _SUPPORTED_CLASSES:
        root.bind_class(class_name, "<Button-3>", show, add="+")

