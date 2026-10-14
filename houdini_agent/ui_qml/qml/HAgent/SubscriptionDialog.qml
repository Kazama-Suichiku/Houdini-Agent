import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import HAgent

// 「ChatGPT 订阅」登录：本机 CLIProxyAPI（CPA）+ Codex OAuth。
// 状态全部来自 controller.subscriptionStatus()，变化时由 subscriptionChanged 通知刷新。
// 只保留必要信息：已登录 = 账号 + 退出；未登录 = 一句说明 + 两种登录方式；过程提示只在登录中出现。
Popup {
    id: dlg
    property var st: ({})
    property bool loggedIn: !!(st.accounts && st.accounts.length > 0)
    property bool busy: !!st.busy
    // 已登录且不在登录流程中时不显示提示框（"已登录 xxx" 与账号行重复）
    property bool showMsg: busy || (!loggedIn && (st.message || "").length > 0)
    function loc(s) { return controller ? (controller.lang, controller.tr(s)) : s }
    function refresh() { if (controller) st = controller.subscriptionStatus() }
    function show() { refresh(); open() }

    Connections {
        target: controller
        ignoreUnknownSignals: true
        function onSubscriptionChanged() { dlg.refresh() }
    }

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(440, parent ? parent.width - 36 : 440)
    padding: 18
    modal: true
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    background: Rectangle { color: Theme.bg; border.color: Theme.border; border.width: 1; radius: Theme.radSm }

    contentItem: ColumnLayout {
        width: dlg.availableWidth
        spacing: 12

        Text {
            Layout.fillWidth: true
            text: dlg.loc("ChatGPT 订阅")
            color: Theme.textBright
            font.family: Theme.fontDisplay; font.pixelSize: Theme.fLg; font.weight: Font.Medium
        }
        Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }

        // ---- 未登录：一句说明
        Text {
            visible: !dlg.loggedIn && !dlg.busy
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            color: Theme.textDim
            font.family: Theme.fontBody; font.pixelSize: Theme.fSm
            text: dlg.loc("登录后即可使用订阅额度，无需 API key。")
        }

        // ---- 已登录：账号 + 退出
        Repeater {
            model: dlg.st.accounts || []
            delegate: RowLayout {
                required property var modelData
                Layout.fillWidth: true; spacing: 8
                Text {
                    Layout.fillWidth: true; elide: Text.ElideRight
                    color: Theme.textBright; font.family: Theme.fontBody; font.pixelSize: Theme.fSm
                    text: (modelData.email || dlg.loc("ChatGPT 账号")) + (modelData.plan ? ("  ·  " + modelData.plan) : "")
                }
                Pill {
                    label: dlg.loc("退出登录"); visible: !dlg.busy
                    dashed: true        // 常显边框，一眼看得出是按钮
                    onClicked: if (controller) controller.subscriptionLogout(modelData.file)
                }
            }
        }

        // ---- 登录过程提示 / 错误
        Rectangle {
            visible: dlg.showMsg
            Layout.fillWidth: true
            implicitHeight: msgCol.implicitHeight + 20
            color: Theme.surface; border.color: Theme.borderSoft; radius: Theme.radSm
            ColumnLayout {
                id: msgCol
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                anchors.margins: 10
                spacing: 8
                Text {
                    Layout.fillWidth: true; wrapMode: Text.Wrap
                    color: Theme.text; font.family: Theme.fontBody; font.pixelSize: Theme.fSm
                    text: dlg.st.message || ""
                }
                // 设备码
                RowLayout {
                    visible: (dlg.st.deviceCode || "").length > 0
                    spacing: 10
                    Text {
                        text: dlg.st.deviceCode || ""
                        color: Theme.accent; font.family: Theme.fontMono; font.pixelSize: Theme.fLg
                        font.letterSpacing: 2
                    }
                    Pill { label: dlg.loc("复制"); onClicked: if (controller) controller.copyToClipboard(dlg.st.deviceCode) }
                    Pill { label: dlg.loc("打开页面"); onClicked: if (controller) controller.openExternalUrl(dlg.st.deviceUrl) }
                }
                // 浏览器没自动打开时的兜底链接
                RowLayout {
                    visible: (dlg.st.loginUrl || "").length > 0
                    spacing: 10
                    Text {
                        Layout.fillWidth: true
                        text: dlg.loc("浏览器没有打开？")
                        color: Theme.textDim; font.family: Theme.fontBody; font.pixelSize: Theme.fSm
                    }
                    Pill { label: dlg.loc("复制登录链接"); onClicked: if (controller) controller.copyToClipboard(dlg.st.loginUrl) }
                    Pill { label: dlg.loc("再次打开"); onClicked: if (controller) controller.openExternalUrl(dlg.st.loginUrl) }
                }
            }
        }

        // ---- 按钮：右对齐，放不下时自动换行；RightToLeft 下先声明的排在最右。
        Flow {
            Layout.fillWidth: true
            layoutDirection: Qt.RightToLeft
            spacing: 8
            Pill {
                label: dlg.loc("关闭")
                onClicked: dlg.close()
            }
            Pill {
                visible: !dlg.loggedIn && !dlg.busy
                label: dlg.loc("浏览器登录")
                accent: true
                onClicked: if (controller) controller.subscriptionLogin(false)
            }
            Pill {
                visible: !dlg.loggedIn && !dlg.busy
                label: dlg.loc("设备码登录")
                onClicked: if (controller) controller.subscriptionLogin(true)
            }
            Pill {
                visible: dlg.busy
                label: dlg.loc("取消登录")
                onClicked: if (controller) controller.subscriptionCancelLogin()
            }
        }
    }
}
