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

    RowLayout {
        anchors.fill: parent
        spacing: 0

        // Meshy 资产库抽屉：依附面板、向左外扩的列。
        // 打开时 app.py 把整窗口向左加宽 360px，新增空间全给这列，聊天区宽度不变。
        Rectangle {
            id: libPanel
            Layout.fillHeight: true
            Layout.preferredWidth: (controller && controller.libraryOpen) ? 360 : 0
            Behavior on Layout.preferredWidth { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
            visible: Layout.preferredWidth > 1
            clip: true
            color: Theme.bg
            LibraryContent { anchors.fill: parent; anchors.rightMargin: 1 }
            Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: Theme.border }
        }

    ColumnLayout {
        Layout.fillWidth: true
        Layout.fillHeight: true
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

    // Meshy cloud asset library now lives in its own floating window
    // (host.create_library_view + app.py), so it is no longer embedded here.

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
