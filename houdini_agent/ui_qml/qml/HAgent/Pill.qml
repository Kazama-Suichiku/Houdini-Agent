import QtQuick
import QtQuick.Layouts
import HAgent

// Generic compact button: chip / toggle / tab base (Editorial = square).
Item {
    id: pill
    property string label: ""
    property bool active: false
    property bool caret: false
    property bool accent: false
    property bool dashed: false
    property bool uppercase: false
    signal clicked()

    implicitHeight: Math.round(28 * Theme.scale)
    implicitWidth: row.implicitWidth + 22

    Rectangle {
        id: bg
        anchors.fill: parent
        radius: Theme.radSm
        color: pill.active ? Theme.accentSoft : (ma.containsMouse ? Theme.surface : "transparent")
        border.width: 1
        border.color: pill.active ? Theme.accentLine
                     : pill.dashed ? Theme.border
                     : (ma.containsMouse ? Theme.border : "transparent")
        Behavior on color { ColorAnimation { duration: 120 } }
    }

    RowLayout {
        id: row
        anchors.centerIn: parent
        spacing: 5
        Text {
            text: pill.uppercase ? pill.label.toUpperCase() : pill.label
            color: pill.active ? Theme.accent : (pill.accent ? Theme.accent : Theme.textDim)
            font.family: Theme.fontBody
            font.pixelSize: Theme.fSm
            font.letterSpacing: pill.uppercase ? Theme.trackLabel : 0
        }
        Text {
            visible: pill.caret
            text: "▾"
            color: Theme.textMute
            font.pixelSize: Theme.fMicro
        }
    }

    MouseArea {
        id: ma
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: pill.clicked()
    }
}
