# -*- coding: utf-8 -*-
"""
Standalone preview / self-test for the QML UI (no Houdini needed).

Run a live window:
    cd <repo root>
    python -m houdini_agent.ui_qml.preview

Headless validation (loads QML offscreen, reports errors, exits non-zero on failure):
    python -m houdini_agent.ui_qml.preview --selftest
"""

import sys
import os

# ---- syntax-highlight helpers (Editorial palette) ----
C = dict(com="#5e5b54", kw="#e8e2d4", fn="#c9b896", attr="#d4a373", num="#a3b18a", str="#bc9b7a")
def _s(color, t): return '<span style="color:%s">%s</span>' % (color, t)
def _code_lines(lines): return "<br>".join(lines)

VEX1 = _code_lines([
    _s(C["com"], "// 抬升曲面，制造山脉起伏"),
    _s(C["kw"], "float") + " amp  = " + _s(C["num"], "2.5") + ";",
    _s(C["kw"], "float") + " freq = " + _s(C["num"], "0.35") + ";",
    _s(C["kw"], "vector") + " p = " + _s(C["attr"], "@P") + ";",
    _s(C["kw"], "float") + " n = " + _s(C["fn"], "noise") + "(p * freq);",
    _s(C["attr"], "@P") + ".y += n * amp;",
    _s(C["attr"], "@Cd") + "  = " + _s(C["fn"], "set") + "(n, n*" + _s(C["num"], "0.6") + ", " + _s(C["num"], "1.0") + "-n);",
])
VEX2 = _code_lines([
    _s(C["com"], "// 每个实例随机大小"),
    _s(C["attr"], "@pscale") + " = " + _s(C["fn"], "fit01") + "(" + _s(C["fn"], "rand") + "(" + _s(C["attr"], "@ptnum") + "), " + _s(C["num"], "0.4") + ", " + _s(C["num"], "1.2") + ");",
])
_mono = 'font-family:monospace;color:#d4a373'


def mock_conversation():
    return [
        {"type": "user", "payload": {"text": "参考这两张图，给 grid 加 noise 让表面起伏，再 scatter 5000 个点。",
            "images": ["file:///C:/Users/Administrator/Desktop/Houdini-Agent/website/assets/ui-main.png",
                       "file:///C:/Users/Administrator/Desktop/Houdini-Agent/design-preview/qml_editorial_reference.png"]}},
        {"type": "ai", "payload": {"blocks": [
            {"kind": "thinking", "dur": "4.2s",
             "text": "在 grid 上叠加 noise 有两条路：mountain SOP 最快，或用 attribwrangle 写 @P.y 拿到完全控制。后者更利于后续调参。scatter 默认沿曲面均匀采样，5000 点足够。法线对齐用 polyframe 或直接继承曲面 N。"},
            {"kind": "exec", "label": "Completed · 4 tools · 1.0s", "tools": [
                {"state": "ok", "name": "create_node", "arg": "grid1", "time": "0.3s", "detail": "parent /obj/geo1 · type grid · rows 200 · cols 200 → /obj/geo1/grid1 ✓"},
                {"state": "ok", "name": "create_wrangle_node", "arg": "mountain_vex", "time": "0.5s", "detail": "run_over Points · VEX 6 lines · input grid1 → /obj/geo1/mountain_vex ✓"},
                {"state": "ok", "name": "create_node", "arg": "scatter1", "time": "0.2s", "detail": "type scatter · input mountain_vex → /obj/geo1/scatter1 ✓"},
                {"state": "ok", "name": "set_parameter", "arg": "npts = 5000", "time": "0.1s", "detail": "node scatter1 · npts 1000 → 5000 · seed 0"},
            ]},
            {"kind": "nodeop", "badge": "+3", "text": "nodes created", "paths": "/obj/geo1/grid1 · mountain_vex · scatter1"},
            {"kind": "code", "lang": "VEX", "html": VEX1},
            {"kind": "prose", "html": "已搭好 <b>grid → mountain_vex → scatter</b> 链路。noise 通过 VEX 抬升 <span style='%s'>@P.y</span>，scatter 输出 5000 点并继承曲面法线。需要我把点再 <span style='%s'>copy</span> 上实例几何吗？" % (_mono, _mono)},
        ]}},
        {"type": "plan", "payload": {
            "title": "Terrain Scatter Setup", "badge": "5 steps",
            "steps": [
                {"label": "Create grid (200 × 200)", "state": "done", "detail": "grid1 · size 10 × 10 · rows/cols 200 · orient ZX"},
                {"label": "Displace with noise VEX", "state": "done", "detail": "mountain_vex · run_over Points · amp 2.5 · freq 0.35"},
                {"label": "Scatter 5000 points", "state": "active", "detail": "scatter1 · npts 5000 · relax 25 iters · seed 0"},
                {"label": "Align normals (polyframe)", "state": "pending", "detail": "polyframe1 · N from Tangent+Up · style first_edge"},
                {"label": "Copy instances to points", "state": "pending", "detail": "copytopoints1 · pack 实例几何 · 继承 N / pscale"},
            ],
            "dag": [
                {"name": "grid", "kind": "normal"}, {"name": "noise", "kind": "normal"},
                {"name": "scatter", "kind": "hot"}, {"name": "polyframe", "kind": "ghost"},
            ],
        }},
        {"type": "user", "payload": {"text": "很好，把这些点 copy 上一个 box 实例，pscale 用 noise 随机一点。"}},
        {"type": "ai", "payload": {"blocks": [
            {"kind": "thinking", "dur": "1.8s",
             "text": "用 copytopoints 把 box 实例到 scatter 点；pscale 在 wrangle 里用 rand(@ptnum) 写入，copytopoints 会自动读取 pscale 缩放每个实例。"},
            {"kind": "exec", "label": "Completed · 3 tools · 0.8s", "tools": [
                {"state": "ok", "name": "create_node", "arg": "box1", "time": "0.2s", "detail": "type box · size 0.1 → /obj/geo1/box1 ✓"},
                {"state": "ok", "name": "create_wrangle_node", "arg": "pscale_vex", "time": "0.3s", "detail": "run_over Points · @pscale = fit01(rand(@ptnum), 0.4, 1.2)"},
                {"state": "warn", "name": "cook_node", "arg": "copytopoints1 · 5000 copies", "time": "1.4s", "detail": "warning · cook 较慢（5000 实例未打包）· 建议改用 packed primitive 提升视口性能"},
            ]},
            {"kind": "code", "lang": "VEX", "html": VEX2},
            {"kind": "prose", "html": "已用 <b>copytopoints</b> 把 box 实例到 5000 个点，<span style='%s'>@pscale</span> 由 rand 随机驱动。注意：实例数较多，建议改用 <b>packed primitive</b> 减轻视口压力。" % _mono},
            {"kind": "meshy", "op": "demo1abc", "tool": "meshy_text_to_3d",
             "stage": "生成贴图", "progress": 62, "status": "IN_PROGRESS",
             "done": False, "ok": True, "thumb": "", "summary": "", "backgroundable": True},
            {"kind": "meshy", "op": "demo2", "tool": "meshy_image_to_3d",
             "stage": "完成", "progress": 100, "status": "SUCCEEDED",
             "done": True, "ok": True, "thumb": "",
             "summary": "图生3D完成。\n本地 glb: cache/meshy/abc/model.glb\n消耗 credits: 5\n下一步：调用 import_3d_asset(glb_path=…, texture_dir=…) 导入 Houdini。"},
            {"kind": "concept", "token": "demoGen", "phase": "gen",
             "prompt": "a mossy stone fountain", "count": 3,
             "images": [
                 {"index": 0, "image": "C:/Users/Administrator/Desktop/Houdini-Agent/website/assets/ui-main.png"},
             ],
             "selected": [], "progress": 33, "note": "概念图 1/3 完成"},
            {"kind": "concept", "token": "demo", "phase": "pick",
             "prompt": "a mossy stone fountain, weathered, fantasy", "count": 2,
             "images": [
                 {"index": 0, "image": "C:/Users/Administrator/Desktop/Houdini-Agent/website/assets/ui-main.png",
                  "prompt": "古典欧式多层石头喷泉"},
                 {"index": 1, "image": "C:/Users/Administrator/Desktop/Houdini-Agent/design-preview/qml_editorial_reference.png",
                  "prompt": "日式枯山水石钵喷泉"},
             ],
             "selected": [], "progress": 100, "note": ""},
            {"kind": "concept", "token": "demoDone", "phase": "done", "mode": "concept",
             "prompt": "a mossy stone fountain", "count": 2,
             "images": [
                 {"index": 0, "image": "C:/Users/Administrator/Desktop/Houdini-Agent/website/assets/ui-main.png"},
                 {"index": 1, "image": "C:/Users/Administrator/Desktop/Houdini-Agent/design-preview/qml_editorial_reference.png"},
             ],
             "results": [
                 {"index": 0, "image": "C:/Users/Administrator/Desktop/Houdini-Agent/website/assets/ui-main.png"},
             ],
             "selected": [0], "progress": 100, "note": "生成的 3D 模型 · 1"},
        ]}},
    ]


def main():
    selftest = "--selftest" in sys.argv
    capture_path = None
    if "--capture" in sys.argv:
        i = sys.argv.index("--capture")
        capture_path = sys.argv[i + 1] if i + 1 < len(sys.argv) else "preview.png"
    if selftest or capture_path:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        os.environ.setdefault("QT_QUICK_BACKEND", "software")

    try:
        from PySide6.QtWidgets import QApplication, QMainWindow
        from PySide6.QtCore import QTimer
    except ImportError:
        from PySide2.QtWidgets import QApplication, QMainWindow
        from PySide2.QtCore import QTimer

    from houdini_agent.ui_qml.host import create_view
    from houdini_agent.ui_qml.controller import ChatModel, Controller

    app = QApplication(sys.argv)
    model = ChatModel()
    controller = Controller(model)
    from houdini_agent.ui_qml.controller import wrap_ai_rows
    model.load(wrap_ai_rows(mock_conversation()))

    # Meshy 资产库抽屉的演示数据（仅 live 预览/截图用；selftest 不依赖）
    _ui = "C:/Users/Administrator/Desktop/Houdini-Agent/website/assets/ui-main.png"
    _ref = "C:/Users/Administrator/Desktop/Houdini-Agent/design-preview/qml_editorial_reference.png"
    controller._library_items = [
        {"id": "t1", "kind": "image-to-3d", "status": "SUCCEEDED",
         "prompt": "古典欧式多层石头喷泉，风化质感", "thumbnail": _ui,
         "glb_url": "https://x/y.glb", "local_glb": "", "cached": False,
         "created_at": 1718000000000, "created_label": "2026-06-10",
         "credits": 5, "expired": False, "importable": True,
         "importing": True, "import_stage": "下载中…"},
        {"id": "t2", "kind": "text-to-3d", "status": "SUCCEEDED",
         "prompt": "low-poly stylized pine tree", "thumbnail": _ref,
         "glb_url": "", "local_glb": "C:/cache/meshy/t2/model.glb", "cached": True,
         "created_at": 1717900000000, "created_label": "2026-06-09",
         "credits": 8, "expired": False, "importable": True},
        {"id": "t3", "kind": "retexture", "status": "SUCCEEDED",
         "prompt": "rusted metal barrel, PBR", "thumbnail": _ui,
         "glb_url": "https://x/expired.glb", "local_glb": "", "cached": False,
         "created_at": 1717800000000, "created_label": "2026-05-28",
         "credits": 3, "expired": True, "importable": False},
        {"id": "t4", "kind": "remesh", "status": "SUCCEEDED",
         "prompt": "", "thumbnail": "",
         "glb_url": "https://x/z.glb", "local_glb": "", "cached": False,
         "created_at": 1717700000000, "created_label": "2026-05-20",
         "credits": 2, "expired": False, "importable": True},
    ]
    controller._library_open = True
    # 模拟"已登录"账号状态（仅预览：设置假 key + 余额，不发任何网络请求）
    os.environ.setdefault("MESHY_API_KEY", "msy_demo_key_abcd")
    controller._meshy_balance = 412

    view = create_view(controller=controller, model=model)

    if selftest:
        # QQuickWidget loads synchronously; report errors and exit.
        try:
            from PySide6.QtQuickWidgets import QQuickWidget
        except ImportError:
            from PySide2.QtQuickWidgets import QQuickWidget
        status = view.status()
        if status == QQuickWidget.Error:
            print("[selftest] QML ERRORS:")
            for e in view.errors():
                print("  ", e.toString())
            sys.exit(1)
        print("[selftest] QML loaded OK — root:", view.rootObject() is not None,
              "rows:", model.rowCount())
        # pump one event cycle then quit
        QTimer.singleShot(200, app.quit)
        app.exec()
        sys.exit(0)

    win = QMainWindow()
    win.setWindowTitle("Houdini Agent · Mono Editorial (QML preview)")
    win.setCentralWidget(view)
    # library drawer is open in the mock -> widen so the left column shows
    win.resize(440 + 360 if controller._library_open else 440, 940)
    win.show()

    if capture_path:
        def grab_and_quit():
            pm = view.grab()
            ok = pm.save(capture_path)
            print("[capture]", "saved" if ok else "FAILED", capture_path, pm.size())
            app.quit()
        QTimer.singleShot(900, grab_and_quit)
        app.exec()
        sys.exit(0)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
