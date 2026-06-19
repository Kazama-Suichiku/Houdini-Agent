import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs
import HAgent

// 规则 / 插件 / 记忆 的可复用主体（无弹窗外壳、无标题栏）。
// 既内嵌在设置面板里，也被 ManagementPanel 复用。靠 mode 切换三类内容。
Item {
    id: mc
    property string mode: "rules"
    property bool active: true            // 设为 true 时自动刷新数据
    property int tab: 0
    property int selected: 0
    property var rules: []
    property var plugins: []
    property var tools: []
    property var skills: []
    property var memories: []
    property var memStats: ({})
    property string settingsPlugin: ""
    property var settingsRows: []
    property string skillDir: ""
    property bool ruleDirty: false
    property string statText: ""

    function loc(s) { if (controller && controller.lang) return controller.tr(s); return s }
    function refresh() {
        if (!controller) return
        if (mode === "rules") {
            rules = controller.rulesItems()
            statText = rules.length + " RULES"
        } else if (mode === "plugins") {
            plugins = controller.pluginItems()
            tools = controller.toolItems()
            skills = controller.skillItems()
            skillDir = controller.userSkillDir()
            statText = plugins.length + " PLUGINS"
        } else {
            memStats = controller.memoryStats()
            memories = controller.memoryItems(tab === 1 ? "semantic" : tab === 2 ? "procedural" : "episodic")
            statText = (memStats.episodic || 0) + "/" + (memStats.semantic || 0) + "/" + (memStats.procedural || 0)
        }
    }
    function selectedRule() { return rules.length > 0 && selected >= 0 && selected < rules.length ? rules[selected] : ({ readonly: true, enabled: false, title: "", content: "" }) }
    function loadRule() {
        var r = selectedRule()
        ruleTitle.text = r.title || ""
        ruleBody.text = r.content || ""
        ruleEnabled.checked = r.enabled !== false
        ruleDirty = false
    }
    function reload() { tab = 0; selected = 0; refresh(); if (mode === "rules") loadRule() }

    onActiveChanged: if (active) reload()
    onModeChanged: if (active) reload()
    Component.onCompleted: if (active) reload()
    Connections {
        target: controller
        ignoreUnknownSignals: true
        function onManagementChanged() { if (mc.active) { mc.refresh(); if (mc.mode === "rules") mc.loadRule() } }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // ---- tab strip (plugins / memory) ----
        RowLayout {
            visible: mc.mode !== "rules"
            Layout.fillWidth: true
            Layout.preferredHeight: Math.round(42 * Theme.scale)
            spacing: 8
            Item { Layout.preferredWidth: 4 }
            Pill { label: mc.mode === "plugins" ? "Plugins" : "Episodic"; active: mc.tab === 0; onClicked: { mc.tab = 0; mc.refresh() } }
            Pill { label: mc.mode === "plugins" ? "Tools" : "Semantic"; active: mc.tab === 1; onClicked: { mc.tab = 1; mc.refresh() } }
            Pill { label: mc.mode === "plugins" ? "Skills" : "Procedural"; active: mc.tab === 2; onClicked: { mc.tab = 2; mc.refresh() } }
            Item { Layout.fillWidth: true }
            Pill { visible: mc.mode === "plugins"; label: "↻ " + mc.loc("重载"); onClicked: { if (controller) controller.reloadAllPlugins(); mc.refresh() } }
            Pill { visible: mc.mode === "plugins"; label: mc.loc("打开插件目录"); onClicked: if (controller) controller.openPluginsFolder() }
        }

        Rectangle {
            visible: mc.mode === "plugins" && mc.tab === 2
            Layout.fillWidth: true
            Layout.preferredHeight: Math.round(40 * Theme.scale)
            color: Theme.surface
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.borderSoft }
            RowLayout {
                anchors.fill: parent; anchors.leftMargin: 4; anchors.rightMargin: 4; spacing: 8
                Text {
                    Layout.fillWidth: true
                    text: mc.skillDir ? mc.skillDir : mc.loc("用户技能目录未设置，仅使用内置技能")
                    color: mc.skillDir ? Theme.textDim : Theme.textMute
                    elide: Text.ElideMiddle
                    font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                }
                Pill { label: mc.loc("浏览"); onClicked: skillDirDialog.open() }
            }
        }

        // ---- rules: list + editor ----
        RowLayout {
            visible: mc.mode === "rules"
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0

            Rectangle {
                Layout.preferredWidth: Math.min(Math.round(264 * Theme.scale), Math.round(mc.width * 0.44))
                Layout.fillHeight: true
                color: Theme.codeBg
                Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: Theme.border }
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 14; spacing: 12
                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            Layout.fillWidth: true
                            text: "RULES · " + mc.rules.length
                            color: Theme.textMute; font.family: Theme.fontMono
                            font.pixelSize: Theme.fMicro; font.letterSpacing: Theme.trackLabel
                        }
                        Pill {
                            label: mc.loc("新建"); accent: true
                            onClicked: { if (controller) controller.addRule(); mc.refresh(); mc.selected = Math.max(0, mc.rules.length - 1); mc.loadRule() }
                        }
                    }
                    ListView {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        boundsBehavior: Flickable.StopAtBounds
                        model: mc.rules
                        spacing: 6
                        ScrollBar.vertical: SmartScrollBar {}
                        delegate: Rectangle {
                            required property int index
                            required property var modelData
                            width: ListView.view.width
                            height: Math.round(54 * Theme.scale)
                            color: index === mc.selected ? Theme.accentSoft : Theme.surface
                            border.color: index === mc.selected ? Theme.accentLine : Theme.borderSoft
                            border.width: 1
                            radius: Theme.radSm
                            Rectangle {
                                anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom
                                width: 2; color: Theme.accent
                                visible: index === mc.selected
                            }
                            ColumnLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 12; anchors.rightMargin: 10
                                anchors.topMargin: 8; anchors.bottomMargin: 8
                                spacing: 3
                                Text {
                                    Layout.fillWidth: true
                                    text: modelData.title || mc.loc("未命名")
                                    elide: Text.ElideRight
                                    color: modelData.enabled === false ? Theme.textMute : Theme.text
                                    font.family: Theme.fontBody; font.pixelSize: Theme.fSm
                                }
                                RowLayout {
                                    Layout.fillWidth: true; spacing: 8
                                    Text {
                                        text: (modelData.source || "ui").toUpperCase()
                                        color: Theme.textMute; font.family: Theme.fontMono
                                        font.pixelSize: Theme.fMicro; font.letterSpacing: Theme.trackLabel
                                    }
                                    Text {
                                        text: modelData.readonly ? "READONLY" : (modelData.enabled === false ? "OFF" : "ON")
                                        color: (!modelData.readonly && modelData.enabled !== false) ? Theme.ok : Theme.textMute
                                        font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                                    }
                                    Item { Layout.fillWidth: true }
                                }
                            }
                            MouseArea {
                                anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                onClicked: {
                                    if (mc.ruleDirty) { if (controller) controller.showToast(mc.loc("当前规则有未保存修改，请先提交或取消")); return }
                                    mc.selected = index; mc.loadRule()
                                }
                            }
                        }
                    }
                    Text {
                        text: mc.loc("打开规则目录") + " →"
                        color: dirMa.containsMouse ? Theme.accent : Theme.textMute
                        font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                        MouseArea {
                            id: dirMa; anchors.fill: parent; anchors.margins: -4; hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: if (controller) controller.openRulesFolder()
                        }
                    }
                }
            }

            Item {
                Layout.fillWidth: true
                Layout.fillHeight: true

                ColumnLayout {
                    anchors.centerIn: parent
                    width: Math.min(parent.width - 48, 360)
                    visible: mc.rules.length === 0
                    spacing: 10
                    Text { Layout.alignment: Qt.AlignHCenter; text: "◇"; color: Theme.textMute; font.pixelSize: Theme.fXl }
                    Text {
                        Layout.alignment: Qt.AlignHCenter
                        text: mc.loc("还没有规则")
                        color: Theme.textDim; font.family: Theme.fontDisplay; font.pixelSize: Theme.fLg
                    }
                    Text {
                        Layout.fillWidth: true
                        horizontalAlignment: Text.AlignHCenter
                        wrapMode: Text.Wrap
                        text: mc.loc("规则会注入到每次对话、长期生效。点左上角「新建」创建第一条。")
                        color: Theme.textMute; font.family: Theme.fontBody; font.pixelSize: Theme.fSm
                    }
                }

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 16
                    visible: mc.rules.length > 0
                    spacing: 12
                    Text {
                        text: mc.selectedRule().readonly ? "RULE · READONLY" : ("RULE · " + mc.loc("编辑"))
                        color: Theme.accent; font.family: Theme.fontMono
                        font.pixelSize: Theme.fMicro; font.letterSpacing: Theme.trackLabel
                    }
                    RowLayout {
                        Layout.fillWidth: true; spacing: 14
                        LabeledField {
                            id: ruleTitle; Layout.fillWidth: true; label: "TITLE"
                            enabled: !mc.selectedRule().readonly
                            onTextChanged: if (!mc.selectedRule().readonly) mc.ruleDirty = true
                        }
                        ColumnLayout {
                            spacing: 5
                            Text { text: "STATE"; color: Theme.textDim; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro; font.letterSpacing: Theme.trackLabel }
                            Item {
                                id: ruleEnabled
                                property bool checked: true
                                implicitWidth: Math.round(76 * Theme.scale)
                                implicitHeight: Math.round(30 * Theme.scale)
                                Rectangle {
                                    anchors.fill: parent; radius: Theme.radSm
                                    color: ruleEnabled.checked ? Theme.accentSoft : "transparent"
                                    border.width: 1
                                    border.color: ruleEnabled.checked ? Theme.accentLine : Theme.border
                                }
                                Text {
                                    anchors.centerIn: parent
                                    text: ruleEnabled.checked ? mc.loc("启用") : mc.loc("停用")
                                    color: ruleEnabled.checked ? Theme.accent : Theme.textMute
                                    font.family: Theme.fontBody; font.pixelSize: Theme.fSm
                                }
                                MouseArea {
                                    anchors.fill: parent
                                    enabled: !mc.selectedRule().readonly
                                    cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                                    onClicked: { ruleEnabled.checked = !ruleEnabled.checked; mc.ruleDirty = true }
                                }
                            }
                        }
                    }
                    Text { text: "CONTENT"; color: Theme.textDim; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro; font.letterSpacing: Theme.trackLabel }
                    Rectangle {
                        Layout.fillWidth: true; Layout.fillHeight: true
                        color: Theme.codeBg
                        border.color: ruleBody.activeFocus ? Theme.accentLine : Theme.border
                        border.width: 1; radius: Theme.radSm
                        TextArea {
                            id: ruleBody
                            anchors.fill: parent; anchors.margins: 11
                            readOnly: mc.selectedRule().readonly
                            wrapMode: TextArea.Wrap
                            color: Theme.text
                            selectedTextColor: Theme.bg
                            selectionColor: Theme.accent
                            font.family: Theme.fontBody; font.pixelSize: Theme.fBody
                            background: null
                            placeholderText: mc.loc("写下这条规则的内容…")
                            placeholderTextColor: Theme.textMute
                            onTextChanged: if (!mc.selectedRule().readonly) mc.ruleDirty = true
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true; spacing: 10
                        Text {
                            Layout.fillWidth: true
                            text: mc.selectedRule().readonly
                                  ? (mc.selectedRule().path || mc.loc("文件规则 · 只读"))
                                  : (mc.ruleDirty ? mc.loc("有未保存修改") : mc.loc("UI 规则"))
                            color: (mc.ruleDirty && !mc.selectedRule().readonly) ? Theme.warn : Theme.textMute
                            elide: Text.ElideMiddle
                            font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                        }
                        Pill { label: mc.loc("删除"); dashed: true; visible: !mc.selectedRule().readonly && mc.rules.length > 0; onClicked: { if (controller) controller.deleteRule(mc.selectedRule().id) } }
                        Pill { label: mc.loc("提交"); accent: true; visible: !mc.selectedRule().readonly && mc.rules.length > 0; onClicked: { if (!controller || controller.saveRule(mc.selectedRule().id, ruleTitle.text, ruleBody.text, ruleEnabled.checked)) { mc.ruleDirty = false; mc.refresh(); mc.loadRule() } } }
                    }
                }
            }
        }

        // ---- plugins ----
        ListView {
            visible: mc.mode === "plugins"
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.topMargin: 4
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            spacing: 8
            model: mc.tab === 0 ? mc.plugins : mc.tab === 1 ? mc.tools : mc.skills
            ScrollBar.vertical: SmartScrollBar {}
            delegate: Rectangle {
                required property var modelData
                width: ListView.view.width
                height: Math.round((mc.tab === 2 ? 72 : 82) * Theme.scale)
                color: Theme.surface
                border.color: Theme.border
                border.width: 1
                radius: Theme.radSm
                Text { anchors.left: parent.left; anchors.leftMargin: 12; anchors.right: ctrl.left; anchors.rightMargin: 10; anchors.top: parent.top; anchors.topMargin: 10; text: modelData.name || ""; color: Theme.textBright; elide: Text.ElideRight; font.family: Theme.fontBody; font.pixelSize: Theme.fMd }
                Text { anchors.left: parent.left; anchors.leftMargin: 12; anchors.right: ctrl.left; anchors.rightMargin: 10; anchors.top: parent.top; anchors.topMargin: 32; text: modelData.description || ""; color: Theme.textDim; elide: Text.ElideRight; font.family: Theme.fontBody; font.pixelSize: Theme.fSm }
                Text { anchors.left: parent.left; anchors.leftMargin: 12; anchors.bottom: parent.bottom; anchors.bottomMargin: 8; text: mc.tab === 0 ? ((modelData.version || "") + "  " + (modelData.author || "")) : mc.tab === 1 ? ((modelData.source || "tool").toUpperCase()) : "SKILL"; color: Theme.textMute; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro }
                Row {
                    id: ctrl
                    anchors.right: parent.right; anchors.rightMargin: 10
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 6
                    Pill {
                        visible: mc.tab === 0 && modelData.settings
                        label: "⚙"
                        onClicked: {
                            if (!controller) return
                            mc.settingsPlugin = modelData.name
                            mc.settingsRows = controller.pluginSettings(modelData.name)
                            settingsPopup.open()
                        }
                    }
                    Pill { visible: mc.tab === 0; label: "↻"; onClicked: { if (controller) controller.reloadPlugin(modelData.name); mc.refresh() } }
                    Pill {
                        visible: mc.tab !== 2
                        label: modelData.enabled === false ? "OFF" : "ON"
                        active: modelData.enabled !== false
                        onClicked: {
                            if (!controller) return
                            if (mc.tab === 0) controller.setPluginEnabled(modelData.name, !(modelData.enabled !== false))
                            else controller.setToolEnabled(modelData.name, !(modelData.enabled !== false))
                            mc.refresh()
                        }
                    }
                }
            }
        }

        // ---- memory ----
        ListView {
            visible: mc.mode === "memory"
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.topMargin: 4
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            spacing: 8
            model: mc.memories
            ScrollBar.vertical: SmartScrollBar {}
            delegate: Rectangle {
                required property var modelData
                width: ListView.view.width
                height: Math.round(92 * Theme.scale)
                color: Theme.surface
                border.color: Theme.border
                border.width: 1
                radius: Theme.radSm
                Text { anchors.left: parent.left; anchors.leftMargin: 12; anchors.right: delPill.left; anchors.rightMargin: 10; anchors.top: parent.top; anchors.topMargin: 10; text: modelData.title || ""; color: Theme.textBright; elide: Text.ElideRight; font.family: Theme.fontBody; font.pixelSize: Theme.fSm }
                Text { anchors.left: parent.left; anchors.leftMargin: 12; anchors.right: parent.right; anchors.rightMargin: 12; anchors.top: parent.top; anchors.topMargin: 32; height: 34; text: modelData.body || ""; color: Theme.textDim; elide: Text.ElideRight; wrapMode: Text.Wrap; font.family: Theme.fontBody; font.pixelSize: Theme.fXs }
                Text { anchors.left: parent.left; anchors.leftMargin: 12; anchors.bottom: parent.bottom; anchors.bottomMargin: 8; text: modelData.meta || ""; color: Theme.textMute; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro }
                Pill { id: delPill; anchors.right: parent.right; anchors.rightMargin: 10; anchors.verticalCenter: parent.verticalCenter; label: mc.loc("删除"); onClicked: { if (controller) controller.deleteMemory(mc.tab === 1 ? "semantic" : mc.tab === 2 ? "procedural" : "episodic", modelData.id) } }
            }
        }
    }

    FolderDialog {
        id: skillDirDialog
        title: mc.loc("选择用户技能目录")
        onAccepted: { if (controller) controller.setUserSkillDir("" + selectedFolder); mc.refresh() }
    }

    Popup {
        id: settingsPopup
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(460, parent ? parent.width - 48 : 460)
        padding: 18
        modal: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background: Rectangle { color: Theme.bg; border.color: Theme.border; border.width: 1; radius: Theme.radSm }
        contentItem: ColumnLayout {
            spacing: 12
            Text {
                Layout.fillWidth: true
                text: mc.loc("插件设置") + " · " + mc.settingsPlugin
                color: Theme.textBright
                font.family: Theme.fontDisplay; font.pixelSize: Theme.fLg
                elide: Text.ElideRight
            }
            Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }
            Repeater {
                model: mc.settingsRows
                delegate: RowLayout {
                    required property int index
                    required property var modelData
                    Layout.fillWidth: true
                    Text {
                        Layout.preferredWidth: 130
                        text: modelData.label || modelData.key
                        color: Theme.textDim
                        elide: Text.ElideRight
                        font.family: Theme.fontBody; font.pixelSize: Theme.fSm
                    }
                    CheckBox {
                        visible: modelData.type === "bool"
                        checked: !!modelData.value
                        onToggled: mc.settingsRows[index].value = checked
                        palette.windowText: Theme.text
                    }
                    TextField {
                        visible: modelData.type !== "bool"
                        Layout.fillWidth: true
                        text: modelData.value === undefined ? "" : "" + modelData.value
                        color: Theme.textBright
                        font.family: Theme.fontMono; font.pixelSize: Theme.fSm
                        onTextEdited: mc.settingsRows[index].value = text
                        background: Rectangle { color: Theme.surface; border.color: parent.activeFocus ? Theme.accentLine : Theme.border; radius: Theme.radSm }
                    }
                }
            }
            Text {
                visible: mc.settingsRows.length === 0
                text: mc.loc("这个插件没有可配置项")
                color: Theme.textMute
                font.family: Theme.fontBody; font.pixelSize: Theme.fSm
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                Pill { label: mc.loc("取消"); onClicked: settingsPopup.close() }
                Pill { label: mc.loc("提交"); accent: true; onClicked: { if (controller) controller.savePluginSettings(mc.settingsPlugin, JSON.stringify(mc.settingsRows)); settingsPopup.close(); mc.refresh() } }
            }
        }
    }
}
