#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local account authentication for the desktop application."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import messagebox
from typing import Optional

from text_context_menu import install_text_context_menu


SCHEMA_VERSION = 1
PBKDF2_ITERATIONS = 600_000
SALT_BYTES = 16
HASH_BYTES = 32

BG = "#F3F5F7"
NAVY = "#003087"
BLUE = "#0078D4"
GREEN = "#107C10"
WHITE = "#FFFFFF"
MUTED = "#666666"
ERROR = "#C42B1C"
FONT = ("微软雅黑", 10)
FONT_B = ("微软雅黑", 10, "bold")
FONT_T = ("微软雅黑", 16, "bold")
FONT_S = ("微软雅黑", 9)


class AuthenticationError(ValueError):
    """Raised when credentials cannot be created or verified."""


class AuthenticationDataError(AuthenticationError):
    """Raised when the local credential record is missing required data."""


@dataclass(frozen=True)
class AuthSession:
    username: str


class CredentialStore:
    """Persist a single local account using PBKDF2-HMAC-SHA256."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def is_configured(self) -> bool:
        if not self.path.exists():
            return False
        self._read_record()
        return True

    def username(self) -> str:
        return str(self._read_record()["username"])

    def create_account(self, username: str, password: str) -> AuthSession:
        if self.path.exists():
            raise AuthenticationError("本机已经创建账号，请直接登录。")
        normalized = self.validate_username(username)
        self.validate_password(password, normalized)
        now = datetime.now().isoformat(timespec="seconds")
        salt = secrets.token_bytes(SALT_BYTES)
        record = {
            "schema_version": SCHEMA_VERSION,
            "algorithm": "PBKDF2-HMAC-SHA256",
            "iterations": PBKDF2_ITERATIONS,
            "username": normalized,
            "salt": base64.b64encode(salt).decode("ascii"),
            "password_hash": base64.b64encode(
                self._derive(password, salt, PBKDF2_ITERATIONS)
            ).decode("ascii"),
            "created_at": now,
            "updated_at": now,
        }
        self._write_record(record)
        return AuthSession(normalized)

    def authenticate(self, username: str, password: str) -> Optional[AuthSession]:
        record = self._read_record()
        supplied_username = str(username).strip()
        salt = base64.b64decode(record["salt"], validate=True)
        expected_hash = base64.b64decode(record["password_hash"], validate=True)
        actual_hash = self._derive(password, salt, int(record["iterations"]))
        username_ok = hmac.compare_digest(
            supplied_username.casefold().encode("utf-8"),
            str(record["username"]).casefold().encode("utf-8"),
        )
        password_ok = hmac.compare_digest(actual_hash, expected_hash)
        if username_ok and password_ok:
            return AuthSession(str(record["username"]))
        return None

    def change_password(
        self,
        username: str,
        current_password: str,
        new_password: str,
    ) -> None:
        session = self.authenticate(username, current_password)
        if not session:
            raise AuthenticationError("当前密码不正确。")
        self.validate_password(new_password, session.username)
        if hmac.compare_digest(
            current_password.encode("utf-8"), new_password.encode("utf-8")
        ):
            raise AuthenticationError("新密码不能与当前密码相同。")

        record = self._read_record()
        salt = secrets.token_bytes(SALT_BYTES)
        record.update({
            "iterations": PBKDF2_ITERATIONS,
            "salt": base64.b64encode(salt).decode("ascii"),
            "password_hash": base64.b64encode(
                self._derive(new_password, salt, PBKDF2_ITERATIONS)
            ).decode("ascii"),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        })
        self._write_record(record)

    @staticmethod
    def validate_username(username: str) -> str:
        value = str(username).strip()
        if not 2 <= len(value) <= 32:
            raise AuthenticationError("账号长度应为 2 至 32 个字符。")
        if any(ord(char) < 32 for char in value):
            raise AuthenticationError("账号不能包含控制字符。")
        allowed = {"_", "-", ".", "@"}
        if any(not (char.isalnum() or char in allowed) for char in value):
            raise AuthenticationError("账号仅可使用中文、字母、数字及 _ - . @。")
        return value

    @staticmethod
    def validate_password(password: str, username: str = "") -> None:
        value = str(password)
        if not 8 <= len(value) <= 128:
            raise AuthenticationError("密码长度应为 8 至 128 个字符。")
        if value.strip() != value or not value.strip():
            raise AuthenticationError("密码首尾不能有空格，也不能全部为空格。")
        if username and value.casefold() == username.casefold():
            raise AuthenticationError("密码不能与账号相同。")
        categories = sum((
            any(char.isalpha() for char in value),
            any(char.isdigit() for char in value),
            any(not char.isalnum() for char in value),
        ))
        if categories < 2:
            raise AuthenticationError("密码至少应包含字母、数字、符号中的两类。")

    @staticmethod
    def _derive(password: str, salt: bytes, iterations: int) -> bytes:
        return hashlib.pbkdf2_hmac(
            "sha256", str(password).encode("utf-8"), salt, iterations, HASH_BYTES
        )

    def _read_record(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            required = {
                "schema_version", "algorithm", "iterations", "username",
                "salt", "password_hash", "created_at", "updated_at",
            }
            if not isinstance(data, dict) or not required.issubset(data):
                raise ValueError("缺少必要字段")
            if data["schema_version"] != SCHEMA_VERSION:
                raise ValueError("不支持的凭据版本")
            if data["algorithm"] != "PBKDF2-HMAC-SHA256":
                raise ValueError("不支持的密码算法")
            iterations = int(data["iterations"])
            if not 100_000 <= iterations <= 2_000_000:
                raise ValueError("迭代次数异常")
            self.validate_username(str(data["username"]))
            salt = base64.b64decode(data["salt"], validate=True)
            password_hash = base64.b64decode(data["password_hash"], validate=True)
            if len(salt) < SALT_BYTES or len(password_hash) != HASH_BYTES:
                raise ValueError("密码摘要长度异常")
            return data
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise AuthenticationDataError(
                f"本地账号文件无法读取或已经损坏：{self.path}"
            ) from exc

    def _write_record(self, record: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = json.dumps(record, ensure_ascii=False, indent=2)
        try:
            temporary.write_text(payload, encoding="utf-8")
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            os.replace(temporary, self.path)
        finally:
            if temporary.exists():
                temporary.unlink(missing_ok=True)


def _set_icon(window: tk.Misc, icon_path: Optional[Path]) -> None:
    if not icon_path or not Path(icon_path).exists():
        return
    try:
        window.iconbitmap(str(icon_path))
    except tk.TclError:
        pass


def _center(window: tk.Misc, width: int, height: int) -> None:
    window.update_idletasks()
    x = max(0, (window.winfo_screenwidth() - width) // 2)
    y = max(0, (window.winfo_screenheight() - height) // 2)
    window.geometry(f"{width}x{height}+{x}+{y}")


def _primary_button(parent: tk.Misc, text: str, command) -> tk.Button:
    return tk.Button(
        parent, text=text, command=command, bg=BLUE, fg=WHITE, font=FONT_B,
        relief="flat", padx=18, pady=7, activebackground=NAVY,
        activeforeground=WHITE, cursor="hand2",
    )


def show_startup_auth(
    app_name: str,
    store: CredentialStore,
    icon_path: Optional[Path] = None,
) -> Optional[AuthSession]:
    """Show the mandatory first-run setup or login window."""
    root = tk.Tk()
    install_text_context_menu(root)
    root.title(f"{app_name} - 账户登录")
    root.configure(bg=BG)
    root.resizable(False, False)
    _set_icon(root, icon_path)
    result: dict[str, Optional[AuthSession]] = {"session": None}

    try:
        configured = store.is_configured()
    except AuthenticationDataError as exc:
        root.withdraw()
        messagebox.showerror(
            "账户文件异常",
            f"程序未进入账套，也没有启动本地模型。\n\n{exc}\n\n"
            "请从可信备份恢复 security 目录，或联系维护人员处理。",
            parent=root,
        )
        root.destroy()
        return None

    header = tk.Frame(root, bg=NAVY, height=78)
    header.pack(fill="x")
    tk.Label(
        header,
        text="首次设置本机账号" if not configured else "登录本机账套",
        font=FONT_T, bg=NAVY, fg=WHITE,
    ).pack(anchor="w", padx=28, pady=(17, 2))
    tk.Label(
        header,
        text="验证成功后才会加载账套界面和本地智能模型",
        font=FONT_S, bg=NAVY, fg="#C9DCFF",
    ).pack(anchor="w", padx=28)

    body = tk.Frame(root, bg=BG)
    body.pack(fill="both", expand=True, padx=28, pady=18)
    username_var = tk.StringVar()
    password_var = tk.StringVar()
    confirm_var = tk.StringVar()
    error_var = tk.StringVar()

    def add_field(label: str, variable: tk.StringVar, show: str = "") -> tk.Entry:
        tk.Label(body, text=label, font=FONT_B, bg=BG, fg="#222").pack(anchor="w")
        entry = tk.Entry(
            body, textvariable=variable, show=show, font=("微软雅黑", 11),
            relief="solid", bd=1,
        )
        entry.pack(fill="x", pady=(4, 11), ipady=5)
        return entry

    username_entry = add_field("账号", username_var)
    password_entry = add_field("密码", password_var, "*")
    confirm_entry = None
    if not configured:
        confirm_entry = add_field("确认密码", confirm_var, "*")

    tk.Label(
        body,
        text=(
            "密码只保存为加盐摘要，不保存明文；请妥善保管。\n"
            "此登录用于限制软件入口，不等同于磁盘或账套文件加密。"
        ),
        font=FONT_S, bg=BG, fg=MUTED, justify="left",
    ).pack(anchor="w", pady=(0, 8))
    tk.Label(
        body, textvariable=error_var, font=FONT_S, bg=BG, fg=ERROR,
        justify="left", wraplength=440,
    ).pack(anchor="w", pady=(0, 6))

    button_row = tk.Frame(body, bg=BG)
    button_row.pack(fill="x", side="bottom", pady=(6, 0))

    def finish(session: AuthSession) -> None:
        result["session"] = session
        root.destroy()

    def submit(event=None) -> None:
        error_var.set("")
        try:
            if configured:
                session = store.authenticate(username_var.get(), password_var.get())
                if not session:
                    raise AuthenticationError("账号或密码不正确，请重新输入。")
            else:
                if password_var.get() != confirm_var.get():
                    raise AuthenticationError("两次输入的密码不一致。")
                session = store.create_account(username_var.get(), password_var.get())
            finish(session)
        except AuthenticationError as exc:
            password_var.set("")
            confirm_var.set("")
            error_var.set(str(exc))
            password_entry.focus_set()

    def cancel() -> None:
        result["session"] = None
        root.destroy()

    tk.Button(
        button_row, text="退出", command=cancel, bg="#E5E5E5", fg="#333",
        font=FONT_B, relief="flat", padx=16, pady=7, cursor="hand2",
    ).pack(side="right", padx=(8, 0))
    _primary_button(
        button_row,
        "创建账号并进入" if not configured else "登录并进入",
        submit,
    ).pack(side="right")

    root.protocol("WM_DELETE_WINDOW", cancel)
    root.bind("<Return>", submit)
    _center(root, 520, 500 if not configured else 420)
    username_entry.focus_set()
    root.mainloop()
    return result["session"]


def show_change_password_dialog(
    parent: tk.Misc,
    store: CredentialStore,
    username: str,
) -> bool:
    """Require the current password before replacing it."""
    dialog = tk.Toplevel(parent)
    dialog.title("修改登录密码")
    dialog.configure(bg=BG)
    dialog.resizable(False, False)
    dialog.transient(parent)
    dialog.grab_set()
    result = {"changed": False}

    tk.Label(dialog, text="修改登录密码", font=FONT_T, bg=BG, fg=NAVY).pack(
        anchor="w", padx=24, pady=(20, 4)
    )
    tk.Label(
        dialog, text=f"当前账号：{username}", font=FONT, bg=BG, fg=MUTED,
    ).pack(anchor="w", padx=24, pady=(0, 14))

    form = tk.Frame(dialog, bg=BG)
    form.pack(fill="x", padx=24)
    current_var = tk.StringVar()
    new_var = tk.StringVar()
    confirm_var = tk.StringVar()
    error_var = tk.StringVar()

    def field(label: str, variable: tk.StringVar) -> tk.Entry:
        tk.Label(form, text=label, font=FONT_B, bg=BG).pack(anchor="w")
        entry = tk.Entry(form, textvariable=variable, show="*", font=FONT, bd=1)
        entry.pack(fill="x", pady=(4, 10), ipady=4)
        return entry

    current_entry = field("当前密码", current_var)
    field("新密码", new_var)
    field("确认新密码", confirm_var)
    tk.Label(
        form, textvariable=error_var, font=FONT_S, bg=BG, fg=ERROR,
        wraplength=390, justify="left",
    ).pack(anchor="w")

    row = tk.Frame(dialog, bg=BG)
    row.pack(fill="x", padx=24, pady=(14, 20))

    def submit(event=None) -> None:
        error_var.set("")
        if new_var.get() != confirm_var.get():
            error_var.set("两次输入的新密码不一致。")
            return
        try:
            store.change_password(username, current_var.get(), new_var.get())
        except AuthenticationError as exc:
            error_var.set(str(exc))
            current_var.set("")
            current_entry.focus_set()
            return
        result["changed"] = True
        messagebox.showinfo("密码已修改", "登录密码已更新，下次启动请使用新密码。", parent=dialog)
        dialog.destroy()

    tk.Button(
        row, text="取消", command=dialog.destroy, bg="#E5E5E5", fg="#333",
        font=FONT_B, relief="flat", padx=16, pady=6, cursor="hand2",
    ).pack(side="right", padx=(8, 0))
    _primary_button(row, "确认修改", submit).pack(side="right")
    dialog.bind("<Return>", submit)
    dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
    _center(dialog, 460, 440)
    current_entry.focus_set()
    parent.wait_window(dialog)
    return result["changed"]
