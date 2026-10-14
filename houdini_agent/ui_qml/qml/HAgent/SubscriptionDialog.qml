import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import HAgent

// 「ChatGPT 订阅」登录：本机 CLIProxyAPI（CPA）+ Codex OAuth。
// 状态全部来自 controller.subscriptionStatus()，变化时由 subscriptionChanged 通知刷新。
Popup {
    id: dlg
    property var st: ({})
    property bool loggedIn: !!(st.accounts && st.accounts.length > 0)
    property bool busy: !!st.busy
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
    width: Math.min(460, parent ? parent.width - 36 : 460)
    padding: 18
    modal: true
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    background: Rectangle { color: Theme.bg; border.color: Theme.border; border.width: 1; radius: Theme.radSm }

    contentItem: ColumnLayout {
        spacing: 12

        Text {
            Layout.fillWidth: true
            text: dlg.loc("ChatGPT 订阅")
            color: Theme.textBright
            font.family: Theme.fontDisplay; font.pixelSize: Theme.fLg; font.weight: Font.Medium
        }
        Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }

        Text {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            color: Theme.textDim
            font.family: Theme.fontBody; font.pixelSize: Theme.fSm
            text: "用 ChatGPT Plus / Pro 订阅里的 Codex 额度驱动 Houdini Agent，不需要 API key。"
                  + "登录由本机运行的开源组件 CLIProxyAPI 完成，凭据只保存在这台电脑上。"
        }

        // ---- 状态
        GridLayout {
            Layout.fillWidth: true
            columns: 2; columnSpacing: 12; rowSpacing: 6
            Text { text: "组件"; color: Theme.textMute; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro }
            Text {
                Layout.fillWidth: true; wrapMode: Text.Wrap
                color: Theme.text; font.family: Theme.fontBody; font.pixelSize: Theme.fSm
                text: dlg.st.installed ? ("CLIProxyAPI " + (dlg.st.version || "") + " 已安装")
                                       : "未安装（首次登录时自动下载，约 24 MB）"
            }
            Text { text: "账号"; color: Theme.textMute; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro }
            ColumnLayout {
                Layout.fillWidth: true; spacing: 4
                Text {
                    visible: !dlg.loggedIn
                    text: "未登录"; color: Theme.textDim; font.family: Theme.fontBody; font.pixelSize: Theme.fSm
                }
                Repeater {
                    model: dlg.st.accounts || []
                    delegate: RowLayout {
                        required property var modelData
                        Layout.fillWidth: true; spacing: 8
                        Text {
                            Layout.fillWidth: true; elide: Text.ElideRight
                            color: Theme.textBright; font.family: Theme.fontBody; font.pixelSize: Theme.fSm
                            text: (modelData.email || "ChatGPT 账号") + (modelData.plan ? ("  ·  " + modelData.plan) : "")
                        }
                        Pill {
                            label: dlg.loc("退出登录"); visible: !dlg.busy
                            onClicked: if (controller) controller.subscriptionLogout(modelData.file)
                        }
                    }
                }
            }
        }

        // ---- 进度 / 提示
        Rectangle {
            visible: (dlg.st.message || "").length > 0 || dlg.busy
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
                    Pill { label: "打开页面"; onClicked: if (controller) controller.openExternalUrl(dlg.st.deviceUrl) }
                }
                // 浏览器没自动打开时的兜底链接
                RowLayout {
                    visible: (dlg.st.loginUrl || "").length > 0
                    spacing: 10
                    Text {
                        Layout.fillWidth: true
                        text: "浏览器没有打开？"
                        color: Theme.textDim; font.family: Theme.fontBody; font.pixelSize: Theme.fSm
                    }
                    Pill { label: "复制登录链接"; onClicked: if (controller) controller.copyToClipboard(dlg.st.loginUrl) }
                    Pill { label: "再次打开"; onClicked: if (controller) controller.openExternalUrl(dlg.st.loginUrl) }
                }
            }
        }

        Text {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            color: Theme.textMute
            font.family: Theme.fontBody; font.pixelSize: Theme.fMicro
            text: "用量计入你的 ChatGPT 订阅额度。OpenAI 目前允许在第三方工具里使用订阅，但这不是合同条款，政策可能变化。"
        }

        // ---- 按钮
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Pill {
                visible: dlg.loggedIn && !dlg.busy
                label: dlg.loc("刷新模型")
                onClicked: if (controller) controller.subscriptionRefreshModels()
            }
            Item { Layout.fillWidth: true }
            Pill {
                visible: dlg.busy
                label: dlg.loc("取消登录")
                onClicked: if (controller) controller.subscriptionCancelLogin()
            }
            Pill {
                visible: !dlg.busy
                label: dlg.loc("设备码登录")
                onClicked: if (controller) controller.subscriptionLogin(true)
            }
            Pill {
                visible: !dlg.busy
                label: dlg.loggedIn ? "再登录一个账号" : dlg.loc("浏览器登录")
                accent: true
                onClicked: if (controller) controller.subscriptionLogin(false)
            }
            Pill {
                label: dlg.loc("关闭")
                onClicked: dlg.close()
            }
        }
    }
}
