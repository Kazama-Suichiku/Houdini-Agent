import QtQuick
import QtQuick.Layouts
import HAgent

// Concept-gallery card for meshy_concept_to_3d. Phases:
//   gen      -> generating concept images (progress bar)
//   pick     -> image grid (multi-select) + editable prompt + actions
//   making3d -> generating 3D from the selected concepts (progress)
//   done / cancelled -> terminal
// Block: { token, phase, prompt, count, images:[{index,image}], selected:[], progress, note }
Item {
    id: cg
    property var block: ({})
    property string token: (block && block.token) ? block.token : ""
    property string phase: (block && block.phase) ? block.phase : "gen"
    property string mode: (block && block.mode) ? block.mode : "concept"  // "concept" | "image" | "batch"
    property string title: (block && block.title) ? block.title : loc("概念图")
    property var images: (block && block.images) ? block.images : []
    property var results: (block && block.results) ? block.results : []
    property int count: (block && block.count) ? block.count : 2
    // gen   -> `count` slots (filled-by-index or generating placeholder) — parallelism visible
    // done  -> the generated 3D model thumbnails (the payoff: concept -> 3D closed loop)
    // else  -> the concept images
    property var displayImages: {
        if (phase === "gen") {
            var byIdx = {}
            for (var k = 0; k < images.length; k++) byIdx[images[k].index] = images[k]
            var out = []
            for (var i = 0; i < count; i++)
                out.push(byIdx[i] ? byIdx[i] : {"index": i, "pending": true})
            return out
        }
        if (phase === "done" && results.length > 0) return results
        return images
    }
    property int prog: (block && block.progress !== undefined) ? block.progress : 0
    property string note: (block && block.note) ? block.note : ""
    property var sel: []        // locally-selected indices (pick phase)

    function loc(s) { return controller ? (controller.lang, controller.tr(s)) : s }
    function fileUrl(p) { return (p && ("" + p).length) ? ("file:///" + ("" + p).replace(/\\/g, "/")) : "" }
    function isSel(i) { return sel.indexOf(i) >= 0 }
    function toggle(i) {
        var a = sel.slice(); var j = a.indexOf(i)
        if (j >= 0) a.splice(j, 1); else a.push(i)
        sel = a
    }
    // fresh batch begins -> clear selection
    onPhaseChanged: { if (phase === "gen") sel = [] }

    implicitHeight: box.height

    Rectangle {
        id: box
        width: parent.width
        height: inner.implicitHeight + 22
        color: Theme.accentSoft
        border.color: cg.phase === "cancelled" ? Theme.border : Theme.accentLine
        border.width: 1
        radius: Theme.radSm

        Column {
            id: inner
            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
            anchors.leftMargin: 13; anchors.rightMargin: 13; anchors.topMargin: 11
            spacing: 11

            // header
            Item {
                width: parent.width; height: hdr.implicitHeight
                Text {
                    id: hdr
                    text: ("MESHY · " + cg.title).toUpperCase()
                    color: Theme.accent; font.family: Theme.fontMono
                    font.pixelSize: Theme.fMicro; font.letterSpacing: Theme.trackLabel
                }
                Text {
                    anchors.right: parent.right
                    text: cg.phase === "pick" ? (cg.sel.length + " " + cg.loc("已选"))
                        : cg.phase === "done" ? "✓"
                        : cg.phase === "background" ? "↗"
                        : cg.phase === "cancelled" ? "—"
                        : (cg.prog + "%")
                    color: Theme.accent; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                }
            }

            // note line
            Text {
                visible: cg.note.length > 0
                width: parent.width
                text: cg.note
                color: Theme.textMute; font.family: Theme.fontBody; font.pixelSize: Theme.fXs
                wrapMode: Text.Wrap
            }

            // progress bar (gen / making3d)
            Rectangle {
                visible: cg.phase === "gen" || cg.phase === "making3d"
                width: parent.width; height: 2; color: Theme.border
                Rectangle {
                    height: parent.height
                    width: parent.width * Math.max(0, Math.min(100, cg.prog)) / 100.0
                    color: Theme.accent
                    Behavior on width { NumberAnimation { duration: 250 } }
                }
            }

            // 转入后台（生成 / 做3D 进行中）
            Item {
                visible: (cg.phase === "gen" || cg.phase === "making3d") && cg.token.length > 0
                width: parent.width
                height: visible ? Math.round(30 * Theme.scale) : 0
                Text {
                    anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter
                    width: parent.width - 110
                    text: cg.loc("耗时较久？可转后台")
                    color: Theme.textMute; font.family: Theme.fontBody; font.pixelSize: Theme.fMicro
                    elide: Text.ElideRight
                }
                Pill {
                    anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                    label: cg.loc("转入后台"); accent: true
                    onClicked: if (controller) controller.backgroundMeshyTask(cg.token)
                }
            }

            // image grid
            Grid {
                id: grid
                width: parent.width
                visible: cg.displayImages.length > 0
                columns: 2
                spacing: 8
                Repeater {
                    model: cg.displayImages
                    delegate: Item {
                        id: cell
                        required property var modelData
                        property int idx: modelData.index
                        property bool pending: modelData.pending === true
                        // no selection styling on the done-phase result thumbnails
                        property bool selected: !pending && cg.phase !== "done" && cg.isSel(idx)
                        property bool pickable: cg.phase === "pick" && !pending
                        width: (grid.width - grid.spacing) / 2
                        height: width
                        Rectangle {
                            anchors.fill: parent
                            color: Theme.surface
                            border.width: cell.selected ? 2 : 1
                            border.color: cell.selected ? Theme.accent : Theme.border
                            radius: Theme.radSm
                            Image {
                                visible: !cell.pending
                                anchors.fill: parent; anchors.margins: 2
                                source: cg.fileUrl(cell.modelData.image)
                                // 完整显示整图（非正方形比例不裁切，正方格内留边），尊重 API 生成的实际画幅
                                fillMode: Image.PreserveAspectFit
                                asynchronous: true; clip: true
                            }
                            // generating placeholder (pulsing dot + label)
                            Column {
                                visible: cell.pending
                                anchors.centerIn: parent
                                spacing: 8
                                Rectangle {
                                    width: 9; height: 9; radius: 5; color: Theme.accent
                                    anchors.horizontalCenter: parent.horizontalCenter
                                    SequentialAnimation on opacity {
                                        running: cell.pending; loops: Animation.Infinite
                                        NumberAnimation { to: 0.25; duration: 600 }
                                        NumberAnimation { to: 1.0; duration: 600 }
                                    }
                                }
                                Text {
                                    text: cg.loc("生成中…")
                                    color: Theme.textMute; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                                }
                            }
                            // dim non-selected concepts while 3D is generating
                            Rectangle {
                                anchors.fill: parent; anchors.margins: 2; radius: Theme.radSm
                                visible: cg.phase === "making3d" && cg.sel.length > 0 && !cell.selected
                                color: "#cc0d0d0d"
                            }
                            // selected check badge
                            Rectangle {
                                visible: cell.selected
                                anchors.top: parent.top; anchors.right: parent.right; anchors.margins: 5
                                width: 18; height: 18; radius: 9; color: Theme.accent
                                Text { anchors.centerIn: parent; text: "✓"; color: "#0d0d0d"; font.pixelSize: Theme.fMicro }
                            }
                            // per-image prompt caption (useful when each image used a different prompt)
                            Rectangle {
                                visible: cg.phase === "pick" && !cell.pending
                                         && cell.modelData.prompt !== undefined
                                         && ("" + cell.modelData.prompt).length > 0
                                anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
                                anchors.margins: 2
                                height: capTxt.implicitHeight + 6
                                color: "#cc0d0d0d"
                                Text {
                                    id: capTxt
                                    anchors.fill: parent; anchors.margins: 3
                                    text: ("" + cell.modelData.prompt)
                                    elide: Text.ElideRight; maximumLineCount: 2; wrapMode: Text.Wrap
                                    color: Theme.textDim; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                                }
                            }
                            MouseArea {
                                anchors.fill: parent
                                enabled: cell.pickable
                                cursorShape: cell.pickable ? Qt.PointingHandCursor : Qt.ArrowCursor
                                onClicked: cg.toggle(cell.idx)
                            }
                        }
                    }
                }
            }

            // editable prompt + actions (pick phase)
            Column {
                visible: cg.phase === "pick"
                width: parent.width
                spacing: 8
                LabeledField {
                    id: promptField
                    width: parent.width
                    label: cg.loc("提示词（重生留空=沿用原词；二次编辑必填改动）")
                    placeholder: (cg.block && cg.block.prompt) ? ("" + cg.block.prompt) : cg.loc("描述想要的改动…")
                    text: ""
                }
                Flow {
                    width: parent.width
                    spacing: 6
                    Pill {
                        label: cg.mode === "image" ? cg.loc("选中的做成 3D") : cg.loc("生成选中的 3D")
                        accent: cg.sel.length > 0
                        onClicked: {
                            if (cg.sel.length === 0) {
                                if (controller) controller.showToast(cg.loc("请先选择至少一张概念图"))
                                return
                            }
                            if (controller)
                                controller.resolveConcept(cg.token, JSON.stringify({action: "submit", selected: cg.sel}))
                        }
                    }
                    Pill {
                        label: cg.loc("换提示词重新生成"); dashed: true
                        onClicked: {
                            if (controller)
                                controller.resolveConcept(cg.token, JSON.stringify({action: "regenerate", prompt: promptField.text}))
                        }
                    }
                    // 二次编辑：以选中图为参考 + 提示词，图生图局部改图（必须先填改动）
                    Pill {
                        label: cg.loc("二次编辑选中图"); dashed: true
                        onClicked: {
                            if (cg.sel.length === 0) {
                                if (controller) controller.showToast(cg.loc("请先选中要编辑的图片"))
                                return
                            }
                            if (promptField.text.trim().length === 0) {
                                if (controller) controller.showToast(cg.loc("请先在上方填写想要的改动"))
                                return
                            }
                            if (controller)
                                controller.resolveConcept(cg.token, JSON.stringify({action: "edit", selected: cg.sel, prompt: promptField.text}))
                        }
                    }
                    // image mode: keep just the pictures (no 3D); concept mode: cancel
                    Pill {
                        visible: cg.mode === "image"
                        label: cg.loc("完成"); accent: cg.sel.length === 0
                        onClicked: {
                            if (controller)
                                controller.resolveConcept(cg.token, JSON.stringify({action: "done", selected: cg.sel}))
                        }
                    }
                    Pill {
                        visible: cg.mode !== "image"
                        label: cg.loc("取消"); dashed: true
                        onClicked: {
                            if (controller)
                                controller.resolveConcept(cg.token, JSON.stringify({action: "cancel"}))
                        }
                    }
                }
            }
        }
    }
}
