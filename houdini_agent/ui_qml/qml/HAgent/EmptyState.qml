import QtQuick
import QtQuick.Layouts
import HAgent

// 空会话起手式：Meshy 生成排在最前（品牌绿），Houdini 程序化当配角。
// 点卡片把模板填进输入框（占位符取默认值），不直接发送。
Item {
    id: es
    implicitHeight: col.implicitHeight

    function loc(s) { return controller ? (controller.lang, controller.tr(s)) : s }
    // {{名称:默认值}} -> 默认值（纯文本预览）
    function plain(b) {
        return String(b || "").replace(/\{\{([^:}]+):?([^}]*)\}\}/g,
            function (_, n, d) { return d || n })
    }
    // 随语言刷新内置数据
    property var meshy: controller ? (controller.lang, JSON.parse(controller.starterMeshy())) : []
    property var hou:   controller ? (controller.lang, JSON.parse(controller.starterHou()))   : []
    property var post:  controller ? (controller.lang, JSON.parse(controller.templateLibrary()).meshy.filter(function (x) { return !x.scratch })) : []

    ColumnLayout {
        id: col
        width: es.width
        spacing: 0

        Text {
            Layout.fillWidth: true
            text: es.loc("先出一个模型，再接着做。")
            color: Theme.textBright; font.family: Theme.fontDisplay
            font.pixelSize: Theme.fXl; font.weight: Font.Medium; wrapMode: Text.Wrap
        }
        Text {
            Layout.fillWidth: true; Layout.topMargin: 7
            text: es.loc("点下面的起手式填进输入框，参数改完直接发；也可以不用，自己输入。")
            color: Theme.textMute; font.family: Theme.fontBody; font.pixelSize: Theme.fXs
            wrapMode: Text.Wrap; lineHeight: 1.4
        }

        // ---- Meshy 特写区块 ----
        Rectangle {
            Layout.fillWidth: true; Layout.topMargin: 20
            implicitHeight: featCol.implicitHeight + 28
            radius: Theme.radSm; color: "transparent"
            border.width: 1; border.color: Theme.meshyLine
            gradient: Gradient {
                GradientStop { position: 0.0; color: Theme.meshySoft }
                GradientStop { position: 0.7; color: "transparent" }
            }
            ColumnLayout {
                id: featCol
                x: 14; y: 14; width: parent.width - 28; spacing: 0
                RowLayout {
                    Layout.fillWidth: true; spacing: 9
                    Text {
                        text: "Meshy · 3D " + (controller && controller.lang === "en" ? "generation" : "生成")
                        color: Theme.meshy; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                        font.letterSpacing: Theme.trackLabel
                    }
                    Text {
                        Layout.fillWidth: true
                        text: es.loc("从文字、图片或概念图生成，直接进场景")
                        color: Theme.textMute; font.family: Theme.fontBody; font.pixelSize: Theme.fMicro
                        elide: Text.ElideRight
                    }
                }
                Flow {
                    Layout.fillWidth: true; Layout.topMargin: 12; spacing: 8
                    Repeater {
                        model: es.meshy
                        delegate: Rectangle {
                            id: mc
                            required property var modelData
                            width: parent.width >= 380 ? (parent.width - 8) / 2 : parent.width
                            implicitHeight: mcCol.implicitHeight + 22
                            radius: Theme.radSm
                            color: mcMa.containsMouse ? Theme.meshySoft : Qt.rgba(0, 0, 0, 0.18)
                            border.width: 1; border.color: mcMa.containsMouse ? Theme.meshyLine : Theme.border
                            ColumnLayout {
                                id: mcCol
                                x: 12; y: 11; width: parent.width - 24; spacing: 6
                                Text { text: mc.modelData.ty; color: Theme.meshy
                                       font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                                       font.letterSpacing: 1.2 }
                                Text { Layout.fillWidth: true; text: mc.modelData.t; color: Theme.text
                                       font.family: Theme.fontBody; font.pixelSize: Theme.fMd; wrapMode: Text.Wrap }
                                Text { Layout.fillWidth: true; text: es.plain(mc.modelData.body)
                                       color: Theme.textMute; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                                       wrapMode: Text.Wrap; lineHeight: 1.4 }
                                Text {
                                    visible: mc.modelData.note && mc.modelData.note.length > 0
                                    Layout.fillWidth: true
                                    text: "! " + (mc.modelData.note || "")
                                    color: Theme.meshy; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                                    wrapMode: Text.Wrap
                                }
                            }
                            MouseArea { id: mcMa; anchors.fill: parent; hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: if (controller) controller.insertTemplate(mc.modelData.body) }
                        }
                    }
                }
                // 后处理提示（需已有模型）→ 指向模板库
                Text {
                    Layout.fillWidth: true; Layout.topMargin: 12
                    visible: es.post.length > 0
                    text: {
                        var names = es.post.map(function (x) { return x.t })
                        return es.loc("有了模型，还能：") + names.join(" · ")
                    }
                    color: Theme.textMute; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                    wrapMode: Text.Wrap; lineHeight: 1.4
                }
            }
        }

        // ---- Houdini 配角 ----
        RowLayout {
            Layout.fillWidth: true; Layout.topMargin: 22
            Text { text: es.loc("Houdini 程序化"); color: Theme.textMute
                   font.family: Theme.fontMono; font.pixelSize: Theme.fMicro; font.letterSpacing: Theme.trackLabel }
            Item { Layout.fillWidth: true }
            Text { text: es.loc("有了模型，用 Houdini 接着做"); color: Theme.textMute
                   font.family: Theme.fontBody; font.pixelSize: Theme.fMicro; elide: Text.ElideRight }
        }
        Rectangle { Layout.fillWidth: true; Layout.topMargin: 7; height: 1; color: Theme.borderSoft }
        Flow {
            Layout.fillWidth: true; Layout.topMargin: 11; spacing: 7
            Repeater {
                model: es.hou
                delegate: Rectangle {
                    id: hc
                    required property var modelData
                    implicitHeight: Math.round(30 * Theme.scale)
                    implicitWidth: hcRow.implicitWidth + 22
                    radius: Theme.radSm
                    color: hcMa.containsMouse ? Theme.surface : "transparent"
                    border.width: 1; border.color: hcMa.containsMouse ? Theme.accentLine : Theme.border
                    Row {
                        id: hcRow; anchors.centerIn: parent; spacing: 7
                        Text { anchors.verticalCenter: parent.verticalCenter; text: hc.modelData.cat
                               color: Theme.textMute; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro }
                        Text { anchors.verticalCenter: parent.verticalCenter; text: hc.modelData.t
                               color: Theme.textDim; font.family: Theme.fontBody; font.pixelSize: Theme.fSm }
                    }
                    MouseArea { id: hcMa; anchors.fill: parent; hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: if (controller) controller.insertTemplate(hc.modelData.body) }
                }
            }
        }

        // ---- 浏览全部 ----
        Item {
            Layout.fillWidth: true; Layout.topMargin: 18; Layout.bottomMargin: 4
            implicitHeight: Math.round(34 * Theme.scale)
            Rectangle {
                anchors.centerIn: parent
                width: baTxt.implicitWidth + 32; height: parent.height
                radius: Theme.radSm; color: baMa.containsMouse ? Theme.surface : "transparent"
                border.width: 1; border.color: Theme.border
                Text { id: baTxt; anchors.centerIn: parent
                       text: es.loc("浏览全部模板") + "  →"
                       color: Theme.textDim; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                       font.letterSpacing: 1.2 }
                MouseArea { id: baMa; anchors.fill: parent; hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: if (controller) controller.openTemplateLibrary() }
            }
        }
    }
}
