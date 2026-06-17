import QtQuick
import HAgent

// Todo checklist (add_todo / update_todo).
Item {
    id: tb
    property var block: ({})
    property var items: (block && block.items) ? block.items : []
    implicitHeight: box.height

    Rectangle {
        id: box
        width: parent.width
        height: col.implicitHeight + 18
        color: "transparent"
        border.color: Theme.border
        border.width: 1
        radius: Theme.radSm

        Column {
            id: col
            anchors.left: parent.left; anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            anchors.leftMargin: 13; anchors.rightMargin: 13
            spacing: 7

            Text {
                text: "TODO"
                color: Theme.textDim
                font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                font.letterSpacing: Theme.trackLabel
            }
            Repeater {
                model: tb.items
                delegate: Row {
                    required property var modelData
                    width: col.width
                    spacing: 9
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        width: 14
                        text: modelData.status === "done" ? "✓"
                            : modelData.status === "in_progress" ? "◐"
                            : modelData.status === "error" ? "✗" : "○"
                        color: modelData.status === "done" ? Theme.ok
                             : modelData.status === "in_progress" ? Theme.accent
                             : modelData.status === "error" ? Theme.err : Theme.textMute
                        font.family: Theme.fontMono; font.pixelSize: Theme.fSm
                    }
                    Text {
                        width: parent.width - 23
                        text: modelData.text || ""
                        color: modelData.status === "done" ? Theme.textMute : Theme.text
                        font.family: Theme.fontBody; font.pixelSize: Theme.fSm
                        wrapMode: Text.Wrap
                    }
                }
            }
        }
    }
}
