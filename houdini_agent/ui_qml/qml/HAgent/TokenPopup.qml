import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import HAgent

// Token analytics (native QML; no billing/cost display).
Popup {
    id: tp
    property var st: ({})
    function loc(s) { return controller ? (controller.lang, controller.tr(s)) : s }
    function fmt(n) {
        n = n || 0
        if (n >= 1000000) return (n / 1000000).toFixed(2) + "m"
        if (n >= 1000) return (n / 1000).toFixed(1) + "k"
        return "" + n
    }
    function raw(n) { return Math.round(Number(n || 0)).toLocaleString(Qt.locale(), "f", 0) }
    function clamp01(n) { return Math.max(0, Math.min(1, n || 0)) }
    function total() { return Math.max(1, st.total || ((st.input || 0) + (st.output || 0) + (st.reasoning || 0))) }
    function pct(n) { return clamp01((n || 0) / total()) }
    function pctOf(n, d) { return clamp01((n || 0) / Math.max(1, d || 0)) }
    function pctText(n) {
        var p = clamp01(n) * 100
        if (p > 0 && p < 1) return p.toFixed(1) + "%"
        return Math.round(p) + "%"
    }

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(560, parent ? parent.width - 32 : 560)
    padding: 18
    modal: true
    closePolicy: Popup.CloseOnPressOutside | Popup.CloseOnEscape
    background: Rectangle { color: Theme.bg; border.color: Theme.border; border.width: 1; radius: Theme.radSm }

    property var summaryRows: [
        { k: loc("总计"), v: raw(st.total), sub: "TOTAL TOKENS", accent: Theme.accent },
        { k: loc("请求次数"), v: raw(st.requests), sub: "REQUESTS", accent: Theme.textBright },
        { k: loc("平均/请求"), v: raw(st.avg_per_request), sub: "AVG / REQ", accent: Theme.ok },
        { k: loc("上下文"), v: st.ctx_text || "0 / 0", sub: "CONTEXT", accent: Theme.warn }
    ]
    property var tokenRows: [
        { k: loc("输入 token"), v: st.input || 0, c: Theme.accent, soft: Theme.accentSoft },
        { k: loc("输出 token"), v: st.output || 0, c: Theme.ok, soft: Theme.okSoft },
        { k: loc("推理 token"), v: st.reasoning || 0, c: Theme.warn, soft: Theme.warnSoft }
    ]
    property var cacheRows: [
        { k: loc("缓存命中"), v: st.cache_read || 0, c: Theme.textMute, soft: Theme.surface2 },
        { k: loc("缓存写入"), v: st.cache_write || 0, c: Theme.textDim, soft: Theme.surface }
    ]

    contentItem: ColumnLayout {
        spacing: 16
        RowLayout {
            Layout.fillWidth: true
            Text {
                Layout.fillWidth: true
                text: tp.loc("Token 用量")
                color: Theme.textBright
                font.family: Theme.fontDisplay; font.pixelSize: Theme.fXl; font.weight: Font.Medium
            }
            Column {
                Layout.alignment: Qt.AlignRight | Qt.AlignVCenter
                spacing: 2
                Text {
                    anchors.right: parent.right
                    text: "TOKEN ANALYTICS"
                    color: Theme.textMute
                    font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                    font.letterSpacing: Theme.trackLabel
                }
                Text {
                    anchors.right: parent.right
                    text: st.model || ""
                    color: Theme.textDim
                    font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                    elide: Text.ElideLeft
                    width: Math.round(190 * Theme.scale)
                    horizontalAlignment: Text.AlignRight
                }
            }
        }
        Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }

        GridLayout {
            Layout.fillWidth: true
            columns: 2
            rowSpacing: 8
            columnSpacing: 8
            Repeater {
                model: tp.summaryRows
                delegate: Rectangle {
                    required property var modelData
                    Layout.fillWidth: true
                    Layout.preferredWidth: Math.round(250 * Theme.scale)
                    Layout.preferredHeight: Math.round(70 * Theme.scale)
                    color: Theme.surface
                    border.color: Theme.border
                    border.width: 1
                    radius: Theme.radSm
                    Text {
                        anchors.left: parent.left; anchors.leftMargin: 10
                        anchors.top: parent.top; anchors.topMargin: 9
                        text: modelData.sub
                        color: Theme.textMute
                        font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                    }
                    Text {
                        text: modelData.v
                        anchors.left: parent.left; anchors.leftMargin: 10
                        anchors.right: parent.right; anchors.rightMargin: 10
                        anchors.top: parent.top; anchors.topMargin: 28
                        elide: Text.ElideRight
                        color: modelData.accent
                        font.family: Theme.fontMono; font.pixelSize: Theme.fLg
                    }
                    Text {
                        anchors.left: parent.left; anchors.leftMargin: 10
                        anchors.right: parent.right; anchors.rightMargin: 10
                        anchors.bottom: parent.bottom; anchors.bottomMargin: 8
                        text: modelData.k
                        elide: Text.ElideRight
                        color: Theme.textDim
                        font.family: Theme.fontBody; font.pixelSize: Theme.fXs
                    }
                }
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 8
            RowLayout {
                Layout.fillWidth: true
                Text {
                    Layout.fillWidth: true
                    text: tp.loc("上下文")
                    color: Theme.textBright
                    font.family: Theme.fontBody; font.pixelSize: Theme.fSm
                }
                Text {
                    text: tp.raw(st.ctx_used) + " / " + tp.raw(st.ctx_limit) + " · " + tp.pctText(st.ctx_pct)
                    color: Theme.textMute
                    font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                }
            }
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 8
                color: Theme.surface
                radius: 2
                Rectangle {
                    width: parent.width * tp.clamp01(st.ctx_pct)
                    height: parent.height
                    radius: 2
                    color: tp.clamp01(st.ctx_pct) > 0.75 ? Theme.warn : Theme.accent
                }
            }
        }

        Text {
            Layout.fillWidth: true
            text: tp.loc("Token 结构")
            color: Theme.textBright
            font.family: Theme.fontBody; font.pixelSize: Theme.fSm
        }
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: Math.round(40 * Theme.scale)
            color: Theme.surface
            border.color: Theme.borderSoft
            border.width: 1
            radius: Theme.radSm
            Row {
                anchors.fill: parent
                anchors.margins: 1
                Repeater {
                    model: tp.tokenRows
                    delegate: Rectangle {
                        required property var modelData
                        width: modelData.v > 0 ? Math.max(2, (parent.width - 2) * tp.pct(modelData.v)) : 0
                        height: parent.height
                        color: modelData.c
                        opacity: 0.72
                    }
                }
            }
            Text {
                anchors.centerIn: parent
                text: "TOTAL  " + tp.raw(tp.st.total || 0) + " TOKENS"
                color: Theme.bg
                opacity: (tp.st.total || 0) > 0 ? 0.86 : 0
                font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
            }
        }

        Column {
            Layout.fillWidth: true
            spacing: 8
            Repeater {
                model: tp.tokenRows
                delegate: Item {
                    required property var modelData
                    width: parent.width
                    height: Math.round(34 * Theme.scale)
                    Text {
                        anchors.left: parent.left
                        anchors.top: parent.top
                        text: modelData.k
                        color: Theme.text
                        font.family: Theme.fontBody; font.pixelSize: Theme.fSm
                    }
                    Text {
                        anchors.right: parent.right
                        anchors.top: parent.top
                        text: tp.raw(modelData.v) + "  ·  " + Math.round(tp.pct(modelData.v) * 100) + "%"
                        color: modelData.c
                        font.family: Theme.fontMono; font.pixelSize: Theme.fXs
                    }
                    Rectangle {
                        anchors.left: parent.left; anchors.right: parent.right
                        anchors.bottom: parent.bottom
                        height: 4
                        color: modelData.soft
                        radius: 1
                        Rectangle {
                            width: parent.width * tp.pct(modelData.v)
                            height: parent.height
                            radius: 1
                            color: modelData.c
                        }
                    }
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: Math.round(56 * Theme.scale)
            color: "transparent"
            border.color: Theme.borderSoft
            border.width: 1
            radius: Theme.radSm
            RowLayout {
                anchors.fill: parent
                anchors.margins: 10
                spacing: 12
                Column {
                    Layout.fillWidth: true
                    spacing: 4
                    Text {
                        text: tp.loc("缓存效率") + "  " + tp.pctText(tp.st.cache_hit_rate)
                        color: Theme.text
                        font.family: Theme.fontBody; font.pixelSize: Theme.fSm
                    }
                    Rectangle {
                        width: parent.width
                        height: 5
                        color: Theme.surface2
                        radius: 1
                        Rectangle {
                            width: parent.width * tp.clamp01(tp.st.cache_hit_rate)
                            height: parent.height
                            radius: 1
                            color: Theme.accent
                        }
                    }
                }
                Repeater {
                    model: tp.cacheRows
                    delegate: Column {
                        required property var modelData
                        Layout.preferredWidth: Math.round(78 * Theme.scale)
                        spacing: 3
                        Text {
                            text: modelData.k
                            color: Theme.textDim
                            font.family: Theme.fontBody; font.pixelSize: Theme.fXs
                            elide: Text.ElideRight
                            width: parent.width
                        }
                        Text {
                            text: tp.raw(modelData.v)
                            color: modelData.c
                            font.family: Theme.fontMono; font.pixelSize: Theme.fXs
                            elide: Text.ElideRight
                            width: parent.width
                        }
                    }
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Item { Layout.fillWidth: true }
            Pill { label: tp.loc("关闭"); accent: true; onClicked: tp.close() }
        }
    }
}
