import QtQuick
import HAgent

// Viewport screenshot / image result.
Item {
    id: ib
    property var block: ({})
    implicitHeight: frame.height

    Rectangle {
        id: frame
        width: parent.width
        height: img.height + 2
        color: Theme.codeBg
        border.color: Theme.border
        border.width: 1
        radius: Theme.radSm

        Image {
            id: img
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.top: parent.top; anchors.topMargin: 1
            width: parent.width - 2
            source: (ib.block && ib.block.src) ? ib.block.src : ""
            fillMode: Image.PreserveAspectFit
            asynchronous: true
            smooth: true
        }
    }
}
