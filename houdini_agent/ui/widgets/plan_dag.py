import math
from houdini_agent.qt_compat import QtWidgets, QtCore, QtGui
from .theme import CursorTheme
from ..i18n import tr


class PlanBlock(QtWidgets.QWidget):
    """执行计划显示"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._steps = []

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(2)

        self.title = QtWidgets.QLabel("Plan")
        self.title.setObjectName("planTitle")
        layout.addWidget(self.title)

        self.steps_layout = QtWidgets.QVBoxLayout()
        self.steps_layout.setSpacing(1)
        layout.addLayout(self.steps_layout)

    def add_step(self, text: str, status: str = "pending") -> QtWidgets.QLabel:
        icons = {"pending": "○", "running": "◎", "done": "●", "error": "✗"}

        label = QtWidgets.QLabel(f"{icons[status]} {text}")
        label.setObjectName("planStep")
        label.setProperty("state", status)
        label.style().unpolish(label)
        label.style().polish(label)
        self.steps_layout.addWidget(label)
        self._steps.append((label, text))
        return label

    def update_step(self, index: int, status: str):
        if 0 <= index < len(self._steps):
            label, text = self._steps[index]
            icons = {"pending": "○", "running": "◎", "done": "●", "error": "✗"}
            label.setText(f"{icons[status]} {text}")
            label.setProperty("state", status)
            label.style().unpolish(label)
            label.style().polish(label)


# ============================================================
# PlanDAGWidget — QPainter 自绘 DAG 流程图
# ============================================================

class PlanDAGWidget(QtWidgets.QWidget):
    """Houdini 节点网络架构蓝图，用 QPainter 自绘。

    展示 Plan 执行完成后的 **节点网络拓扑**（设计蓝图），
    而不是执行步骤顺序。

    特性：
    - 按节点类型着色（SOP=蓝、OBJ=橙、MAT=绿 等）
    - 分组容器（地形系统、散布系统 等）
    - 新节点 vs 已有节点 视觉区分
    - 贝塞尔曲线连线 + 箭头
    - 自动分层布局
    - QScrollArea 包裹，窗口窄时横向滚动
    """

    # 节点类型 → (填充色, 边框色, 文字色)
    _TYPE_COLORS = {
        "sop":    ("#0d1f3c", "#4a9eff", "#a3d4ff"),
        "obj":    ("#2d1f0d", "#e8a838", "#ffe0a0"),
        "mat":    ("#0d2d1a", "#34d399", "#6ee7b7"),
        "vop":    ("#1f0d2d", "#a78bfa", "#c4b5fd"),
        "rop":    ("#2d0d1a", "#f472b6", "#f9a8d4"),
        "dop":    ("#0d2d2d", "#22d3ee", "#67e8f9"),
        "lop":    ("#1a2d0d", "#a3e635", "#d9f99d"),
        "cop":    ("#2d2d0d", "#facc15", "#fef08a"),
        "chop":   ("#1f2d0d", "#84cc16", "#bef264"),
        "out":    ("#2d1215", "#f87171", "#fca5a5"),
        "subnet": ("#1a1a2e", "#818cf8", "#c7d2fe"),
        "null":   ("#1a1a1a", "#6b7280", "#9ca3af"),
        "other":  ("#1e2030", "#4a5068", "#8892a8"),
    }

    # 已有节点的暗化系数
    _EXISTING_ALPHA = 0.5

    NODE_W = 160
    NODE_H = 42
    H_GAP = 50       # 层间距（水平，连线区域）
    V_GAP = 20       # 同层节点间距（垂直）
    PAD = 30          # 画布内边距
    GROUP_PAD = 16    # 分组容器内边距
    GROUP_TITLE_H = 22  # 分组标题高度

    def __init__(self, arch_data: dict = None, parent=None):
        """
        Args:
            arch_data: architecture 字段数据，包含 nodes, connections, groups
        """
        super().__init__(parent)
        self._arch = arch_data or {}
        self._nodes = self._arch.get("nodes", [])
        self._connections = self._arch.get("connections", [])
        self._groups = self._arch.get("groups", [])
        self._node_rects = {}      # node_id -> QRectF
        self._group_rects = {}     # group_name -> QRectF
        self._collapsed = True
        self.setObjectName("planDAG")
        self._content_w = 0
        self._content_h = 0
        self._layout_nodes()
        self._pulse_phase = 0.0
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(40)   # ~25fps

    def _tick(self):
        self._pulse_phase = (self._pulse_phase + 0.04) % (math.pi * 2)
        # 架构图有新节点标记时微弱脉动
        has_new = any(n.get("is_new", True) for n in self._nodes)
        if has_new:
            self.update()

    def set_collapsed(self, collapsed: bool):
        self._collapsed = collapsed
        if collapsed:
            self.setFixedHeight(0)
            self.setMinimumWidth(0)
        else:
            self._layout_nodes()
        self.update()

    def update_architecture(self, arch_data: dict):
        """更新架构数据并重新布局"""
        self._arch = arch_data or {}
        self._nodes = self._arch.get("nodes", [])
        self._connections = self._arch.get("connections", [])
        self._groups = self._arch.get("groups", [])
        self._layout_nodes()
        self.update()

    # ----------------------------------------------------------
    # 布局算法
    # ----------------------------------------------------------
    def _layout_nodes(self):
        """Sugiyama 分层布局：按连接拓扑自动分层排列节点。"""
        if self._collapsed:
            self.setFixedHeight(0)
            self.setMinimumWidth(0)
            return
        if not self._nodes:
            self.setFixedHeight(0)
            self.setMinimumWidth(0)
            return

        node_map = {n["id"]: n for n in self._nodes}

        # ── 1) 构建邻接表 ──
        children = {n["id"]: [] for n in self._nodes}      # from → [to, ...]
        parents = {n["id"]: [] for n in self._nodes}        # to   → [from, ...]
        for conn in self._connections:
            f, t = conn.get("from", ""), conn.get("to", "")
            if f in node_map and t in node_map:
                children[f].append(t)
                parents[t].append(f)

        # ── 2) 计算深度（从源头开始 BFS） ──
        depths = {}
        def get_depth(nid, visited=None):
            if nid in depths:
                return depths[nid]
            if visited is None:
                visited = set()
            if nid in visited:  # 防环
                depths[nid] = 0
                return 0
            visited.add(nid)
            if not parents[nid]:
                depths[nid] = 0
                return 0
            d = max(get_depth(p, visited) for p in parents[nid]) + 1
            depths[nid] = d
            return d

        for n in self._nodes:
            get_depth(n["id"])

        # ── 3) 分层 ──
        layers = {}
        for nid, d in depths.items():
            layers.setdefault(d, []).append(nid)

        max_depth = max(layers.keys()) if layers else 0
        max_per_layer = max(len(v) for v in layers.values()) if layers else 1

        # 垂直方向布局（从上到下，更符合 Houdini 节点网络习惯）
        total_w = max_per_layer * (self.NODE_W + self.H_GAP) - self.H_GAP
        total_h = (max_depth + 1) * (self.NODE_H + self.V_GAP + 10) - self.V_GAP

        self._node_rects.clear()
        for depth, nids in layers.items():
            y = self.PAD + depth * (self.NODE_H + self.V_GAP + 10)
            layer_w = len(nids) * (self.NODE_W + self.H_GAP) - self.H_GAP
            start_x = self.PAD + (total_w - layer_w) / 2
            for i, nid in enumerate(nids):
                x = start_x + i * (self.NODE_W + self.H_GAP)
                self._node_rects[nid] = QtCore.QRectF(x, y, self.NODE_W, self.NODE_H)

        # ── 4) 计算分组容器 ──
        self._group_rects.clear()
        for grp in self._groups:
            grp_name = grp.get("name", "")
            grp_nids = [nid for nid in grp.get("node_ids", []) if nid in self._node_rects]
            if not grp_name or not grp_nids:
                continue
            rects = [self._node_rects[nid] for nid in grp_nids]
            gp = self.GROUP_PAD
            min_x = min(r.left() for r in rects) - gp
            min_y = min(r.top() for r in rects) - gp - self.GROUP_TITLE_H
            max_x = max(r.right() for r in rects) + gp
            max_y = max(r.bottom() for r in rects) + gp
            self._group_rects[grp_name] = (
                QtCore.QRectF(min_x, min_y, max_x - min_x, max_y - min_y),
                grp.get("color", ""),
            )

        # ── 5) 最终尺寸 ──
        all_rects = list(self._node_rects.values())
        all_rects += [r for r, _ in self._group_rects.values()]
        if all_rects:
            max_right = max(r.right() for r in all_rects)
            max_bottom = max(r.bottom() for r in all_rects)
            self._content_w = int(max_right + self.PAD)
            self._content_h = int(max_bottom + self.PAD)
        else:
            self._content_w = 200
            self._content_h = 80

        self.setMinimumWidth(self._content_w)
        self.setFixedHeight(self._content_h)

    # ----------------------------------------------------------
    # 工具方法
    # ----------------------------------------------------------
    def _elide_text(self, painter, text: str, max_width: int) -> str:
        """按像素宽度截断文字（支持 CJK）"""
        fm = painter.fontMetrics()
        if fm.horizontalAdvance(text) <= max_width:
            return text
        for i in range(len(text), 0, -1):
            candidate = text[:i] + "…"
            if fm.horizontalAdvance(candidate) <= max_width:
                return candidate
        return "…"

    _GROUP_HINT_COLORS = {
        "blue":    (74, 158, 255),
        "green":   (52, 211, 153),
        "purple":  (167, 139, 250),
        "orange":  (232, 168, 56),
        "red":     (248, 113, 113),
        "cyan":    (34, 211, 238),
        "yellow":  (250, 204, 21),
        "pink":    (244, 114, 182),
    }

    # ----------------------------------------------------------
    # 绘制
    # ----------------------------------------------------------
    def paintEvent(self, event):
        if self._collapsed or not self._nodes:
            return

        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.setRenderHint(QtGui.QPainter.TextAntialiasing)

        # ── 0) 背景 ──
        bg_grad = QtGui.QLinearGradient(0, 0, self.width(), self.height())
        bg_grad.setColorAt(0.0, QtGui.QColor("#0d0f1a"))
        bg_grad.setColorAt(1.0, QtGui.QColor("#111420"))
        p.fillRect(self.rect(), bg_grad)

        # 背景网格点
        grid_color = QtGui.QColor(100, 116, 139, 12)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(grid_color)
        for gx in range(0, self.width(), 20):
            for gy in range(0, self.height(), 20):
                p.drawEllipse(QtCore.QPointF(gx, gy), 0.5, 0.5)

        # ── 1) 分组容器 ──
        for grp_name, (grect, color_hint) in self._group_rects.items():
            r, g, b = self._GROUP_HINT_COLORS.get(color_hint, (167, 139, 250))
            # 半透明填充
            p.setBrush(QtGui.QColor(r, g, b, 8))
            pen = QtGui.QPen(QtGui.QColor(r, g, b, 40), 1.0, QtCore.Qt.DashLine)
            p.setPen(pen)
            p.drawRoundedRect(grect, 10, 10)
            # 标题
            title_font = QtGui.QFont(CursorTheme.FONT_BODY.split(",")[0].strip("' "), 8)
            title_font.setWeight(QtGui.QFont.Medium)
            p.setFont(title_font)
            p.setPen(QtGui.QColor(r, g, b, 140))
            title_rect = QtCore.QRectF(grect.left() + 10, grect.top() + 3,
                                        grect.width() - 20, self.GROUP_TITLE_H - 4)
            p.drawText(title_rect, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, grp_name)

        node_map = {n["id"]: n for n in self._nodes}

        # ── 2) 连线（贝塞尔曲线）──
        for conn in self._connections:
            src_id = conn.get("from", "")
            dst_id = conn.get("to", "")
            src_rect = self._node_rects.get(src_id)
            dst_rect = self._node_rects.get(dst_id)
            if not src_rect or not dst_rect:
                continue

            # 连线颜色（取源节点类型色的淡化版）
            src_node = node_map.get(src_id, {})
            ntype = src_node.get("type", "other")
            _, border_c_hex, _ = self._TYPE_COLORS.get(ntype, self._TYPE_COLORS["other"])
            line_color = QtGui.QColor(border_c_hex)
            line_color.setAlpha(80)

            # 从源底部中点 → 目标顶部中点（垂直布局）
            x1 = src_rect.center().x()
            y1 = src_rect.bottom()
            x2 = dst_rect.center().x()
            y2 = dst_rect.top()

            path = QtGui.QPainterPath()
            path.moveTo(x1, y1)
            ctrl_v = abs(y2 - y1) * 0.4
            if abs(x2 - x1) < 5:
                # 纯垂直
                path.cubicTo(x1, y1 + ctrl_v, x2, y2 - ctrl_v, x2, y2)
            else:
                # S 形曲线
                mid_y = (y1 + y2) / 2
                path.cubicTo(x1, mid_y, x2, mid_y, x2, y2)

            p.setPen(QtGui.QPen(line_color, 1.4))
            p.setBrush(QtCore.Qt.NoBrush)
            p.drawPath(path)

            # 箭头（向下）
            al = 6
            arr_angle = math.pi / 2
            arr_tip_x, arr_tip_y = x2, y2
            ax1 = arr_tip_x - al * math.cos(arr_angle - 0.4)
            ay1 = arr_tip_y - al * math.sin(arr_angle - 0.4)
            ax2 = arr_tip_x - al * math.cos(arr_angle + 0.4)
            ay2 = arr_tip_y - al * math.sin(arr_angle + 0.4)
            arrow = QtGui.QPolygonF([
                QtCore.QPointF(arr_tip_x, arr_tip_y),
                QtCore.QPointF(ax1, ay1),
                QtCore.QPointF(ax2, ay2),
            ])
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(line_color)
            p.drawPolygon(arrow)

            # 连线标签（如果有）
            conn_label = conn.get("label", "")
            if conn_label:
                lbl_font = QtGui.QFont(CursorTheme.FONT_BODY.split(",")[0].strip("' "), 7)
                p.setFont(lbl_font)
                lbl_color = QtGui.QColor(border_c_hex)
                lbl_color.setAlpha(100)
                p.setPen(lbl_color)
                mid_x = (x1 + x2) / 2
                mid_y_lbl = (y1 + y2) / 2
                p.drawText(QtCore.QPointF(mid_x + 4, mid_y_lbl), conn_label)

        # ── 3) 节点 ──
        label_font = QtGui.QFont(CursorTheme.FONT_BODY.split(",")[0].strip("' "), 9)
        type_font = QtGui.QFont(CursorTheme.FONT_BODY.split(",")[0].strip("' "), 7)

        for n in self._nodes:
            nid = n["id"]
            rect = self._node_rects.get(nid)
            if not rect:
                continue

            ntype = n.get("type", "other")
            is_new = n.get("is_new", True)
            fill_c, border_c, text_c = self._TYPE_COLORS.get(ntype, self._TYPE_COLORS["other"])

            # 新节点微弱脉动光晕
            if is_new:
                pulse = 0.7 + 0.3 * math.sin(self._pulse_phase)
                glow_color = QtGui.QColor(border_c)
                glow_color.setAlpha(int(30 * pulse))
                glow_rect = rect.adjusted(-3, -3, 3, 3)
                p.setPen(QtCore.Qt.NoPen)
                p.setBrush(glow_color)
                p.drawRoundedRect(glow_rect, 10, 10)

            # 节点背景
            bg = QtGui.QColor(fill_c)
            alpha = 220 if is_new else int(220 * self._EXISTING_ALPHA)
            bg.setAlpha(alpha)
            p.setBrush(bg)

            # 边框
            bc = QtGui.QColor(border_c)
            if not is_new:
                bc.setAlpha(int(255 * self._EXISTING_ALPHA))
            p.setPen(QtGui.QPen(bc, 1.5 if is_new else 1.0))
            p.drawRoundedRect(rect, 6, 6)

            # 左侧类型色条
            bar_w = 3
            bar_rect = QtCore.QRectF(rect.left() + 2, rect.top() + 4,
                                      bar_w, rect.height() - 8)
            p.setPen(QtCore.Qt.NoPen)
            bar_color = QtGui.QColor(border_c)
            if not is_new:
                bar_color.setAlpha(int(200 * self._EXISTING_ALPHA))
            p.setBrush(bar_color)
            p.drawRoundedRect(bar_rect, 1.5, 1.5)

            # 上行：节点标签（label）
            p.setFont(label_font)
            label_text = n.get("label", nid)
            label_text = self._elide_text(p, label_text, int(self.NODE_W - 20))
            tc = QtGui.QColor(text_c)
            if not is_new:
                tc.setAlpha(int(255 * self._EXISTING_ALPHA))
            p.setPen(tc)
            label_rect = QtCore.QRectF(rect.left() + 10, rect.top() + 3,
                                        rect.width() - 14, 20)
            p.drawText(label_rect, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, label_text)

            # 下行：类型 + 节点名
            p.setFont(type_font)
            sub_text = f"{ntype.upper()}: {nid}"
            sub_text = self._elide_text(p, sub_text, int(self.NODE_W - 20))
            sub_color = QtGui.QColor(border_c)
            if not is_new:
                sub_color.setAlpha(int(180 * self._EXISTING_ALPHA))
            else:
                sub_color.setAlpha(180)
            p.setPen(sub_color)
            sub_rect = QtCore.QRectF(rect.left() + 10, rect.top() + 22,
                                      rect.width() - 14, 16)
            p.drawText(sub_rect, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, sub_text)

            # 已有节点标记（虚线边框覆盖）
            if not is_new:
                exist_pen = QtGui.QPen(QtGui.QColor(border_c), 0.8, QtCore.Qt.DotLine)
                exist_pen.setColor(QtGui.QColor(border_c).darker(150))
                p.setPen(exist_pen)
                p.setBrush(QtCore.Qt.NoBrush)
                p.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 5, 5)

        p.end()
