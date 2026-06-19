import QtQuick
import QtQuick.Controls
import HAgent

// Scrollable conversation. Top-level rows: user | ai | plan.
Flickable {
    id: view
    clip: true
    contentWidth: width
    contentHeight: Math.max(height, rows.y + rows.implicitHeight + 18)
    boundsBehavior: Flickable.StopAtBounds
    boundsMovement: Flickable.StopAtBounds
    pixelAligned: false
    flickDeceleration: 3600
    maximumFlickVelocity: 7200

    // Keep every message instantiated instead of virtualizing variable-height
    // rows. This avoids scrollbar thumb jumps while dragging across mixed
    // user/AI/plan message heights.
    property bool stick: true
    function nearBottom() {
        return contentHeight <= height || (contentY >= contentHeight - height - 48)
    }
    function bottomY() {
        return Math.max(0, contentHeight - height)
    }
    function scrollToEndIfSticky() {
        if (stick && !vbar.pressed) contentY = bottomY()
    }
    onHeightChanged: Qt.callLater(scrollToEndIfSticky)
    onContentHeightChanged: Qt.callLater(scrollToEndIfSticky)
    onMovementStarted: stick = false
    onMovementEnded: stick = nearBottom()
    onFlickEnded: stick = nearBottom()

    Column {
        id: rows
        x: 16
        y: 18
        width: view.width - 32
        spacing: 20

        // 空会话起手式（仅在没有任何消息时显示）
        EmptyState {
            width: rows.width
            visible: rep.count === 0
            height: visible ? implicitHeight : 0
        }

        Repeater {
            id: rep
            model: chatModel
            delegate: Loader {
                id: ld
                required property string rtype
                required property var payload
                width: rows.width
                height: item ? item.implicitHeight : 0
                sourceComponent: rtype === "user" ? cUser
                               : rtype === "plan" ? cPlan
                               : cAi
                Component { id: cUser; MessageUser { msg: ld.payload; width: ld.width } }
                Component { id: cAi;   MessageAI  { msg: ld.payload; width: ld.width } }
                Component { id: cPlan; PlanCard   { plan: ld.payload; width: ld.width } }
            }
            onCountChanged: Qt.callLater(view.scrollToEndIfSticky)
        }
    }

    ScrollBar.vertical: SmartScrollBar {
        id: vbar
        onPressedChanged: {
            if (pressed) view.stick = false
            else view.stick = view.nearBottom()
        }
    }
}
