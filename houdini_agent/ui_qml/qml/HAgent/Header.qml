import QtQuick
import QtQuick.Layouts
import HAgent

// Top settings bar: brand · provider · model · web · think · overflow
Rectangle {
    id: header
    color: "transparent"
    implicitHeight: row.implicitHeight + 26

    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.border }

    RowLayout {
        id: row
        anchors.fill: parent
        anchors.leftMargin: 15; anchors.rightMargin: 15
        anchors.topMargin: 13; anchors.bottomMargin: 13
        spacing: 9

        Rectangle {
            width: 26; height: 26; radius: Theme.radSm; color: Theme.textBright
            Text { anchors.centerIn: parent; text: "◇"; color: Theme.bg; font.pixelSize: Theme.fMd }
        }
        Text {
            text: "Houdini Agent"
            color: Theme.textBright
            font.family: Theme.fontDisplay
            font.pixelSize: Theme.fLg
            font.weight: Font.Medium
        }

        Item { Layout.fillWidth: true }

        // Meshy 入口（引流）：品牌 logo + 下拉菜单（官网 / 工作台 / 充值 / Key / 文档）
        Item {
            id: meshyBtn
            implicitHeight: Math.round(28 * Theme.scale)
            implicitWidth: mrow.implicitWidth + 14
            Rectangle {
                anchors.fill: parent; radius: Theme.radSm
                color: mma.containsMouse ? Theme.surface : "transparent"
                border.width: 1
                border.color: mma.containsMouse ? Theme.accentLine : "transparent"
                Behavior on color { ColorAnimation { duration: 120 } }
            }
            RowLayout {
                id: mrow
                anchors.centerIn: parent
                spacing: 4
                Image {
                    source: "meshy-logo.svg"
                    sourceSize.width: 30; sourceSize.height: 30
                    Layout.preferredWidth: Math.round(16 * Theme.scale)
                    Layout.preferredHeight: Math.round(16 * Theme.scale)
                    fillMode: Image.PreserveAspectFit
                }
                Text { text: "▾"; color: Theme.textMute; font.pixelSize: Theme.fMicro }
            }
            MouseArea {
                id: mma; anchors.fill: parent; hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: { if (controller) meshyMenu.items = controller.meshyMenuItems(); meshyMenu.open() }
            }
        }

        Pill {
            id: provPill
            label: controller ? controller.providerLabel : "Provider"
            caret: true
            onClicked: { if (controller) provMenu.items = controller.providerItems(); provMenu.open() }
        }
        Pill {
            id: modelPill
            label: controller ? controller.model : "Model"
            caret: true; accent: true
            onClicked: { if (controller) modelMenu.items = controller.modelItems(); modelMenu.open() }
        }
        Pill {
            label: "Think"
            active: controller ? controller.showThinking : true
            onClicked: if (controller) controller.setThink(!controller.showThinking)
        }
        Pill { id: morePill; label: "···"
            onClicked: { if (controller) moreMenu.items = controller.overflowItems(); moreMenu.open() } }
    }

    MenuPopup {
        id: meshyMenu
        x: meshyBtn.x + meshyBtn.width - menuWidth + row.anchors.leftMargin
        y: row.y + row.height
        checkable: false
        menuWidth: 200
        onPicked: function(val) { if (controller) controller.openMeshy(val) }
    }

    MenuPopup {
        id: provMenu
        x: provPill.x + provPill.width - menuWidth + row.anchors.leftMargin
        y: row.y + row.height
        checkable: true
        menuWidth: 190
        onPicked: function(val) { if (controller) controller.setProvider(val) }
    }

    MenuPopup {
        id: modelMenu
        x: modelPill.x + modelPill.width - menuWidth + row.anchors.leftMargin
        y: row.y + row.height
        checkable: true
        menuWidth: 230
        onPicked: function(val) { if (controller) controller.setModel(val) }
    }

    MenuPopup {
        id: moreMenu
        x: morePill.x + morePill.width - menuWidth + row.anchors.leftMargin
        y: row.y + row.height
        checkable: false
        menuWidth: 210
        onPicked: function(val) { if (controller) controller.menuAction(val) }
    }
}
