import QtQuick
import QtQuick.Controls.Basic
import HAgent

ScrollBar {
    id: bar
    property bool verticalBar: orientation === Qt.Vertical
    property int thickness: Math.max(7, Math.round(7 * Theme.scale))
    property int minLength: Math.max(34, Math.round(34 * Theme.scale))
    policy: ScrollBar.AsNeeded
    interactive: true
    hoverEnabled: true
    minimumSize: 0.06
    padding: 1
    contentItem: Rectangle {
        implicitWidth: bar.verticalBar ? bar.thickness : bar.minLength
        implicitHeight: bar.verticalBar ? bar.minLength : bar.thickness
        radius: Math.min(width, height) / 2
        color: bar.pressed ? Theme.accent : bar.hovered ? Theme.textMute : Theme.border
        opacity: bar.pressed || bar.hovered || bar.active ? 0.95 : 0.48
    }
    background: Rectangle {
        implicitWidth: bar.verticalBar ? bar.thickness + 4 : bar.minLength
        implicitHeight: bar.verticalBar ? bar.minLength : bar.thickness + 4
        radius: Math.min(width, height) / 2
        color: bar.hovered || bar.pressed ? Theme.surface2 : "transparent"
        opacity: 0.72
    }
}
