import QtQuick
import HAgent

// Python / System shell execution result.
Item {
    id: sb
    property var block: ({})
    property bool isPy: (block && block.shellKind === "execute_python")
    property string out: (block ? ((block.error && block.error.length) ? block.error : (block.output || "")) : "")
    property bool collapsed: false              // whole block fold
    property bool outCollapsed: out.split("\n").length > 6
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

            // header
            Item {
                width: parent.width
                height: Math.round(30 * Theme.scale)
                Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.border }
                Rectangle { width: 2; height: parent.height; color: sb.isPy ? Theme.warn : Theme.ok }
                Text {
                    anchors.left: parent.left; anchors.leftMargin: 13
                    anchors.verticalCenter: parent.verticalCenter
                    text: sb.isPy ? "PYTHON SHELL" : "SYSTEM SHELL"
                    color: Theme.textMute
                    font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                    font.letterSpacing: Theme.trackLabel
                }
                Text {
                    anchors.right: parent.right; anchors.rightMargin: 30
                    anchors.verticalCenter: parent.verticalCenter
                    text: (sb.block && sb.block.success) ? "✓" : "✗"
                    color: (sb.block && sb.block.success) ? Theme.ok : Theme.err
                    font.pixelSize: Theme.fXs
                }
                Text {
                    anchors.right: parent.right; anchors.rightMargin: 13
                    anchors.verticalCenter: parent.verticalCenter
                    text: sb.collapsed ? "▸" : "▾"
                    color: Theme.textMute; font.pixelSize: Theme.fMicro
                }
                MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: sb.collapsed = !sb.collapsed }
            }
            // code
            Text {
                visible: !sb.collapsed
                width: parent.width
                leftPadding: 13; rightPadding: 13; topPadding: 9; bottomPadding: 4
                text: (sb.block ? sb.block.code : "") || ""
                color: Theme.text
                font.family: Theme.fontMono; font.pixelSize: Theme.fXs
                wrapMode: Text.NoWrap
            }
            // output
            Rectangle { visible: !sb.collapsed && sb.out.length > 0; width: parent.width; height: 1; color: Theme.border }
            Text {
                visible: !sb.collapsed && sb.out.length > 0 && !sb.outCollapsed
                width: parent.width
                leftPadding: 13; rightPadding: 13; topPadding: 7; bottomPadding: 9
                text: sb.out
                color: (sb.block && !sb.block.success) ? Theme.err : Theme.textDim
                font.family: Theme.fontMono; font.pixelSize: Theme.fXs
                wrapMode: Text.Wrap
            }
            Item {
                visible: !sb.collapsed && sb.out.length > 0 && sb.outCollapsed
                width: parent.width; height: Math.round(28 * Theme.scale)
                Text {
                    anchors.centerIn: parent
                    text: "▾ 展开输出 (" + sb.out.split("\n").length + " 行)"
                    color: Theme.textMute; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                }
                MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: sb.outCollapsed = false }
            }
        }
    }
}
