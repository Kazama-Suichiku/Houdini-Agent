import QtQuick
import QtQuick.Controls.Basic
import HAgent

// Interactive plan card: steps (expandable) + DAG + confirm/reject.
Item {
    id: pc
    property var plan: ({})
    property var stepsLocal: (plan && plan.steps) ? plan.steps.slice() : []
    property var architecture: (plan && plan.architecture) ? plan.architecture : ({})
    property var phases: (plan && plan.phases) ? plan.phases : []
    property string actionState: ""   // "", "confirmed", "rejected"
    property bool editMode: false
    implicitHeight: box.height
    function loc(s) { return controller ? (controller.lang, controller.tr(s)) : s }
    function clone(v) {
        try { return JSON.parse(JSON.stringify(v || {})) } catch (e) { return v || {} }
    }
    function resetEditor() {
        stepsLocal = clone(plan.steps || [])
        if (titleEdit) titleEdit.text = plan.title || "Plan"
        if (overviewEdit) overviewEdit.text = plan.overview || ""
        if (revisionEdit) revisionEdit.text = ""
    }
    function makePayload() {
        var p = clone(plan)
        p.title = titleEdit.text
        p.overview = overviewEdit.text
        p.steps = []
        for (var i = 0; i < stepEditRepeater.count; i++) {
            var it = stepEditRepeater.itemAt(i)
            if (it) p.steps.push(it.stepPayload())
        }
        p.architecture = architecture
        return p
    }
    function submitManualEdit() {
        try {
            var ok = !controller || controller.applyPlanEdit(JSON.stringify(pc.makePayload()))
            if (ok) pc.editMode = false
        } catch (e) {
            if (controller) controller.showToast("保存计划失败：" + e)
        }
    }
    function submitAiRevision() {
        try {
            if (!controller) return
            var ok = controller.revisePlan(revisionEdit.text, JSON.stringify(pc.makePayload()))
            if (ok) pc.editMode = false
        } catch (e) {
            if (controller) controller.showToast("提交修改失败：" + e)
        }
    }
    onPlanChanged: if (!editMode) stepsLocal = clone(plan.steps || [])
    Connections {
        target: controller
        ignoreUnknownSignals: true
        function onPlanExecutionStarted() { if (pc.actionState === "pending") pc.actionState = "confirmed" }
        function onPlanConfirmFailed(msg) { if (pc.actionState === "pending") pc.actionState = "" }
    }

    Rectangle {
        id: box
        width: parent.width
        height: inner.implicitHeight
        color: "transparent"
        border.color: Theme.border
        border.width: 1
        radius: Theme.radSm

        // editorial: bold top accent rule
        Rectangle { width: parent.width; height: 2; color: Theme.accent }

        Column {
            id: inner
            width: parent.width
            topPadding: 2

            // header
            Item {
                width: parent.width
                height: Math.round(44 * Theme.scale)
                Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.border }
                Row {
                    anchors.left: parent.left; anchors.leftMargin: 14
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 9
                    Text { text: "◈"; color: Theme.accent; font.pixelSize: Theme.fMd; anchors.verticalCenter: parent.verticalCenter }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: pc.plan.title || "Plan"
                        color: Theme.textBright
                        font.family: Theme.fontDisplay
                        font.pixelSize: Theme.fMd
                        font.italic: true
                    }
                }
                Rectangle {
                    anchors.right: parent.right; anchors.rightMargin: 14
                    anchors.verticalCenter: parent.verticalCenter
                    width: badgeT.implicitWidth + 16; height: Math.round(20 * Theme.scale)
                    color: "transparent"; border.color: Theme.border; border.width: 1; radius: 100
                    Text { id: badgeT; anchors.centerIn: parent; text: pc.plan.badge || ""; color: Theme.textMute; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro }
                }
            }

            Column {
                visible: !!((pc.plan.overview && pc.plan.overview.length > 0) || pc.plan.complexity || pc.plan.estimated_total_operations)
                width: parent.width
                topPadding: 10; bottomPadding: 4; leftPadding: 14; rightPadding: 14
                spacing: 7
                Text {
                    visible: !!(pc.plan.overview && pc.plan.overview.length > 0)
                    width: parent.width - 28
                    text: pc.plan.overview || ""
                    wrapMode: Text.Wrap
                    color: Theme.textDim
                    font.family: Theme.fontBody; font.pixelSize: Theme.fSm
                }
                Row {
                    visible: !!(pc.plan.complexity || pc.plan.estimated_total_operations)
                    spacing: 6
                    Text {
                        visible: !!pc.plan.complexity
                        text: "COMPLEXITY " + String(pc.plan.complexity).toUpperCase()
                        color: Theme.textMute
                        font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                    }
                    Text {
                        visible: !!pc.plan.estimated_total_operations
                        text: "OPS " + pc.plan.estimated_total_operations
                        color: Theme.textMute
                        font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                    }
                }
            }

            // steps
            Column {
                width: parent.width
                topPadding: 8; bottomPadding: 6
                Repeater {
                    model: pc.stepsLocal
                    delegate: Item {
                        id: stepRow
                        required property int index
                        required property var modelData
                        property bool open: false
                        property bool hasDetail: !!((modelData.detail && modelData.detail.length > 0)
                                             || (modelData.sub_steps && modelData.sub_steps.length > 0)
                                             || (modelData.tools && modelData.tools.length > 0)
                                             || (modelData.expected_result && modelData.expected_result.length > 0))
                        width: parent.width
                        height: srLine.height + (open && hasDetail ? srDetail.implicitHeight : 0)

                        Item {
                            id: srLine
                            width: parent.width
                            height: Math.round(32 * Theme.scale)
                            Row {
                                anchors.left: parent.left; anchors.leftMargin: 14
                                anchors.verticalCenter: parent.verticalCenter
                                spacing: 10
                                Rectangle {
                                    width: Math.round(20 * Theme.scale); height: Math.round(20 * Theme.scale)
                                    radius: width / 2
                                    anchors.verticalCenter: parent.verticalCenter
                                    color: modelData.state === "done" ? Theme.okSoft
                                         : modelData.state === "active" ? Theme.accentSoft
                                         : modelData.state === "error" ? Qt.rgba(0.87, 0.6, 0.6, 0.14) : "transparent"
                                    border.width: 1
                                    border.color: (modelData.state === "pending") ? Theme.border : "transparent"
                                    Text {
                                        anchors.centerIn: parent
                                        text: modelData.state === "done" ? "✓"
                                            : modelData.state === "error" ? "✗" : (stepRow.index + 1)
                                        color: modelData.state === "done" ? Theme.ok
                                             : modelData.state === "active" ? Theme.accent
                                             : modelData.state === "error" ? Theme.err : Theme.textMute
                                        font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                                    }
                                }
                                Text {
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: modelData.label || modelData.title || ""
                                    width: Math.max(80, srLine.width - 140)
                                    elide: Text.ElideRight
                                    color: modelData.state === "pending" ? Theme.textDim : Theme.text
                                    font.family: Theme.fontBody; font.pixelSize: Theme.fSm
                                }
                                Text {
                                    visible: !!(modelData.risk && modelData.risk !== "low")
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: String(modelData.risk).toUpperCase()
                                    color: Theme.warn
                                    font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                                }
                            }
                            MouseArea {
                                anchors.fill: parent
                                enabled: stepRow.hasDetail
                                cursorShape: stepRow.hasDetail ? Qt.PointingHandCursor : Qt.ArrowCursor
                                onClicked: stepRow.open = !stepRow.open
                            }
                        }
                        Column {
                            id: srDetail
                            visible: stepRow.open && stepRow.hasDetail
                            anchors.top: srLine.bottom
                            width: parent.width
                            leftPadding: 44; rightPadding: 14; bottomPadding: 9
                            spacing: 4
                            Text {
                                visible: !!(modelData.detail && modelData.detail.length > 0)
                                width: parent.width - 58
                                text: modelData.detail || ""
                                wrapMode: Text.Wrap
                                color: Theme.textMute; font.family: Theme.fontBody; font.pixelSize: Theme.fXs
                            }
                            Repeater {
                                model: modelData.sub_steps || []
                                delegate: Text {
                                    required property var modelData
                                    width: srDetail.width - 58
                                    text: "├ " + modelData
                                    wrapMode: Text.Wrap
                                    color: Theme.textMute; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                                }
                            }
                            Text {
                                visible: !!(modelData.tools && modelData.tools.length > 0)
                                width: parent.width - 58
                                text: "TOOLS  " + (modelData.tools || []).join(", ")
                                wrapMode: Text.Wrap
                                color: Theme.accent; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                            }
                            Text {
                                visible: !!(modelData.expected_result && modelData.expected_result.length > 0)
                                width: parent.width - 58
                                text: "EXPECTED  " + modelData.expected_result
                                wrapMode: Text.Wrap
                                color: Theme.textDim; font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                            }
                        }
                    }
                }
            }

            // DAG
            Column {
                width: parent.width
                topPadding: 4; bottomPadding: 12; leftPadding: 14; rightPadding: 14
                spacing: 8
                Rectangle { width: parent.width - 28; height: 1; color: Theme.border }
                Row {
                    width: parent.width - 28
                    Text {
                        width: parent.width - nodesText.implicitWidth - 12
                        text: (pc.architecture && pc.architecture.nodes && pc.architecture.nodes.length > 0) ? "ARCHITECTURE DAG" : "STEP FLOW"
                        color: Theme.textMute
                        font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                    }
                    Text {
                        id: nodesText
                        text: ((pc.architecture.nodes || []).length || pc.stepsLocal.length) + " nodes"
                        color: Theme.textMute
                        font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                    }
                }
                Flickable {
                    id: dagScroll
                    width: parent.width - 28
                    height: Math.min(Math.max(planDag.implicitHeight, 110), 360)
                    contentWidth: planDag.implicitWidth
                    contentHeight: planDag.implicitHeight
                    clip: true
                    boundsBehavior: Flickable.StopAtBounds
                    boundsMovement: Flickable.StopAtBounds
                    pixelAligned: false
                    flickDeceleration: 3600
                    maximumFlickVelocity: 7200
                    ScrollBar.vertical: SmartScrollBar {}
                    ScrollBar.horizontal: SmartScrollBar {}
                    Rectangle { anchors.fill: parent; color: "transparent"; border.color: Theme.borderSoft; border.width: 1; radius: Theme.radSm }
                    PlanDag {
                        id: planDag
                        width: dagScroll.width
                        architecture: pc.architecture
                        steps: pc.stepsLocal
                    }
                }
            }

            // revision editor
            Rectangle {
                visible: pc.editMode && pc.actionState === ""
                width: parent.width
                height: visible ? editCol.implicitHeight + 24 : 0
                color: Theme.surface
                border.color: Theme.border
                border.width: 1
                Column {
                    id: editCol
                    width: parent.width - 28
                    anchors.left: parent.left; anchors.leftMargin: 14
                    anchors.top: parent.top; anchors.topMargin: 12
                    spacing: 10
                    Text {
                        width: parent.width
                        text: "修改当前计划 · 可手动微调，也可以写一句要求让 AI 基于原计划局部修改"
                        color: Theme.textMute
                        font.family: Theme.fontBody; font.pixelSize: Theme.fXs
                        wrapMode: Text.Wrap
                    }
                    TextField {
                        id: titleEdit
                        width: parent.width
                        text: pc.plan.title || "Plan"
                        color: Theme.text
                        selectedTextColor: Theme.bg
                        selectionColor: Theme.accent
                        font.family: Theme.fontBody; font.pixelSize: Theme.fSm
                        placeholderText: "Plan title"
                        background: Rectangle { color: Theme.bg; border.color: Theme.border; border.width: 1; radius: Theme.radSm }
                    }
                    TextArea {
                        id: overviewEdit
                        width: parent.width
                        height: Math.round(70 * Theme.scale)
                        text: pc.plan.overview || ""
                        wrapMode: TextArea.Wrap
                        color: Theme.text
                        selectedTextColor: Theme.bg
                        selectionColor: Theme.accent
                        font.family: Theme.fontBody; font.pixelSize: Theme.fXs
                        placeholderText: "Overview"
                        background: Rectangle { color: Theme.bg; border.color: Theme.border; border.width: 1; radius: Theme.radSm }
                    }
                    Column {
                        width: parent.width
                        spacing: 8
                        Repeater {
                            id: stepEditRepeater
                            model: pc.stepsLocal
                            delegate: Rectangle {
                                id: stepEdit
                                required property int index
                                required property var modelData
                                width: editCol.width
                                height: stepEditBody.implicitHeight + 16
                                color: Theme.bg
                                border.color: Theme.borderSoft
                                border.width: 1
                                radius: Theme.radSm
                                function stepPayload() {
                                    var s = pc.clone(modelData)
                                    s.title = stepTitle.text
                                    s.label = stepTitle.text
                                    s.description = stepDetail.text
                                    s.detail = stepDetail.text
                                    s.risk = stepRisk.text || s.risk || "low"
                                    return s
                                }
                                Column {
                                    id: stepEditBody
                                    width: parent.width - 16
                                    anchors.left: parent.left; anchors.leftMargin: 8
                                    anchors.top: parent.top; anchors.topMargin: 8
                                    spacing: 6
                                    Row {
                                        width: parent.width
                                        spacing: 8
                                        Text {
                                            text: String(stepEdit.index + 1)
                                            width: 20
                                            color: Theme.textMute
                                            font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                                            anchors.verticalCenter: parent.verticalCenter
                                        }
                                        TextField {
                                            id: stepTitle
                                            width: parent.width - 118
                                            text: modelData.title || modelData.label || ""
                                            color: Theme.text
                                            selectedTextColor: Theme.bg
                                            selectionColor: Theme.accent
                                            font.family: Theme.fontBody; font.pixelSize: Theme.fXs
                                            background: Rectangle { color: Theme.surface; border.color: Theme.borderSoft; border.width: 1; radius: Theme.radSm }
                                        }
                                        TextField {
                                            id: stepRisk
                                            width: 70
                                            text: modelData.risk || "low"
                                            color: Theme.warn
                                            selectedTextColor: Theme.bg
                                            selectionColor: Theme.accent
                                            font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                                            background: Rectangle { color: Theme.surface; border.color: Theme.borderSoft; border.width: 1; radius: Theme.radSm }
                                        }
                                    }
                                    TextArea {
                                        id: stepDetail
                                        width: parent.width
                                        height: Math.round(48 * Theme.scale)
                                        text: modelData.detail || modelData.description || ""
                                        wrapMode: TextArea.Wrap
                                        color: Theme.textDim
                                        selectedTextColor: Theme.bg
                                        selectionColor: Theme.accent
                                        font.family: Theme.fontBody; font.pixelSize: Theme.fXs
                                        background: Rectangle { color: Theme.surface; border.color: Theme.borderSoft; border.width: 1; radius: Theme.radSm }
                                    }
                                }
                            }
                        }
                    }
                    TextArea {
                        id: revisionEdit
                        width: parent.width
                        height: Math.round(70 * Theme.scale)
                        wrapMode: TextArea.Wrap
                        color: Theme.text
                        selectedTextColor: Theme.bg
                        selectionColor: Theme.accent
                        font.family: Theme.fontBody; font.pixelSize: Theme.fXs
                        placeholderText: "例如：第 3 步改成先做低分辨率预览；去掉第 8 步；DAG 保持原结构"
                        background: Rectangle { color: Theme.bg; border.color: Theme.border; border.width: 1; radius: Theme.radSm }
                    }
                    Row {
                        width: parent.width
                        spacing: 8
                        Rectangle {
                            width: (parent.width - 16) / 3; height: Math.round(28 * Theme.scale)
                            color: "transparent"; border.color: Theme.border; border.width: 1; radius: Theme.radSm
                            Text { anchors.centerIn: parent; text: "取消修改"; color: Theme.text; font.family: Theme.fontBody; font.pixelSize: Theme.fXs }
                            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: { pc.editMode = false; pc.resetEditor() } }
                        }
                        Rectangle {
                            width: (parent.width - 16) / 3; height: Math.round(28 * Theme.scale)
                            color: Theme.surface2; border.color: Theme.border; border.width: 1; radius: Theme.radSm
                            Text { anchors.centerIn: parent; text: "保存手动修改"; color: Theme.textBright; font.family: Theme.fontBody; font.pixelSize: Theme.fXs }
                            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: pc.submitManualEdit() }
                        }
                        Rectangle {
                            width: (parent.width - 16) / 3; height: Math.round(28 * Theme.scale)
                            color: Theme.accent; radius: Theme.radSm
                            Text { anchors.centerIn: parent; text: "AI 局部修改"; color: Theme.bg; font.family: Theme.fontBody; font.pixelSize: Theme.fXs }
                            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: pc.submitAiRevision() }
                        }
                    }
                }
            }

            // actions
            Item {
                width: parent.width
                height: Math.round(52 * Theme.scale)
                Row {
                    anchors.fill: parent
                    anchors.leftMargin: 14; anchors.rightMargin: 14
                    anchors.topMargin: 4; anchors.bottomMargin: 14
                    spacing: 8
                    visible: pc.actionState === "" && !pc.editMode
                    Rectangle {
                        width: (parent.width - 16) / 3; height: parent.height - 18
                        color: "transparent"; border.color: Theme.border; border.width: 1; radius: Theme.radSm
                        Text { anchors.centerIn: parent; text: "修改计划"; color: Theme.text; font.family: Theme.fontBody; font.pixelSize: Theme.fSm }
                        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                            onClicked: { pc.editMode = true; pc.resetEditor() } }
                    }
                    Rectangle {
                        width: (parent.width - 16) / 3; height: parent.height - 18
                        color: "transparent"; border.color: Theme.border; border.width: 1; radius: Theme.radSm
                        Text { anchors.centerIn: parent; text: pc.loc("驳回"); color: Theme.text; font.family: Theme.fontBody; font.pixelSize: Theme.fSm }
                        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                            onClicked: { if (!controller || controller.rejectPlan("")) pc.actionState = "rejected" } }
                    }
                    Rectangle {
                        width: (parent.width - 16) / 3; height: parent.height - 18
                        color: Theme.accent; radius: Theme.radSm
                        Text { anchors.centerIn: parent; text: pc.loc("确认执行"); color: Theme.bg; font.family: Theme.fontBody; font.pixelSize: Theme.fSm }
                        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                if (!controller) { pc.actionState = "confirmed"; return }
                                var r = controller.confirmPlan("")
                                if (r === "confirmed") pc.actionState = "confirmed"
                                else if (r === "pending") pc.actionState = "pending"
                            } }
                    }
                }
                Text {
                    anchors.left: parent.left; anchors.leftMargin: 14
                    anchors.verticalCenter: parent.verticalCenter
                    visible: pc.actionState !== ""
                    text: pc.actionState === "confirmed" ? ("✓ " + pc.loc("计划已确认 · 开始执行"))
                        : pc.actionState === "pending" ? "… 计划确认中，等待生成收尾"
                        : ("✕ " + pc.loc("计划已驳回"))
                    color: pc.actionState === "confirmed" ? Theme.ok : pc.actionState === "pending" ? Theme.warn : Theme.textMute
                    font.family: Theme.fontMono; font.pixelSize: Theme.fSm
                }
            }
        }
    }
    // step states are driven live by the controller (execution phase),
    // not advanced locally.
}
