import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import HAgent

// 规则 / 插件 / 记忆 的独立弹窗外壳（复用 ManagementContent 主体）。
// 注：默认入口已迁到「设置」面板内嵌；此弹窗保留以兼容旧的直达调用。
Popup {
    id: panel
    property string mode: "rules"
    function loc(s) { if (controller && controller.lang) return controller.tr(s); return s }
    function title() { return mode === "rules" ? loc("规则编辑器") : mode === "plugins" ? loc("插件管理") : loc("记忆管理") }
    function openRules() { mode = "rules"; open() }
    function openPlugins() { mode = "plugins"; open() }
    function openMemory() { mode = "memory"; open() }
    function refresh() { body.refresh() }

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(820, parent ? parent.width - 32 : 820)
    height: Math.min(620, parent ? parent.height - 32 : 620)
    padding: 0
    modal: true
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    background: Rectangle { color: Theme.bg; border.color: Theme.border; border.width: 1; radius: Theme.radSm }

    contentItem: ColumnLayout {
        spacing: 0
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: Math.round(50 * Theme.scale)
            color: "transparent"
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.border }
            RowLayout {
                anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 12; spacing: 10
                Text { text: "◇"; color: Theme.accent; font.pixelSize: Theme.fLg }
                Text {
                    Layout.fillWidth: true
                    text: panel.title()
                    color: Theme.textBright
                    font.family: Theme.fontDisplay; font.pixelSize: Theme.fXl; font.weight: Font.Medium
                }
                Text {
                    text: body.statText
                    color: Theme.textMute
                    font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                }
                Pill { label: "×"; onClicked: panel.close() }
            }
        }
        ManagementContent {
            id: body
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.leftMargin: 12; Layout.rightMargin: 12
            Layout.topMargin: 8; Layout.bottomMargin: 12
            mode: panel.mode
            active: panel.visible
        }
    }
}
