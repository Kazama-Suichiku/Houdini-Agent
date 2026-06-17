import QtQuick
import HAgent

// Markdown/rich-text answer paragraph — selectable (drag to select, Ctrl+C).
TextEdit {
    id: prose
    property var block: ({})
    textFormat: TextEdit.RichText
    text: (block && block.html) ? block.html : ""
    readOnly: true
    selectByMouse: true
    persistentSelection: true
    wrapMode: TextEdit.Wrap
    color: Theme.text
    selectionColor: Theme.accentSoft
    selectedTextColor: Theme.textBright
    font.family: Theme.fontBody
    font.pixelSize: Theme.fMd
    onLinkActivated: function(link) { if (controller) controller.focusNode(link) }
}
