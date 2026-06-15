# -*- coding: utf-8 -*-
"""Standalone Houdini Agent UI entrypoint."""
from __future__ import annotations

import argparse
import sys

from houdini_agent import configure_text_output


def _apply_standalone_dark_palette(QtWidgets, QtGui):
    app = QtWidgets.QApplication.instance()
    if app is None:
        return
    system_font = QtGui.QFont(app.font())
    try:
        app.setStyle("Fusion")
    except Exception:
        pass
    try:
        app.setFont(system_font)
    except Exception:
        pass
    palette = QtGui.QPalette()
    palette.setColor(QtGui.QPalette.Window, QtGui.QColor("#0a0a12"))
    palette.setColor(QtGui.QPalette.WindowText, QtGui.QColor("#e2e8f0"))
    palette.setColor(QtGui.QPalette.Base, QtGui.QColor("#0d0f1a"))
    palette.setColor(QtGui.QPalette.AlternateBase, QtGui.QColor("#16182a"))
    palette.setColor(QtGui.QPalette.ToolTipBase, QtGui.QColor("#101220"))
    palette.setColor(QtGui.QPalette.ToolTipText, QtGui.QColor("#e2e8f0"))
    palette.setColor(QtGui.QPalette.Text, QtGui.QColor("#e2e8f0"))
    palette.setColor(QtGui.QPalette.Button, QtGui.QColor("#16182a"))
    palette.setColor(QtGui.QPalette.ButtonText, QtGui.QColor("#e2e8f0"))
    palette.setColor(QtGui.QPalette.Highlight, QtGui.QColor("#3b82f6"))
    palette.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor("#ffffff"))
    try:
        app.setPalette(palette)
    except Exception:
        pass


def main(argv=None) -> int:
    configure_text_output()
    parser = argparse.ArgumentParser(description="Launch Houdini Agent standalone UI")
    parser.add_argument("--bridge-url", required=True, help="Local Houdini bridge URL")
    args = parser.parse_args(argv)

    try:
        from houdini_agent.qt_compat import QtWidgets, QtGui
    except Exception as e:
        print(f"[Houdini Agent] PySide is not available for standalone UI: {e}", file=sys.stderr)
        return 2

    from houdini_agent.core.main_window import MainWindow
    from houdini_agent.ui.theme_engine import ThemeEngine
    from houdini_agent.utils.bridge import RemoteHoudiniMCPClient

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])
    _apply_standalone_dark_palette(QtWidgets, QtGui)
    ThemeEngine.install_application_scaling(
        app,
        owned_only=False,
        scale_application_font=True,
    )
    client = RemoteHoudiniMCPClient(args.bridge_url)
    window = MainWindow(parent=None, embedded=False, mcp_client=client, bridge_url=args.bridge_url)
    window.show()
    window.raise_()
    window.activateWindow()
    return app.exec_() if hasattr(app, "exec_") else app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
