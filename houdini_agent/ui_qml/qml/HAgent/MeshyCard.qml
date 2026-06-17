import QtQuick
import QtQuick.Layouts
import HAgent

// Meshy generation progress card. Driven by a {kind:"meshy"} block:
//   { tool, op, stage, progress(0-100), status, done, ok, thumb, summary, backgroundable }
// Shows a live progress bar while running, then a thumbnail + summary on success
// (or the error in warn colour on failure). While running it offers 转入后台.
Item {
    id: mc
    property var block: ({})
    property string toolName: (block && block.tool) ? block.tool : "meshy"
    property string op: (block && block.op) ? ("" + block.op) : ""
    property int prog: (block && block.progress !== undefined) ? block.progress : 0
    property bool done: !!(block && block.done)
    property bool ok: !block || block.ok === undefined ? true : !!block.ok
    property bool background: !!(block && block.background)
    property bool backgroundable: !!(block && block.backgroundable) && !done && op.length > 0
    function loc(s) { return controller ? (controller.lang, controller.tr(s)) : s }
    property string thumbUrl: (block && block.thumb && ("" + block.thumb).length)
        ? ("file:///" + ("" + block.thumb).replace(/\\/g, "/")) : ""

    implicitHeight: box.height

    Rectangle {
        id: box
        width: parent.width
        height: inner.implicitHeight
        color: "transparent"
        border.color: mc.done && !mc.ok ? Theme.warn : Theme.border
        border.width: 1
        radius: Theme.radSm

        Column {
            id: inner
            width: parent.width

            // header
            Item {
                width: parent.width
                height: Math.round(38 * Theme.scale)
                Row {
                    anchors.left: parent.left; anchors.leftMargin: 13
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 8
                    Text {
                        text: "◆"; color: Theme.accent; font.pixelSize: Theme.fSm
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: ("MESHY · " + mc.toolName).toUpperCase()
                        color: Theme.textDim
                        font.family: Theme.fontMono
                        font.pixelSize: Theme.fMicro
                        font.letterSpacing: Theme.trackLabel
                    }
                }
                Text {
                    anchors.right: parent.right; anchors.rightMargin: 13
                    anchors.verticalCenter: parent.verticalCenter
                    text: mc.done ? (mc.background ? "↗" : (mc.ok ? "✓" : "!")) : (mc.prog + "%")
                    color: mc.done ? (mc.background ? Theme.accent : (mc.ok ? Theme.ok : Theme.warn)) : Theme.accent
                    font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                }
            }

            Rectangle { width: parent.width; height: 1; color: Theme.border }

            // progress bar (while running)
            Item {
                visible: !mc.done
                width: parent.width
                height: Math.round(30 * Theme.scale)
                Text {
                    id: stageLbl
                    anchors.left: parent.left; anchors.leftMargin: 13
                    anchors.verticalCenter: parent.verticalCenter
                    text: (block && block.stage) ? block.stage : "…"
                    color: Theme.textMute
                    font.family: Theme.fontBody; font.pixelSize: Theme.fXs
                }
                Rectangle {
                    anchors.left: parent.left; anchors.leftMargin: 13
                    anchors.right: parent.right; anchors.rightMargin: 13
                    anchors.bottom: parent.bottom; anchors.bottomMargin: 4
                    height: 2
                    color: Theme.border
                    Rectangle {
                        height: parent.height
                        width: parent.width * Math.max(0, Math.min(100, mc.prog)) / 100.0
                        color: Theme.accent
                        Behavior on width { NumberAnimation { duration: 250 } }
                    }
                }
            }

            // 转入后台（运行中且可后台化时显示）
            Item {
                visible: mc.backgroundable
                width: parent.width
                height: visible ? Math.round(36 * Theme.scale) : 0
                Text {
                    anchors.left: parent.left; anchors.leftMargin: 13
                    anchors.verticalCenter: parent.verticalCenter
                    width: parent.width - 130
                    text: mc.loc("耗时较久？可转后台，完成后自动通知")
                    color: Theme.textMute; font.family: Theme.fontBody; font.pixelSize: Theme.fMicro
                    elide: Text.ElideRight
                }
                Pill {
                    anchors.right: parent.right; anchors.rightMargin: 13
                    anchors.verticalCenter: parent.verticalCenter
                    label: mc.loc("转入后台"); accent: true
                    onClicked: if (controller) controller.backgroundMeshyTask(mc.op)
                }
            }

            // result thumbnail (on success)
            Image {
                visible: mc.done && mc.ok && mc.thumbUrl.length > 0
                source: mc.thumbUrl
                width: parent.width
                fillMode: Image.PreserveAspectFit
                asynchronous: true
                height: visible ? Math.round(160 * Theme.scale) : 0
            }

            // summary / error text
            Text {
                visible: mc.done && (block && block.summary && ("" + block.summary).length > 0)
                width: parent.width
                leftPadding: 13; rightPadding: 13; topPadding: 8; bottomPadding: 10
                text: (block && block.summary) ? block.summary : ""
                wrapMode: Text.Wrap
                color: mc.ok ? Theme.textMute : Theme.warn
                font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                lineHeight: 1.5
            }
        }
    }
}
