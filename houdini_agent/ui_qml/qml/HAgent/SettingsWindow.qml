import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import HAgent

// 统一设置浮窗（第一版）：左侧分类导航 + 右侧内容。大量复用 controller.menuAction 既有派发，
// 把散落在 ⋯ 菜单里的设置归并到一处。窄面板自适应缩放。
Popup {
    id: win
    function loc(s) { return controller ? (controller.lang, controller.tr(s)) : s }

    property string sec: "sessions"

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(Math.round(720 * Theme.scale), parent ? parent.width - 18 : 720)
    height: Math.min(Math.round(560 * Theme.scale), parent ? parent.height - 18 : 560)
    padding: 0
    modal: true
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    background: Rectangle { color: Theme.panel; border.color: Theme.border; border.width: 1; radius: 6 }

    // ---- reusable inline pieces ----
    component Toggle: Rectangle {
        id: tg
        property bool on: false
        signal toggled()
        width: 38; height: 21; radius: 11
        color: on ? Theme.accentSoft : Theme.surface2
        border.width: 1; border.color: on ? Theme.accentLine : Theme.border
        Rectangle {
            y: 1; width: 17; height: 17; radius: 8.5
            x: tg.on ? 18 : 1
            color: tg.on ? Theme.accent : Theme.textMute
            Behavior on x { NumberAnimation { duration: 130; easing.type: Easing.OutCubic } }
        }
        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: tg.toggled() }
    }

    component FieldRow: ColumnLayout {
        property string name: ""
        property string desc: ""
        default property alias content: slot.data
        Layout.fillWidth: true
        spacing: 0
        RowLayout {
            Layout.fillWidth: true
            Layout.topMargin: 14; Layout.bottomMargin: 14
            spacing: 14
            ColumnLayout {
                Layout.fillWidth: true; spacing: 3
                Text { text: name; color: Theme.text; font.family: Theme.fontBody; font.pixelSize: Theme.fMd }
                Text {
                    visible: desc.length > 0; text: desc
                    Layout.fillWidth: true
                    color: Theme.textMute; font.family: Theme.fontBody; font.pixelSize: Theme.fXs
                    wrapMode: Text.Wrap; lineHeight: 1.35
                }
            }
            Item {
                id: slot
                Layout.alignment: Qt.AlignTop | Qt.AlignRight
                Layout.preferredWidth: childrenRect.width
                Layout.preferredHeight: childrenRect.height
            }
        }
        Rectangle { Layout.fillWidth: true; height: 1; color: Theme.borderSoft }
    }

    component SectionTitle: Text {
        color: Theme.textMute; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
        font.letterSpacing: Theme.trackLabel; bottomPadding: 6
    }

    // small flat text button
    component TButton: Rectangle {
        property string label: ""
        signal clicked()
        implicitHeight: Math.round(28 * Theme.scale)
        implicitWidth: tbtxt.implicitWidth + 24
        radius: Theme.radSm
        color: tbma.containsMouse ? Theme.surface : "transparent"
        border.width: 1; border.color: Theme.border
        Text { id: tbtxt; anchors.centerIn: parent; text: label; color: Theme.text
               font.family: Theme.fontBody; font.pixelSize: Theme.fSm }
        MouseArea { id: tbma; anchors.fill: parent; hoverEnabled: true
            cursorShape: Qt.PointingHandCursor; onClicked: parent.clicked() }
    }

    // 选项胶囊（可带圆形色块）
    component OptChip: Rectangle {
        id: chip
        property string text: ""
        property bool on: false
        property color dot: "transparent"
        property bool showDot: false
        signal clicked()
        implicitHeight: Math.round(30 * Theme.scale)
        implicitWidth: ocRow.implicitWidth + 22
        radius: Theme.radSm
        color: on ? Theme.surface2 : (ocMa.containsMouse ? Theme.surface : "transparent")
        border.width: 1; border.color: on ? Theme.accentLine : Theme.border
        Row {
            id: ocRow; anchors.centerIn: parent; spacing: 7
            Rectangle {
                visible: chip.showDot; anchors.verticalCenter: parent.verticalCenter
                width: 12; height: 12; radius: 6; color: chip.dot
                border.width: 1; border.color: Theme.border
            }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: chip.text
                color: chip.on ? Theme.textBright : Theme.textDim
                font.family: Theme.fontBody; font.pixelSize: Theme.fSm
            }
        }
        MouseArea { id: ocMa; anchors.fill: parent; hoverEnabled: true
            cursorShape: Qt.PointingHandCursor; onClicked: chip.clicked() }
    }

    // 名称 + 说明 + 一排自动换行的选项（model: [{key,label,dot,showDot}]）
    component OptSection: ColumnLayout {
        id: secRoot
        property string name: ""
        property string desc: ""
        property var model: []
        property string current: ""
        signal picked(string key)
        Layout.fillWidth: true
        spacing: 0
        ColumnLayout {
            Layout.fillWidth: true; Layout.topMargin: 14; spacing: 3
            Text { text: secRoot.name; color: Theme.text
                   font.family: Theme.fontBody; font.pixelSize: Theme.fMd }
            Text {
                visible: secRoot.desc.length > 0; text: secRoot.desc
                Layout.fillWidth: true
                color: Theme.textMute; font.family: Theme.fontBody; font.pixelSize: Theme.fXs
                wrapMode: Text.Wrap; lineHeight: 1.35
            }
        }
        Flow {
            Layout.fillWidth: true; Layout.topMargin: 11; Layout.bottomMargin: 14; spacing: 7
            Repeater {
                model: secRoot.model
                delegate: OptChip {
                    required property var modelData
                    text: modelData.label
                    showDot: modelData.showDot === true
                    dot: modelData.dot !== undefined ? modelData.dot : "transparent"
                    on: secRoot.current === modelData.key
                    onClicked: secRoot.picked(modelData.key)
                }
            }
        }
        Rectangle { Layout.fillWidth: true; height: 1; color: Theme.borderSoft }
    }

    property var navModel: [
        { grp: loc("设置") },
        { id: "general",    name: loc("常规") },
        { id: "appearance", name: loc("外观与字号") },
        { id: "sessions",   name: loc("会话") },
        { id: "model",      name: loc("模型与 Provider") },
        { grp: loc("扩展") },
        { id: "rules",      name: loc("规则") },
        { id: "plugins",    name: loc("插件") },
        { id: "memory",     name: loc("记忆") },
        { id: "about",      name: loc("关于") }
    ]
    function secName(id) {
        for (var i = 0; i < navModel.length; i++) if (navModel[i].id === id) return navModel[i].name
        return ""
    }

    contentItem: RowLayout {
        spacing: 0

        // ---------- left nav ----------
        Rectangle {
            Layout.preferredWidth: Math.round(150 * Theme.scale)
            Layout.fillHeight: true
            color: Theme.panelDeep
            Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: Theme.borderSoft }
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 10
                spacing: 2
                Repeater {
                    model: win.navModel
                    delegate: Item {
                        id: navd
                        required property var modelData
                        Layout.fillWidth: true
                        implicitHeight: modelData.grp !== undefined
                                        ? Math.round(28 * Theme.scale) : Math.round(32 * Theme.scale)
                        // group label
                        Text {
                            visible: navd.modelData.grp !== undefined
                            anchors.left: parent.left; anchors.leftMargin: 10
                            anchors.bottom: parent.bottom; anchors.bottomMargin: 5
                            text: navd.modelData.grp || ""
                            color: Theme.textMute; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                            font.letterSpacing: Theme.trackLabel
                        }
                        // nav item
                        Rectangle {
                            visible: navd.modelData.id !== undefined
                            anchors.fill: parent
                            radius: Theme.radSm
                            color: navd.modelData.id === win.sec ? Theme.surface2
                                  : (niMa.containsMouse ? Theme.surface : "transparent")
                            Text {
                                anchors.left: parent.left; anchors.leftMargin: 10
                                anchors.verticalCenter: parent.verticalCenter
                                text: navd.modelData.name || ""
                                color: navd.modelData.id === win.sec ? Theme.text : Theme.textDim
                                font.family: Theme.fontBody; font.pixelSize: Theme.fSm
                            }
                            MouseArea {
                                id: niMa; anchors.fill: parent; hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: if (navd.modelData.id !== undefined) win.sec = navd.modelData.id
                            }
                        }
                    }
                }
                Item { Layout.fillHeight: true }
            }
        }

        // ---------- right body ----------
        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0

            // top bar
            RowLayout {
                Layout.fillWidth: true
                Layout.leftMargin: 18; Layout.rightMargin: 14
                Layout.topMargin: 14; Layout.bottomMargin: 14
                Text {
                    Layout.fillWidth: true
                    text: win.secName(win.sec)
                    color: Theme.textBright
                    font.family: Theme.fontDisplay; font.pixelSize: Theme.fLg; font.weight: Font.Medium
                }
                Rectangle {
                    width: 26; height: 26; radius: Theme.radSm
                    color: closeMa.containsMouse ? Theme.surface : "transparent"
                    border.width: 1; border.color: closeMa.containsMouse ? Theme.border : "transparent"
                    Text { anchors.centerIn: parent; text: "✕"; color: Theme.textMute; font.pixelSize: Theme.fSm }
                    MouseArea { id: closeMa; anchors.fill: parent; hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor; onClicked: win.close() }
                }
            }
            Rectangle { Layout.fillWidth: true; height: 1; color: Theme.borderSoft }

            // content
            Item {
                id: bodyArea
                Layout.fillWidth: true; Layout.fillHeight: true
                property bool isMgmt: win.sec === "rules" || win.sec === "plugins" || win.sec === "memory"
                property bool isProviders: win.sec === "model"

                // 配置类分区：可滚动的字段列
                ScrollView {
                    id: scroll
                    anchors.fill: parent
                    visible: !bodyArea.isMgmt && !bodyArea.isProviders
                    clip: true
                    contentWidth: availableWidth
                    Item {
                        width: scroll.availableWidth
                        implicitHeight: secCol.implicitHeight + 32
                        ColumnLayout {
                            id: secCol
                            x: 22; y: 16
                            width: parent.width - 44
                            spacing: 0
                            Loader {
                                Layout.fillWidth: true
                                sourceComponent: win.sec === "general" ? cGeneral
                                               : win.sec === "appearance" ? cAppearance
                                               : win.sec === "sessions" ? cSessions
                                               : cAbout
                            }
                        }
                    }
                }

                // 模型与 Provider：内嵌多供应商管理器
                ProviderManager {
                    anchors.fill: parent
                    anchors.leftMargin: 18; anchors.rightMargin: 16
                    anchors.topMargin: 12; anchors.bottomMargin: 14
                    visible: bodyArea.isProviders
                    active: bodyArea.isProviders
                }

                // 规则 / 插件 / 记忆：直接内嵌完整面板（不再弹独立窗口）
                ManagementContent {
                    anchors.fill: parent
                    anchors.leftMargin: 18; anchors.rightMargin: 16
                    anchors.topMargin: 12; anchors.bottomMargin: 14
                    visible: bodyArea.isMgmt
                    active: bodyArea.isMgmt
                    mode: win.sec === "rules" ? "rules" : win.sec === "plugins" ? "plugins" : "memory"
                }
            }
        }
    }

    // ================= section contents =================
    Component {
        id: cGeneral
        ColumnLayout {
            spacing: 0
            SectionTitle { text: win.loc("行为") }
            FieldRow {
                name: win.loc("显示思考过程")
                desc: win.loc("在回答里显示模型的思考过程（<think> 内容）。")
                Toggle { on: controller ? controller.showThinking : true
                    onToggled: if (controller) controller.setThink(!controller.showThinking) }
            }
            FieldRow {
                name: win.loc("实时 Cook")
                desc: win.loc("执行修改场景的工具后实时重算（cook）。关闭可在批量操作时提速。")
                Toggle { on: controller ? controller.cookRealtime : true
                    onToggled: if (controller) controller.menuAction("cook") }
            }
            FieldRow {
                name: win.loc("长期记忆")
                desc: win.loc("让助手跨会话记住你的偏好与项目信息。可在「记忆」分区查看与删除。")
                Toggle { on: controller ? controller.memoryEnabled : false
                    onToggled: if (controller) controller.menuAction("memory") }
            }
            Item { Layout.preferredHeight: 10 }
            SectionTitle { text: win.loc("常规") }
            FieldRow {
                name: win.loc("语言 / Language")
                RowLayout {
                    spacing: 6
                    Pill { label: "中文"; active: controller && controller.lang === "zh"
                        onClicked: if (controller && controller.lang !== "zh") controller.menuAction("lang_toggle") }
                    Pill { label: "English"; active: controller && controller.lang === "en"
                        onClicked: if (controller && controller.lang !== "en") controller.menuAction("lang_toggle") }
                }
            }
        }
    }

    Component {
        id: cAppearance
        ColumnLayout {
            spacing: 0
            SectionTitle { text: win.loc("外观与字号") }
            OptSection {
                name: win.loc("主题")
                desc: win.loc("深色三档，外加日光（浅色）。选日光会整体翻成浅色，强调色自动加深保证可读。")
                current: controller ? controller.appTheme : "noir"
                model: [
                    { key: "noir",     label: win.loc("极夜"),   showDot: true, dot: "#0d0d0d" },
                    { key: "graphite", label: win.loc("石墨"),   showDot: true, dot: "#1b1b1b" },
                    { key: "midnight", label: win.loc("午夜蓝"), showDot: true, dot: "#0c0e14" },
                    { key: "day",      label: win.loc("日光"),   showDot: true, dot: "#e6e1d6" }
                ]
                onPicked: function(k) { if (controller) controller.setAppTheme(k) }
            }
            OptSection {
                name: win.loc("强调色")
                desc: win.loc("选中态、指示点、用户气泡与按钮描边的颜色。")
                current: controller ? controller.accentKey : "warm"
                model: [
                    { key: "warm",    label: win.loc("暖米"), showDot: true, dot: "#e8e2d4" },
                    { key: "steel",   label: win.loc("冷钢"), showDot: true, dot: "#aec4d6" },
                    { key: "celadon", label: win.loc("青瓷"), showDot: true, dot: "#aecdb8" },
                    { key: "clay",    label: win.loc("暖砂"), showDot: true, dot: "#e0b48c" },
                    { key: "neutral", label: win.loc("中性"), showDot: true, dot: "#cfccc4" }
                ]
                onPicked: function(k) { if (controller) controller.setAccentKey(k) }
            }
            OptSection {
                name: win.loc("字体方案")
                desc: win.loc("标题 / 正文 / 等宽三件套的整体气质。")
                current: controller ? controller.fontFamilyKey : "editorial"
                model: [
                    { key: "editorial", label: win.loc("编辑体") },
                    { key: "modern",    label: win.loc("现代") },
                    { key: "mono",      label: win.loc("等宽") }
                ]
                onPicked: function(k) { if (controller) controller.setFontFamilyKey(k) }
            }
            OptSection {
                name: win.loc("界面密度")
                desc: win.loc("间距与行高的松紧，不改字号。")
                current: controller ? controller.densityKey : "normal"
                model: [
                    { key: "compact", label: win.loc("紧凑") },
                    { key: "normal",  label: win.loc("标准") },
                    { key: "roomy",   label: win.loc("宽松") }
                ]
                onPicked: function(k) { if (controller) controller.setDensityKey(k) }
            }
            FieldRow {
                name: win.loc("界面字号")
                desc: win.loc("调整整个面板的字号缩放。")
                Row {
                    spacing: 0
                    Rectangle {
                        width: 30; height: 28; radius: Theme.radSm; color: m1.containsMouse ? Theme.surface : "transparent"
                        border.width: 1; border.color: Theme.border
                        Text { anchors.centerIn: parent; text: "−"; color: Theme.text; font.pixelSize: Theme.fMd }
                        MouseArea { id: m1; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                            onClicked: if (controller) controller.setFontScale(controller.fontScale - 0.1) }
                    }
                    Rectangle {
                        width: 50; height: 28; color: "transparent"; border.width: 1; border.color: Theme.border
                        Text { anchors.centerIn: parent
                            text: controller ? Math.round(controller.fontScale * 100) + "%" : "100%"
                            color: Theme.text; font.family: Theme.fontMono; font.pixelSize: Theme.fSm }
                    }
                    Rectangle {
                        width: 30; height: 28; radius: Theme.radSm; color: m2.containsMouse ? Theme.surface : "transparent"
                        border.width: 1; border.color: Theme.border
                        Text { anchors.centerIn: parent; text: "+"; color: Theme.text; font.pixelSize: Theme.fMd }
                        MouseArea { id: m2; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                            onClicked: if (controller) controller.setFontScale(controller.fontScale + 0.1) }
                    }
                }
            }
        }
    }

    Component {
        id: cSessions
        ColumnLayout {
            spacing: 0
            SectionTitle { text: win.loc("会话") }
            FieldRow {
                name: win.loc("删除会话前需确认")
                desc: win.loc("关闭后删除会话不再弹确认框。在删除弹窗里勾选「不再询问」会自动关掉这里——随时可重新打开。")
                Toggle { on: controller ? controller.askDeleteSession : true
                    onToggled: if (controller) controller.setAskDeleteSession(!controller.askDeleteSession) }
            }
            FieldRow {
                name: win.loc("每步操作需批准（确认模式）")
                desc: win.loc("执行会修改场景或计费的工具前逐个弹确认卡。")
                Toggle { on: controller ? controller.confirmMode : false
                    onToggled: if (controller) controller.menuAction("confirm") }
            }
        }
    }

    Component {
        id: cAbout
        ColumnLayout {
            spacing: 0
            SectionTitle { text: win.loc("关于") }
            FieldRow {
                name: win.loc("版本")
                Text { text: "Houdini Agent · " + (controller ? controller.appVersion : "")
                    color: Theme.textDim; font.family: Theme.fontMono; font.pixelSize: Theme.fSm }
            }
            FieldRow {
                name: win.loc("检查更新")
                TButton { label: win.loc("检查更新"); onClicked: if (controller) controller.menuAction("update") }
            }
        }
    }
}
