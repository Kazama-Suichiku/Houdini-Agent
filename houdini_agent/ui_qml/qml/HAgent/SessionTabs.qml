import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import HAgent

// 会话管理（方案A）：窄面板里只显示当前会话名 + 会话数 + 新建；
// 点会话名弹出可滚动的竖向列表（搜索 / 新建 / hover 删除）。彻底取代会溢出窗口的横向条。
Rectangle {
    id: tabs
    color: "transparent"
    implicitHeight: Math.round(38 * Theme.scale)

    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.border }

    function loc(s) { return controller ? (controller.lang, controller.tr(s)) : s }

    property var items: controller ? controller.sessionItems() : []
    property string filterText: ""
    Connections {
        target: controller
        function onSessionsChanged() { tabs.items = controller.sessionItems() }
    }

    function activeItem() {
        for (var i = 0; i < items.length; i++) if (items[i].active) return items[i]
        return items.length ? items[0] : null
    }
    function filtered() {
        var out = [], q = filterText.toLowerCase()
        for (var i = 0; i < items.length; i++) {
            var it = items[i]
            if (q.length === 0 || (it.title || "").toLowerCase().indexOf(q) >= 0)
                out.push({ title: it.title, time: it.time, active: it.active, idx: i })
        }
        return out
    }

    // ---------- compact bar ----------
    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 12; anchors.rightMargin: 10
        spacing: 8

        // current session + caret -> open popup
        Rectangle {
            id: cur
            Layout.fillWidth: true
            Layout.preferredHeight: Math.round(28 * Theme.scale)
            radius: Theme.radSm
            color: curMa.containsMouse ? Theme.surface : "transparent"
            border.width: 1
            border.color: curMa.containsMouse ? Theme.border : "transparent"
            Behavior on color { ColorAnimation { duration: 120 } }
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 8; anchors.rightMargin: 8
                spacing: 6
                Text {
                    Layout.fillWidth: true
                    text: { var a = tabs.activeItem(); return a ? (a.title || "").toUpperCase() : "" }
                    color: Theme.text
                    font.family: Theme.fontMono; font.pixelSize: Theme.fXs
                    font.letterSpacing: Theme.trackLabel
                    elide: Text.ElideRight
                }
                Text { text: "▾"; color: Theme.textMute; font.pixelSize: Theme.fMicro }
            }
            MouseArea {
                id: curMa; anchors.fill: parent; hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: { tabs.filterText = ""; sessionsPopup.open() }
            }
        }

        Text {
            text: tabs.items.length + " " + tabs.loc("会话")
            color: Theme.textMute
            font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
            font.letterSpacing: 1
        }

        // new session
        Rectangle {
            id: plusBtn
            Layout.preferredWidth: Math.round(26 * Theme.scale)
            Layout.preferredHeight: Math.round(26 * Theme.scale)
            radius: Theme.radSm
            color: plusMa.containsMouse ? Theme.surface : "transparent"
            border.width: 1
            border.color: plusMa.containsMouse ? Theme.border : "transparent"
            Text { anchors.centerIn: parent; text: "+"; color: plusMa.containsMouse ? Theme.text : Theme.textMute
                   font.pixelSize: Theme.fMd }
            MouseArea {
                id: plusMa; anchors.fill: parent; hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: if (controller) controller.newSession()
            }
        }
    }

    // ---------- sessions popup ----------
    Popup {
        id: sessionsPopup
        y: tabs.height + 2
        x: 10
        width: tabs.width - 20
        padding: 0
        modal: false
        focus: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background: Rectangle { color: Theme.panel; border.color: Theme.border; border.width: 1; radius: 4 }

        contentItem: ColumnLayout {
            spacing: 0

            // search
            RowLayout {
                Layout.fillWidth: true
                Layout.margins: 0
                Layout.leftMargin: 11; Layout.rightMargin: 11
                Layout.topMargin: 9; Layout.bottomMargin: 9
                spacing: 7
                Text { text: "⌕"; color: Theme.textMute; font.pixelSize: Theme.fSm }
                TextField {
                    id: searchField
                    Layout.fillWidth: true
                    placeholderText: tabs.loc("搜索会话…")
                    placeholderTextColor: Theme.textMute
                    color: Theme.text
                    font.family: Theme.fontBody; font.pixelSize: Theme.fSm
                    background: null
                    onTextChanged: tabs.filterText = text
                }
            }
            Rectangle { Layout.fillWidth: true; height: 1; color: Theme.borderSoft }

            // list
            ListView {
                id: list
                Layout.fillWidth: true
                Layout.preferredHeight: Math.min(contentHeight, Math.round(300 * Theme.scale))
                Layout.margins: 5
                clip: true
                model: tabs.filtered()
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                delegate: Rectangle {
                    id: row
                    required property var modelData
                    width: ListView.view.width
                    height: Math.round(38 * Theme.scale)
                    radius: Theme.radSm
                    color: modelData.active ? Theme.accentSoft
                          : (rowMa.containsMouse ? Theme.surface : "transparent")
                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 10; anchors.rightMargin: 8
                        spacing: 8
                        Rectangle {
                            width: 5; height: 5; radius: 2.5
                            color: Theme.accent
                            opacity: row.modelData.active ? 1 : 0
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 1
                            Text {
                                Layout.fillWidth: true
                                text: row.modelData.title || ""
                                color: row.modelData.active ? Theme.accent : Theme.text
                                font.family: Theme.fontBody; font.pixelSize: Theme.fSm
                                elide: Text.ElideRight
                            }
                            Text {
                                visible: (row.modelData.time || "").length > 0
                                text: row.modelData.time || ""
                                color: Theme.textMute
                                font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                            }
                        }
                        // hover-reveal delete
                        Rectangle {
                            id: del
                            Layout.preferredWidth: Math.round(24 * Theme.scale)
                            Layout.preferredHeight: Math.round(24 * Theme.scale)
                            radius: Theme.radSm
                            visible: tabs.items.length > 1
                            opacity: (rowMa.containsMouse || delMa.containsMouse) ? 1 : 0
                            Behavior on opacity { NumberAnimation { duration: 110 } }
                            color: delMa.containsMouse ? Qt.rgba(0.867, 0.6, 0.6, 0.14) : "transparent"
                            TrashIcon {
                                anchors.centerIn: parent
                                size: Math.round(14 * Theme.scale)
                                color: delMa.containsMouse ? Theme.err : Theme.textMute
                            }
                            MouseArea {
                                id: delMa; anchors.fill: parent; hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: {
                                    if (!controller) return
                                    if (controller.askDeleteSession) {
                                        delDlg.targetIdx = row.modelData.idx
                                        delDlg.targetName = row.modelData.title || ""
                                        delDlg.dontAsk = false
                                        delDlg.open()
                                    } else {
                                        controller.deleteSession(row.modelData.idx)
                                    }
                                }
                            }
                        }
                    }
                    MouseArea {
                        id: rowMa
                        anchors.fill: parent
                        anchors.rightMargin: Math.round(34 * Theme.scale)   // leave delete hit-area
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: { if (controller) controller.switchSession(row.modelData.idx); sessionsPopup.close() }
                    }
                }

                // empty state
                Text {
                    anchors.centerIn: parent
                    visible: list.count === 0
                    text: tabs.loc("没有匹配的会话")
                    color: Theme.textMute
                    font.family: Theme.fontBody; font.pixelSize: Theme.fSm
                }
            }

            Rectangle { Layout.fillWidth: true; height: 1; color: Theme.borderSoft }
            // new session row
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: Math.round(38 * Theme.scale)
                color: newMa.containsMouse ? Theme.accentSoft : "transparent"
                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 11; spacing: 8
                    Text { text: "+"; color: Theme.accent; font.pixelSize: Theme.fMd }
                    Text {
                        text: tabs.loc("新建会话")
                        color: Theme.accent
                        font.family: Theme.fontMono; font.pixelSize: Theme.fXs
                        font.letterSpacing: Theme.trackLabel
                    }
                }
                MouseArea {
                    id: newMa; anchors.fill: parent; hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: { if (controller) controller.newSession(); sessionsPopup.close() }
                }
            }
        }
    }

    // ---------- delete confirm dialog (with "don't ask again") ----------
    Popup {
        id: delDlg
        property int targetIdx: -1
        property string targetName: ""
        property bool dontAsk: false

        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(340, parent ? parent.width - 36 : 340)
        padding: 20
        modal: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background: Rectangle { color: Theme.panel; border.color: Theme.border; border.width: 1; radius: 4 }

        contentItem: ColumnLayout {
            spacing: 0
            Text {
                Layout.fillWidth: true
                text: tabs.loc("删除会话")
                color: Theme.textBright
                font.family: Theme.fontDisplay; font.pixelSize: Theme.fXl; font.weight: Font.Medium
            }
            Text {
                Layout.fillWidth: true
                Layout.topMargin: 9
                text: "「" + delDlg.targetName + "」 " + tabs.loc("此操作无法撤销。")
                color: Theme.textDim
                font.family: Theme.fontBody; font.pixelSize: Theme.fSm
                wrapMode: Text.Wrap; lineHeight: 1.3
            }

            // don't-ask checkbox (whole row clickable; MouseArea anchored inside a
            // plain Item so it isn't layout-managed)
            Item {
                Layout.fillWidth: true
                Layout.topMargin: 16; Layout.bottomMargin: 18
                implicitHeight: chkRow.implicitHeight
                Row {
                    id: chkRow
                    anchors.left: parent.left; anchors.right: parent.right
                    spacing: 9
                    Rectangle {
                        width: 16; height: 16; radius: Theme.radSm
                        color: delDlg.dontAsk ? Theme.accent : "transparent"
                        border.width: 1; border.color: delDlg.dontAsk ? Theme.accent : Theme.border
                        Text { anchors.centerIn: parent; visible: delDlg.dontAsk; text: "✓"
                               color: Theme.bg; font.pixelSize: Theme.fMicro; font.bold: true }
                    }
                    Text {
                        width: chkRow.width - 25
                        text: tabs.loc("以后删除不再询问（可在 设置 › 会话 中恢复）")
                        color: Theme.textMute
                        font.family: Theme.fontBody; font.pixelSize: Theme.fXs
                        wrapMode: Text.Wrap
                    }
                }
                MouseArea {
                    anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                    onClicked: delDlg.dontAsk = !delDlg.dontAsk
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Item { Layout.fillWidth: true }
                Pill { label: tabs.loc("取消"); onClicked: delDlg.close() }
                // danger-styled delete
                Rectangle {
                    Layout.preferredHeight: Math.round(28 * Theme.scale)
                    implicitWidth: delTxt.implicitWidth + 24
                    radius: Theme.radSm
                    color: dma.containsMouse ? Qt.rgba(0.867, 0.6, 0.6, 0.14) : "transparent"
                    border.width: 1; border.color: Qt.rgba(0.867, 0.6, 0.6, 0.45)
                    Text { id: delTxt; anchors.centerIn: parent; text: tabs.loc("删除")
                           color: Theme.err; font.family: Theme.fontBody; font.pixelSize: Theme.fSm }
                    MouseArea {
                        id: dma; anchors.fill: parent; hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            if (controller) {
                                if (delDlg.dontAsk) controller.setAskDeleteSession(false)
                                controller.deleteSession(delDlg.targetIdx)
                            }
                            delDlg.close()
                        }
                    }
                }
            }
        }
        onClosed: { sessionsPopup.close() }
    }
}
