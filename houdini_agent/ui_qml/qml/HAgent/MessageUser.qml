import QtQuick
import HAgent

// User message — right-aligned, bordered, square (Editorial).
// Shows attached image thumbnails (if any) above the text.
Item {
    id: root
    property var msg: ({})
    property var images: (msg && msg.images) ? msg.images : []
    implicitHeight: col.height

    Column {
        id: col
        anchors.right: parent.right
        width: parent.width
        spacing: 6

        // attached image thumbnails (right-aligned, wraps for multiple)
        Flow {
            anchors.right: parent.right
            width: Math.min(parent.width * 0.86, 320)
            layoutDirection: Qt.RightToLeft
            spacing: 6
            visible: root.images.length > 0
            Repeater {
                model: root.images
                delegate: Rectangle {
                    required property var modelData
                    width: 76; height: 76
                    color: Theme.codeBg
                    border.color: Theme.userBorder; border.width: 1
                    radius: Theme.radSm
                    clip: true
                    Image {
                        anchors.fill: parent; anchors.margins: 1
                        source: "" + modelData
                        fillMode: Image.PreserveAspectCrop
                        asynchronous: true
                    }
                }
            }
        }

        // text bubble
        Rectangle {
            id: bubble
            anchors.right: parent.right
            visible: txt.text.length > 0
            width: Math.min(parent.width * 0.86, txt.implicitWidth + 28)
            height: txt.implicitHeight + 22
            color: "transparent"
            border.color: Theme.userBorder
            border.width: 1
            radius: Theme.radSm

            TextEdit {
                id: txt
                anchors.left: parent.left; anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.leftMargin: 14; anchors.rightMargin: 14
                text: root.msg && root.msg.text ? root.msg.text : ""
                readOnly: true
                selectByMouse: true
                wrapMode: TextEdit.Wrap
                color: Theme.userFg
                selectionColor: Theme.accentSoft
                selectedTextColor: Theme.textBright
                font.family: Theme.fontBody
                font.pixelSize: Theme.fBody
            }
        }
    }
}
