# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path


project_root = Path(SPECPATH).resolve()
python312_root = Path(os.environ["ACCOUNTINGDEMO_PYTHON312_ROOT"]).resolve()
if not (python312_root / "python.exe").exists():
    raise SystemExit(f"Embedded Python 3.12 not found: {python312_root}")

datas = [
    (str(project_root / "config.json"), "."),
    (str(project_root / "account_catalog_small_enterprise.json"), "."),
    (str(project_root / "vocab_library_small_enterprise.json"), "."),
    (str(project_root / "semantic_categories_small_enterprise.json"), "."),
    (str(project_root / "LICENSE"), "."),
    (str(project_root / "LICENSE_SUMMARY_EN.md"), "."),
    (str(project_root / "NOTICE"), "."),
    (str(project_root / "PATENTS.md"), "."),
    (str(project_root / "THIRD_PARTY_NOTICES.md"), "."),
    (str(project_root / "README.md"), "."),
    (str(project_root / "assets"), "assets"),
    (str(project_root / "models"), "models"),
    (str(project_root / "runtime"), "runtime"),
    (str(python312_root), "runtime/python"),
]

a = Analysis(
    [str(project_root / "main.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=["openpyxl", "requests", "sqlite3", "tkinter"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "IPython", "matplotlib", "notebook"],
    noarchive=False,
    optimize=1,
)

bundled_runtime_roots = (project_root / "runtime",)


def is_flat_runtime_duplicate(entry):
    destination, source, _typecode = entry
    if Path(destination).parent != Path("."):
        return False
    source_path = Path(source).resolve()
    for root in bundled_runtime_roots:
        try:
            source_path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


# Runtime DLLs are already preserved under runtime/. PyInstaller's dependency
# scan also flattens copies beside the GUI executable unless they are removed.
a.binaries[:] = [entry for entry in a.binaries if not is_flat_runtime_duplicate(entry)]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="小企业会计智能记账报税工具",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=True,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(project_root / "assets" / "finance-app-icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="SmallEnterpriseAccounting",
)
