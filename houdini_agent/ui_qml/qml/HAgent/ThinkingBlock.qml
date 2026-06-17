import QtQuick
import HAgent

// Collapsible reasoning block.
Item {
    id: tb
    property var block: ({})
    property bool collapsed: false
    implicitHeight: box.height

    Rectangle {
        id: box
        width: parent.width
        height: inner.implicitHeight
        color: "transparent"
        border.color: Theme.border
        border.width: 1
        radius: Theme.radSm

        Column {
            id: inner
            width: parent.width

            Item {
                id: head
                width: parent.width
                height: Math.round(38 * Theme.scale)
                Row {
                    anchors.left: parent.left; anchors.leftMargin: 13
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 8
                    Text { text: "✦"; color: Theme.accent; font.pixelSize: Theme.fSm; anchors.verticalCenter: parent.verticalCenter }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: "THOUGHT FOR " + (tb.block.dur || "")
                        color: Theme.textDim
                        font.family: Theme.fontMono
                        font.pixelSize: Theme.fMicro
                        font.letterSpacing: Theme.trackLabel
                    }
                }
                Text {
                    anchors.right: parent.right; anchors.rightMargin: 13
                    anchors.verticalCenter: parent.verticalCenter
                    text: tb.collapsed ? "▸" : "▾"
                    color: Theme.textMute; font.pixelSize: Theme.fMicro
                }
                MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: tb.collapsed = !tb.collapsed }
            }

            Rectangle { visible: !tb.collapsed; width: parent.width; height: 1; color: Theme.border }

            Text {
                visible: !tb.collapsed
                width: parent.width
                leftPadding: 13; rightPadding: 13; topPadding: 11; bottomPadding: 12
                text: tb.block.text || ""
                wrapMode: Text.Wrap
                color: Theme.textMute
                font.family: Theme.fontBody
                font.pixelSize: Theme.fSm
                lineHeight: 1.55
            }
        }
    }
}
