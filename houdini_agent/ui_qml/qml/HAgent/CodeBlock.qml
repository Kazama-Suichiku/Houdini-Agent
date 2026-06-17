import QtQuick
import HAgent

// Code block with header actions; body is pre-highlighted rich text.
Item {
    id: cb
    property var block: ({})
    property string copyLabel: "Copy"
    implicitHeight: box.height

    Rectangle {
        id: box
        width: parent.width
        height: inner.implicitHeight
        color: Theme.codeBg
        border.color: Theme.border
        border.width: 1
        radius: Theme.radSm

        Column {
            id: inner
            width: parent.width

            Item {
                width: parent.width
                height: Math.round(34 * Theme.scale)
                Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.border }
                Text {
                    anchors.left: parent.left; anchors.leftMargin: 12
                    anchors.verticalCenter: parent.verticalCenter
                    text: (cb.block.lang || "CODE").toUpperCase()
                    color: Theme.textMute
                    font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                    font.letterSpacing: Theme.trackLabel
                }
                Row {
                    anchors.right: parent.right; anchors.rightMargin: 10
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 6
                    Pill { label: cb.copyLabel; onClicked: { if (!controller || (cb.block.code && controller.copyToClipboard(cb.block.code))) { cb.copyLabel = "✓ Copied"; copyReset.restart() } } }
                    Pill { label: "＋ Wrangle"; accent: true
                        onClicked: if (controller && cb.block.code) controller.createWrangle(cb.block.code) }
                }
            }

            Text {
                width: parent.width
                leftPadding: 13; rightPadding: 13; topPadding: 12; bottomPadding: 12
                textFormat: Text.RichText
                text: cb.block.html || ""
                font.family: Theme.fontMono
                font.pixelSize: Theme.fXs
                color: Theme.text
                lineHeight: 1.6
                wrapMode: Text.NoWrap
            }
        }
    }

    Timer { id: copyReset; interval: 1200; onTriggered: cb.copyLabel = "Copy" }
}
