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
datas += datas_for("assets/houdini-agent.ico", "assets")
datas += datas_for("houdini_agent", "bridge_payload/houdini_agent")
# bridge_payload 是 Houdini 侧 bridge server 的加载根（HAGENT_REPO 指向它）。
# meshy 依赖顶层 shared 包，必须一并放进去，否则 Houdini 里 import meshy 会 No module named 'shared'。
datas += datas_for("shared", "bridge_payload/shared")
datas += datas_for("houdini_agent/ui_qml/qml", "houdini_agent/ui_qml/qml")
datas += datas_for("houdini_agent/ui_qml/fonts", "houdini_agent/ui_qml/fonts")
datas += datas_for("houdini_agent/houdini_package", "houdini_agent/houdini_package")
datas += datas_for("houdini_agent/utils/mcp/node_inputs.json", "houdini_agent/utils/mcp")
# meshy 动作库目录：app 侧由冻结模块按 __file__ 相对路径加载，必须放到顶层 meshy 包路径下
# （bridge_payload 那份只供 Houdini 侧；此处与 node_inputs.json 同理）。
datas += datas_for("houdini_agent/meshy/animation_library.json", "houdini_agent/meshy")
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

# QML 聊天界面只用到 QtQuick / QtQuick.Controls(.Basic) / QtQuick.Layouts / QtQuick.Dialogs，
# 其余 Qt 模块（WebEngine ~200MB、Multimedia/FFmpeg、Pdf、Quick3D、Designer 等）运行时用不到。
# 把它们排除掉给安装包大幅瘦身（保留 opengl32sw 软件渲染兜底与全部 Controls 样式，避免误伤）。
qt_excludes = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtWebView", "PySide6.QtWebSockets",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtSpatialAudio",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtQuick3D", "PySide6.Qt3DCore", "PySide6.Qt3DRender",
    "PySide6.Qt3DInput", "PySide6.Qt3DLogic", "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
    "PySide6.QtDesigner", "PySide6.QtUiTools", "PySide6.QtTest", "PySide6.QtQuickTest",
    "PySide6.QtSensors", "PySide6.QtPositioning", "PySide6.QtNfc", "PySide6.QtBluetooth",
    "PySide6.QtSerialPort", "PySide6.QtSerialBus", "PySide6.QtRemoteObjects",
    "PySide6.QtScxml", "PySide6.QtTextToSpeech", "PySide6.QtHelp",
]
hiddenimports = [h for h in hiddenimports if h not in qt_excludes]


a = Analysis(
    ["launcher.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PySide2", "hou", "hdefereval"] + qt_excludes,
    noarchive=False,
    optimize=0,
)

# 二次兜底：从最终产物里物理剔除未用 Qt 模块的 DLL / 资源 / qml / 语言包。
# （collect_data_files 的 Qt/qml|plugins|translations 通配会把所有模块拉进来，这里按名字过滤掉。）
_strip_substr = (
    "webengine", "qtwebengine",
    "qt6multimedia", "multimediawidgets", "spatialaudio",
    "avcodec-", "avformat-", "avutil-", "swscale-", "swresample-",   # FFmpeg
    "qt6pdf", "pdfquick", "pdfwidgets",
    "quick3d", "qt63d", "qt3d",
    "qt6charts", "chartsqml", "datavisualization", "qt6graphs",
    "qt6designer", "qt6quicktest", "qt6test",
    "qt6sensors", "qt6positioning", "qt6nfc", "qt6bluetooth",
    "qt6serialport", "qt6serialbus", "qt6webview", "qt6webchannel",
    "qt6websockets", "qt6remoteobjects", "qt6scxml", "qt6texttospeech",
    "virtualkeyboard", "qt6help",
)


def _keep(dest):
    d = str(dest).lower().replace("\\", "/")
    return not any(s in d for s in _strip_substr)


a.binaries = [b for b in a.binaries if _keep(b[0])]
a.datas = [d for d in a.datas if _keep(d[0])]

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
    icon=str(ROOT / "assets" / "houdini-agent.ico"),
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
