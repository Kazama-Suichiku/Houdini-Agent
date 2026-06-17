import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs
import HAgent

Popup {
    id: panel
    property string mode: "rules"
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
    function loc(s) { if (controller && controller.lang) return controller.tr(s); return s }
    function title() { return mode === "rules" ? loc("规则编辑器") : mode === "plugins" ? loc("插件管理") : loc("记忆管理") }
    function openRules() { mode = "rules"; selected = 0; refresh(); open() }
    function openPlugins() { mode = "plugins"; tab = 0; refresh(); open() }
    function openMemory() { mode = "memory"; tab = 0; refresh(); open() }
    function refresh() {
        if (!controller) return
        if (mode === "rules") rules = controller.rulesItems()
        else if (mode === "plugins") {
            plugins = controller.pluginItems()
            tools = controller.toolItems()
            skills = controller.skillItems()
            skillDir = controller.userSkillDir()
        } else {
            memStats = controller.memoryStats()
            memories = controller.memoryItems(tab === 1 ? "semantic" : tab === 2 ? "procedural" : "episodic")
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

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(820, parent ? parent.width - 32 : 820)
    height: Math.min(620, parent ? parent.height - 32 : 620)
    padding: 0
    modal: true
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    background: Rectangle { color: Theme.bg; border.color: Theme.border; border.width: 1; radius: Theme.radSm }
    onOpened: { refresh(); if (mode === "rules") loadRule() }

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
                    text: mode === "rules" ? (rules.length + " RULES") : mode === "plugins" ? (plugins.length + " PLUGINS") : ((memStats.episodic || 0) + "/" + (memStats.semantic || 0) + "/" + (memStats.procedural || 0))
                    color: Theme.textMute
                    font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                }
                Pill { label: "×"; onClicked: panel.close() }
            }
        }

        RowLayout {
            visible: panel.mode !== "rules"
            Layout.fillWidth: true
            Layout.preferredHeight: Math.round(42 * Theme.scale)
            spacing: 8
            Item { Layout.preferredWidth: 12 }
            Pill { label: panel.mode === "plugins" ? "Plugins" : "Episodic"; active: panel.tab === 0; onClicked: { panel.tab = 0; panel.refresh() } }
            Pill { label: panel.mode === "plugins" ? "Tools" : "Semantic"; active: panel.tab === 1; onClicked: { panel.tab = 1; panel.refresh() } }
            Pill { label: panel.mode === "plugins" ? "Skills" : "Procedural"; active: panel.tab === 2; onClicked: { panel.tab = 2; panel.refresh() } }
            Item { Layout.fillWidth: true }
            Pill { visible: panel.mode === "plugins"; label: "↻ " + panel.loc("重载"); onClicked: { if (controller) controller.reloadAllPlugins(); panel.refresh() } }
            Pill { visible: panel.mode === "plugins"; label: panel.loc("打开插件目录"); onClicked: if (controller) controller.openPluginsFolder() }
            Item { Layout.preferredWidth: 12 }
        }

        Rectangle {
            visible: panel.mode === "plugins" && panel.tab === 2
            Layout.fillWidth: true
            Layout.preferredHeight: Math.round(40 * Theme.scale)
            color: Theme.surface
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.borderSoft }
            RowLayout {
                anchors.fill: parent; anchors.leftMargin: 14; anchors.rightMargin: 14; spacing: 8
                Text {
                    Layout.fillWidth: true
                    text: panel.skillDir ? panel.skillDir : "用户技能目录未设置，仅使用内置技能"
                    color: panel.skillDir ? Theme.textDim : Theme.textMute
                    elide: Text.ElideMiddle
                    font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                }
                Pill { label: "浏览"; onClicked: skillDirDialog.open() }
            }
        }

        RowLayout {
            visible: panel.mode === "rules"
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0

            // ---- left: rule list ----
            Rectangle {
                Layout.preferredWidth: Math.round(264 * Theme.scale)
                Layout.fillHeight: true
                color: Theme.codeBg
                Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: Theme.border }
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 16; spacing: 12
                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            Layout.fillWidth: true
                            text: "RULES · " + panel.rules.length
                            color: Theme.textMute; font.family: Theme.fontMono
                            font.pixelSize: Theme.fMicro; font.letterSpacing: Theme.trackLabel
                        }
                        Pill {
                            label: panel.loc("新建"); accent: true
                            onClicked: { if (controller) controller.addRule(); panel.refresh(); panel.selected = Math.max(0, panel.rules.length - 1); panel.loadRule() }
                        }
                    }
                    ListView {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        boundsBehavior: Flickable.StopAtBounds
                        boundsMovement: Flickable.StopAtBounds
                        pixelAligned: false
                        flickDeceleration: 3600
                        maximumFlickVelocity: 7200
                        model: panel.rules
                        spacing: 6
                        ScrollBar.vertical: SmartScrollBar {}
                        delegate: Rectangle {
                            required property int index
                            required property var modelData
                            width: ListView.view.width
                            height: Math.round(54 * Theme.scale)
                            color: index === panel.selected ? Theme.accentSoft : Theme.surface
                            border.color: index === panel.selected ? Theme.accentLine : Theme.borderSoft
                            border.width: 1
                            radius: Theme.radSm
                            Rectangle {
                                anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom
                                width: 2; color: Theme.accent
                                visible: index === panel.selected
                            }
                            ColumnLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 12; anchors.rightMargin: 10
                                anchors.topMargin: 8; anchors.bottomMargin: 8
                                spacing: 3
                                Text {
                                    Layout.fillWidth: true
                                    text: modelData.title || panel.loc("未命名")
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
                                    if (panel.ruleDirty) { if (controller) controller.showToast(panel.loc("当前规则有未保存修改，请先提交或取消")); return }
                                    panel.selected = index; panel.loadRule()
                                }
                            }
                        }
                    }
                    Text {
                        text: panel.loc("打开规则目录") + " →"
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

            // ---- right: editor / empty state ----
            Item {
                Layout.fillWidth: true
                Layout.fillHeight: true

                // empty state
                ColumnLayout {
                    anchors.centerIn: parent
                    width: Math.min(parent.width - 64, 360)
                    visible: panel.rules.length === 0
                    spacing: 10
                    Text { Layout.alignment: Qt.AlignHCenter; text: "◇"; color: Theme.textMute; font.pixelSize: Theme.fXl }
                    Text {
                        Layout.alignment: Qt.AlignHCenter
                        text: panel.loc("还没有规则")
                        color: Theme.textDim; font.family: Theme.fontDisplay; font.pixelSize: Theme.fLg
                    }
                    Text {
                        Layout.fillWidth: true
                        horizontalAlignment: Text.AlignHCenter
                        wrapMode: Text.Wrap
                        text: panel.loc("规则会注入到每次对话、长期生效。点左上角「新建」创建第一条。")
                        color: Theme.textMute; font.family: Theme.fontBody; font.pixelSize: Theme.fSm
                    }
                }

                // editor
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 18
                    visible: panel.rules.length > 0
                    spacing: 12
                    Text {
                        text: panel.selectedRule().readonly ? "RULE · READONLY" : "RULE · 编辑"
                        color: Theme.accent; font.family: Theme.fontMono
                        font.pixelSize: Theme.fMicro; font.letterSpacing: Theme.trackLabel
                    }
                    RowLayout {
                        Layout.fillWidth: true; spacing: 14
                        LabeledField {
                            id: ruleTitle; Layout.fillWidth: true; label: "TITLE"
                            enabled: !panel.selectedRule().readonly
                            onTextChanged: if (!panel.selectedRule().readonly) panel.ruleDirty = true
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
                                    text: ruleEnabled.checked ? panel.loc("启用") : panel.loc("停用")
                                    color: ruleEnabled.checked ? Theme.accent : Theme.textMute
                                    font.family: Theme.fontBody; font.pixelSize: Theme.fSm
                                }
                                MouseArea {
                                    anchors.fill: parent
                                    enabled: !panel.selectedRule().readonly
                                    cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                                    onClicked: { ruleEnabled.checked = !ruleEnabled.checked; panel.ruleDirty = true }
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
                            readOnly: panel.selectedRule().readonly
                            wrapMode: TextArea.Wrap
                            color: Theme.text
                            selectedTextColor: Theme.bg
                            selectionColor: Theme.accent
                            font.family: Theme.fontBody; font.pixelSize: Theme.fBody
                            background: null
                            placeholderText: panel.loc("写下这条规则的内容…")
                            placeholderTextColor: Theme.textMute
                            onTextChanged: if (!panel.selectedRule().readonly) panel.ruleDirty = true
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true; spacing: 10
                        Text {
                            Layout.fillWidth: true
                            text: panel.selectedRule().readonly
                                  ? (panel.selectedRule().path || panel.loc("文件规则 · 只读"))
                                  : (panel.ruleDirty ? panel.loc("有未保存修改") : panel.loc("UI 规则"))
                            color: (panel.ruleDirty && !panel.selectedRule().readonly) ? Theme.warn : Theme.textMute
                            elide: Text.ElideMiddle
                            font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                        }
                        Pill { label: panel.loc("删除"); dashed: true; visible: !panel.selectedRule().readonly && panel.rules.length > 0; onClicked: { if (controller) controller.deleteRule(panel.selectedRule().id) } }
                        Pill { label: panel.loc("提交"); accent: true; visible: !panel.selectedRule().readonly && panel.rules.length > 0; onClicked: { if (!controller || controller.saveRule(panel.selectedRule().id, ruleTitle.text, ruleBody.text, ruleEnabled.checked)) { panel.ruleDirty = false; panel.refresh(); panel.loadRule() } } }
                    }
                }
            }
        }

        ListView {
            visible: panel.mode === "plugins"
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.margins: 14
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            boundsMovement: Flickable.StopAtBounds
            pixelAligned: false
            flickDeceleration: 3600
            maximumFlickVelocity: 7200
            spacing: 8
            model: panel.tab === 0 ? panel.plugins : panel.tab === 1 ? panel.tools : panel.skills
            ScrollBar.vertical: SmartScrollBar {}
            delegate: Rectangle {
                required property var modelData
                width: ListView.view.width
                height: Math.round((panel.tab === 2 ? 72 : 82) * Theme.scale)
                color: Theme.surface
                border.color: Theme.border
                border.width: 1
                radius: Theme.radSm
                Text { anchors.left: parent.left; anchors.leftMargin: 12; anchors.right: ctrl.left; anchors.rightMargin: 10; anchors.top: parent.top; anchors.topMargin: 10; text: modelData.name || ""; color: Theme.textBright; elide: Text.ElideRight; font.family: Theme.fontBody; font.pixelSize: Theme.fMd }
                Text { anchors.left: parent.left; anchors.leftMargin: 12; anchors.right: ctrl.left; anchors.rightMargin: 10; anchors.top: parent.top; anchors.topMargin: 32; text: modelData.description || ""; color: Theme.textDim; elide: Text.ElideRight; font.family: Theme.fontBody; font.pixelSize: Theme.fSm }
                Text { anchors.left: parent.left; anchors.leftMargin: 12; anchors.bottom: parent.bottom; anchors.bottomMargin: 8; text: panel.tab === 0 ? ((modelData.version || "") + "  " + (modelData.author || "")) : panel.tab === 1 ? ((modelData.source || "tool").toUpperCase()) : "SKILL"; color: Theme.textMute; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro }
                Row {
                    id: ctrl
                    anchors.right: parent.right; anchors.rightMargin: 10
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 6
                    Pill {
                        visible: panel.tab === 0 && modelData.settings
                        label: "⚙"
                        onClicked: {
                            if (!controller) return
                            panel.settingsPlugin = modelData.name
                            panel.settingsRows = controller.pluginSettings(modelData.name)
                            settingsPopup.open()
                        }
                    }
                    Pill { visible: panel.tab === 0; label: "↻"; onClicked: { if (controller) controller.reloadPlugin(modelData.name); panel.refresh() } }
                    Pill {
                        visible: panel.tab !== 2
                        label: modelData.enabled === false ? "OFF" : "ON"
                        active: modelData.enabled !== false
                        onClicked: {
                            if (!controller) return
                            if (panel.tab === 0) controller.setPluginEnabled(modelData.name, !(modelData.enabled !== false))
                            else controller.setToolEnabled(modelData.name, !(modelData.enabled !== false))
                            panel.refresh()
                        }
                    }
                }
            }
        }

        FolderDialog {
            id: skillDirDialog
            title: "选择用户技能目录"
            onAccepted: {
                if (controller) controller.setUserSkillDir("" + selectedFolder)
                panel.refresh()
            }
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
                    text: "插件设置 · " + panel.settingsPlugin
                    color: Theme.textBright
                    font.family: Theme.fontDisplay; font.pixelSize: Theme.fLg
                    elide: Text.ElideRight
                }
                Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }
                Repeater {
                    model: panel.settingsRows
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
                            onToggled: panel.settingsRows[index].value = checked
                            palette.windowText: Theme.text
                        }
                        TextField {
                            visible: modelData.type !== "bool"
                            Layout.fillWidth: true
                            text: modelData.value === undefined ? "" : "" + modelData.value
                            color: Theme.textBright
                            font.family: Theme.fontMono; font.pixelSize: Theme.fSm
                            onTextEdited: panel.settingsRows[index].value = text
                            background: Rectangle { color: Theme.surface; border.color: parent.activeFocus ? Theme.accentLine : Theme.border; radius: Theme.radSm }
                        }
                    }
                }
                Text {
                    visible: panel.settingsRows.length === 0
                    text: "这个插件没有可配置项"
                    color: Theme.textMute
                    font.family: Theme.fontBody; font.pixelSize: Theme.fSm
                }
                RowLayout {
                    Layout.fillWidth: true
                    Item { Layout.fillWidth: true }
                    Pill { label: panel.loc("取消"); onClicked: settingsPopup.close() }
                    Pill { label: panel.loc("提交"); accent: true; onClicked: { if (controller) controller.savePluginSettings(panel.settingsPlugin, JSON.stringify(panel.settingsRows)); settingsPopup.close(); panel.refresh() } }
                }
            }
        }

        ListView {
            visible: panel.mode === "memory"
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.margins: 14
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            boundsMovement: Flickable.StopAtBounds
            pixelAligned: false
            flickDeceleration: 3600
            maximumFlickVelocity: 7200
            spacing: 8
            model: panel.memories
            ScrollBar.vertical: SmartScrollBar {}
            delegate: Rectangle {
                required property var modelData
                width: ListView.view.width
                height: Math.round(92 * Theme.scale)
                color: Theme.surface
                border.color: Theme.border
                border.width: 1
                radius: Theme.radSm
                Text { anchors.left: parent.left; anchors.leftMargin: 12; anchors.right: del.left; anchors.rightMargin: 10; anchors.top: parent.top; anchors.topMargin: 10; text: modelData.title || ""; color: Theme.textBright; elide: Text.ElideRight; font.family: Theme.fontBody; font.pixelSize: Theme.fSm }
                Text { anchors.left: parent.left; anchors.leftMargin: 12; anchors.right: parent.right; anchors.rightMargin: 12; anchors.top: parent.top; anchors.topMargin: 32; height: 34; text: modelData.body || ""; color: Theme.textDim; elide: Text.ElideRight; wrapMode: Text.Wrap; font.family: Theme.fontBody; font.pixelSize: Theme.fXs }
                Text { anchors.left: parent.left; anchors.leftMargin: 12; anchors.bottom: parent.bottom; anchors.bottomMargin: 8; text: modelData.meta || ""; color: Theme.textMute; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro }
                Pill { id: del; anchors.right: parent.right; anchors.rightMargin: 10; anchors.verticalCenter: parent.verticalCenter; label: panel.loc("删除"); onClicked: { if (controller) controller.deleteMemory(panel.tab === 1 ? "semantic" : panel.tab === 2 ? "procedural" : "episodic", modelData.id) } }
            }
        }
    }
}
