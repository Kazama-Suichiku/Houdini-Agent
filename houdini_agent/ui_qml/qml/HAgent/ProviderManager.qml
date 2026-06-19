import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import HAgent

// 多供应商管理：内置 provider（只读 + API Key）+ 自定义供应商（增删改，每个含多模型）。
// 窄面板友好：列表态与编辑态切换（不再两栏挤）。
Item {
    id: pm
    property bool active: true
    property var customs: []
    property var builtins: []
    property string editingId: ""      // "" 列表态；"new" 新增；其余为编辑某 id

    // 编辑态字段
    property string fName: ""
    property string fUrl: ""
    property string fKey: ""
    property bool fAnthropic: false

    function loc(s) { return controller ? (controller.lang, controller.tr(s)) : s }
    function reload() {
        if (!controller) return
        customs = controller.customProviderItems()
        builtins = controller.builtinProviderItems()
    }
    onActiveChanged: if (active) { editingId = ""; reload() }
    Component.onCompleted: if (active) reload()
    Connections {
        target: controller
        ignoreUnknownSignals: true
        function onCustomProvidersChanged() { pm.reload() }
        function onProviderChanged() { pm.reload() }
    }

    function startAdd() {
        editingId = "new"; fName = ""; fUrl = ""; fKey = ""; fAnthropic = false
        modelModel.clear(); modelModel.append({ mname: "", ctx: "128000", vis: false })
    }
    function startEdit(p) {
        editingId = p.id; fName = p.name; fUrl = p.base_url; fKey = p.api_key; fAnthropic = p.anthropic
        modelModel.clear()
        for (var i = 0; i < p.models.length; i++)
            modelModel.append({ mname: p.models[i].name, ctx: "" + p.models[i].context, vis: p.models[i].vision })
        if (p.models.length === 0) modelModel.append({ mname: "", ctx: "128000", vis: false })
    }
    function submit() {
        var models = []
        for (var i = 0; i < modelModel.count; i++) {
            var r = modelModel.get(i)
            if (("" + r.mname).trim().length > 0)
                models.push({ name: r.mname, context: parseInt(r.ctx) || 128000, vision: r.vis === true })
        }
        var payload = { id: editingId === "new" ? "" : editingId, name: fName, base_url: fUrl,
                        api_key: fKey, anthropic: fAnthropic, models: models }
        var res = controller ? controller.saveCustomProvider(JSON.stringify(payload)) : ({ ok: false })
        if (res && res.ok) { editingId = ""; reload(); if (controller) controller.showToast(loc("已保存")) }
        else if (controller) controller.showToast((res && res.error) ? res.error : loc("无效数据"))
    }

    ListModel { id: modelModel }

    // 小工具组件
    component Field: Rectangle {
        property alias text: inp.text
        property string placeholder: ""
        property bool password: false
        implicitWidth: Math.round(120 * Theme.scale)
        implicitHeight: Math.round(34 * Theme.scale)
        radius: Theme.radSm; color: Theme.surface
        border.width: 1; border.color: inp.activeFocus ? Theme.accentLine : Theme.border
        TextField {
            id: inp
            anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 10
            verticalAlignment: TextInput.AlignVCenter
            echoMode: parent.password ? TextInput.Password : TextInput.Normal
            placeholderText: parent.placeholder
            placeholderTextColor: Theme.textMute
            color: Theme.text
            font.family: Theme.fontMono; font.pixelSize: Theme.fSm
            background: null
        }
    }
    component FieldLabel: Text {
        color: Theme.textDim; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
        font.letterSpacing: Theme.trackLabel; bottomPadding: 5; topPadding: 12
    }
    component MiniToggle: Rectangle {
        property bool on: false
        signal toggled()
        width: 34; height: 19; radius: 10
        color: on ? Theme.accentSoft : Theme.surface2
        border.width: 1; border.color: on ? Theme.accentLine : Theme.border
        Rectangle { y: 1; x: parent.on ? 16 : 1; width: 15; height: 15; radius: 7.5
            color: parent.on ? Theme.accent : Theme.textMute
            Behavior on x { NumberAnimation { duration: 120 } } }
        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: parent.toggled() }
    }
    component DotChip: Rectangle {
        property bool on: false
        width: 6; height: 6; radius: 3
        color: on ? Theme.ok : Theme.textMute
    }

    // ====== 列表态 ======
    ScrollView {
        anchors.fill: parent
        visible: pm.editingId === ""
        clip: true
        contentWidth: availableWidth
        ColumnLayout {
            width: pm.width
            spacing: 0

            Text {
                Layout.fillWidth: true; Layout.bottomMargin: 14
                text: pm.loc("管理自定义模型供应商，配置后可在聊天时选择使用。")
                color: Theme.textMute; font.family: Theme.fontBody; font.pixelSize: Theme.fSm
                wrapMode: Text.Wrap
            }

            // 内置供应商
            Text { text: pm.loc("内置供应商"); color: Theme.textMute; font.family: Theme.fontMono
                   font.pixelSize: Theme.fMicro; font.letterSpacing: Theme.trackLabel; bottomPadding: 8 }
            Repeater {
                model: pm.builtins
                delegate: Rectangle {
                    required property var modelData
                    Layout.fillWidth: true
                    implicitHeight: Math.round(40 * Theme.scale)
                    radius: Theme.radSm
                    color: bma.containsMouse ? Theme.surface : "transparent"
                    RowLayout {
                        anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 8; spacing: 9
                        DotChip { on: true }
                        Text { Layout.fillWidth: true; text: modelData.name; color: Theme.text
                               font.family: Theme.fontBody; font.pixelSize: Theme.fSm; elide: Text.ElideRight }
                        Text { visible: modelData.active; text: pm.loc("当前"); color: Theme.accent
                               font.family: Theme.fontMono; font.pixelSize: Theme.fMicro; font.letterSpacing: Theme.trackLabel }
                        Pill { visible: !modelData.active; label: pm.loc("使用")
                               onClicked: if (controller) controller.selectProviderModel(modelData.key, "") }
                        Pill { label: "API Key"; onClicked: if (controller) controller.openProviderApiKey(modelData.key) }
                    }
                    MouseArea { id: bma; anchors.fill: parent; hoverEnabled: true; acceptedButtons: Qt.NoButton }
                }
            }

            // 自定义供应商
            Text { text: pm.loc("自定义供应商"); color: Theme.textMute; font.family: Theme.fontMono
                   font.pixelSize: Theme.fMicro; font.letterSpacing: Theme.trackLabel; topPadding: 18; bottomPadding: 8 }
            Text {
                visible: pm.customs.length === 0
                Layout.fillWidth: true; Layout.bottomMargin: 6
                text: pm.loc("还没有自定义供应商")
                color: Theme.textMute; font.family: Theme.fontBody; font.pixelSize: Theme.fSm
            }
            Repeater {
                model: pm.customs
                delegate: Rectangle {
                    required property var modelData
                    Layout.fillWidth: true
                    Layout.bottomMargin: 6
                    implicitHeight: Math.round(52 * Theme.scale)
                    radius: Theme.radSm
                    color: Theme.surface
                    border.width: 1; border.color: modelData.active ? Theme.accentLine : Theme.border
                    RowLayout {
                        anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 8; spacing: 9
                        DotChip { on: modelData.configured }
                        ColumnLayout {
                            Layout.fillWidth: true; spacing: 1
                            Text { text: modelData.name; color: Theme.text; font.family: Theme.fontBody
                                   font.pixelSize: Theme.fSm; elide: Text.ElideRight; Layout.fillWidth: true }
                            Text { text: modelData.models.length + " " + pm.loc("模型列表") + (modelData.anthropic ? " · Anthropic" : " · OpenAI")
                                   color: Theme.textMute; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                                   elide: Text.ElideRight; Layout.fillWidth: true }
                        }
                        Text { visible: modelData.active; text: pm.loc("当前"); color: Theme.accent
                               font.family: Theme.fontMono; font.pixelSize: Theme.fMicro; font.letterSpacing: Theme.trackLabel }
                        Pill { visible: !modelData.active; label: pm.loc("使用")
                               onClicked: if (controller) controller.selectProviderModel("custom:" + modelData.id, "") }
                        Pill { label: pm.loc("编辑"); onClicked: pm.startEdit(modelData) }
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true; Layout.topMargin: 6
                implicitHeight: Math.round(38 * Theme.scale)
                radius: Theme.radSm; color: addMa.containsMouse ? Theme.accentSoft : "transparent"
                border.width: 1; border.color: Theme.border
                RowLayout {
                    anchors.centerIn: parent; spacing: 7
                    Text { text: "+"; color: Theme.accent; font.pixelSize: Theme.fMd }
                    Text { text: pm.loc("添加供应商"); color: Theme.accent; font.family: Theme.fontMono
                           font.pixelSize: Theme.fXs; font.letterSpacing: Theme.trackLabel }
                }
                MouseArea { id: addMa; anchors.fill: parent; hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor; onClicked: pm.startAdd() }
            }
        }
    }

    // ====== 编辑/新增态 ======
    ScrollView {
        anchors.fill: parent
        visible: pm.editingId !== ""
        clip: true
        contentWidth: availableWidth
        ColumnLayout {
            width: pm.width
            spacing: 0

            RowLayout {
                Layout.fillWidth: true; spacing: 8
                Text { Layout.fillWidth: true
                    text: pm.editingId === "new" ? pm.loc("添加模型供应商") : pm.fName
                    color: Theme.textBright; font.family: Theme.fontDisplay; font.pixelSize: Theme.fLg; elide: Text.ElideRight }
                Pill { label: pm.loc("取消"); onClicked: pm.editingId = "" }
            }
            Text { Layout.fillWidth: true; Layout.topMargin: 4
                text: pm.loc("配置一个自定义 API 端点和它的模型。")
                color: Theme.textMute; font.family: Theme.fontBody; font.pixelSize: Theme.fXs; wrapMode: Text.Wrap }

            FieldLabel { text: pm.loc("名称") }
            Field { Layout.fillWidth: true; id: fld_name; text: pm.fName; placeholder: "Kimi / GLM / ..."; onTextChanged: pm.fName = text }
            FieldLabel { text: "Base URL" }
            Field { Layout.fillWidth: true; id: fld_url; text: pm.fUrl; placeholder: "https://api.example.com/v1"; onTextChanged: pm.fUrl = text }
            FieldLabel { text: "API Key" }
            Field { Layout.fillWidth: true; id: fld_key; text: pm.fKey; placeholder: "sk-..."; password: true; onTextChanged: pm.fKey = text }

            FieldLabel { text: pm.loc("API 格式") }
            RowLayout {
                Layout.fillWidth: true; spacing: 6
                Pill { label: "OpenAI"; active: !pm.fAnthropic; onClicked: pm.fAnthropic = false }
                Pill { label: "Anthropic"; active: pm.fAnthropic; onClicked: pm.fAnthropic = true }
                Item { Layout.fillWidth: true }
            }

            FieldLabel { text: pm.loc("模型列表") }
            Repeater {
                model: modelModel
                delegate: RowLayout {
                    required property int index
                    Layout.fillWidth: true
                    Layout.bottomMargin: 6
                    spacing: 6
                    Field {
                        Layout.fillWidth: true
                        text: model.mname; placeholder: pm.loc("模型名")
                        onTextChanged: modelModel.setProperty(index, "mname", text)
                    }
                    Field {
                        Layout.preferredWidth: Math.round(78 * Theme.scale)
                        Layout.fillWidth: false
                        text: model.ctx; placeholder: pm.loc("上下文窗口")
                        onTextChanged: modelModel.setProperty(index, "ctx", text)
                    }
                    ColumnLayout {
                        spacing: 2
                        Text { text: pm.loc("图片"); color: Theme.textMute; font.family: Theme.fontMono
                               font.pixelSize: Theme.fMicro; Layout.alignment: Qt.AlignHCenter }
                        MiniToggle { on: model.vis === true; onToggled: modelModel.setProperty(index, "vis", !(model.vis === true)) }
                    }
                    Rectangle {
                        Layout.preferredWidth: 24; Layout.preferredHeight: 24
                        radius: Theme.radSm; color: rmMa.containsMouse ? Qt.rgba(0.867, 0.6, 0.6, 0.14) : "transparent"
                        TrashIcon { anchors.centerIn: parent; size: Math.round(13 * Theme.scale)
                                    color: rmMa.containsMouse ? Theme.err : Theme.textMute }
                        MouseArea { id: rmMa; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                            onClicked: if (modelModel.count > 1) modelModel.remove(index)
                                       else modelModel.setProperty(index, "mname", "") }
                    }
                }
            }
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: Math.round(32 * Theme.scale)
                radius: Theme.radSm; color: addmMa.containsMouse ? Theme.surface : "transparent"
                border.width: 1; border.color: Theme.border
                Text { anchors.centerIn: parent; text: "+ " + pm.loc("添加模型"); color: Theme.textDim
                       font.family: Theme.fontMono; font.pixelSize: Theme.fXs }
                MouseArea { id: addmMa; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                    onClicked: modelModel.append({ mname: "", ctx: "128000", vis: false }) }
            }

            RowLayout {
                Layout.fillWidth: true; Layout.topMargin: 16
                Pill {
                    label: pm.loc("删除"); dashed: true; visible: pm.editingId !== "new"
                    onClicked: { if (controller) controller.deleteCustomProvider(pm.editingId); pm.editingId = ""; pm.reload() }
                }
                Item { Layout.fillWidth: true }
                Pill { label: pm.loc("取消"); onClicked: pm.editingId = "" }
                Pill { label: pm.loc("保存"); accent: true; onClicked: pm.submit() }
            }
            Item { Layout.preferredHeight: 12 }
        }
    }
}
