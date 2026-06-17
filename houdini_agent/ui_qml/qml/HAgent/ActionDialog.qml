import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import HAgent

Popup {
    id: dlg
    property string mode: "info"
    property string dialogTitle: ""
    property string body: ""
    property string provider: ""
    property string confirmToken: ""
    property bool anthropic: false
    property bool supportsVision: false
    function loc(s) { return controller ? (controller.lang, controller.tr(s)) : s }
    function openInfo(t, b) { mode = "info"; dialogTitle = t; body = b; open() }
    function openApi(p) { mode = "api"; provider = p; dialogTitle = "API Key · " + p; apiField.text = ""; open() }
    function openConfirm(t, b, token) { mode = "confirm"; dialogTitle = t; body = b; confirmToken = token; open() }
    function openCustom(url, key, model, isAnthropic, contextLimit, visionFlag) {
        mode = "custom"; dialogTitle = "Custom Provider"
        urlField.text = url || ""; customKeyField.text = key || ""; modelField.text = model || ""
        contextField.text = contextLimit || "128000"
        anthropic = !!isAnthropic; supportsVision = !!visionFlag; open()
    }

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(420, parent ? parent.width - 36 : 420)
    padding: 18
    modal: true
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    onClosed: {
        if (mode === "custom" && controller) controller.cancelCustomProvider()
        if (mode === "confirm" && controller) controller.cancelDialogConfirm(confirmToken)
    }
    background: Rectangle { color: Theme.bg; border.color: Theme.border; border.width: 1; radius: Theme.radSm }

    contentItem: ColumnLayout {
        spacing: 14
        Text {
            Layout.fillWidth: true
            text: dlg.dialogTitle
            color: Theme.textBright
            font.family: Theme.fontDisplay; font.pixelSize: Theme.fLg; font.weight: Font.Medium
            wrapMode: Text.Wrap
        }
        Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }

        TextArea {
            visible: dlg.mode === "info"
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(260, Math.max(90, contentHeight + 18))
            text: dlg.body
            readOnly: true
            wrapMode: TextArea.Wrap
            color: Theme.text
            selectedTextColor: Theme.bg
            selectionColor: Theme.accent
            font.family: Theme.fontBody; font.pixelSize: Theme.fSm
            background: Rectangle { color: Theme.surface; border.color: Theme.borderSoft; radius: Theme.radSm }
        }

        TextArea {
            visible: dlg.mode === "confirm"
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(160, Math.max(70, contentHeight + 18))
            text: dlg.body
            readOnly: true
            wrapMode: TextArea.Wrap
            color: Theme.text
            selectedTextColor: Theme.bg
            selectionColor: Theme.accent
            font.family: Theme.fontBody; font.pixelSize: Theme.fSm
            background: Rectangle { color: Theme.surface; border.color: Theme.warn; radius: Theme.radSm }
        }

        ColumnLayout {
            visible: dlg.mode === "api"
            Layout.fillWidth: true
            spacing: 8
            Text { text: "API Key"; color: Theme.textDim; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro }
            TextField {
                id: apiField
                Layout.fillWidth: true
                echoMode: TextInput.Password
                color: Theme.textBright
                placeholderText: "sk-..."
                placeholderTextColor: Theme.textMute
                font.family: Theme.fontMono; font.pixelSize: Theme.fSm
                background: Rectangle { color: Theme.surface; border.color: apiField.activeFocus ? Theme.accentLine : Theme.border; radius: Theme.radSm }
            }
        }

        ColumnLayout {
            visible: dlg.mode === "custom"
            Layout.fillWidth: true
            spacing: 10
            LabeledField { id: urlField; label: "API URL"; placeholder: "https://api.example.com/v1" }
            LabeledField { id: customKeyField; label: "API Key"; password: true; placeholder: "optional" }
            LabeledField { id: modelField; label: "Model"; placeholder: "model-name" }
            LabeledField { id: contextField; label: "Max context window"; placeholder: "128000" }
            RowLayout {
                spacing: 8
                Text { text: "Protocol"; color: Theme.textDim; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro }
                Item { Layout.fillWidth: true }
                Pill { label: "OpenAI"; active: !dlg.anthropic; onClicked: dlg.anthropic = false }
                Pill { label: "Anthropic"; active: dlg.anthropic; onClicked: dlg.anthropic = true }
            }
            RowLayout {
                spacing: 8
                Text { text: dlg.loc("图片输入"); color: Theme.textDim; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro }
                Item { Layout.fillWidth: true }
                Pill { label: dlg.loc("支持"); active: dlg.supportsVision; onClicked: dlg.supportsVision = true }
                Pill { label: dlg.loc("不支持"); active: !dlg.supportsVision; onClicked: dlg.supportsVision = false }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Item { Layout.fillWidth: true }
            Pill {
                label: dlg.loc("关闭")
                visible: dlg.mode === "info"
                accent: true
                onClicked: dlg.close()
            }
            Pill {
                label: dlg.loc("取消")
                visible: dlg.mode !== "info"
                onClicked: { if (dlg.mode === "confirm" && controller) controller.cancelDialogConfirm(dlg.confirmToken); dlg.close() }
            }
            Pill {
                label: dlg.loc("提交")
                visible: dlg.mode === "api"
                accent: true
                onClicked: { if (!controller || controller.submitApiKey(dlg.provider, apiField.text)) dlg.close() }
            }
            Pill {
                label: dlg.loc("提交")
                visible: dlg.mode === "custom"
                accent: true
                onClicked: { if (!controller || controller.submitCustomProvider(urlField.text, customKeyField.text, modelField.text, dlg.anthropic, contextField.text, dlg.supportsVision)) dlg.close() }
            }
            Pill {
                label: dlg.loc("确认")
                visible: dlg.mode === "confirm"
                accent: true
                onClicked: { if (controller) controller.acceptDialogConfirm(dlg.confirmToken); dlg.close() }
            }
        }
    }
}
