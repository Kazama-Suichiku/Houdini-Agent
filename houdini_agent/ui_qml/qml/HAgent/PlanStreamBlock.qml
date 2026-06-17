import QtQuick
import QtQuick.Controls.Basic
import HAgent

// Live plan-generation preview (title + steps appear as create_plan streams).
Item {
    id: ps
    property var block: ({})
    property var steps: (block && block.steps) ? block.steps : []
    property var architecture: (block && block.architecture) ? block.architecture : ({})
    implicitHeight: box.height

    Rectangle {
        id: box
        width: parent.width
        height: inner.implicitHeight + 16
        color: "transparent"
        border.color: Theme.accentLine
        border.width: 1
        radius: Theme.radSm
        Rectangle { width: parent.width; height: 2; color: Theme.accent }

        Column {
            id: inner
            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
            anchors.leftMargin: 13; anchors.rightMargin: 13; anchors.topMargin: 12
            spacing: 8

            Row {
                spacing: 8
                Text { text: "◈"; color: Theme.accent; font.pixelSize: Theme.fMd; anchors.verticalCenter: parent.verticalCenter }
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: (ps.block && ps.block.title) ? ps.block.title : "Plan"
                    color: Theme.textBright
                    font.family: Theme.fontDisplay; font.pixelSize: Theme.fMd; font.italic: true
                }
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: "· 生成中"
                    color: Theme.textMute; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                    SequentialAnimation on opacity {
                        running: true; loops: Animation.Infinite
                        NumberAnimation { to: 0.35; duration: 600 }
                        NumberAnimation { to: 1.0; duration: 600 }
                    }
                }
            }
            Text {
                visible: ps.block.overview && ps.block.overview.length > 0
                width: inner.width
                text: ps.block.overview || ""
                color: Theme.textDim
                font.family: Theme.fontBody; font.pixelSize: Theme.fSm
                wrapMode: Text.Wrap
            }
            Repeater {
                model: ps.steps
                delegate: Row {
                    required property int index
                    required property var modelData
                    width: inner.width
                    spacing: 9
                    Text { text: (index + 1) + "."; color: Theme.textMute; font.family: Theme.fontMono; font.pixelSize: Theme.fXs }
                    Text {
                        width: parent.width - 24
                        text: modelData.label || modelData.title || modelData
                        color: Theme.text; font.family: Theme.fontBody; font.pixelSize: Theme.fSm
                        wrapMode: Text.Wrap
                    }
                }
            }
            Flickable {
                visible: ps.steps.length > 1 || (ps.architecture.nodes && ps.architecture.nodes.length > 0)
                width: inner.width
                height: Math.min(Math.max(streamDag.implicitHeight, 96), 210)
                contentWidth: streamDag.implicitWidth
                contentHeight: streamDag.implicitHeight
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                boundsMovement: Flickable.StopAtBounds
                pixelAligned: false
                flickDeceleration: 3600
                maximumFlickVelocity: 7200
                ScrollBar.vertical: SmartScrollBar {}
                ScrollBar.horizontal: SmartScrollBar {}
                Rectangle { anchors.fill: parent; color: "transparent"; border.color: Theme.borderSoft; border.width: 1; radius: Theme.radSm }
                PlanDag {
                    id: streamDag
                    width: parent.width
                    architecture: ps.architecture
                    steps: ps.steps
                    streaming: true
                }
            }
        }
    }
}
