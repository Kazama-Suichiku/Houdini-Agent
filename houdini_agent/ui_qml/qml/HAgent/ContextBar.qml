import QtQuick
import QtQuick.Layouts
import HAgent

// Houdini instance switcher (multi-instance) + network path + selection indicator.
Rectangle {
    id: ctx
    color: "transparent"
    implicitHeight: Math.round(30 * Theme.scale)

    function loc(s) { return controller ? (controller.lang, controller.tr(s)) : s }

    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.border }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 12; anchors.rightMargin: 14
        spacing: 7

        // 多实例切换胶囊（独立 Bridge 模式下显示当前连接的 Houdini 版本与 .hip 名）
        Rectangle {
            id: instPill
            visible: controller && controller.activeInstanceLabel.length > 0
            Layout.preferredHeight: Math.round(21 * Theme.scale)
            Layout.preferredWidth: Math.min(Math.round(185 * Theme.scale), instRow.implicitWidth + 14)
            radius: Theme.radSm
            color: instMa.containsMouse ? Theme.surface2 : Theme.surface
            border.width: 1
            border.color: (controller && controller.houdiniInstanceCount > 1) ? Theme.accentLine : Theme.border

            RowLayout {
                id: instRow
                anchors.fill: parent
                anchors.leftMargin: 7; anchors.rightMargin: 6
                spacing: 5
                Rectangle {
                    width: 6; height: 6; radius: 3
                    color: (controller && controller.houdiniConnected) ? Theme.ok : Theme.textMute
                }
                Text {
                    Layout.fillWidth: true
                    text: controller ? controller.activeInstanceLabel : ""
                    color: (controller && controller.houdiniConnected) ? Theme.textBright : Theme.textMute
                    font.family: Theme.fontMono
                    font.pixelSize: Theme.fMicro
                    elide: Text.ElideMiddle
                }
                Rectangle {
                    visible: controller && controller.houdiniInstanceCount > 1
                    Layout.preferredHeight: Math.round(14 * Theme.scale)
                    Layout.preferredWidth: cntTxt.implicitWidth + 8
                    radius: 7
                    color: Theme.accentSoft
                    Text {
                        id: cntTxt
                        anchors.centerIn: parent
                        text: controller ? ("" + controller.houdiniInstanceCount) : ""
                        color: Theme.accent
                        font.family: Theme.fontMono
                        font.pixelSize: Math.max(9, Theme.fMicro - 1)
                    }
                }
                Text {
                    text: "▾"
                    color: Theme.textMute
                    font.pixelSize: Theme.fMicro
                }
            }
            MouseArea {
                id: instMa
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: {
                    if (!controller) return
                    var p = instPill.mapToItem(ctx, 0, instPill.height + 3)
                    instMenu.x = p.x; instMenu.y = p.y
                    instMenu.items = controller.houdiniInstanceItems()
                    instMenu.open()
                }
            }
        }

        Text {
            visible: instPill.visible
            text: "·"
            color: Theme.textMute
            font.pixelSize: Theme.fXs
        }

        Text {
            text: controller ? controller.scenePath : "/obj/geo1"
            color: Theme.accent
            font.family: Theme.fontMono
            font.pixelSize: Theme.fXs
            elide: Text.ElideLeft
            Layout.maximumWidth: Math.round(130 * Theme.scale)
        }
        Text { text: "·"; color: Theme.textMute; font.pixelSize: Theme.fXs }
        Text {
            Layout.fillWidth: true
            text: controller ? controller.sceneSelection : "2 nodes selected"
            color: Theme.textMute
            font.family: Theme.fontBody
            font.pixelSize: Theme.fXs
            elide: Text.ElideRight
        }
        Text {
            text: "↻"
            color: refreshMa.containsMouse ? Theme.text : Theme.textMute
            font.pixelSize: Theme.fSm
            MouseArea {
                id: refreshMa
                anchors.fill: parent; anchors.margins: -6
                hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                onClicked: if (controller) controller.refreshContext()
            }
        }
    }

    MenuPopup {
        id: instMenu
        menuWidth: 265
        onPicked: function(val) {
            if (controller) controller.selectHoudiniInstance(val)
        }
    }
}
