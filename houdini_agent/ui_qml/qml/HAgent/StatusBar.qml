import QtQuick
import HAgent

// Minimal Editorial run-status line (thinking / generating / tool / planning).
Item {
    id: sbar
    property string phase: controller ? controller.statusPhase : ""
    property bool active: phase.length > 0
    visible: active
    implicitHeight: active ? Math.round(20 * Theme.scale) : 0

    function label() {
        if (phase === "thinking") return "Thinking…"
        if (phase === "generating") return "Generating…"
        if (phase === "planning") return "Planning…"
        if (phase.indexOf("tool:") === 0) return phase.substring(5) + " …"
        return ""
    }

    Row {
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        spacing: 8
        Rectangle {
            width: 6; height: 6; radius: 3; color: Theme.accent
            anchors.verticalCenter: parent.verticalCenter
            SequentialAnimation on opacity {
                running: sbar.active; loops: Animation.Infinite
                NumberAnimation { to: 0.3; duration: 600 }
                NumberAnimation { to: 1.0; duration: 600 }
            }
        }
        Text {
            anchors.verticalCenter: parent.verticalCenter
            text: sbar.label()
            color: Theme.textDim
            font.family: Theme.fontMono; font.pixelSize: Theme.fXs
        }
    }
}
