import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import HAgent

// 模板库浮层：左侧分类（Meshy 绿在前）+ 右侧模板列表 + 新建/我的模板。
// 插入即填进输入框并关闭；「返回对话」或 Esc / 点外部也可关。
Popup {
    id: lib
    function loc(s) { return controller ? (controller.lang, controller.tr(s)) : s }

    property var data: ({ meshy: [], hou: [], mine: [] })
    property string cat: "meshy"
    property bool formOpen: false

    function refresh() {
        if (!controller) return
        try { data = JSON.parse(controller.templateLibrary()) } catch (e) { }
    }
    function houCats() {
        var seen = [], out = []
        for (var i = 0; i < data.hou.length; i++) {
            var c = data.hou[i].cat
            if (seen.indexOf(c) < 0) { seen.push(c); out.push(c) }
        }
        return out
    }
    function rowsFor(k) {
        if (k === "meshy") return data.meshy
        if (k === "mine") return data.mine
        return data.hou.filter(function (x) { return x.cat === k })
    }
    function plain(b) {
        return String(b || "").replace(/\{\{([^:}]+):?([^}]*)\}\}/g,
            function (_, n, d) { return d || n })
    }
    function catLabel(k) {
        return k === "meshy" ? "Meshy" : k === "mine" ? loc("我的模板") : k
    }

    onAboutToShow: { refresh(); cat = "meshy"; formOpen = false }
    Connections {
        target: controller
        ignoreUnknownSignals: true
        function onTemplatesChanged() { lib.refresh() }
        function onLangChanged() { lib.refresh() }
    }

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(Math.round(720 * Theme.scale), parent ? parent.width - 18 : 720)
    height: Math.min(Math.round(560 * Theme.scale), parent ? parent.height - 18 : 560)
    padding: 0
    modal: true
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    background: Rectangle { color: Theme.panel; border.color: Theme.border; border.width: 1; radius: 6 }

    contentItem: RowLayout {
        spacing: 0

        // ---------- left nav ----------
        Rectangle {
            Layout.preferredWidth: Math.round(150 * Theme.scale)
            Layout.fillHeight: true
            color: Theme.panelDeep
            Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: Theme.borderSoft }
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 10; spacing: 2
                Repeater {
                    model: ["meshy"].concat(lib.houCats()).concat(["mine"])
                    delegate: Rectangle {
                        id: nav
                        required property var modelData
                        property bool isMeshy: modelData === "meshy"
                        Layout.fillWidth: true
                        implicitHeight: Math.round(32 * Theme.scale)
                        radius: Theme.radSm
                        color: lib.cat === modelData ? Theme.surface2 : (nMa.containsMouse ? Theme.surface : "transparent")
                        RowLayout {
                            anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 9
                            Text {
                                Layout.fillWidth: true
                                text: lib.catLabel(nav.modelData)
                                color: nav.isMeshy ? Theme.meshy
                                     : (lib.cat === nav.modelData ? Theme.text : Theme.textDim)
                                font.family: Theme.fontBody; font.pixelSize: Theme.fSm
                            }
                            Text {
                                text: lib.rowsFor(nav.modelData).length
                                color: Theme.textMute; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                            }
                        }
                        MouseArea { id: nMa; anchors.fill: parent; hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: { lib.cat = nav.modelData; lib.formOpen = false } }
                    }
                }
                Item { Layout.fillHeight: true }
            }
        }

        // ---------- right body ----------
        ColumnLayout {
            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 0

            // header: back + title + new
            RowLayout {
                Layout.fillWidth: true
                Layout.leftMargin: 16; Layout.rightMargin: 14
                Layout.topMargin: 14; Layout.bottomMargin: 12
                spacing: 12
                Text {
                    text: "← " + lib.loc("返回对话")
                    color: backMa.containsMouse ? Theme.accent : Theme.textMute
                    font.family: Theme.fontMono; font.pixelSize: Theme.fMicro; font.letterSpacing: 1.2
                    MouseArea { id: backMa; anchors.fill: parent; anchors.margins: -6; hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor; onClicked: lib.close() }
                }
                Text {
                    text: lib.catLabel(lib.cat)
                    color: lib.cat === "meshy" ? Theme.meshy : Theme.textBright
                    font.family: Theme.fontDisplay; font.pixelSize: Theme.fLg; font.weight: Font.Medium
                }
                Item { Layout.fillWidth: true }
                Rectangle {
                    implicitHeight: Math.round(28 * Theme.scale); implicitWidth: nbTxt.implicitWidth + 22
                    radius: Theme.radSm; color: nbMa.containsMouse ? Theme.surface : "transparent"
                    border.width: 1; border.color: Theme.accentLine
                    Text { id: nbTxt; anchors.centerIn: parent; text: "+ " + lib.loc("新建模板")
                           color: Theme.accent; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro }
                    MouseArea { id: nbMa; anchors.fill: parent; hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor; onClicked: lib.formOpen = !lib.formOpen }
                }
            }
            Rectangle { Layout.fillWidth: true; height: 1; color: Theme.borderSoft }

            // scrollable list
            ScrollView {
                Layout.fillWidth: true; Layout.fillHeight: true
                clip: true; contentWidth: availableWidth
                Item {
                    width: parent.width
                    implicitHeight: bodyCol.implicitHeight + 28
                    ColumnLayout {
                        id: bodyCol
                        x: 16; y: 14; width: parent.width - 32; spacing: 9

                        // new-template form
                        Rectangle {
                            Layout.fillWidth: true
                            visible: lib.formOpen
                            implicitHeight: visible ? formCol.implicitHeight + 28 : 0
                            radius: Theme.radSm; color: Theme.surface
                            border.width: 1; border.color: Theme.accentLine
                            ColumnLayout {
                                id: formCol
                                x: 14; y: 14; width: parent.width - 28; spacing: 7
                                function fl(t) { return t }
                                Text { text: lib.loc("名称"); color: Theme.textMute
                                       font.family: Theme.fontMono; font.pixelSize: Theme.fMicro; font.letterSpacing: 1.4 }
                                TextField {
                                    id: fName; Layout.fillWidth: true
                                    placeholderText: lib.loc("例如：我的破碎参数")
                                    color: Theme.text; font.family: Theme.fontBody; font.pixelSize: Theme.fSm
                                    background: Rectangle { color: Theme.codeBg; border.color: Theme.border; border.width: 1; radius: Theme.radSm }
                                }
                                Text { text: lib.loc("内容"); color: Theme.textMute; Layout.topMargin: 4
                                       font.family: Theme.fontMono; font.pixelSize: Theme.fMicro; font.letterSpacing: 1.4 }
                                TextArea {
                                    id: fBody; Layout.fillWidth: true
                                    Layout.preferredHeight: Math.round(66 * Theme.scale)
                                    placeholderText: lib.loc("比如：生成一个 {{物体:宝箱}}，{{风格:写实}}风格，导进场景。")
                                    wrapMode: TextArea.Wrap
                                    color: Theme.text; font.family: Theme.fontMono; font.pixelSize: Theme.fSm
                                    background: Rectangle { color: Theme.codeBg; border.color: Theme.border; border.width: 1; radius: Theme.radSm }
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: lib.loc("想留个能改的空，就写 {{名称:默认值}}，插入时点一下就能改。")
                                    color: Theme.textMute; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                                    wrapMode: Text.Wrap; lineHeight: 1.4
                                }
                                RowLayout {
                                    Layout.fillWidth: true; Layout.topMargin: 4; spacing: 8
                                    Item { Layout.fillWidth: true }
                                    Pill { label: lib.loc("取消"); onClicked: lib.formOpen = false }
                                    Pill {
                                        label: lib.loc("保存"); accent: true
                                        onClicked: {
                                            if (!controller) return
                                            controller.saveTemplate(JSON.stringify({ name: fName.text, body: fBody.text }))
                                            fName.text = ""; fBody.text = ""
                                            lib.formOpen = false; lib.cat = "mine"
                                        }
                                    }
                                }
                            }
                        }

                        // rows
                        Repeater {
                            model: lib.rowsFor(lib.cat)
                            delegate: Rectangle {
                                id: row
                                required property var modelData
                                Layout.fillWidth: true
                                implicitHeight: Math.max(rowInfo.implicitHeight + 26, Math.round(48 * Theme.scale))
                                radius: Theme.radSm; color: "transparent"
                                border.width: 1; border.color: Theme.borderSoft
                                RowLayout {
                                    anchors.fill: parent; anchors.leftMargin: 13; anchors.rightMargin: 12
                                    anchors.topMargin: 12; anchors.bottomMargin: 12; spacing: 12
                                    ColumnLayout {
                                        id: rowInfo
                                        Layout.fillWidth: true; spacing: 4
                                        Text {
                                            visible: row.modelData.ty !== undefined && row.modelData.ty !== ""
                                            text: row.modelData.ty || ""
                                            color: Theme.meshy; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro; font.letterSpacing: 1.2
                                        }
                                        Text { Layout.fillWidth: true; text: row.modelData.t; color: Theme.text
                                               font.family: Theme.fontBody; font.pixelSize: Theme.fMd; wrapMode: Text.Wrap }
                                        Text { Layout.fillWidth: true; text: lib.plain(row.modelData.body)
                                               color: Theme.textMute; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                                               wrapMode: Text.Wrap; lineHeight: 1.4 }
                                        Text {
                                            visible: row.modelData.note && row.modelData.note.length > 0
                                            Layout.fillWidth: true; text: "! " + (row.modelData.note || "")
                                            color: Theme.meshy; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro; wrapMode: Text.Wrap
                                        }
                                    }
                                    // insert
                                    Rectangle {
                                        Layout.alignment: Qt.AlignVCenter
                                        implicitHeight: Math.round(28 * Theme.scale); implicitWidth: insTxt.implicitWidth + 22
                                        radius: Theme.radSm
                                        property bool green: lib.cat === "meshy"
                                        color: green ? (insMa.containsMouse ? Theme.meshyHover : Theme.meshy)
                                                     : (insMa.containsMouse ? Theme.surface : Theme.surface2)
                                        border.width: 1; border.color: green ? Theme.meshy : Theme.border
                                        Text { id: insTxt; anchors.centerIn: parent; text: lib.loc("插入")
                                               color: parent.green ? Theme.meshyInk : Theme.text
                                               font.family: Theme.fontMono; font.pixelSize: Theme.fMicro; font.letterSpacing: 1.2 }
                                        MouseArea { id: insMa; anchors.fill: parent; hoverEnabled: true
                                            cursorShape: Qt.PointingHandCursor
                                            onClicked: { if (controller) controller.insertTemplate(row.modelData.body); lib.close() } }
                                    }
                                    // delete (mine only)
                                    Rectangle {
                                        Layout.alignment: Qt.AlignVCenter
                                        visible: lib.cat === "mine"
                                        implicitHeight: Math.round(28 * Theme.scale); implicitWidth: Math.round(34 * Theme.scale)
                                        radius: Theme.radSm; color: delMa.containsMouse ? Theme.surface : "transparent"
                                        border.width: 1; border.color: Theme.border
                                        TrashIcon { anchors.centerIn: parent; width: 13; height: 13 }
                                        MouseArea { id: delMa; anchors.fill: parent; hoverEnabled: true
                                            cursorShape: Qt.PointingHandCursor
                                            onClicked: if (controller) controller.deleteTemplate(row.modelData.id) }
                                    }
                                }
                            }
                        }

                        // empty hint
                        Text {
                            Layout.fillWidth: true; Layout.topMargin: 6
                            visible: lib.rowsFor(lib.cat).length === 0 && !lib.formOpen
                            text: lib.loc("还没有模板，点右上「新建模板」。")
                            color: Theme.textMute; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                        }
                    }
                }
            }
        }
    }
}
