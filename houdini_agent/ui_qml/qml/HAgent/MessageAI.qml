import QtQuick
import QtQuick.Layouts
import HAgent

// AI response — left rule + stacked blocks. Blocks come from a per-message
// QAbstractListModel (msg.bm) so only changed rows update (no full rebuild).
Item {
    id: root
    property var msg: ({})
    implicitHeight: bodyRow.implicitHeight

    RowLayout {
        id: bodyRow
        width: parent.width
        spacing: 0

        Rectangle {
            Layout.fillHeight: true
            Layout.preferredWidth: 1
            color: Theme.accentLine
        }

        ColumnLayout {
            id: col
            Layout.fillWidth: true
            Layout.leftMargin: 14
            spacing: 12

            // empty-state generating indicator
            RowLayout {
                visible: rep.count === 0
                spacing: 8
                Rectangle {
                    width: 7; height: 7; radius: 4; color: Theme.accent
                    SequentialAnimation on opacity {
                        running: rep.count === 0; loops: Animation.Infinite
                        NumberAnimation { to: 0.3; duration: 600 }
                        NumberAnimation { to: 1.0; duration: 600 }
                    }
                }
                Text { text: "Thinking…"; color: Theme.textDim; font.family: Theme.fontBody; font.pixelSize: Theme.fSm }
            }

            Repeater {
                id: rep
                model: root.msg ? root.msg.bm : null
                delegate: Loader {
                    id: bl
                    required property var block
                    property bool shown: block.kind !== "thinking" || !controller || controller.showThinking
                    Layout.fillWidth: true
                    visible: shown
                    active: shown
                    sourceComponent: block.kind === "thinking" ? cThink
                                   : block.kind === "exec"     ? cExec
                                   : block.kind === "nodeop"   ? cNode
                                   : block.kind === "code"     ? cCode
                                   : block.kind === "confirm"  ? cConfirm
                                   : block.kind === "askq"     ? cAskq
                                   : block.kind === "image"    ? cImage
                                   : block.kind === "todo"     ? cTodo
                                   : block.kind === "shell"    ? cShell
                                   : block.kind === "codepreview" ? cPrev
                                   : block.kind === "planstream" ? cPlanS
                                   : block.kind === "meshy"    ? cMeshy
                                   : block.kind === "concept"  ? cConcept
                                   : cProse
                    Component { id: cThink; ThinkingBlock { block: bl.block; width: bl.width } }
                    Component { id: cExec;  ExecBlock     { block: bl.block; width: bl.width } }
                    Component { id: cNode;  NodeOpRow     { block: bl.block; width: bl.width } }
                    Component { id: cCode;  CodeBlock     { block: bl.block; width: bl.width } }
                    Component { id: cProse; ProseBlock    { block: bl.block; width: bl.width } }
                    Component { id: cConfirm; ConfirmCard { block: bl.block; width: bl.width } }
                    Component { id: cAskq;  AskQuestionCard { block: bl.block; width: bl.width } }
                    Component { id: cImage; ImageBlock { block: bl.block; width: bl.width } }
                    Component { id: cTodo;  TodoBlock { block: bl.block; width: bl.width } }
                    Component { id: cShell; ShellBlock { block: bl.block; width: bl.width } }
                    Component { id: cPrev;  CodePreviewBlock { block: bl.block; width: bl.width } }
                    Component { id: cPlanS; PlanStreamBlock { block: bl.block; width: bl.width } }
                    Component { id: cMeshy; MeshyCard { block: bl.block; width: bl.width } }
                    Component { id: cConcept; ConceptGalleryCard { block: bl.block; width: bl.width } }
                }
            }
        }
    }
}
