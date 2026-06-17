import QtQuick
import QtQuick.Layouts
import HAgent

// Houdini network path + selection indicator.
Rectangle {
    id: ctx
    color: "transparent"
    implicitHeight: Math.round(30 * Theme.scale)

    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.border }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 16; anchors.rightMargin: 14
        spacing: 8

        Text {
            text: controller ? controller.scenePath : "/obj/geo1"
            color: Theme.accent
            font.family: Theme.fontMono
            font.pixelSize: Theme.fXs
        }
        Text { text: "·"; color: Theme.textMute; font.pixelSize: Theme.fXs }
        Text {
            Layout.fillWidth: true
            text: controller ? controller.sceneSelection : "2 nodes selected"
            color: Theme.textMute
            font.family: Theme.fontBody
            font.pixelSize: Theme.fXs
            elide: Text.ElideRight
        }
        Text {
            text: "↻"
            color: refreshMa.containsMouse ? Theme.text : Theme.textMute
            font.pixelSize: Theme.fSm
            MouseArea {
                id: refreshMa
                anchors.fill: parent; anchors.margins: -6
                hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                onClicked: if (controller) controller.refreshContext()
            }
        }
    }
}
