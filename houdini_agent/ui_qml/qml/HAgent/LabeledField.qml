import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import HAgent

ColumnLayout {
    id: root
    property alias text: field.text
    property string label: ""
    property string placeholder: ""
    property bool password: false
    spacing: 5

    Text {
        text: root.label
        color: Theme.textDim
        font.family: Theme.fontMono
        font.pixelSize: Theme.fMicro
    }
    TextField {
        id: field
        Layout.fillWidth: true
        echoMode: root.password ? TextInput.Password : TextInput.Normal
        color: Theme.textBright
        placeholderText: root.placeholder
        placeholderTextColor: Theme.textMute
        font.family: Theme.fontMono
        font.pixelSize: Theme.fSm
        background: Rectangle {
            color: Theme.surface
            border.color: field.activeFocus ? Theme.accentLine : Theme.border
            border.width: 1
            radius: Theme.radSm
        }
    }
}
