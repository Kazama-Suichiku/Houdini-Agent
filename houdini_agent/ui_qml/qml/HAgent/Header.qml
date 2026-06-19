import QtQuick
import QtQuick.Layouts
import HAgent

// Top settings bar: brand · (meshy · provider · model) · think · overflow
// 三区锚定布局：品牌钉左、Think/··· 钉右（任何宽度都可见可点），
// 中间 Meshy/provider/model 占剩余空间，过窄时省略号截断 / 裁切。
Rectangle {
    id: header
    color: "transparent"
    implicitHeight: Math.round(28 * Theme.scale) + 26

    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.border }

    // 菜单定位：把锚点 pill 映射到 header 坐标，右对齐并夹在窗口内
    function menuX(item, menuW) {
        var p = item.mapToItem(header, 0, 0)
        return Math.max(6, Math.min(p.x + item.width - menuW, header.width - menuW - 6))
    }
    function menuY(item) {
        var p = item.mapToItem(header, 0, 0)
        return p.y + item.height + 4
    }

    // ---------- 左区：品牌 ----------
    Row {
        id: leftZone
        anchors.left: parent.left; anchors.leftMargin: 15
        anchors.verticalCenter: parent.verticalCenter
        spacing: 9
        Rectangle {
            width: 26; height: 26; radius: Theme.radSm; color: Theme.textBright
            anchors.verticalCenter: parent.verticalCenter
            Text { anchors.centerIn: parent; text: "◇"; color: Theme.bg; font.pixelSize: Theme.fMd }
        }
        Text {
            anchors.verticalCenter: parent.verticalCenter
            // 窄窗隐藏标题文字，只留 ◇，把空间让给中区
            visible: header.width > Math.round(360 * Theme.scale)
            text: "Houdini Agent"
            color: Theme.textBright
            font.family: Theme.fontDisplay; font.pixelSize: Theme.fLg; font.weight: Font.Medium
        }
    }

    // ---------- 右区：操作按钮（永远可见） ----------
    RowLayout {
        id: rightZone
        anchors.right: parent.right; anchors.rightMargin: 15
        anchors.verticalCenter: parent.verticalCenter
        spacing: 9
        Pill {
            label: "Think"
            active: controller ? controller.showThinking : true
            onClicked: if (controller) controller.setThink(!controller.showThinking)
        }
        Pill {
            id: morePill; label: "···"
            onClicked: {
                if (controller) moreMenu.items = controller.overflowItems()
                moreMenu.x = header.menuX(morePill, moreMenu.menuWidth)
                moreMenu.y = header.menuY(morePill)
                moreMenu.open()
            }
        }
    }

    // ---------- 中区：Meshy / provider / model（占剩余空间，过窄省略/裁切） ----------
    RowLayout {
        id: midZone
        anchors.left: leftZone.right; anchors.leftMargin: 12
        anchors.right: rightZone.left; anchors.rightMargin: 12
        anchors.verticalCenter: parent.verticalCenter
        spacing: 9
        clip: true

        // Meshy 快捷入口
        Item {
            id: meshyBtn
            Layout.preferredHeight: Math.round(28 * Theme.scale)
            Layout.preferredWidth: mrow.implicitWidth + 14
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
                onClicked: {
                    if (controller) meshyMenu.items = controller.meshyMenuItems()
                    meshyMenu.x = header.menuX(meshyBtn, meshyMenu.menuWidth)
                    meshyMenu.y = header.menuY(meshyBtn)
                    meshyMenu.open()
                }
            }
        }

        Pill {
            id: provPill
            label: controller ? controller.providerLabel : "Provider"
            caret: true; elideLabel: true
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            Layout.maximumWidth: provPill.implicitWidth
            onClicked: {
                if (controller) provMenu.items = controller.providerItems()
                provMenu.x = header.menuX(provPill, provMenu.menuWidth)
                provMenu.y = header.menuY(provPill)
                provMenu.open()
            }
        }
        Pill {
            id: modelPill
            label: controller ? controller.model : "Model"
            caret: true; accent: true; elideLabel: true
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            Layout.maximumWidth: modelPill.implicitWidth
            onClicked: {
                if (controller) modelMenu.items = controller.modelItems()
                modelMenu.x = header.menuX(modelPill, modelMenu.menuWidth)
                modelMenu.y = header.menuY(modelPill)
                modelMenu.open()
            }
        }
    }

    MenuPopup {
        id: meshyMenu
        checkable: false
        menuWidth: 200
        onPicked: function(val) { if (controller) controller.openMeshy(val) }
    }
    MenuPopup {
        id: provMenu
        checkable: true
        menuWidth: 190
        onPicked: function(val) { if (controller) controller.setProvider(val) }
    }
    MenuPopup {
        id: modelMenu
        checkable: true
        menuWidth: 230
        onPicked: function(val) { if (controller) controller.setModel(val) }
    }
    MenuPopup {
        id: moreMenu
        checkable: false
        menuWidth: 210
        onPicked: function(val) { if (controller) controller.menuAction(val) }
    }
}
