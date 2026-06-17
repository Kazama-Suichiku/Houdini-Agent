import QtQuick
import QtQuick.Layouts
import HAgent

// Multi-session tab strip (driven by the controller).
Rectangle {
    id: tabs
    color: "transparent"
    implicitHeight: Math.round(36 * Theme.scale)

    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.border }

    property var items: controller ? controller.sessionItems() : []
    Connections {
        target: controller
        function onSessionsChanged() { tabs.items = controller.sessionItems() }
    }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 12; anchors.rightMargin: 12
        spacing: 4

        Repeater {
            model: tabs.items
            delegate: Row {
                required property int index
                required property var modelData
                spacing: 0
                Pill {
                    label: modelData.title
                    active: modelData.active
                    uppercase: true
                    onClicked: if (controller) controller.switchSession(index)
                }
                Text {
                    visible: tabs.items.length > 1
                    text: "×"; color: Theme.textMute; font.pixelSize: Theme.fSm
                    anchors.verticalCenter: parent.verticalCenter
                    leftPadding: 2; rightPadding: 4
                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                        onClicked: if (controller) controller.closeSession(index) }
                }
            }
        }
        Pill {
            label: "+"
            dashed: true
            onClicked: if (controller) controller.newSession()
        }
        Item { Layout.fillWidth: true }
    }
}
