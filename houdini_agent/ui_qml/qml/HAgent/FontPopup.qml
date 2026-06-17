import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import HAgent

// Font-size slider (Editorial styled).
Popup {
    id: fp
    function loc(s) { return controller ? (controller.lang, controller.tr(s)) : s }
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: 300
    padding: 18
    modal: true
    closePolicy: Popup.CloseOnPressOutside | Popup.CloseOnEscape
    background: Rectangle { color: Theme.bg; border.color: Theme.border; border.width: 1; radius: Theme.radSm }

    contentItem: ColumnLayout {
        spacing: 16
        Text {
            text: fp.loc("字号")
            color: Theme.textBright
            font.family: Theme.fontDisplay; font.pixelSize: Theme.fLg; font.weight: Font.Medium
        }
        RowLayout {
            spacing: 12
            Slider {
                id: sld
                Layout.fillWidth: true
                from: 0.7; to: 1.6; stepSize: 0.05
                value: controller ? controller.fontScale : 1.0
                onMoved: if (controller) controller.setFontScale(value)
                background: Rectangle {
                    x: sld.leftPadding; y: sld.topPadding + sld.availableHeight / 2 - 1
                    width: sld.availableWidth; height: 2; color: Theme.border
                    Rectangle { width: sld.visualPosition * parent.width; height: parent.height; color: Theme.accent }
                }
                handle: Rectangle {
                    x: sld.leftPadding + sld.visualPosition * (sld.availableWidth - width)
                    y: sld.topPadding + sld.availableHeight / 2 - height / 2
                    width: 14; height: 14; radius: 2; color: Theme.accent; border.color: Theme.bg; border.width: 1
                }
            }
            Text {
                Layout.preferredWidth: 44
                text: Math.round((controller ? controller.fontScale : 1) * 100) + "%"
                color: Theme.text; font.family: Theme.fontMono; font.pixelSize: Theme.fSm
            }
        }
        RowLayout {
            Item { Layout.fillWidth: true }
            Pill { label: fp.loc("重置"); onClicked: if (controller) controller.setFontScale(1.0) }
            Pill { label: fp.loc("关闭"); accent: true; onClicked: fp.close() }
        }
    }
}
