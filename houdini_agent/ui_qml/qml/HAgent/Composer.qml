import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import HAgent

// Bottom input area: mode segmented control + input + meta line.
Rectangle {
    id: composer
    color: "transparent"
    implicitHeight: col.implicitHeight + 24

    Rectangle { anchors.top: parent.top; width: parent.width; height: 1; color: Theme.border }

    property var modes: ["Agent", "Ask", "Plan"]
    property int modeIndex: 0

    function loc(s) { return controller ? (controller.lang, controller.tr(s)) : s }

    property var imgUris: controller ? controller.pendingImageUris() : []
    Connections {
        target: controller
        ignoreUnknownSignals: true
        function onImagesChanged() { composer.imgUris = controller.pendingImageUris() }
        // 资产库"+用 Meshy 生成"：把起始提示词塞进输入框、切到 Agent 模式并聚焦
        function onPrefillComposer(t) {
            if (controller && controller.setMode("Agent")) composer.modeIndex = 0
            inputArea.text = t
            inputArea.cursorPosition = inputArea.text.length
            inputArea.forceActiveFocus()
        }
    }

    ColumnLayout {
        id: col
        anchors.left: parent.left; anchors.right: parent.right
        anchors.top: parent.top
        anchors.leftMargin: 14; anchors.rightMargin: 14; anchors.topMargin: 12
        spacing: 10

        // ---- batch undo / keep bar ----
        Rectangle {
            Layout.fillWidth: true
            visible: controller && controller.pendingOps > 0
            implicitHeight: visible ? Math.round(30 * Theme.scale) : 0
            color: Theme.surface
            border.color: Theme.border; border.width: 1; radius: Theme.radSm
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 11; anchors.rightMargin: 7
                spacing: 8
                Text {
                    Layout.fillWidth: true
                    text: (controller ? controller.pendingOps : 0) + composer.loc(" 个操作待确认")
                    color: Theme.textDim; font.family: Theme.fontMono; font.pixelSize: Theme.fXs
                }
                Pill { label: composer.loc("全部撤销"); onClicked: if (controller) controller.undoAll() }
                Pill { label: composer.loc("全部保留"); accent: true; onClicked: if (controller) controller.keepAll() }
            }
        }

        // ---- run status ----
        StatusBar { Layout.fillWidth: true }

        // ---- mode segmented control ----
        Row {
            spacing: 0
            Repeater {
                model: composer.modes
                delegate: Rectangle {
                    required property int index
                    required property string modelData
                    width: Math.round(64 * Theme.scale)
                    height: Math.round(28 * Theme.scale)
                    color: composer.modeIndex === index ? Theme.textBright : "transparent"
                    border.width: 1
                    border.color: composer.modeIndex === index ? Theme.textBright : Theme.border
                    Text {
                        anchors.centerIn: parent
                        text: modelData
                        color: composer.modeIndex === index ? Theme.bg : Theme.textDim
                        font.family: Theme.fontBody
                        font.pixelSize: Theme.fSm
                    }
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            if (!controller || controller.setMode(modelData)) composer.modeIndex = index
                        }
                    }
                }
            }
        }

        // ---- image thumbnails ----
        Flow {
            Layout.fillWidth: true
            visible: composer.imgUris.length > 0
            spacing: 6
            Repeater {
                model: composer.imgUris
                delegate: Rectangle {
                    required property int index
                    required property string modelData
                    width: Math.round(46 * Theme.scale); height: Math.round(46 * Theme.scale)
                    color: Theme.codeBg; border.color: Theme.border; border.width: 1; radius: Theme.radSm
                    Image { anchors.fill: parent; anchors.margins: 1; source: modelData; fillMode: Image.PreserveAspectCrop; clip: true }
                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                        onClicked: { fullImg.src = modelData; fullImg.open() } }
                    Rectangle {
                        anchors.top: parent.top; anchors.right: parent.right
                        width: 15; height: 15; color: Theme.bg; border.color: Theme.border; border.width: 1
                        Text { anchors.centerIn: parent; text: "✕"; color: Theme.textMute; font.pixelSize: 9 }
                        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                            onClicked: if (controller) controller.removeImage(index) }
                    }
                }
            }
        }

        // ---- input row ----
        Rectangle {
            id: inputRow
            Layout.fillWidth: true
            implicitHeight: Math.max(Math.round(40 * Theme.scale), inputArea.implicitHeight + 14)
            color: "transparent"
            border.color: dropArea.containsDrag ? Theme.accent : Theme.border
            border.width: 1
            radius: Theme.radSm

            DropArea {
                id: dropArea
                anchors.fill: parent
                onDropped: function(drop) {
                    if (drop.hasUrls && controller) {
                        for (var i = 0; i < drop.urls.length; i++) controller.attachImage("" + drop.urls[i])
                        drop.accept()
                    }
                }
            }

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 10; anchors.rightMargin: 8
                anchors.topMargin: 6; anchors.bottomMargin: 6
                spacing: 8

                Text {
                    text: "+"
                    color: (controller && controller.imageCount > 0) ? Theme.accent : Theme.textDim
                    font.pixelSize: Theme.fLg
                    Layout.alignment: Qt.AlignVCenter
                    MouseArea { anchors.fill: parent; anchors.margins: -4; cursorShape: Qt.PointingHandCursor
                        onClicked: imgDialog.open() }
                }

                TextArea {
                    id: inputArea
                    Layout.fillWidth: true
                    Layout.alignment: Qt.AlignVCenter
                    wrapMode: TextArea.Wrap
                    placeholderText: {
                        var m = composer.modes[composer.modeIndex]
                        return m === "Ask" ? composer.loc("问一个关于场景的问题…  (只读模式)")
                             : m === "Plan" ? composer.loc("描述目标，先生成可确认的执行计划…")
                             : composer.loc("描述你想在场景里做的事…  (Enter 发送)")
                    }
                    color: Theme.text
                    placeholderTextColor: Theme.textMute
                    font.family: Theme.fontBody
                    font.pixelSize: Theme.fBody
                    background: null
                    Keys.onReturnPressed: function(e) {
                        if (completer.opened && completer.items.length > 0) {
                            composer.applyCompletion(completer.items[0].text); e.accepted = true; return
                        }
                        if (e.modifiers & Qt.ShiftModifier) { e.accepted = false; return }
                        e.accepted = true
                        composer.submit()
                    }
                    Keys.onPressed: function(e) {
                        if ((e.modifiers & Qt.ControlModifier) && e.key === Qt.Key_V) {
                            if (controller) controller.pasteImage()   // text paste still proceeds
                        } else if (e.key === Qt.Key_Escape) {
                            completer.close()
                        }
                    }
                    onTextChanged: composer.updateCompleter(text, cursorPosition)
                }

                Rectangle {
                    width: Math.round(32 * Theme.scale); height: Math.round(32 * Theme.scale)
                    radius: Theme.radSm
                    property bool busy: controller ? controller.running : false
                    color: busy ? "transparent" : Theme.accent
                    border.width: busy ? 1 : 0
                    border.color: Theme.border
                    Layout.alignment: Qt.AlignVCenter
                    Text { anchors.centerIn: parent; text: parent.busy ? "■" : "↑"; color: parent.busy ? Theme.text : Theme.bg; font.pixelSize: Theme.fLg }
                    MouseArea {
                        anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                        onClicked: { if (controller && controller.running) controller.stop(); else composer.submit() }
                    }
                }
            }
        }

        // ---- meta line ----
        RowLayout {
            spacing: 8
            Text {
                visible: controller && controller.imageCount > 0
                text: controller ? ("已附 " + controller.imageCount + " 图 ✕") : ""
                color: Theme.accent; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                    onClicked: if (controller) controller.clearImages() }
            }
            Text { text: controller ? controller.model : "Opus 4.8"; color: Theme.textMute; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro }
            Text { text: "·"; color: Theme.textMute; font.pixelSize: Theme.fMicro; opacity: 0.5 }
            Text { text: (controller ? controller.ctxText : "0 / 200k") + " ctx"; color: Theme.textMute; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro }
            Text { text: "·"; color: Theme.textMute; font.pixelSize: Theme.fMicro; opacity: 0.5 }
            Text { text: controller ? controller.tokenText : "0 tokens"; color: Theme.ok; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro }
            Item { Layout.fillWidth: true }
        }
    }

    function submit() {
        var t = inputArea.text
        if (t.trim().length === 0) return
        completer.close()
        if (!controller || controller.send(t)) inputArea.text = ""
    }

    function updateCompleter(text, pos) {
        var before = text.substring(0, pos)
        var m = before.match(/([@/])([^\s@/]*)$/)
        if (!m) { completer.close(); return }
        var trig = m[1], pref = m[2].toLowerCase()
        var list = []
        if (trig === "@") {
            var nodes = controller ? controller.nodePaths() : []
            for (var i = 0; i < nodes.length; i++)
                if (nodes[i].toLowerCase().indexOf(pref) >= 0) { list.push({text: nodes[i], sub: ""}); if (list.length >= 20) break }
        } else {
            var cmds = controller ? controller.slashCommands() : []
            for (var j = 0; j < cmds.length; j++)
                if (pref === "" || cmds[j].cmd.indexOf("/" + pref) === 0) list.push({text: cmds[j].cmd, sub: cmds[j].desc})
        }
        if (list.length === 0) { completer.close(); return }
        completer.trig = trig
        completer.tokenStart = pos - m[2].length - 1
        completer.items = list
        completer.open()
    }

    function applyCompletion(val) {
        var text = inputArea.text, start = completer.tokenStart, end = inputArea.cursorPosition
        if (completer.trig === "/") {
            inputArea.text = text.substring(0, start) + text.substring(end)
            completer.close()
            if (controller) controller.runSlash(val)
            return
        }
        inputArea.text = text.substring(0, start) + val + " " + text.substring(end)
        inputArea.cursorPosition = start + val.length + 1
        completer.close()
    }

    Popup {
        id: completer
        property string trig: "@"
        property int tokenStart: 0
        property var items: []
        parent: inputRow
        x: 0; y: -height - 4; width: inputRow.width
        padding: 6
        closePolicy: Popup.NoAutoClose
        background: Rectangle { color: "#161616"; border.color: Theme.border; border.width: 1; radius: Theme.radSm }
        contentItem: ListView {
            implicitHeight: Math.min(contentHeight, 180)
            clip: true
            model: completer.items
            delegate: Rectangle {
                required property var modelData
                width: ListView.view ? ListView.view.width : 200
                height: Math.round(26 * Theme.scale)
                radius: Theme.radSm
                color: hov.containsMouse ? Theme.surface : "transparent"
                Row {
                    anchors.fill: parent; anchors.leftMargin: 8; anchors.rightMargin: 8; spacing: 8
                    Text { anchors.verticalCenter: parent.verticalCenter; text: modelData.text; color: Theme.text
                        font.family: Theme.fontMono; font.pixelSize: Theme.fXs }
                    Text { anchors.verticalCenter: parent.verticalCenter; text: modelData.sub || ""; color: Theme.textMute
                        font.pixelSize: Theme.fMicro }
                }
                MouseArea { id: hov; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                    onClicked: composer.applyCompletion(modelData.text) }
            }
        }
    }

    Popup {
        id: fullImg
        property string src: ""
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: parent ? parent.width * 0.9 : 420
        height: parent ? parent.height * 0.9 : 420
        modal: true
        closePolicy: Popup.CloseOnPressOutside | Popup.CloseOnEscape
        background: Rectangle { color: "#000000ee"; border.color: Theme.border; border.width: 1 }
        contentItem: Image {
            source: fullImg.src; fillMode: Image.PreserveAspectFit
            MouseArea { anchors.fill: parent; onClicked: fullImg.close() }
        }
    }

    FileDialog {
        id: imgDialog
        title: composer.loc("选择图片")
        nameFilters: ["Images (*.png *.jpg *.jpeg *.gif *.webp)"]
        onAccepted: if (controller) controller.attachImage("" + selectedFile)
    }
}
