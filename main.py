#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py - 语义对齐财务录入系统主程序（模块化架构）
"""

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import os
import sys
import json
import atexit
import threading
import traceback
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List

# PyInstaller运行时资源位于_MEIPASS；开发模式仍使用源码目录。
PROJECT_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)).resolve()
sys.path.insert(0, str(PROJECT_ROOT))

import model_runner as MR
import logger as L

# 导入模块
from modules import batch_import_module
from modules import manual_entry_module
from modules import audit_module
from modules import vocabulary_module
from modules import tax_workbench_module
from modules import basic_accounting_module
from modules import solo_workbench_module
from modules.loading_dialog import ApproxProgressDialog
from finance_store import FinanceDataStore
from legal_notice import LEGAL_NOTICE_SUMMARY
from management_dialogs import (
    company_profile_errors,
    show_archive_manager,
    show_legal_notice,
    show_settings,
)
from auth_manager import (
    CredentialStore,
    show_change_password_dialog,
    show_startup_auth,
)
from text_context_menu import install_text_context_menu


# ── 颜色和字体常量 ──
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


class AppConfig:
    """应用配置"""
    def __init__(self, config_path: Path, profile_key: Optional[str] = None):
        with open(config_path, encoding='utf-8') as f:
            data = json.load(f)

        self.project_root = config_path.parent.resolve()
        self.app_name = data["app"]["name"]
        self.app_version = data["app"].get("version", "1.0.0")
        self.organization = data["app"].get("organization", "")
        self.organization_type = data["app"].get("organizationType", "")

        accounting_cfg = data.get("accounting", {})
        self.profiles = accounting_cfg.get("profiles", {})
        if not self.profiles:
            self.profiles = {
                "enterprise": {
                    "label": "小企业会计",
                    "type": "small_enterprise",
                    "standards": ["小企业会计准则"],
                    "vocabPath": "vocab_library_small_enterprise.json",
                    "semanticCategoriesPath": "semantic_categories_small_enterprise.json",
                    "dataDir": "data/small_enterprise",
                }
            }
        default_profile = accounting_cfg.get("defaultProfile", next(iter(self.profiles)))
        self.profile_key = profile_key or default_profile
        if self.profile_key not in self.profiles:
            raise ValueError(f"未知会计方向：{self.profile_key}")

        profile = self.profiles[self.profile_key]
        self.profile_label = profile.get("label", self.profile_key)
        self.accounting_type = profile.get("type", "")
        self.accounting_standards = list(profile.get("standards", []))
        self.primary_accounting_standard = (
            self.accounting_standards[0] if self.accounting_standards else ""
        )
        self.account_catalog_path = self.project_root / profile.get(
            "accountCatalogPath", "account_catalog_small_enterprise.json"
        )
        self.vocab_path = self.project_root / profile["vocabPath"]
        self.semantic_categories_path = self.project_root / profile["semanticCategoriesPath"]
        configured_data_dir = Path(profile.get("dataDir", f"data/{self.profile_key}"))
        if getattr(sys, "frozen", False):
            writable_root = Path(
                os.environ.get("ACCOUNTINGDEMO_DATA_ROOT", "")
                or Path(os.environ.get("LOCALAPPDATA", Path.home()))
                / "SmallEnterpriseAccounting"
            )
            parts = configured_data_dir.parts
            relative = Path(*parts[1:]) if parts and parts[0].lower() == "data" else configured_data_dir
            self.data_dir = writable_root / "data" / relative
        else:
            self.data_dir = self.project_root / configured_data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.audit_log_path = self.data_dir / "operation_log.json"
        auth_root = os.environ.get("ACCOUNTINGDEMO_AUTH_ROOT", "").strip()
        if auth_root:
            security_root = Path(auth_root).expanduser().resolve()
        elif getattr(sys, "frozen", False):
            security_root = writable_root / "security"
        else:
            security_root = self.project_root / "data" / "security"
        self.auth_path = security_root / "credentials.json"

        # 模型配置
        model_cfg = data["models"]["semantic"]
        self.model_config = MR.ModelConfig(
            name=model_cfg["name"],
            model_path=self._resolve_resource_path(
                model_cfg["modelPath"], "ACCOUNTINGDEMO_MODEL_ROOT", "models"
            ),
            host=model_cfg["host"],
            port=model_cfg["port"],
            context_size=model_cfg.get("contextSize", 4096),
            threads=model_cfg.get("threads", 4),
            batch_size=model_cfg.get("batchSize", 512),
            ubatch_size=model_cfg.get("ubatchSize", 128),
            cache_prompt=model_cfg.get("cachePrompt", True),
            cache_reuse=model_cfg.get("cacheReuse", 0),
            max_tokens=model_cfg.get("maxTokens", 256),
            temperature=model_cfg.get("temperature", 0.0),
            prefer_gpu=model_cfg.get("preferGPU", True),
            gpu_layers=model_cfg.get("gpuLayers", "all"),
            flash_attention=model_cfg.get("flashAttention", "auto"),
            reasoning=model_cfg.get("reasoning", False),
            startup_timeout=model_cfg.get("startupTimeout", 120),
        )

        self.llama_server_path = self._resolve_resource_path(
            data["runtime"]["llamaServerPath"],
            "ACCOUNTINGDEMO_RUNTIME_ROOT",
            "runtime",
        )
        cuda_path = data["runtime"].get("llamaCudaServerPath")
        self.llama_cuda_server_path = (
            self._resolve_resource_path(
                cuda_path, "ACCOUNTINGDEMO_RUNTIME_ROOT", "runtime"
            )
            if cuda_path else None
        )

        # OCR配置
        self.ocr_config = data["models"]["ocr"]
        adapter_path = self.ocr_config.get(
            "adapterPath", "runtime/ocr/bin/rapidocr_adapter.py"
        )
        self.ocr_adapter_path = self._resolve_resource_path(
            adapter_path, "ACCOUNTINGDEMO_RUNTIME_ROOT", "runtime"
        )

    def _resolve_resource_path(self, value: str, env_name: str,
                               relative_root: str) -> Path:
        """Resolve bundled resources without tying the project to a drive letter."""
        raw_path = Path(os.path.expandvars(str(value))).expanduser()
        if raw_path.is_absolute():
            return raw_path.resolve()

        local_path = (self.project_root / raw_path).resolve()
        external_root = os.environ.get(env_name, "").strip()
        if local_path.exists() or not external_root:
            return local_path

        parts = raw_path.parts
        relative = Path(*parts[1:]) if parts and parts[0] == relative_root else raw_path
        return (Path(external_root).expanduser() / relative).resolve()


def make_btn(parent, text, cmd, color=BLUE, width=12):
    """创建标准按钮"""
    return tk.Button(parent, text=text, command=cmd,
                     bg=color, fg=WHITE, font=FONT_B,
                     relief="flat", padx=8, pady=4,
                     activebackground=DARK, activeforeground=WHITE,
                     cursor="hand2", width=width)


class MainWindow(tk.Tk):
    """主窗口"""

    def __init__(self, config: AppConfig, operator: str,
                 credential_store: CredentialStore):
        super().__init__()
        install_text_context_menu(self)
        self.config = config
        self.authenticated_operator = operator
        self.credential_store = credential_store
        self.title(f"{config.app_name} · {config.profile_label} v{config.app_version}")
        self.configure(bg=BG)
        self.geometry("1400x900")
        self.status_var = tk.StringVar(value="正在初始化...")
        self._is_shutting_down = False
        self.finance_store = FinanceDataStore(
            config.data_dir,
            config.profile_key,
            config.profile_label,
            config.primary_accounting_standard,
        )
        self._refresh_company_identity(self.finance_store.get_settings())
        self._startup_safety_error = None
        try:
            self._startup_safety = self.finance_store.startup_safety_check(keep=5)
        except Exception as exc:
            self._startup_safety = {}
            self._startup_safety_error = exc

        # 注册窗口销毁协议
        self.protocol("WM_DELETE_WINDOW", self._on_window_close)

        # 加载词库
        self.vocab_library = vocabulary_module.load_vocab(
            config.vocab_path, config.account_catalog_path
        )
        self.semantic_categories = vocabulary_module.load_semantic_categories(
            config.semantic_categories_path
        )

        # 初始化模型运行器
        self.model_runner = None
        self.semantic_matcher = None
        self._init_model_client()

        # 全局状态
        self.authenticated = True
        # 构建UI
        self._build_ui()

        self._startup_tasks_started = False
        self.after(80, self._begin_startup_flow)

        # 注册atexit退出钩子
        atexit.register(self._cleanup_resources)

    def _begin_startup_flow(self):
        settings = self.finance_store.get_settings()
        if company_profile_errors(settings):
            show_settings(
                self,
                self.finance_store,
                self._on_first_company_profile_saved,
                first_run=True,
                on_cancel=self._cancel_first_company_profile,
            )
            return
        self._continue_startup_flow()

    def _continue_startup_flow(self):
        if self._startup_tasks_started or self._is_shutting_down:
            return
        self._startup_tasks_started = True
        self.after(50, self._show_startup_safety_result)
        # The model is loaded only after mandatory company information is saved.
        self.after(100, self._preload_model_async)

    def _on_first_company_profile_saved(self, settings):
        self._on_settings_saved(settings)
        L.log("企业资料设置", "首次企业名称和统一社会信用代码已保存")
        self._continue_startup_flow()

    def _cancel_first_company_profile(self):
        messagebox.showinfo(
            "需要企业资料",
            "首次进入必须先确认企业名称和统一社会信用代码，本次不会进入账套。",
            parent=self,
        )
        self._cleanup_resources()
        self.destroy()

    def report_callback_exception(self, exc_type, exc_value, exc_traceback):
        """Turn otherwise silent Tk callback crashes into actionable Chinese guidance."""
        detail = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        try:
            crash_log = self.config.data_dir / "crash.log"
            with open(crash_log, "a", encoding="utf-8") as handle:
                handle.write(f"\n[{datetime.now().isoformat(timespec='seconds')}]\n{detail}")
        except OSError:
            crash_log = None
        message = self._friendly_error(exc_value)
        if crash_log:
            message += f"\n\n诊断信息已保存到：\n{crash_log}"
        messagebox.showerror("操作没有完成", message, parent=self)

    @staticmethod
    def _friendly_error(exc: Exception) -> str:
        text = str(exc).strip()
        lower = text.lower()
        if "ocr" in lower or "识别" in text:
            return f"票据识别没有完成。请确认图片清晰、方向正确，再重试；仍失败时可手动录入。\n\n原因：{text}"
        if "model" in lower or "模型" in text or "llama" in lower:
            return f"本地智能模型暂时没有就绪。明确词库规则仍可使用，也可以稍后重试。\n\n原因：{text}"
        if "不平衡" in text or "借贷" in text:
            return f"这张凭证借方和贷方金额不一致，系统没有保存。请核对金额后再试。\n\n原因：{text}"
        if "归档" in text:
            return f"这个月份已经归档，系统已阻止修改历史申报数据。\n\n原因：{text}"
        return f"操作没有完成，账套数据未被强行写入。请按提示检查后重试。\n\n原因：{text or exc.__class__.__name__}"

    def _show_startup_safety_result(self):
        if self._startup_safety_error:
            messagebox.showerror(
                "账套安全检查未完成",
                self._friendly_error(self._startup_safety_error)
                + "\n\n请打开“存档管理”执行完整性检查或恢复备份。",
                parent=self,
            )
            return
        backup = Path(self._startup_safety.get("backup", "")).name
        notice = self._startup_safety.get("recovery_notice", "")
        if backup:
            self._status_update(f"账套完整性检查通过 | 已自动备份：{backup}")
        if notice:
            messagebox.showwarning(
                "账套已自动修复",
                f"{notice}\n\n系统已创建启动备份，请在存档管理中确认。",
                parent=self,
            )

    def _init_model_client(self):
        """Initialize the local model client."""
        self._status_update("正在初始化模型客户端...")
        try:
            self.model_runner = MR.LlamaServerRunner(
                self.config.model_config,
                self.config.llama_server_path,
                self.config.llama_cuda_server_path,
                log_path=self.config.data_dir / "logs" / "llama-server.log",
            )
            self.semantic_matcher = MR.SemanticMatcher(
                self.model_runner,
                self.config.vocab_path,
                self.config.semantic_categories_path,
                self.config.account_catalog_path,
            )
            self._status_update("模型客户端就绪 | 即将预载Qwen3.5 2B")
        except Exception as e:
            self._status_update(f"模型初始化失败: {e}")

    def _preload_model_async(self):
        """Load the model without blocking Tk's UI thread."""
        if not self.model_runner or self._is_shutting_down:
            return

        self._status_update("正在预载Qwen3.5 2B | GPU优先，异常时自动切换CPU...")
        self._startup_loading = ApproxProgressDialog(
            self,
            "正在启动本地智能模型",
            [
                "检查CUDA设备和模型文件",
                "加载Qwen3.5 2B量化模型",
                "分配显存并建立4K上下文",
                "预热模型服务",
            ],
            expected_seconds=2.5,
        )

        def preload():
            ready = False
            try:
                ready = self.model_runner.ensure_ready(
                    timeout=self.config.model_config.startup_timeout
                )
                if ready:
                    backend = self.model_runner.backend_label
                    status = f"模型已常驻 | {backend} | 4K上下文 | 已关闭思考"
                else:
                    detail = self.model_runner.last_error or "服务未在限定时间内就绪"
                    status = f"模型预载失败: {detail}"
            except Exception as exc:
                status = f"模型预载失败: {exc}"

            if not self._is_shutting_down:
                try:
                    self.after(0, lambda: self._finish_model_preload(status, ready))
                except tk.TclError:
                    pass

        threading.Thread(target=preload, name="model-preload", daemon=True).start()

    def _finish_model_preload(self, status: str, ready: bool):
        self._status_update(status)
        dialog = getattr(self, "_startup_loading", None)
        if not dialog:
            return
        if ready:
            dialog.complete("模型加载完成，正在进入系统")
        else:
            detail = status.split(":", 1)[-1].strip() if ":" in status else status
            dialog.fail(
                "模型加载失败，可继续使用明确词库规则",
                callback=lambda: messagebox.showwarning(
                    "本地模型暂未就绪",
                    (
                        "Qwen本地模型没有在限定时间内启动。明确词库规则仍可使用，"
                        "模糊业务请稍后重试。\n\n"
                        f"可能原因：{detail}"
                    ),
                    parent=self,
                ),
            )
        self._startup_loading = None

    def _status_update(self, text: str):
        """更新状态栏"""
        self.status_var.set(text)

    def _build_ui(self):
        """构建主UI"""
        # 标题栏
        title_bar = tk.Frame(self, bg=DARK, height=60)
        title_bar.pack(fill="x")
        tk.Label(title_bar,
                 text=f"  {self.config.app_name}  v{self.config.app_version}",
                 font=FONT_T, bg=DARK, fg=WHITE).pack(side="left", pady=14, padx=14)
        organization = (
            f" · {self.config.organization}" if self.config.organization else ""
        )
        self.organization_label = tk.Label(
            title_bar, text=f"{self.config.profile_label}{organization}",
            font=FONT_S, bg=DARK, fg="#AACCFF",
        )
        self.organization_label.pack(side="right", padx=18)

        # 模式切换栏
        mode_bar = tk.Frame(self, bg=GRAY, height=45)
        mode_bar.pack(fill="x")

        self.b_home = tk.Button(mode_bar, text="  本月工作台  ", font=FONT_B,
                                   bg=BLUE, fg=WHITE, relief="flat", pady=10,
                                   cursor="hand2",
                                   command=lambda: self._switch_module("home"))
        self.b_batch = tk.Button(mode_bar, text="  票据批量导入  ", font=FONT_B,
                                   bg=GRAY, fg="#444", relief="flat", pady=10,
                                   cursor="hand2",
                                   command=lambda: self._switch_module("batch"))
        self.b_manual = tk.Button(mode_bar, text="  完整手工录入  ", font=FONT_B,
                                   bg=GRAY, fg="#444", relief="flat", pady=10,
                                   cursor="hand2",
                                   command=lambda: self._switch_module("manual"))
        self.b_audit = tk.Button(mode_bar, text="  操作日志  ", font=FONT_B,
                                   bg=GRAY, fg="#444", relief="flat", pady=10,
                                   cursor="hand2",
                                   command=lambda: self._switch_module("audit"))
        self.b_vocab = tk.Button(mode_bar, text="  词库管理  ", font=FONT_B,
                                   bg=GRAY, fg="#444", relief="flat", pady=10,
                                   cursor="hand2",
                                   command=lambda: self._switch_module("vocab"))
        self.b_basic = tk.Button(mode_bar, text="  对账与资产  ", font=FONT_B,
                                 bg=GRAY, fg="#444", relief="flat", pady=10,
                                 cursor="hand2",
                                 command=lambda: self._switch_module("basic"))
        self.b_tax = tk.Button(mode_bar, text="  月结与导出  ", font=FONT_B,
                               bg=GRAY, fg="#444", relief="flat", pady=10,
                               cursor="hand2",
                               command=lambda: self._switch_module("tax"))

        self.b_home.pack(side="left", padx=2)
        self.b_batch.pack(side="left", padx=2)
        self.b_manual.pack(side="left", padx=2)
        self.b_audit.pack(side="left", padx=2)
        self.b_vocab.pack(side="left", padx=2)
        self.b_basic.pack(side="left", padx=2)
        self.b_tax.pack(side="left", padx=2)

        # 工具栏
        tool_bar = tk.Frame(self, bg=BG, pady=8)
        tool_bar.pack(fill="x", padx=14)

        make_btn(tool_bar, "系统设置", self._open_settings, width=12).pack(side="left", padx=4)
        make_btn(tool_bar, "存档管理", self._open_save_mgr, width=12).pack(side="left", padx=4)
        make_btn(tool_bar, "操作日志", self._open_log_view, width=12).pack(side="left", padx=4)
        make_btn(tool_bar, "使用说明", self._open_legal_notice, color="#666", width=12).pack(
            side="left", padx=4
        )

        self.account_lbl = tk.Label(
            tool_bar, text=f"已登录 · {self.authenticated_operator}", font=FONT_S,
            bg=BG, fg=GREEN,
        )
        self.account_lbl.pack(side="right", padx=6)
        self.password_btn = tk.Button(
            tool_bar, text="修改密码", font=FONT_S,
            command=self._change_password, bg="#E0E0E0", relief="flat", padx=8,
            cursor="hand2",
        )
        self.password_btn.pack(side="right")

        notice_bar = tk.Frame(self, bg="#FFF4CE", padx=14, pady=7)
        notice_bar.pack(fill="x")
        tk.Label(
            notice_bar, text=LEGAL_NOTICE_SUMMARY, font=FONT_S,
            bg="#FFF4CE", fg="#6B5200", anchor="w", justify="left",
            wraplength=1120,
        ).pack(side="left", fill="x", expand=True)
        tk.Button(
            notice_bar, text="查看责任边界", font=FONT_S,
            command=self._open_legal_notice, bg="#FFF4CE", fg=DARK,
            relief="flat", cursor="hand2", padx=8,
        ).pack(side="right")

        # 主内容区
        self.content_frame = tk.Frame(self, bg=BG)
        self.content_frame.pack(fill="both", expand=True, padx=14, pady=8)

        # 初始化各模块
        self.home_module = solo_workbench_module.SoloWorkbenchModule(
            self.content_frame, self.config, self.semantic_matcher,
            self.finance_store, self._switch_module, self._open_settings,
            self._on_basic_data_changed,
        )
        self.batch_module = batch_import_module.BatchImportModule(
            self.content_frame, self.config, self.semantic_matcher, self.authenticated,
            self.finance_store,
        )
        self.manual_module = manual_entry_module.ManualEntryModule(
            self.content_frame, self.config, self.semantic_matcher, self.authenticated,
            self.finance_store,
        )
        self.audit_module = audit_module.AuditModule(
            self.content_frame, self.config, self.authenticated
        )
        self.vocab_module = vocabulary_module.VocabModule(
            self.content_frame, self.config, self.authenticated, self.semantic_matcher
        )
        self.tax_module = tax_workbench_module.TaxWorkbenchModule(
            self.content_frame, self.config, self.finance_store, self.authenticated
        )
        self.basic_module = basic_accounting_module.BasicAccountingModule(
            self.content_frame, self.config, self.finance_store, self.authenticated,
            self._on_basic_data_changed,
        )

        # 默认显示一人公司月度工作台
        self._switch_module("home")

        # 底部状态栏
        bottom = tk.Frame(self, bg=GRAY)
        bottom.pack(fill="x", side="bottom")
        tk.Label(bottom, textvariable=self.status_var, font=FONT_S,
                 bg=GRAY, anchor="w", padx=14).pack(fill="x", side="left", expand=True)

    def _switch_module(self, module_name: str):
        """切换模块"""
        # 隐藏所有模块
        self.home_module.pack_forget()
        self.batch_module.pack_forget()
        self.manual_module.pack_forget()
        self.audit_module.pack_forget()
        self.vocab_module.pack_forget()
        self.basic_module.pack_forget()
        self.tax_module.pack_forget()

        # 更新按钮状态
        modules = {
            "home": self.b_home,
            "batch": self.b_batch,
            "manual": self.b_manual,
            "audit": self.b_audit,
            "vocab": self.b_vocab,
            "basic": self.b_basic,
            "tax": self.b_tax,
        }

        for name, btn in modules.items():
            if name == module_name:
                btn.configure(bg=BLUE, fg=WHITE)
            else:
                btn.configure(bg=GRAY, fg="#444")

        # 显示选中模块
        if module_name == "home":
            self.home_module.pack(fill="both", expand=True)
            self._status_update("本月工作台 | 一句话记账与关账进度")
        elif module_name == "batch":
            self.batch_module.pack(fill="both", expand=True)
            self._status_update("批量导入模式 | 支持多文件批量识别")
        elif module_name == "manual":
            self.manual_module.pack(fill="both", expand=True)
            self._status_update("手工入账模式 | 智能科目匹配")
        elif module_name == "audit":
            if hasattr(self.audit_module, "_refresh"):
                self.audit_module._refresh(show_message=False)
            self.audit_module.pack(fill="both", expand=True)
            self._status_update(
                f"只读操作日志 | 当前账户：{self.authenticated_operator}"
            )
        elif module_name == "vocab":
            self.vocab_module.pack(fill="both", expand=True)
            self._status_update("词库管理 | 查看和编辑会计科目")
        elif module_name == "basic":
            self.basic_module.pack(fill="both", expand=True)
            self._status_update("基础账务 | 期初余额、银行对账、工资社保与固定资产")
        elif module_name == "tax":
            self.tax_module.pack(fill="both", expand=True)
            self._status_update("财税工作台 | 期间复核、税费测算与Excel导出")

    def _change_password(self):
        changed = show_change_password_dialog(
            self,
            self.credential_store,
            self.authenticated_operator,
        )
        if changed:
            L.log("密码变更", "当前登录账号已修改密码")
            self._status_update("登录密码已更新")

    def _open_settings(self):
        """打开设置对话框"""
        show_settings(self, self.finance_store, self._on_settings_saved)

    def _open_legal_notice(self):
        """Show the product positioning, policy snapshot, and responsibility boundary."""
        show_legal_notice(self, self.finance_store.get_settings())

    def _open_save_mgr(self):
        """打开存档管理对话框"""
        show_archive_manager(self, self.finance_store, self._reload_persisted_views)

    def _on_settings_saved(self, settings):
        self._refresh_company_identity(settings)
        self.tax_module.refresh()
        self.home_module.refresh()
        L.log("系统设置", "企业资料及账套设置已保存")
        self._status_update("系统设置已保存并同步到当前账套")

    def _refresh_company_identity(self, settings=None):
        settings = settings or self.finance_store.get_settings()
        company_name = str(
            settings.get("company", {}).get("name", "")
        ).strip()
        self.config.organization = company_name
        identity = company_name or self.config.profile_label
        self.title(
            f"{self.config.app_name} · {identity} v{self.config.app_version}"
        )
        if hasattr(self, "organization_label"):
            suffix = f" · {company_name}" if company_name else ""
            self.organization_label.configure(
                text=f"{self.config.profile_label}{suffix}"
            )

    def _on_basic_data_changed(self):
        if hasattr(self.manual_module, "reload_from_store"):
            self.manual_module.reload_from_store()
        if hasattr(self.batch_module, "reload_from_store"):
            self.batch_module.reload_from_store()
        self.tax_module.refresh()
        self.home_module.refresh()

    def _reload_persisted_views(self):
        if hasattr(self.manual_module, "reload_from_store"):
            self.manual_module.reload_from_store()
        if hasattr(self.batch_module, "reload_from_store"):
            self.batch_module.reload_from_store()
        self.basic_module.reload_from_store()
        self.tax_module.refresh()
        self.home_module.refresh()
        self._status_update("账套数据已恢复并重新载入")

    def _open_log_view(self):
        """Open the in-app read-only log viewer."""
        self._switch_module("audit")

    def _on_window_close(self):
        """窗口关闭事件处理"""
        if self._is_shutting_down:
            return

        # 询问用户确认
        if messagebox.askyesno("确认退出", "确定要退出系统吗？\n\n退出后将关闭所有服务和清理资源。"):
            self._cleanup_resources()
            self.destroy()

    def _cleanup_resources(self):
        """清理资源"""
        if self._is_shutting_down:
            return

        self._is_shutting_down = True
        print("正在清理资源...")

        # 停止模型服务
        if self.model_runner:
            try:
                print("停止模型服务...")
                self.model_runner.stop_server()
            except Exception as e:
                print(f"停止模型服务时出错: {e}")

        # 保存审计日志
        try:
            L.log("系统退出", "用户关闭应用程序")
            print("审计日志已保存")
        except Exception as e:
            print(f"保存审计日志时出错: {e}")

        try:
            self.finance_store.close()
        except Exception as e:
            print(f"关闭账套数据库时出错: {e}")

        print("资源清理完成")

    def __del__(self):
        """析构函数"""
        try:
            self._cleanup_resources()
        except:
            pass


def select_accounting_profile(config_path: Path) -> str:
    """Ask which isolated accounting profile should be opened."""
    with open(config_path, encoding="utf-8") as f:
        data = json.load(f)
    accounting = data.get("accounting", {})
    profiles = accounting.get("profiles", {})
    default_profile = accounting.get("defaultProfile", next(iter(profiles), "enterprise"))
    if len(profiles) <= 1:
        return default_profile

    selector = tk.Tk()
    selector.title("选择会计方向")
    selector.configure(bg=BG)
    selector.resizable(False, False)
    selected = tk.StringVar(value=default_profile)

    tk.Label(selector, text="选择本次打开的账套方向", font=FONT_T,
             bg=BG, fg=DARK).pack(anchor="w", padx=24, pady=(20, 10))
    for key, profile in profiles.items():
        standards = "、".join(profile.get("standards", []))
        row = tk.Frame(selector, bg=WHITE, relief="solid", bd=1)
        row.pack(fill="x", padx=24, pady=5)
        tk.Radiobutton(
            row,
            text=profile.get("label", key),
            variable=selected,
            value=key,
            font=FONT_B,
            bg=WHITE,
            activebackground=WHITE,
        ).pack(anchor="w", padx=12, pady=(9, 2))
        tk.Label(row, text=standards, font=FONT_S, bg=WHITE, fg="#666").pack(
            anchor="w", padx=34, pady=(0, 9)
        )

    def confirm():
        selector.quit()

    make_btn(selector, "打开账套", confirm, color=GREEN, width=14).pack(pady=(12, 20))
    selector.protocol("WM_DELETE_WINDOW", confirm)
    selector.update_idletasks()
    x = (selector.winfo_screenwidth() - selector.winfo_reqwidth()) // 2
    y = (selector.winfo_screenheight() - selector.winfo_reqheight()) // 2
    selector.geometry(f"+{x}+{y}")
    selector.mainloop()
    result = selected.get()
    selector.destroy()
    return result


def main():
    """主函数"""
    config_path = PROJECT_ROOT / "config.json"
    bootstrap_config = AppConfig(config_path)
    credential_store = CredentialStore(bootstrap_config.auth_path)
    session = show_startup_auth(
        bootstrap_config.app_name,
        credential_store,
        PROJECT_ROOT / "assets" / "finance-app-icon.ico",
    )
    if not session:
        return
    profile_key = select_accounting_profile(config_path)
    config = AppConfig(config_path, profile_key)
    L.configure(config.audit_log_path, session.username)
    L.log("账户登录", "登录成功")

    app = MainWindow(config, session.username, credential_store)
    app.mainloop()


def run_packaged_smoke_test() -> int:
    """Verify bundled resources and writable SQLite storage without opening Tk."""
    config = AppConfig(PROJECT_ROOT / "config.json")
    store = FinanceDataStore(
        config.data_dir,
        config.profile_key,
        config.profile_label,
        config.primary_accounting_standard,
    )
    try:
        required = {
            "model": config.model_config.model_path,
            "llama_cpu": config.llama_server_path,
            "llama_cuda": config.llama_cuda_server_path,
            "ocr_adapter": config.ocr_adapter_path,
            "ocr_python": PROJECT_ROOT / "runtime" / "python" / "python.exe",
        }
        missing = [name for name, path in required.items() if not path or not Path(path).exists()]
        integrity = store.integrity_check()
        result = {
            "ok": not missing and integrity["ok"],
            "version": config.app_version,
            "missing": missing,
            "journal_mode": integrity["journal_mode"],
            "data_dir": str(config.data_dir),
        }
        output = os.environ.get("ACCOUNTINGDEMO_SMOKE_OUTPUT", "").strip()
        if output:
            target = Path(output)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0 if result["ok"] else 2
    finally:
        store.close()


def run_full_cycle_test() -> int:
    """Run the isolated full-year, all-account acceptance suite without Tk."""
    from lifecycle_acceptance import run_full_cycle_acceptance, write_acceptance_report

    config_data = json.loads((PROJECT_ROOT / "config.json").read_text(encoding="utf-8"))
    root = Path(
        os.environ.get("ACCOUNTINGDEMO_FULL_CYCLE_ROOT", "")
        or Path(os.environ.get("LOCALAPPDATA", Path.home()))
        / "SmallEnterpriseAccountingAcceptance"
    )
    output = Path(
        os.environ.get("ACCOUNTINGDEMO_FULL_CYCLE_OUTPUT", "")
        or root / "latest-report.json"
    )
    try:
        report = run_full_cycle_acceptance(
            root,
            version=str(config_data.get("app", {}).get("version", "dev")),
            catalog_path=PROJECT_ROOT / "account_catalog_small_enterprise.json",
        )
        write_acceptance_report(output, report)
        return 0
    except Exception as exc:
        write_acceptance_report(output, {
            "ok": False,
            "version": str(config_data.get("app", {}).get("version", "dev")),
            "error": str(exc),
            "traceback": traceback.format_exc(),
        })
        return 3


if __name__ == "__main__":
    if "--smoke-test" in sys.argv:
        raise SystemExit(run_packaged_smoke_test())
    if "--full-cycle-test" in sys.argv:
        raise SystemExit(run_full_cycle_test())
    try:
        main()
    except Exception as exc:
        crash_root = tk.Tk()
        crash_root.withdraw()
        crash_dir = Path(
            os.environ.get("ACCOUNTINGDEMO_DATA_ROOT", "")
            or Path(os.environ.get("LOCALAPPDATA", Path.home()))
            / "SmallEnterpriseAccounting"
        )
        crash_dir.mkdir(parents=True, exist_ok=True)
        crash_log = crash_dir / "startup-crash.log"
        with open(crash_log, "a", encoding="utf-8") as handle:
            handle.write(
                f"\n[{datetime.now().isoformat(timespec='seconds')}]\n"
                + traceback.format_exc()
            )
        messagebox.showerror(
            "小企业会计启动失败",
            "程序没有进入账套，也没有修改会计数据。\n\n"
            f"原因：{exc}\n\n诊断信息：{crash_log}",
            parent=crash_root,
        )
        crash_root.destroy()
