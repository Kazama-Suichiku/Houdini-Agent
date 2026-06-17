import QtQuick
import HAgent

// Live VEX/code preview while the tool's arguments stream in.
Item {
    id: cp
    property var block: ({})
    implicitHeight: box.height

    Rectangle {
        id: box
        width: parent.width
        height: inner.implicitHeight
        color: Theme.codeBg
        border.color: Theme.accentLine
        border.width: 1
        radius: Theme.radSm

        Column {
            id: inner
            width: parent.width
            Item {
                width: parent.width
                height: Math.round(28 * Theme.scale)
                Text {
                    anchors.left: parent.left; anchors.leftMargin: 12
                    anchors.verticalCenter: parent.verticalCenter
                    text: "WRITING…"
                    color: Theme.accent
                    font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                    font.letterSpacing: Theme.trackLabel
                    SequentialAnimation on opacity {
                        running: true; loops: Animation.Infinite
                        NumberAnimation { to: 0.4; duration: 600 }
                        NumberAnimation { to: 1.0; duration: 600 }
                    }
                }
            }
            Rectangle { width: parent.width; height: 1; color: Theme.border }
            Text {
                width: parent.width
                leftPadding: 13; rightPadding: 13; topPadding: 10; bottomPadding: 11
                text: (cp.block ? cp.block.code : "") || ""
                color: Theme.text
                font.family: Theme.fontMono; font.pixelSize: Theme.fXs
                wrapMode: Text.NoWrap
            }
        }
    }
}
