# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec: onefile GUI exe with register catalog + customtkinter assets.

from PyInstaller.utils.hooks import collect_all

datas = [
    ("data/registers.json", "data"),
    ("data/bg_names.tsv", "data"),
]
binaries = []
hiddenimports = []

for pkg in ("customtkinter", "pymodbus"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

a = Analysis(
    ["run_app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

import sys

if sys.platform == "darwin":
    # macOS: onedir app bundle (onefile + windowed is unsupported on macOS)
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="SolisModbusConfig",
        debug=False,
        strip=False,
        upx=False,
        console=False,
        argv_emulation=False,
    )
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="SolisModbusConfig")
    app = BUNDLE(
        coll,
        name="SolisModbusConfig.app",
        bundle_identifier="eu.nikibg.solismodbusconfig",
        info_plist={"NSHighResolutionCapable": True},
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="SolisModbusConfig",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=sys.platform == "win32",
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
