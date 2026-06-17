import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import HAgent

// Root of the Houdini Agent panel (Mono Editorial).
// `controller` and `chatModel` are injected as context properties from Python.
Rectangle {
    id: root
    color: Theme.bg
    implicitWidth: 420
    implicitHeight: 760

    // user font-scale (⋯ 字号 +/−) drives the Theme singleton
    Component.onCompleted: if (controller) Theme.scale = controller.fontScale
    Connections {
        target: controller
        ignoreUnknownSignals: true
        function onFontScaleChanged() { if (controller) Theme.scale = controller.fontScale }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        Header      { Layout.fillWidth: true }
        SessionTabs { Layout.fillWidth: true }
        ContextBar  { Layout.fillWidth: true }

        ChatView {
            Layout.fillWidth: true
            Layout.fillHeight: true
        }

        // 更新提示横幅 —— 紧贴输入框上方（沿用 1.5 的“输入区域上方”位置）
        Rectangle {
            Layout.fillWidth: true
            visible: controller && controller.updateText.length > 0
            implicitHeight: visible ? 34 : 0
            color: Theme.accentSoft
            Rectangle { anchors.top: parent.top; width: parent.width; height: 1; color: Theme.accentLine }
            Row {
                anchors.fill: parent; anchors.leftMargin: 15; anchors.rightMargin: 12; spacing: 8
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    width: parent.width - 70
                    text: controller ? controller.updateText : ""
                    color: Theme.accent; font.family: Theme.fontBody; font.pixelSize: Theme.fXs
                    elide: Text.ElideRight
                }
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: "✕"; color: Theme.textMute; font.pixelSize: Theme.fSm
                    MouseArea { anchors.fill: parent; anchors.margins: -6; cursorShape: Qt.PointingHandCursor
                        onClicked: if (controller) controller.dismissUpdate() }
                }
            }
        }

        Composer { Layout.fillWidth: true }
    }

    // Meshy 资产库浮层抽屉：向内叠在聊天之上；未被面板覆盖处用半透明磨砂遮罩盖住，点遮罩关闭。
    Item {
        id: libOverlay
        anchors.fill: parent
        z: 90
        property bool open: controller ? controller.libraryOpen : false
        visible: open || scrim.opacity > 0.001

        // 磨砂遮罩：半透明盖住未被面板覆盖的区域，点击关闭
        Rectangle {
            id: scrim
            anchors.fill: parent
            color: Qt.rgba(0.05, 0.05, 0.06, 0.55)
            opacity: libOverlay.open ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
            MouseArea {
                anchors.fill: parent
                onClicked: if (controller) controller.setLibraryOpen(false)
            }
        }

        // 抽屉面板：从左侧滑入，叠在聊天上方（不挤占窗口）
        Rectangle {
            id: libDrawer
            width: Math.min(360, libOverlay.width * 0.88)
            height: parent.height
            color: Theme.bg
            x: libOverlay.open ? 0 : -width - 2
            Behavior on x { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }
            Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: Theme.border }
            // 右缘投影，强化"叠在上方"的层次
            Rectangle {
                anchors.left: parent.right; width: 14; height: parent.height
                gradient: Gradient {
                    orientation: Gradient.Horizontal
                    GradientStop { position: 0.0; color: Qt.rgba(0, 0, 0, 0.22) }
                    GradientStop { position: 1.0; color: "transparent" }
                }
            }
            LibraryContent { anchors.fill: parent; anchors.rightMargin: 1 }
        }
    }

    // transient toast
    Rectangle {
        id: toast
        z: 100
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom; anchors.bottomMargin: 96
        visible: opacity > 0
        opacity: 0
        width: toastText.implicitWidth + 26
        height: toastText.implicitHeight + 16
        color: Theme.surface2
        border.color: Theme.border; border.width: 1; radius: Theme.radSm
        Behavior on opacity { NumberAnimation { duration: 200 } }
        Text {
            id: toastText
            anchors.centerIn: parent
            color: Theme.textBright
            font.family: Theme.fontBody; font.pixelSize: Theme.fSm
        }
        Timer { id: toastTimer; interval: 2200; onTriggered: toast.opacity = 0 }
        Connections {
            target: controller
            ignoreUnknownSignals: true
            function onToast(msg) { toastText.text = msg; toast.opacity = 1; toastTimer.restart() }
        }
    }

    FontPopup { id: fontPopup }
    TokenPopup { id: tokenPopup }
    ActionDialog { id: actionDialog }
    ManagementPanel { id: managementPanel }
    Connections {
        target: controller
        ignoreUnknownSignals: true
        function onOpenFontDialog() { fontPopup.open() }
        function onOpenTokenDialog() { tokenPopup.st = controller.tokenStats(); tokenPopup.open() }
        function onOpenInfoDialog(title, body) { actionDialog.openInfo(title, body) }
        function onOpenApiKeyDialog(provider) { actionDialog.openApi(provider) }
        function onOpenCustomProviderDialog(url, key, model, anthropic, contextLimit, supportsVision) { actionDialog.openCustom(url, key, model, anthropic, contextLimit, supportsVision) }
        function onOpenConfirmDialog(title, body, token) { actionDialog.openConfirm(title, body, token) }
        function onOpenRulesDialog() { managementPanel.openRules() }
        function onOpenPluginsDialog() { managementPanel.openPlugins() }
        function onOpenMemoryDialog() { managementPanel.openMemory() }
        function onManagementChanged() { managementPanel.refresh() }
    }
}
