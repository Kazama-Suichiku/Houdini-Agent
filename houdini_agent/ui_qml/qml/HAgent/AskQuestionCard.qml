import QtQuick
import QtQuick.Layouts
import HAgent

// Clarifying-question card (Plan mode ask_question). Single-select per question.
Item {
    id: aq
    property var block: ({})
    property var questions: (block && block.questions) ? block.questions : []
    property string state: (block && block.state) ? block.state : "pending"
    property var answers: ({})
    implicitHeight: box.height
    function loc(s) { return controller ? (controller.lang, controller.tr(s)) : s }
    function isComplete() {
        for (var i = 0; i < questions.length; i++) {
            var q = questions[i]
            var qid = q.id || ("q" + i)
            var a = answers[qid]
            if (q.allow_multiple === true) {
                if (!(a instanceof Array) || a.length === 0) return false
            } else if (a === undefined || a === null || a === "") {
                return false
            }
        }
        return true
    }

    Rectangle {
        id: box
        width: parent.width
        height: inner.implicitHeight + 22
        color: Theme.accentSoft
        border.color: Theme.accentLine
        border.width: 1
        radius: Theme.radSm

        Column {
            id: inner
            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
            anchors.leftMargin: 13; anchors.rightMargin: 13; anchors.topMargin: 11
            spacing: 11

            Text {
                text: aq.loc("需要你的确认")
                color: Theme.accent
                font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
                font.letterSpacing: Theme.trackLabel
            }

            Repeater {
                model: aq.questions
                delegate: Column {
                    id: qCol
                    required property int index
                    required property var modelData
                    property string qid: modelData.id || ("q" + index)
                    property bool multi: modelData.allow_multiple === true
                    width: inner.width
                    spacing: 6
                    Text {
                        width: parent.width
                        text: (qCol.modelData.prompt || "") + (qCol.multi ? "  " + aq.loc("（可多选）") : "")
                        color: Theme.text
                        font.family: Theme.fontBody; font.pixelSize: Theme.fSm
                        wrapMode: Text.Wrap
                    }
                    Flow {
                        width: parent.width
                        spacing: 6
                        Repeater {
                            model: qCol.modelData.options || []
                            delegate: Pill {
                                required property var modelData
                                label: modelData.label
                                active: qCol.multi
                                        ? ((aq.answers[qCol.qid] instanceof Array)
                                           && aq.answers[qCol.qid].indexOf(modelData.id) >= 0)
                                        : (aq.answers[qCol.qid] === modelData.id)
                                onClicked: {
                                    var a = {}
                                    for (var k in aq.answers) a[k] = aq.answers[k]
                                    if (qCol.multi) {
                                        var arr = (a[qCol.qid] instanceof Array) ? a[qCol.qid].slice() : []
                                        var j = arr.indexOf(modelData.id)
                                        if (j >= 0) arr.splice(j, 1); else arr.push(modelData.id)
                                        a[qCol.qid] = arr
                                    } else {
                                        a[qCol.qid] = modelData.id
                                    }
                                    aq.answers = a
                                }
                            }
                        }
                    }
                }
            }

            Row {
                visible: aq.state === "pending"
                Pill { label: aq.loc("提交"); accent: true
                    onClicked: {
                        if (!aq.isComplete()) {
                            if (controller) controller.showToast("请先完成所有问题")
                            return
                        }
                        if (controller) controller.resolveQuestion(aq.block.qid, JSON.stringify(aq.answers))
                    } }
            }
            Text {
                visible: aq.state !== "pending"
                text: "✓ " + aq.loc("已回答")
                color: Theme.ok
                font.family: Theme.fontMono; font.pixelSize: Theme.fXs
            }
        }
    }
}
