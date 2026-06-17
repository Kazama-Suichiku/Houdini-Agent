import QtQuick
import HAgent

Item {
    id: dag
    property var architecture: ({})
    property var steps: []
    property bool streaming: false
    property int nodeW: Math.round(148 * Theme.scale)
    property int nodeH: Math.round(44 * Theme.scale)
    property int hGap: Math.round(34 * Theme.scale)
    property int vGap: Math.round(28 * Theme.scale)
    property int pad: Math.round(22 * Theme.scale)
    property var graph: buildGraph()
    implicitWidth: graph.w
    implicitHeight: graph.h

    onArchitectureChanged: { graph = buildGraph(); lines.requestPaint() }
    onStepsChanged: { graph = buildGraph(); lines.requestPaint() }
    onGraphChanged: lines.requestPaint()
    onWidthChanged: { graph = buildGraph(); lines.requestPaint() }

    function nodeColor(t) {
        var m = {
            sop: ["#131a24", "#7da6d8", "#dbeafe"],
            obj: ["#211a10", "#d4a373", "#ffe4bc"],
            mat: ["#102019", "#8fbf9f", "#d9f5df"],
            vop: ["#1b1625", "#b7a0d8", "#eee7ff"],
            rop: ["#251720", "#d89ab8", "#ffe2ef"],
            dop: ["#102123", "#80c8d2", "#dffcff"],
            lop: ["#172110", "#adc789", "#efffdb"],
            cop: ["#25220f", "#d4c16d", "#fff6b7"],
            chop: ["#18210f", "#a6c66e", "#efffc7"],
            out: ["#241414", "#d99090", "#ffe1e1"],
            subnet: ["#181826", "#a0a5d8", "#eef0ff"],
            null: ["#181818", "#8a8a8a", "#dedbd2"],
            other: ["#181818", "#969184", "#e7e4db"]
        }
        return m[(t || "other").toLowerCase()] || m.other
    }

    function fallbackArch() {
        var nodes = []
        var conns = []
        var hasDeps = false
        for (var i = 0; i < steps.length; i++) {
            var s = steps[i] || {}
            var sid = s.id || ("step-" + (i + 1))
            nodes.push({
                id: sid,
                label: s.title || s.label || sid,
                type: "other",
                group: s.phase || "",
                is_new: true,
                params: (s.tools || []).slice(0, 2).join(", ")
            })
            var deps = s.depends_on || []
            if (deps.length > 0)
                hasDeps = true
            for (var d = 0; d < deps.length; d++)
                conns.push({ from: deps[d], to: sid, label: "" })
        }
        if (!hasDeps && steps.length > 1) {
            for (var j = 0; j < steps.length - 1; j++)
                conns.push({ from: nodes[j].id, to: nodes[j + 1].id, label: "" })
        }
        return { nodes: nodes, connections: conns, groups: [] }
    }

    function buildGraph() {
        var arch = architecture || {}
        if (!arch.nodes || arch.nodes.length === 0)
            arch = fallbackArch()
        var rawNodes = arch.nodes || []
        var rawConns = arch.connections || []
        var nodeMap = {}
        var nodes = []
        for (var i = 0; i < rawNodes.length; i++) {
            var rn = rawNodes[i] || {}
            var id = "" + (rn.id || rn.name || ("node-" + i))
            if (!id || nodeMap[id])
                continue
            var n = {
                id: id,
                label: "" + (rn.label || id),
                type: "" + (rn.type || "other"),
                group: "" + (rn.group || ""),
                is_new: rn.is_new !== false,
                params: "" + (rn.params || ""),
                x: 0, y: 0
            }
            nodeMap[id] = n
            nodes.push(n)
        }
        var parents = {}, children = {}
        for (var p = 0; p < nodes.length; p++) {
            parents[nodes[p].id] = []
            children[nodes[p].id] = []
        }
        var conns = []
        for (var c = 0; c < rawConns.length; c++) {
            var rc = rawConns[c] || {}
            var from = "" + (rc.from || "")
            var to = "" + (rc.to || "")
            if (nodeMap[from] && nodeMap[to]) {
                conns.push({ from: from, to: to, label: "" + (rc.label || "") })
                parents[to].push(from)
                children[from].push(to)
            }
        }
        var depths = {}
        function depthOf(id, seen) {
            if (depths[id] !== undefined)
                return depths[id]
            seen = seen || {}
            if (seen[id]) {
                depths[id] = 0
                return 0
            }
            seen[id] = true
            if (!parents[id] || parents[id].length === 0) {
                depths[id] = 0
                return 0
            }
            var mx = 0
            for (var a = 0; a < parents[id].length; a++)
                mx = Math.max(mx, depthOf(parents[id][a], seen) + 1)
            depths[id] = mx
            return mx
        }
        var layers = {}
        var maxDepth = 0
        var maxLayer = 1
        for (var ni = 0; ni < nodes.length; ni++) {
            var dep = depthOf(nodes[ni].id, {})
            maxDepth = Math.max(maxDepth, dep)
            if (!layers[dep])
                layers[dep] = []
            layers[dep].push(nodes[ni])
            maxLayer = Math.max(maxLayer, layers[dep].length)
        }
        var contentW = Math.max(dag.width || 0, maxLayer * nodeW + (maxLayer - 1) * hGap + pad * 2)
        for (var l = 0; l <= maxDepth; l++) {
            var layer = layers[l] || []
            var layerW = layer.length * nodeW + Math.max(0, layer.length - 1) * hGap
            var startX = pad + Math.max(0, (contentW - pad * 2 - layerW) / 2)
            for (var li = 0; li < layer.length; li++) {
                layer[li].x = startX + li * (nodeW + hGap)
                layer[li].y = pad + l * (nodeH + vGap)
            }
        }
        var groups = []
        var rawGroups = arch.groups || []
        for (var gi = 0; gi < rawGroups.length; gi++) {
            var rg = rawGroups[gi] || {}
            var ids = rg.node_ids || []
            var minX = 999999, minY = 999999, maxX = -1, maxY = -1
            for (var ii = 0; ii < ids.length; ii++) {
                var gn = nodeMap[ids[ii]]
                if (!gn)
                    continue
                minX = Math.min(minX, gn.x); minY = Math.min(minY, gn.y)
                maxX = Math.max(maxX, gn.x + nodeW); maxY = Math.max(maxY, gn.y + nodeH)
            }
            if (maxX >= 0)
                groups.push({ name: "" + (rg.name || ""), color: "" + (rg.color || ""), x: minX - 12, y: minY - 24, w: maxX - minX + 24, h: maxY - minY + 36 })
        }
        return { nodes: nodes, connections: conns, groups: groups, w: contentW, h: Math.max(86, pad * 2 + (maxDepth + 1) * nodeH + maxDepth * vGap) }
    }

    Canvas {
        id: lines
        anchors.fill: parent
        onPaint: {
            var ctx = getContext("2d")
            ctx.clearRect(0, 0, width, height)
            ctx.fillStyle = Theme.surface
            ctx.fillRect(0, 0, width, height)
            ctx.fillStyle = Theme.borderSoft
            for (var gx = 8; gx < width; gx += 20)
                for (var gy = 8; gy < height; gy += 20)
                    ctx.fillRect(gx, gy, 1, 1)
            ctx.font = Theme.fMicro + "px " + Theme.fontMono
            for (var gi = 0; gi < graph.groups.length; gi++) {
                var g = graph.groups[gi]
                ctx.strokeStyle = Theme.accentLine
                ctx.setLineDash([5, 4])
                ctx.strokeRect(g.x, g.y, g.w, g.h)
                ctx.setLineDash([])
                ctx.fillStyle = Theme.textMute
                ctx.fillText(g.name, g.x + 8, g.y + 15)
            }
            var nmap = {}
            for (var n = 0; n < graph.nodes.length; n++)
                nmap[graph.nodes[n].id] = graph.nodes[n]
            for (var c = 0; c < graph.connections.length; c++) {
                var e = graph.connections[c]
                var a = nmap[e.from], b = nmap[e.to]
                if (!a || !b)
                    continue
                var col = nodeColor(a.type)[1]
                var x1 = a.x + nodeW / 2, y1 = a.y + nodeH
                var x2 = b.x + nodeW / 2, y2 = b.y
                ctx.strokeStyle = col
                ctx.globalAlpha = 0.58
                ctx.lineWidth = 1.2
                ctx.beginPath()
                ctx.moveTo(x1, y1)
                var midY = (y1 + y2) / 2
                ctx.bezierCurveTo(x1, midY, x2, midY, x2, y2)
                ctx.stroke()
                ctx.beginPath()
                ctx.moveTo(x2, y2)
                ctx.lineTo(x2 - 4, y2 - 7)
                ctx.lineTo(x2 + 4, y2 - 7)
                ctx.closePath()
                ctx.fillStyle = col
                ctx.fill()
                ctx.globalAlpha = 1
            }
        }
    }

    Repeater {
        model: graph.nodes
        delegate: Rectangle {
            required property var modelData
            x: modelData.x; y: modelData.y
            width: dag.nodeW; height: dag.nodeH
            radius: Theme.radSm
            color: dag.nodeColor(modelData.type)[0]
            opacity: modelData.is_new ? 1 : 0.58
            border.color: dag.nodeColor(modelData.type)[1]
            border.width: modelData.is_new ? 1.4 : 1
            Rectangle { width: 3; anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom; anchors.margins: 4; color: dag.nodeColor(modelData.type)[1] }
            Text {
                anchors.left: parent.left; anchors.leftMargin: 11
                anchors.right: parent.right; anchors.rightMargin: 8
                anchors.top: parent.top; anchors.topMargin: 5
                text: modelData.label
                elide: Text.ElideRight
                color: dag.nodeColor(modelData.type)[2]
                font.family: Theme.fontBody; font.pixelSize: Theme.fSm
            }
            Text {
                anchors.left: parent.left; anchors.leftMargin: 11
                anchors.right: parent.right; anchors.rightMargin: 8
                anchors.bottom: parent.bottom; anchors.bottomMargin: 5
                text: modelData.type.toUpperCase() + " · " + modelData.id
                elide: Text.ElideRight
                color: Theme.textMute
                font.family: Theme.fontMono; font.pixelSize: Theme.fMicro
            }
        }
    }

    Text {
        anchors.centerIn: parent
        visible: graph.nodes.length === 0
        text: streaming ? "生成 DAG 中…" : "No DAG data"
        color: Theme.textMute
        font.family: Theme.fontMono
        font.pixelSize: Theme.fSm
    }
}
