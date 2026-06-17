import QtQuick
import HAgent

// Node operation summary with Keep / Undo (resolves to a tag).
Item {
    id: nr
    property var block: ({})
    property string resolved: ""   // "", "kept", "reverted"
    property bool hasDiff: block && (block.old !== undefined || block.new !== undefined)
    implicitHeight: box.height

    Connections {
        target: controller
        ignoreUnknownSignals: true
        function onBatchResolved(kind) { if (nr.resolved === "") nr.resolved = kind }
    }

    function linkify(s) {
        if (!s) return ""
        var parts = ("" + s).split(" · ")
        var out = []
        for (var i = 0; i < parts.length; i++)
            out.push("<a href='" + parts[i] + "' style='color:" + Theme.synFn + ";text-decoration:none'>" + parts[i] + "</a>")
        return out.join("  ·  ")
    }

    Rectangle {
        id: box
        width: parent.width
        height: inner.implicitHeight + 18
        color: "transparent"
        border.color: Theme.border
        border.width: 1
        radius: Theme.radSm

        Column {
            id: inner
            anchors.left: parent.left; anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            anchors.leftMargin: 12; anchors.rightMargin: 12
            spacing: 6

            Item {
                width: parent.width
                height: Math.max(topLine.implicitHeight, Math.round(26 * Theme.scale))
                Row {
                    id: topLine
                    anchors.left: parent.left
                    anchors.right: actionArea.left
                    anchors.rightMargin: 8
                    spacing: 9
                    Rectangle {
                        property string sign: (nr.block.badge || "+").charAt(0)
                        width: badge.implicitWidth + 14; height: Math.round(20 * Theme.scale)
                        radius: Theme.radSm
                        color: sign === "-" ? Qt.rgba(0.87, 0.6, 0.6, 0.12)
                             : sign === "~" ? Theme.warnSoft : Theme.okSoft
                        anchors.verticalCenter: parent.verticalCenter
                        Text {
                            id: badge; anchors.centerIn: parent; text: nr.block.badge || "+0"
                            color: parent.sign === "-" ? Theme.err : parent.sign === "~" ? Theme.warn : Theme.ok
                            font.family: Theme.fontMono; font.pixelSize: Theme.fXs
                        }
                    }
                    Row {
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 6
                        width: Math.max(40, topLine.width - badge.parent.width - 18)
                        clip: true
                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            width: nr.hasDiff ? Math.min(Math.round(120 * Theme.scale), parent.width * 0.36) : parent.width
                            text: nr.block.text || ""
                            elide: Text.ElideRight
                            color: Theme.text; font.family: nr.hasDiff ? Theme.fontMono : Theme.fontBody
                            font.pixelSize: nr.hasDiff ? Theme.fXs : Theme.fSm
                        }
                        Rectangle {
                            visible: nr.hasDiff
                            anchors.verticalCenter: parent.verticalCenter
                            height: Math.round(18 * Theme.scale); width: Math.min(oldT.implicitWidth + 12, Math.round(105 * Theme.scale))
                            radius: Theme.radSm; color: Qt.rgba(0.87, 0.6, 0.6, 0.14)
                            border.width: 1; border.color: Qt.rgba(0.87, 0.6, 0.6, 0.4)
                            Text { id: oldT; anchors.fill: parent; anchors.leftMargin: 6; anchors.rightMargin: 6; verticalAlignment: Text.AlignVCenter; text: nr.block.old || ""; elide: Text.ElideRight; color: Theme.err
                                font.family: Theme.fontMono; font.pixelSize: Theme.fMicro }
                        }
                        Text { visible: nr.hasDiff; anchors.verticalCenter: parent.verticalCenter
                            text: "→"; color: Theme.textMute; font.pixelSize: Theme.fXs }
                        Rectangle {
                            visible: nr.hasDiff
                            anchors.verticalCenter: parent.verticalCenter
                            height: Math.round(18 * Theme.scale); width: Math.min(newT.implicitWidth + 12, Math.round(105 * Theme.scale))
                            radius: Theme.radSm; color: Theme.okSoft
                            border.width: 1; border.color: Qt.rgba(0.722, 0.773, 0.627, 0.4)
                            Text { id: newT; anchors.fill: parent; anchors.leftMargin: 6; anchors.rightMargin: 6; verticalAlignment: Text.AlignVCenter; text: nr.block.new || ""; elide: Text.ElideRight; color: Theme.ok
                                font.family: Theme.fontMono; font.pixelSize: Theme.fMicro }
                        }
                    }
                }
                // actions / resolved tag
                Row {
                    id: actionArea
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 6
                    visible: nr.resolved === ""
                    Pill { label: "Keep"; onClicked: { if (!controller || (nr.block.opId && controller.keepNodeOp(nr.block.opId))) nr.resolved = "kept" } }
                    Pill { label: "Undo"; onClicked: { if (!controller || (nr.block.opId && controller.undoNodeOp(nr.block.opId))) nr.resolved = "reverted" } }
                }
                Text {
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    visible: nr.resolved !== ""
                    text: nr.resolved === "kept" ? "✓ Kept" : "↩ Reverted"
                    color: nr.resolved === "kept" ? Theme.ok : Theme.textMute
                    font.family: Theme.fontMono; font.pixelSize: Theme.fXs
                }
            }

            Text {
                width: parent.width
                textFormat: Text.RichText
                text: nr.linkify(nr.block.paths)
                color: Theme.textMute; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                wrapMode: Text.Wrap; opacity: 0.85
                onLinkActivated: function(link) { if (controller) controller.focusNode(link) }
            }
        }
    }
}
