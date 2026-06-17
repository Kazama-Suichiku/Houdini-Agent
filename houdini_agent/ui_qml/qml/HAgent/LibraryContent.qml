import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import HAgent

// Meshy cloud asset library — content panel (no scrim/slide).
// Hosted as the LEFT column of Main.qml; the window grows outward to make room,
// so it never occupies the chat's space. Shares the global `controller`.
Rectangle {
    id: lib
    color: Theme.bg

    property var items: []
    property bool loading: controller ? controller.libraryLoading : false
    property var account: ({connected: false, key: "", balance: -1})

    function loc(s) { return controller ? (controller.lang, controller.tr(s)) : s }
    function imgSrc(s) {
        if (!s) return ""
        s = "" + s
        if (s.indexOf("http") === 0) return s
        return "file:///" + s.replace(/\\/g, "/")
    }
    function kindLabel(k) {
        return k === "image-to-3d" ? "图生3D"
             : k === "text-to-3d"  ? "文生3D"
             : k === "retexture"   ? "重打材质"
             : k === "remesh"      ? "重拓扑" : (k || "")
    }
    function reload() { items = controller ? controller.libraryItems() : [] }
    function reloadAccount() {
        account = controller ? controller.meshyAccount()
                             : ({connected: false, key: "", balance: -1})
    }

    Component.onCompleted: { reload(); reloadAccount() }
    Connections {
        target: controller
        ignoreUnknownSignals: true
        function onLibraryChanged() { lib.reload() }
        function onMeshyAccountChanged() { lib.reloadAccount() }
        function onLibraryOpenChanged() { if (controller.libraryOpen) { lib.reload(); lib.reloadAccount() } }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 14
        spacing: 10

        // ---- header ----
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2
                Text {
                    text: "MESHY · " + lib.loc("资产库").toUpperCase()
                    color: Theme.accent; font.family: Theme.fontMono
                    font.pixelSize: Theme.fMicro; font.letterSpacing: Theme.trackLabel
                }
                Text {
                    text: lib.loc("云资产 · 直接拉到 Houdini")
                    color: Theme.textMute; font.family: Theme.fontBody
                    font.pixelSize: Theme.fXs
                }
            }
            Pill {
                label: lib.loc("+ 生成")
                onClicked: {
                    if (!controller) return
                    controller.composePrefill(lib.loc("用 Meshy 生成一个 "))
                    controller.setLibraryOpen(false)
                }
            }
            Pill {
                label: lib.loc("工作台")
                onClicked: if (controller) controller.openMeshy("workspace")
            }
            Pill {
                label: lib.loc("刷新")
                onClicked: if (controller) controller.refreshLibrary()
            }
            Rectangle {
                width: 26; height: 26; radius: Theme.radSm
                color: closeMa.containsMouse ? Theme.surface : "transparent"
                border.width: 1
                border.color: closeMa.containsMouse ? Theme.border : "transparent"
                Text { anchors.centerIn: parent; text: "✕"; color: Theme.textDim; font.pixelSize: Theme.fMd }
                MouseArea {
                    id: closeMa; anchors.fill: parent; hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: if (controller) controller.setLibraryOpen(false)
                }
            }
        }

        // ---- account strip (API Key = Meshy login credential) ----
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: acctRow.implicitHeight + 14
            color: Theme.surface
            border.color: lib.account.connected ? Theme.accentLine : Theme.border
            border.width: 1
            radius: Theme.radSm
            RowLayout {
                id: acctRow
                anchors.fill: parent; anchors.margins: 7
                spacing: 8
                Rectangle {
                    width: 8; height: 8; radius: 4
                    color: lib.account.connected ? Theme.ok : Theme.textMute
                }
                ColumnLayout {
                    Layout.fillWidth: true; spacing: 1
                    Text {
                        Layout.fillWidth: true
                        text: lib.account.connected
                              ? (lib.loc("已连接")
                                 + (lib.account.key ? ("  ·  " + lib.account.key) : ""))
                              : lib.loc("未连接")
                        color: lib.account.connected ? Theme.text : Theme.textMute
                        font.family: Theme.fontMono; font.pixelSize: Theme.fXs
                        elide: Text.ElideRight
                    }
                    Text {
                        Layout.fillWidth: true
                        text: lib.account.connected
                              ? (lib.account.balance >= 0
                                 ? (lib.loc("余额") + " " + lib.account.balance + " credits")
                                 : lib.loc("加载中…"))
                              : lib.loc("登录 Meshy 同步你的资产与额度")
                        color: Theme.textMute; font.family: Theme.fontBody; font.pixelSize: Theme.fMicro
                        elide: Text.ElideRight
                    }
                }
                Pill {
                    visible: lib.account.connected
                    label: lib.loc("充值")
                    // 余额较低时高亮提醒
                    accent: lib.account.balance >= 0 && lib.account.balance < 20
                    onClicked: if (controller) controller.openMeshy("pricing")
                }
                Pill {
                    label: lib.account.connected ? lib.loc("切换账号") : lib.loc("登录 / 配置 Key")
                    accent: !lib.account.connected
                    onClicked: if (controller) controller.openMeshyLogin()
                }
                Pill {
                    visible: lib.account.connected
                    label: lib.loc("退出"); dashed: true
                    onClicked: if (controller) controller.meshyLogout()
                }
            }
        }

        Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }

        // ---- loading line ----
        Text {
            Layout.fillWidth: true
            visible: lib.loading
            text: lib.loc("加载中…")
            color: Theme.textMute; font.family: Theme.fontMono; font.pixelSize: Theme.fXs
            SequentialAnimation on opacity {
                running: lib.loading; loops: Animation.Infinite
                NumberAnimation { to: 0.4; duration: 600 }
                NumberAnimation { to: 1.0; duration: 600 }
            }
        }

        // ---- empty state ----
        Item {
            Layout.fillWidth: true; Layout.fillHeight: true
            visible: !lib.loading && lib.items.length === 0
            Column {
                anchors.centerIn: parent
                width: parent.width - 32
                spacing: 10
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: "◇"; color: Theme.textMute; font.pixelSize: Theme.fXl
                }
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: lib.account.connected ? lib.loc("暂无资产")
                                                : lib.loc("登录 Meshy 同步你的资产")
                    color: Theme.textMute; font.family: Theme.fontBody; font.pixelSize: Theme.fSm
                    horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap
                    width: parent.width
                }
                // 自适应引导 CTA：已连接→引导在 Agent 里生成；未连接→登录同步
                Pill {
                    anchors.horizontalCenter: parent.horizontalCenter
                    label: lib.account.connected ? lib.loc("+ 用 Meshy 生成") : lib.loc("登录 / 配置 Key")
                    accent: true
                    onClicked: {
                        if (!controller) return
                        if (lib.account.connected) {
                            controller.composePrefill(lib.loc("用 Meshy 生成一个 "))
                            controller.setLibraryOpen(false)
                        } else {
                            controller.openMeshyLogin()
                        }
                    }
                }
                Pill {
                    visible: lib.account.connected
                    anchors.horizontalCenter: parent.horizontalCenter
                    label: lib.loc("去 Meshy 工作台"); dashed: true
                    onClicked: if (controller) controller.openMeshy("workspace")
                }
            }
        }

        // ---- asset grid ----
        Flickable {
            id: flick
            Layout.fillWidth: true; Layout.fillHeight: true
            visible: lib.items.length > 0
            clip: true
            contentWidth: width
            contentHeight: grid.implicitHeight + footer.implicitHeight + 24
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: SmartScrollBar {}

            property int cols: Math.max(2, Math.floor(width / 200))
            property int cellW: (width - (cols - 1) * 10) / cols

            Grid {
                id: grid
                width: parent.width
                columns: flick.cols
                spacing: 10
                Repeater {
                    model: lib.items
                    delegate: Item {
                        id: cell
                        required property var modelData
                        width: flick.cellW
                        property int thumbH: Math.round(width * 0.80)
                        property int bodyH: Math.round(108 * Theme.scale)
                        height: thumbH + bodyH

                        Rectangle {
                            anchors.fill: parent
                            color: Theme.surface
                            border.color: Theme.border; border.width: 1
                            radius: Theme.radSm
                            clip: true

                            // ---- thumbnail ----
                            Rectangle {
                                id: thumb
                                width: parent.width
                                height: cell.thumbH
                                color: Theme.codeBg
                                clip: true
                                Image {
                                    anchors.fill: parent; anchors.margins: 1
                                    source: lib.imgSrc(cell.modelData.thumbnail)
                                    fillMode: Image.PreserveAspectCrop
                                    asynchronous: true
                                }
                                Text {
                                    anchors.centerIn: parent
                                    visible: !cell.modelData.thumbnail
                                    text: "◇"; color: Theme.textMute; font.pixelSize: Theme.fLg
                                }
                                // kind badge (top-left)
                                Rectangle {
                                    anchors.top: parent.top; anchors.left: parent.left; anchors.margins: 5
                                    width: kindTxt.implicitWidth + 10; height: kindTxt.implicitHeight + 5
                                    color: "#cc0d0d0d"; radius: Theme.radSm
                                    Text {
                                        id: kindTxt; anchors.centerIn: parent
                                        text: lib.kindLabel(cell.modelData.kind)
                                        color: Theme.textDim; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                                    }
                                }
                                // status badge (top-right): cached / expired
                                Rectangle {
                                    visible: cell.modelData.cached || cell.modelData.expired
                                    anchors.top: parent.top; anchors.right: parent.right; anchors.margins: 5
                                    width: stTxt.implicitWidth + 10; height: stTxt.implicitHeight + 5
                                    radius: Theme.radSm
                                    color: cell.modelData.cached ? Theme.okSoft : Theme.warnSoft
                                    Text {
                                        id: stTxt; anchors.centerIn: parent
                                        text: cell.modelData.cached ? lib.loc("已缓存") : lib.loc("已过期")
                                        color: cell.modelData.cached ? Theme.ok : Theme.warn
                                        font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                                    }
                                }
                                // importing overlay (dim + pulsing dot + stage)
                                Rectangle {
                                    anchors.fill: parent
                                    visible: cell.modelData.importing === true
                                    color: "#cc0d0d0d"
                                    Column {
                                        anchors.centerIn: parent
                                        spacing: 8
                                        Rectangle {
                                            width: 10; height: 10; radius: 5; color: Theme.accent
                                            anchors.horizontalCenter: parent.horizontalCenter
                                            SequentialAnimation on opacity {
                                                running: cell.modelData.importing === true
                                                loops: Animation.Infinite
                                                NumberAnimation { to: 0.25; duration: 550 }
                                                NumberAnimation { to: 1.0; duration: 550 }
                                            }
                                        }
                                        Text {
                                            anchors.horizontalCenter: parent.horizontalCenter
                                            text: cell.modelData.import_stage || lib.loc("导入中…")
                                            color: Theme.accent; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                                        }
                                    }
                                }
                            }

                            // ---- body ----
                            Item {
                                anchors.top: thumb.bottom
                                anchors.left: parent.left; anchors.right: parent.right
                                anchors.bottom: parent.bottom

                                Text {
                                    id: pTxt
                                    anchors.top: parent.top; anchors.left: parent.left; anchors.right: parent.right
                                    anchors.topMargin: 8; anchors.leftMargin: 8; anchors.rightMargin: 8
                                    text: (cell.modelData.prompt && ("" + cell.modelData.prompt).length)
                                          ? ("" + cell.modelData.prompt) : "—"
                                    color: Theme.text; font.family: Theme.fontBody; font.pixelSize: Theme.fXs
                                    lineHeight: 1.05
                                    elide: Text.ElideRight; maximumLineCount: 2; wrapMode: Text.Wrap
                                }
                                Text {
                                    id: mTxt
                                    anchors.top: pTxt.bottom; anchors.left: parent.left; anchors.right: parent.right
                                    anchors.topMargin: 4; anchors.leftMargin: 8; anchors.rightMargin: 8
                                    text: {
                                        var m = cell.modelData.created_label || ""
                                        var c = (cell.modelData.credits !== undefined
                                                 && cell.modelData.credits !== null
                                                 && cell.modelData.credits !== "")
                                                ? (cell.modelData.credits + "cr") : ""
                                        return [m, c].filter(function(x){return x}).join("  ·  ")
                                    }
                                    color: Theme.textMute; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                                    elide: Text.ElideRight
                                }
                                Item {
                                    anchors.bottom: parent.bottom; anchors.left: parent.left; anchors.right: parent.right
                                    anchors.margins: 6
                                    height: Math.round(28 * Theme.scale)
                                    Row {
                                        visible: cell.modelData.importing === true
                                        anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter
                                        spacing: 6
                                        Rectangle {
                                            width: 7; height: 7; radius: 4; color: Theme.accent
                                            anchors.verticalCenter: parent.verticalCenter
                                            SequentialAnimation on opacity {
                                                running: cell.modelData.importing === true
                                                loops: Animation.Infinite
                                                NumberAnimation { to: 0.3; duration: 550 }
                                                NumberAnimation { to: 1.0; duration: 550 }
                                            }
                                        }
                                        Text {
                                            anchors.verticalCenter: parent.verticalCenter
                                            text: cell.modelData.import_stage || lib.loc("导入中…")
                                            color: Theme.accent; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                                        }
                                    }
                                    Pill {
                                        visible: cell.modelData.importable && cell.modelData.importing !== true
                                        anchors.left: parent.left
                                        label: lib.loc("导入"); accent: true
                                        onClicked: if (controller) controller.importLibraryItem(cell.modelData.id)
                                    }
                                    Text {
                                        visible: !cell.modelData.importable && cell.modelData.importing !== true
                                        anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter
                                        text: lib.loc("已过期")
                                        color: Theme.warn; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                                    }
                                }
                            }
                        }
                    }
                }
            }

            Item {
                id: footer
                anchors.top: grid.bottom
                anchors.topMargin: 12
                width: parent.width
                implicitHeight: 32
                Pill {
                    anchors.horizontalCenter: parent.horizontalCenter
                    label: lib.loc("加载更多"); dashed: true
                    onClicked: if (controller) controller.loadMoreLibrary()
                }
            }
        }

        // ---- 底部：Meshy 网页快捷链接（常驻，空/满都显示）----
        Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }
        RowLayout {
            Layout.fillWidth: true
            spacing: 10
            Text {
                Layout.fillWidth: true
                text: lib.loc("在 Meshy 网页管理全部资产 →")
                color: wsMa.containsMouse ? Theme.accent : Theme.textMute
                font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                elide: Text.ElideRight
                MouseArea {
                    id: wsMa; anchors.fill: parent; hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: if (controller) controller.openMeshy("workspace")
                }
            }
            Text {
                text: lib.loc("Meshy 定价")
                color: proMa.containsMouse ? Theme.textBright : Theme.accent
                font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                MouseArea {
                    id: proMa; anchors.fill: parent; anchors.margins: -4; hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: if (controller) controller.openMeshy("pricing")
                }
            }
        }
    }
}
