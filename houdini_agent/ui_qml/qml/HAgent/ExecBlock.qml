import QtQuick
import HAgent

// Collapsible execution group with tool rows.
Item {
    id: eb
    property var block: ({})
    property bool collapsed: true
    property var tools: (block && block.tools) ? block.tools : []
    property var shells: (block && block.shells) ? block.shells : []
    function shellOut(s) {
        if (!s) return ""
        return (s.error && s.error.length) ? s.error : (s.output || "")
    }
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
                    Text { text: "◆"; color: Theme.accent; font.pixelSize: Theme.fSm; anchors.verticalCenter: parent.verticalCenter }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        width: Math.max(80, head.width - 58)
                        elide: Text.ElideRight
                        text: ((eb.block.label || "Executing…") + (eb.shells.length > 0 ? (" · " + eb.shells.length + " shell") : "")).toUpperCase()
                        color: Theme.textDim
                        font.family: Theme.fontMono
                        font.pixelSize: Theme.fMicro
                        font.letterSpacing: Theme.trackLabel
                    }
                }
                Text {
                    anchors.right: parent.right; anchors.rightMargin: 13
                    anchors.verticalCenter: parent.verticalCenter
                    text: eb.collapsed ? "▸" : "▾"
                    color: Theme.textMute; font.pixelSize: Theme.fMicro
                }
                MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: eb.collapsed = !eb.collapsed }
            }

            Rectangle { visible: !eb.collapsed; width: parent.width; height: 1; color: Theme.border }

            Column {
                visible: !eb.collapsed
                width: parent.width
                Repeater {
                    model: eb.tools
                    delegate: Item {
                        id: toolRow
                        required property var modelData
                        property bool open: false
                        property bool hasDetail: modelData.detail && modelData.detail.length > 0
                        width: parent.width
                        height: line.height + (open && hasDetail ? detail.height : 0)

                        Item {
                            id: line
                            width: parent.width
                            height: Math.round(34 * Theme.scale)
                            Text {
                                id: toolState
                                anchors.left: parent.left; anchors.leftMargin: 13
                                anchors.verticalCenter: parent.verticalCenter
                                text: modelData.state === "warn" ? "!" : modelData.state === "run" ? "◐" : "✓"
                                color: modelData.state === "warn" ? Theme.warn : modelData.state === "run" ? Theme.accent : Theme.ok
                                font.pixelSize: Theme.fSm
                                width: 14; horizontalAlignment: Text.AlignHCenter
                            }
                            Text {
                                id: toolName
                                anchors.left: toolState.right; anchors.leftMargin: 9
                                anchors.verticalCenter: parent.verticalCenter
                                width: Math.min(Math.round(135 * Theme.scale), Math.max(72, line.width * 0.33))
                                text: modelData.name || ""
                                elide: Text.ElideRight
                                color: Theme.textBright; font.family: Theme.fontMono; font.pixelSize: Theme.fXs
                            }
                            Text {
                                anchors.left: toolName.right; anchors.leftMargin: 8
                                anchors.right: toolTime.left; anchors.rightMargin: 8
                                anchors.verticalCenter: parent.verticalCenter
                                text: modelData.arg || ""
                                elide: Text.ElideRight
                                color: Theme.textMute; font.family: Theme.fontBody; font.pixelSize: Theme.fXs
                            }
                            Text {
                                id: toolTime
                                anchors.right: parent.right; anchors.rightMargin: 13
                                anchors.verticalCenter: parent.verticalCenter
                                width: Math.round(42 * Theme.scale)
                                horizontalAlignment: Text.AlignRight
                                elide: Text.ElideRight
                                text: modelData.time || ""
                                color: Theme.textMute; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                            }
                            MouseArea {
                                anchors.fill: parent
                                enabled: toolRow.hasDetail
                                cursorShape: toolRow.hasDetail ? Qt.PointingHandCursor : Qt.ArrowCursor
                                onClicked: toolRow.open = !toolRow.open
                            }
                        }
                        Text {
                            id: detail
                            visible: toolRow.open && toolRow.hasDetail
                            anchors.top: line.bottom
                            width: parent.width
                            leftPadding: 34; rightPadding: 13; bottomPadding: 9
                            text: modelData.detail || ""
                            wrapMode: Text.Wrap
                            color: Theme.textMute; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                            lineHeight: 1.5
                        }
                    }
                }
                Rectangle {
                    visible: eb.shells.length > 0
                    width: parent.width
                    height: 1
                    color: Theme.border
                }
                Column {
                    visible: eb.shells.length > 0
                    width: parent.width
                    Repeater {
                        model: eb.shells
                        delegate: Item {
                            id: shellRow
                            required property var modelData
                            property bool open: false
                            property string out: eb.shellOut(modelData)
                            property bool isPy: modelData.shellKind === "execute_python"
                            width: parent.width
                            height: shellHead.height + (open ? shellBody.implicitHeight : 0)

                            Item {
                                id: shellHead
                                width: parent.width
                                height: Math.round(32 * Theme.scale)
                                Rectangle { width: 2; height: parent.height; color: shellRow.isPy ? Theme.warn : Theme.ok }
                                Text {
                                    id: shellKind
                                    anchors.left: parent.left; anchors.leftMargin: 13
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: shellRow.isPy ? "PY" : "SH"
                                    color: shellRow.isPy ? Theme.warn : Theme.ok
                                    font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                                    width: 22
                                }
                                Text {
                                    id: shellTitle
                                    anchors.left: shellKind.right; anchors.leftMargin: 8
                                    anchors.right: shellMeta.left; anchors.rightMargin: 8
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: shellRow.isPy ? "Python shell" : "System shell"
                                    elide: Text.ElideRight
                                    color: Theme.text
                                    font.family: Theme.fontMono; font.pixelSize: Theme.fXs
                                }
                                Text {
                                    id: shellMeta
                                    anchors.right: parent.right; anchors.rightMargin: 32
                                    anchors.verticalCenter: parent.verticalCenter
                                    width: Math.round(76 * Theme.scale)
                                    horizontalAlignment: Text.AlignRight
                                    elide: Text.ElideRight
                                    text: (modelData.success ? "✓" : "✗") + (shellRow.out.length > 0 ? (" · " + shellRow.out.split("\n").length + " lines") : "")
                                    color: modelData.success ? Theme.ok : Theme.err
                                    font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                                }
                                Text {
                                    anchors.right: parent.right; anchors.rightMargin: 13
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: shellRow.open ? "▾" : "▸"
                                    color: Theme.textMute; font.pixelSize: Theme.fMicro
                                }
                                MouseArea {
                                    anchors.fill: parent
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: shellRow.open = !shellRow.open
                                }
                            }

                            Column {
                                id: shellBody
                                visible: shellRow.open
                                anchors.top: shellHead.bottom
                                width: parent.width
                                Text {
                                    width: parent.width
                                    leftPadding: 34; rightPadding: 13; topPadding: 6; bottomPadding: 4
                                    text: modelData.code || ""
                                    color: Theme.text
                                    font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                                    wrapMode: Text.Wrap
                                }
                                Rectangle { visible: shellRow.out.length > 0; width: parent.width; height: 1; color: Theme.border }
                                Text {
                                    visible: shellRow.out.length > 0
                                    width: parent.width
                                    leftPadding: 34; rightPadding: 13; topPadding: 6; bottomPadding: 9
                                    text: shellRow.out
                                    color: modelData.success ? Theme.textMute : Theme.err
                                    font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                                    wrapMode: Text.Wrap
                                    lineHeight: 1.4
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
