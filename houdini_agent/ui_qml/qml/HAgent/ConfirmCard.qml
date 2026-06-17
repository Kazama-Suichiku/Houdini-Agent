import QtQuick
import HAgent

// Inline per-tool approval card (confirm mode).
Item {
    id: cc
    property var block: ({})
    property string state: (block && block.state) ? block.state : "pending"
    implicitHeight: box.height
    function loc(s) { return controller ? (controller.lang, controller.tr(s)) : s }

    Rectangle {
        id: box
        width: parent.width
        height: inner.implicitHeight + 20
        color: Theme.warnSoft
        border.color: Theme.warn
        border.width: 1
        radius: Theme.radSm

        Column {
            id: inner
            anchors.left: parent.left; anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            anchors.leftMargin: 13; anchors.rightMargin: 13
            spacing: 8

            Text {
                text: cc.loc("执行前确认")
                color: Theme.warn
                font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                font.letterSpacing: Theme.trackLabel
            }
            Text {
                width: parent.width
                text: (cc.block.name || "") + (cc.block.arg ? ("  ·  " + cc.block.arg) : "")
                color: Theme.textBright
                font.family: Theme.fontMono; font.pixelSize: Theme.fXs
                wrapMode: Text.Wrap
            }
            Row {
                spacing: 8
                visible: cc.state === "pending"
                Pill { label: cc.loc("取消"); onClicked: if (controller) controller.resolveConfirm(cc.block.cid, false) }
                Pill { label: cc.loc("确认执行"); accent: true; onClicked: if (controller) controller.resolveConfirm(cc.block.cid, true) }
            }
            Text {
                visible: cc.state !== "pending"
                text: cc.state === "confirmed" ? ("✓ " + cc.loc("已确认")) : ("✕ " + cc.loc("已取消"))
                color: cc.state === "confirmed" ? Theme.ok : Theme.textMute
                font.family: Theme.fontMono; font.pixelSize: Theme.fXs
            }
        }
    }
}
