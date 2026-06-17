import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import HAgent

// Standalone Houdini version picker — Mono Editorial styling, loaded as the
// root object of a QQuickWidget (see ui_qml/houdini_picker.py). Talks to the
// `picker` context object: picker.installs (list), choose(i), cancel(), beginMove().
Rectangle {
    id: root
    color: Theme.bg
    border.color: Theme.border
    border.width: 1
    radius: Theme.radSm

    property int selectedIndex: (picker && picker.installs.length > 0) ? 0 : -1
    readonly property bool hasInstalls: picker && picker.installs.length > 0

    function accept() {
        if (selectedIndex >= 0) picker.choose(selectedIndex)
    }

    Shortcut { sequence: "Escape"; onActivated: if (picker) picker.cancel() }
    Shortcut { sequence: "Return"; onActivated: root.accept() }
    Shortcut { sequence: "Enter";  onActivated: root.accept() }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 22
        spacing: 12

        // --- draggable chrome bar ---
        Item {
            Layout.fillWidth: true
            implicitHeight: 22
            MouseArea {
                anchors.fill: parent
                cursorShape: Qt.ArrowCursor
                onPressed: if (picker) picker.beginMove()
            }
            RowLayout {
                anchors.fill: parent
                Text {
                    text: "HOUDINI AGENT"
                    color: Theme.textMute
                    font.family: Theme.fontMono
                    font.pixelSize: Theme.fMicro
                    font.letterSpacing: Theme.trackLabel
                }
                Item { Layout.fillWidth: true }
                Text {
                    id: closeBtn
                    text: "×"
                    color: closeMa.containsMouse ? Theme.textBright : Theme.textMute
                    font.pixelSize: Theme.fXl
                    MouseArea {
                        id: closeMa
                        anchors.fill: parent
                        anchors.margins: -6
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: if (picker) picker.cancel()
                    }
                }
            }
        }

        // --- eyebrow + title + body ---
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Rectangle { width: 18; height: 1; color: Theme.accentLine }
            Text {
                text: "CONNECT"
                color: Theme.accent
                font.family: Theme.fontMono
                font.pixelSize: Theme.fMicro
                font.letterSpacing: Theme.trackLabel
            }
        }
        Text {
            Layout.fillWidth: true
            text: "选择要连接的 Houdini"
            color: Theme.textBright
            font.family: Theme.fontDisplay
            font.pixelSize: Theme.fXl
            font.weight: Font.Medium
            wrapMode: Text.Wrap
        }
        Text {
            Layout.fillWidth: true
            text: "完整的节点创建、场景读取与执行需要 Houdini Bridge。选择一个 Houdini 19.5 或更高版本，Agent 会自动启动并连接。"
            color: Theme.textDim
            font.family: Theme.fontBody
            font.pixelSize: Theme.fSm
            lineHeight: 1.3
            wrapMode: Text.Wrap
        }

        Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }

        // --- install list ---
        ListView {
            id: list
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: 6
            model: picker ? picker.installs : []
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

            delegate: Rectangle {
                width: ListView.view.width
                implicitHeight: 58
                radius: Theme.radSm
                property bool sel: index === root.selectedIndex
                color: sel ? Theme.accentSoft : (rowMa.containsMouse ? Theme.surface2 : Theme.surface)
                border.width: 1
                border.color: sel ? Theme.accentLine : Theme.borderSoft
                Behavior on color { ColorAnimation { duration: 100 } }

                ColumnLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 14
                    anchors.rightMargin: 14
                    anchors.topMargin: 9
                    anchors.bottomMargin: 9
                    spacing: 2
                    Text {
                        text: "Houdini " + (modelData.version || "")
                        color: sel ? Theme.textBright : Theme.text
                        font.family: Theme.fontDisplay
                        font.pixelSize: Theme.fMd
                        font.weight: Font.Medium
                    }
                    Text {
                        Layout.fillWidth: true
                        text: modelData.path || ""
                        color: Theme.textMute
                        font.family: Theme.fontMono
                        font.pixelSize: Theme.fXs
                        elide: Text.ElideMiddle
                    }
                }
                MouseArea {
                    id: rowMa
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.selectedIndex = index
                    onDoubleClicked: { root.selectedIndex = index; root.accept() }
                }
            }
        }

        // --- empty state ---
        Text {
            visible: !root.hasInstalls
            Layout.fillWidth: true
            Layout.fillHeight: true
            text: "未检测到 Houdini 19.5+"
            color: Theme.textMute
            font.family: Theme.fontBody
            font.pixelSize: Theme.fSm
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }

        // --- footer ---
        RowLayout {
            Layout.fillWidth: true
            spacing: 10
            Text {
                Layout.fillWidth: true
                text: "可以稍后从 Agent 再次连接 Houdini"
                color: Theme.textMute
                font.family: Theme.fontBody
                font.pixelSize: Theme.fXs
                wrapMode: Text.Wrap
            }

            // ghost: 稍后再说
            Rectangle {
                implicitWidth: laterTxt.implicitWidth + 28
                implicitHeight: 34
                radius: Theme.radSm
                color: laterMa.containsMouse ? Theme.surface : "transparent"
                border.width: 1
                border.color: laterMa.containsMouse ? Theme.border : Theme.borderSoft
                Behavior on color { ColorAnimation { duration: 120 } }
                Text {
                    id: laterTxt
                    anchors.centerIn: parent
                    text: "稍后再说"
                    color: Theme.textDim
                    font.family: Theme.fontBody
                    font.pixelSize: Theme.fSm
                }
                MouseArea {
                    id: laterMa
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: if (picker) picker.cancel()
                }
            }

            // primary: 打开 Houdini
            Rectangle {
                id: openBtn
                enabled: root.selectedIndex >= 0
                implicitWidth: openTxt.implicitWidth + 32
                implicitHeight: 34
                radius: Theme.radSm
                opacity: enabled ? 1.0 : 0.4
                color: !enabled ? Theme.surface
                     : (openMa.containsMouse ? Theme.textBright : Theme.accent)
                Behavior on color { ColorAnimation { duration: 120 } }
                Text {
                    id: openTxt
                    anchors.centerIn: parent
                    text: "打开 Houdini"
                    color: Theme.bg
                    font.family: Theme.fontBody
                    font.pixelSize: Theme.fSm
                    font.weight: Font.Medium
                }
                MouseArea {
                    id: openMa
                    anchors.fill: parent
                    enabled: openBtn.enabled
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.accept()
                }
            }
        }
    }
}
