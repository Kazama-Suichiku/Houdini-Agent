# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


block_cipher = None
ROOT = Path(SPECPATH).resolve()


def datas_for(path, dest=None):
    src = ROOT / path
    if not src.exists():
        return []
    return [(str(src), dest or path.replace("\\", "/"))]


datas = []
datas += datas_for("lib", "lib")
datas += datas_for("config", "config")
datas += datas_for("rules", "rules")
datas += datas_for("plugins", "plugins")
datas += datas_for("trainData", "trainData")
datas += datas_for("VERSION", ".")
datas += datas_for("houdini_agent", "bridge_payload/houdini_agent")
datas += datas_for("houdini_agent/ui_qml/qml", "houdini_agent/ui_qml/qml")
datas += datas_for("houdini_agent/ui_qml/fonts", "houdini_agent/ui_qml/fonts")
datas += datas_for("houdini_agent/houdini_package", "houdini_agent/houdini_package")
datas += datas_for("houdini_agent/utils/mcp/node_inputs.json", "houdini_agent/utils/mcp")
datas += collect_data_files("PySide6", includes=["Qt/qml/**", "Qt/plugins/**", "Qt/translations/**"])

hiddenimports = []
hiddenimports += collect_submodules("PySide6")
hiddenimports += collect_submodules("houdini_agent")
hiddenimports += [
    "houdini_agent.ui_qml.external_app",
    "houdini_agent.ui_qml.bridge_session",
    "houdini_agent.bridge.client",
    "houdini_agent.bridge.server",
    "houdini_agent.launcher.houdini_discovery",
    "houdini_agent.utils.mcp",
]


a = Analysis(
    ["launcher.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PySide2", "hou", "hdefereval"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Houdini Agent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Houdini Agent",
)
