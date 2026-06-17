import json
from houdini_agent.qt_compat import QtWidgets, QtCore, QtGui
from .theme import CursorTheme
from ..i18n import tr


class PluginManagerDialog(QtWidgets.QDialog):
    """插件管理面板

    从溢出菜单打开，列出所有插件，支持启用/禁用、重载、设置。
    """

    pluginStateChanged = QtCore.Signal()  # 插件状态变化时通知

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("pluginManagerDlg")
        self.setWindowTitle(tr('plugin.manager_title'))
        self.setMinimumSize(620, 480)
        self.resize(660, 520)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ═══════ Header 标题栏 ═══════
        header = QtWidgets.QFrame()
        header.setObjectName("pmHeader")
        header.setFixedHeight(44)
        header_lay = QtWidgets.QHBoxLayout(header)
        header_lay.setContentsMargins(16, 0, 16, 0)
        header_lay.setSpacing(8)

        title_lbl = QtWidgets.QLabel(f"🔌  {tr('plugin.manager_title')}")
        title_lbl.setObjectName("pmTitle")
        header_lay.addWidget(title_lbl)
        header_lay.addStretch()

        self._stats_label = QtWidgets.QLabel("")
        self._stats_label.setObjectName("pmStatsLabel")
        header_lay.addWidget(self._stats_label)

        root.addWidget(header)

        # ═══════ Tab Bar (underline style) ═══════
        self._tabs = QtWidgets.QTabWidget()
        self._tabs.setObjectName("pmTabs")
        self._tabs.setDocumentMode(True)  # 去掉 pane 边框, 更现代

        # ── Tab 1: Plugins ──
        plugins_page = QtWidgets.QWidget()
        plugins_page.setObjectName("pmTabPage")
        plugins_lay = QtWidgets.QVBoxLayout(plugins_page)
        plugins_lay.setContentsMargins(12, 10, 12, 6)
        plugins_lay.setSpacing(6)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setObjectName("pmScroll")
        self._list_container = QtWidgets.QWidget()
        self._list_container.setObjectName("pmScrollInner")
        self._list_layout = QtWidgets.QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(6)
        scroll.setWidget(self._list_container)
        plugins_lay.addWidget(scroll, 1)

        self._tabs.addTab(plugins_page, f"  {tr('plugin.tab_plugins')}  ")

        # ── Tab 2: Tools ──
        tools_page = QtWidgets.QWidget()
        tools_page.setObjectName("pmTabPage")
        tools_lay = QtWidgets.QVBoxLayout(tools_page)
        tools_lay.setContentsMargins(12, 10, 12, 6)
        tools_lay.setSpacing(6)

        # 搜索框
        self._tools_search = QtWidgets.QLineEdit()
        self._tools_search.setObjectName("pmSearchEdit")
        self._tools_search.setPlaceholderText(tr('plugin.search_tools'))
        self._tools_search.setClearButtonEnabled(True)
        self._tools_search.textChanged.connect(self._filter_tools)
        tools_lay.addWidget(self._tools_search)

        tools_scroll = QtWidgets.QScrollArea()
        tools_scroll.setWidgetResizable(True)
        tools_scroll.setObjectName("pmScroll")
        self._tools_container = QtWidgets.QWidget()
        self._tools_container.setObjectName("pmScrollInner")
        self._tools_layout = QtWidgets.QVBoxLayout(self._tools_container)
        self._tools_layout.setContentsMargins(0, 0, 0, 0)
        self._tools_layout.setSpacing(4)
        tools_scroll.setWidget(self._tools_container)
        tools_lay.addWidget(tools_scroll, 1)

        self._tabs.addTab(tools_page, f"  {tr('plugin.tab_tools')}  ")

        # ── Tab 3: Skills ──
        skills_page = QtWidgets.QWidget()
        skills_page.setObjectName("pmTabPage")
        skills_lay = QtWidgets.QVBoxLayout(skills_page)
        skills_lay.setContentsMargins(12, 10, 12, 6)
        skills_lay.setSpacing(6)

        skills_scroll = QtWidgets.QScrollArea()
        skills_scroll.setWidgetResizable(True)
        skills_scroll.setObjectName("pmScroll")
        self._skills_container = QtWidgets.QWidget()
        self._skills_container.setObjectName("pmScrollInner")
        self._skills_layout = QtWidgets.QVBoxLayout(self._skills_container)
        self._skills_layout.setContentsMargins(0, 0, 0, 0)
        self._skills_layout.setSpacing(6)
        skills_scroll.setWidget(self._skills_container)
        skills_lay.addWidget(skills_scroll, 1)

        # Skill 目录配置
        skill_dir_frame = QtWidgets.QFrame()
        skill_dir_frame.setObjectName("pmSkillDirFrame")
        skill_dir_lay = QtWidgets.QHBoxLayout(skill_dir_frame)
        skill_dir_lay.setContentsMargins(10, 6, 10, 6)
        skill_dir_lay.setSpacing(8)
        skill_dir_icon = QtWidgets.QLabel("📁")
        skill_dir_icon.setStyleSheet("background: transparent; font-size: 13px;")
        skill_dir_lay.addWidget(skill_dir_icon)
        skill_dir_lbl = QtWidgets.QLabel(tr('plugin.skill_dir_label'))
        skill_dir_lbl.setObjectName("pmSubLabel")
        skill_dir_lay.addWidget(skill_dir_lbl)
        self._skill_dir_edit = QtWidgets.QLineEdit()
        self._skill_dir_edit.setObjectName("pmPathEdit")
        self._skill_dir_edit.setPlaceholderText(tr('plugin.skill_dir_placeholder'))
        self._skill_dir_edit.setReadOnly(True)
        skill_dir_lay.addWidget(self._skill_dir_edit, 1)
        btn_browse_skill = QtWidgets.QPushButton(tr('plugin.skill_dir_browse'))
        btn_browse_skill.setObjectName("pmBtnSecondary")
        btn_browse_skill.setCursor(QtCore.Qt.PointingHandCursor)
        btn_browse_skill.clicked.connect(self._browse_skill_dir)
        skill_dir_lay.addWidget(btn_browse_skill)
        skills_lay.addWidget(skill_dir_frame)

        self._tabs.addTab(skills_page, f"  {tr('plugin.tab_skills')}  ")

        root.addWidget(self._tabs, 1)

        # ═══════ Footer 底部栏 ═══════
        footer = QtWidgets.QFrame()
        footer.setObjectName("pmFooter")
        footer.setFixedHeight(42)
        footer_lay = QtWidgets.QHBoxLayout(footer)
        footer_lay.setContentsMargins(14, 0, 14, 0)
        footer_lay.setSpacing(8)

        btn_open_dir = QtWidgets.QPushButton(f"📂  {tr('plugin.open_folder')}")
        btn_open_dir.setObjectName("pmFooterBtn")
        btn_open_dir.setCursor(QtCore.Qt.PointingHandCursor)
        btn_open_dir.clicked.connect(self._open_plugins_dir)
        footer_lay.addWidget(btn_open_dir)

        footer_lay.addStretch()

        btn_reload_all = QtWidgets.QPushButton(f"↻  {tr('plugin.reload_all')}")
        btn_reload_all.setObjectName("pmBtnPrimary")
        btn_reload_all.setCursor(QtCore.Qt.PointingHandCursor)
        btn_reload_all.clicked.connect(self._reload_all)
        footer_lay.addWidget(btn_reload_all)

        root.addWidget(footer)

        # Tab 切换刷新
        self._tabs.currentChanged.connect(self._on_tab_changed)

        # 加载插件列表
        self._refresh_list()
        self._update_stats()

    def _update_stats(self):
        """更新 header 统计标签"""
        try:
            from ...utils.hooks import list_plugins
            plugins = list_plugins()
            enabled = sum(1 for p in plugins if p.get("_enabled"))
            self._stats_label.setText(f"{enabled}/{len(plugins)} {tr('plugin.stats_active')}")
        except Exception:
            self._stats_label.setText("")

    def _refresh_list(self):
        """刷新插件列表"""
        # 清空旧项
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        try:
            from ...utils.hooks import list_plugins
            plugins = list_plugins()
        except Exception as e:
            lbl = QtWidgets.QLabel(f"⚠ {tr('plugin.load_error')}: {e}")
            lbl.setObjectName("pmErrorLabel")
            self._list_layout.addWidget(lbl)
            self._list_layout.addStretch()
            return

        if not plugins:
            # 空状态 — 漂亮的引导提示
            empty_frame = QtWidgets.QFrame()
            empty_frame.setObjectName("pmEmptyState")
            ev = QtWidgets.QVBoxLayout(empty_frame)
            ev.setContentsMargins(20, 40, 20, 40)
            ev.setSpacing(10)
            ev.setAlignment(QtCore.Qt.AlignCenter)

            icon_lbl = QtWidgets.QLabel("🔌")
            icon_lbl.setStyleSheet("font-size: 28px; background: transparent;")
            icon_lbl.setAlignment(QtCore.Qt.AlignCenter)
            ev.addWidget(icon_lbl)

            hint1 = QtWidgets.QLabel(tr('plugin.empty_title'))
            hint1.setObjectName("pmEmptyTitle")
            hint1.setAlignment(QtCore.Qt.AlignCenter)
            ev.addWidget(hint1)

            hint2 = QtWidgets.QLabel(tr('plugin.empty_hint'))
            hint2.setObjectName("pmEmptyHint")
            hint2.setAlignment(QtCore.Qt.AlignCenter)
            hint2.setWordWrap(True)
            ev.addWidget(hint2)

            self._list_layout.addWidget(empty_frame)
        else:
            for info in plugins:
                row = self._create_plugin_row(info)
                self._list_layout.addWidget(row)

        self._list_layout.addStretch()
        self._update_stats()

    def _create_plugin_row(self, info: dict) -> QtWidgets.QWidget:
        """创建单个插件行（卡片式）"""
        row = QtWidgets.QFrame()
        row.setObjectName("pmCard")

        h = QtWidgets.QHBoxLayout(row)
        h.setContentsMargins(12, 10, 12, 10)
        h.setSpacing(10)

        # 状态指示灯
        enabled = info.get("_enabled", False)
        dot = QtWidgets.QLabel("●")
        dot.setFixedWidth(12)
        dot.setStyleSheet(
            f"color: {'#6ecf72' if enabled else '#5a5040'}; "
            f"font-size: 8px; background: transparent;"
        )
        dot.setAlignment(QtCore.Qt.AlignCenter)
        h.addWidget(dot)

        # 左侧：名称 + 元信息
        left = QtWidgets.QVBoxLayout()
        left.setSpacing(3)

        name = info.get("name", "Unknown")
        version = info.get("version", "")
        author = info.get("author", "")

        name_lbl = QtWidgets.QLabel(
            f"<span style='font-weight:600; color:#e0d4c0'>{name}</span>"
            f"  <span style='color:#7a6e5e; font-size:10px'>v{version}</span>"
        )
        name_lbl.setObjectName("pmCardName")
        left.addWidget(name_lbl)

        desc = info.get("description", "")
        if author:
            desc = f"by {author}  ·  {desc}" if desc else f"by {author}"
        if desc:
            desc_lbl = QtWidgets.QLabel(desc)
            desc_lbl.setObjectName("pmCardDesc")
            desc_lbl.setWordWrap(True)
            left.addWidget(desc_lbl)

        h.addLayout(left, 1)

        # 操作按钮组
        actions = QtWidgets.QHBoxLayout()
        actions.setSpacing(4)

        # 设置按钮（仅有 settings 时显示）
        if info.get("settings"):
            btn_settings = QtWidgets.QPushButton("⚙")
            btn_settings.setObjectName("pmIconBtn")
            btn_settings.setFixedSize(28, 28)
            btn_settings.setCursor(QtCore.Qt.PointingHandCursor)
            btn_settings.setToolTip(tr('plugin.settings'))
            btn_settings.clicked.connect(
                lambda checked=False, n=name, i=info: self._open_settings(n, i))
            actions.addWidget(btn_settings)

        # 重载按钮
        btn_reload = QtWidgets.QPushButton("↻")
        btn_reload.setObjectName("pmIconBtn")
        btn_reload.setFixedSize(28, 28)
        btn_reload.setCursor(QtCore.Qt.PointingHandCursor)
        btn_reload.setToolTip(tr('plugin.reload'))
        btn_reload.clicked.connect(
            lambda checked=False, n=name: self._on_reload(n))
        actions.addWidget(btn_reload)

        # 启用/禁用开关
        toggle = QtWidgets.QCheckBox()
        toggle.setChecked(enabled)
        toggle.setToolTip(tr('plugin.toggle_tip'))
        toggle.stateChanged.connect(
            lambda state, n=name: self._on_toggle(n, state == QtCore.Qt.Checked))
        actions.addWidget(toggle)

        h.addLayout(actions)

        return row

    def _on_toggle(self, plugin_name: str, enabled: bool):
        """启用/禁用插件"""
        try:
            from ...utils.hooks import enable_plugin, disable_plugin
            if enabled:
                enable_plugin(plugin_name)
            else:
                disable_plugin(plugin_name)
            self.pluginStateChanged.emit()
        except Exception as e:
            print(f"[PluginManager] Toggle error: {e}")

    def _on_reload(self, plugin_name: str):
        """重载单个插件"""
        try:
            from ...utils.hooks import reload_plugin
            reload_plugin(plugin_name)
            self._refresh_list()
            self.pluginStateChanged.emit()
        except Exception as e:
            print(f"[PluginManager] Reload error: {e}")

    def _reload_all(self):
        """重载全部插件"""
        try:
            from ...utils.hooks import reload_all_plugins
            reload_all_plugins()
            self._refresh_list()
            # 如果当前在 Tools/Skills tab, 也刷新
            idx = self._tabs.currentIndex()
            if idx == 1:
                self._refresh_tools_list()
            elif idx == 2:
                self._refresh_skills_list()
            self.pluginStateChanged.emit()
        except Exception as e:
            print(f"[PluginManager] Reload all error: {e}")

    def _open_plugins_dir(self):
        """打开 plugins 目录"""
        try:
            from ...utils.hooks import get_plugins_dir
            import os, subprocess
            import sys as _sys
            plugins_dir = get_plugins_dir()
            plugins_dir.mkdir(parents=True, exist_ok=True)
            if _sys.platform == 'win32':
                os.startfile(str(plugins_dir))
            elif _sys.platform == 'darwin':
                subprocess.Popen(['open', str(plugins_dir)])
            else:
                subprocess.Popen(['xdg-open', str(plugins_dir)])
        except Exception as e:
            print(f"[PluginManager] Open dir error: {e}")

    def _on_tab_changed(self, index: int):
        """Tab 切换时刷新对应列表"""
        if index == 1:
            self._refresh_tools_list()
        elif index == 2:
            self._refresh_skills_list()

    def _filter_tools(self, text: str):
        """搜索框过滤工具列表"""
        text = text.strip().lower()
        for i in range(self._tools_layout.count()):
            item = self._tools_layout.itemAt(i)
            w = item.widget() if item else None
            if w is None:
                continue
            if w.objectName() == "pmCard":
                tool_name = w.property("toolName") or ""
                tool_desc = w.property("toolDesc") or ""
                visible = (not text) or text in tool_name.lower() or text in tool_desc.lower()
                w.setVisible(visible)
            elif w.objectName() == "pmGroupHeader":
                # 组标题: 如果搜索框有内容则隐藏组标题
                w.setVisible(not text)

    # ---------- Tools Tab ----------

    def _refresh_tools_list(self):
        """刷新工具列表"""
        while self._tools_layout.count():
            item = self._tools_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        try:
            from ...utils.tool_registry import get_tool_registry
            reg = get_tool_registry()
            tools = reg.list_all()
        except Exception as e:
            lbl = QtWidgets.QLabel(f"⚠ {tr('plugin.load_error')}: {e}")
            lbl.setObjectName("pmErrorLabel")
            self._tools_layout.addWidget(lbl)
            self._tools_layout.addStretch()
            return

        if not tools:
            lbl = QtWidgets.QLabel(tr('plugin.no_tools'))
            lbl.setObjectName("pmEmptyHint")
            lbl.setAlignment(QtCore.Qt.AlignCenter)
            self._tools_layout.addWidget(lbl)
        else:
            # 按来源分组
            groups = {}
            for t in tools:
                source = t.get("source", "core")
                groups.setdefault(source, []).append(t)

            source_icons = {
                "core": "🔧",
                "skill": "🧠",
                "plugin": "🔌",
                "user": "👤",
                "user_skill": "📐",
            }
            source_labels = {
                "core": tr('plugin.group_core'),
                "skill": tr('plugin.group_skill'),
                "plugin": tr('plugin.group_plugin'),
                "user": tr('plugin.group_user'),
                "user_skill": tr('plugin.group_user_skill'),
            }

            for source in ("core", "skill", "user_skill", "plugin", "user"):
                items = groups.get(source, [])
                if not items:
                    continue

                # 组标题
                group_lbl = QtWidgets.QLabel(
                    f"{source_icons.get(source, '•')}  {source_labels.get(source, source)}"
                    f"  ({len(items)})"
                )
                group_lbl.setObjectName("pmGroupHeader")
                self._tools_layout.addWidget(group_lbl)

                for t in items:
                    row = self._create_tool_row(t)
                    self._tools_layout.addWidget(row)

        self._tools_layout.addStretch()

    def _create_tool_row(self, info: dict) -> QtWidgets.QWidget:
        """创建单个工具行（紧凑卡片）"""
        row = QtWidgets.QFrame()
        row.setObjectName("pmCard")

        name = info.get("name", "")
        desc = info.get("description", "")[:100]
        enabled = info.get("enabled", True)
        modes = info.get("modes", [])
        tags = info.get("tags", [])

        # 存储属性用于搜索过滤
        row.setProperty("toolName", name)
        row.setProperty("toolDesc", desc)

        h = QtWidgets.QHBoxLayout(row)
        h.setContentsMargins(12, 6, 12, 6)
        h.setSpacing(8)

        left = QtWidgets.QVBoxLayout()
        left.setSpacing(2)

        name_lbl = QtWidgets.QLabel(f"<span style='font-weight:600; color:#e0d4c0'>{name}</span>")
        name_lbl.setObjectName("pmCardName")
        left.addWidget(name_lbl)

        if desc:
            desc_lbl = QtWidgets.QLabel(desc)
            desc_lbl.setObjectName("pmCardDesc")
            desc_lbl.setWordWrap(True)
            left.addWidget(desc_lbl)

        # 标签栏 (modes + tags)
        if modes or tags:
            tag_row = QtWidgets.QHBoxLayout()
            tag_row.setSpacing(4)
            for m in modes[:3]:  # 最多显示 3 个 mode 标签
                tag = QtWidgets.QLabel(m)
                tag.setObjectName("pmTagBadge")
                tag_row.addWidget(tag)
            for t_str in tags[:2]:
                tag = QtWidgets.QLabel(t_str)
                tag.setObjectName("pmTagBadgeAlt")
                tag_row.addWidget(tag)
            tag_row.addStretch()
            left.addLayout(tag_row)

        h.addLayout(left, 1)

        # 启用/禁用开关
        toggle = QtWidgets.QCheckBox()
        toggle.setChecked(enabled)
        toggle.setToolTip(tr('plugin.tool_toggle_tip'))
        toggle.stateChanged.connect(
            lambda state, n=name: self._on_tool_toggle(n, state == QtCore.Qt.Checked))
        h.addWidget(toggle)

        return row

    def _on_tool_toggle(self, tool_name: str, enabled: bool):
        """启用/禁用工具"""
        try:
            from ...utils.tool_registry import get_tool_registry
            reg = get_tool_registry()
            reg.set_enabled(tool_name, enabled)
            reg.save_disabled_to_config()
        except Exception as e:
            print(f"[PluginManager] Tool toggle error: {e}")

    # ---------- Skills Tab ----------

    def _refresh_skills_list(self):
        """刷新 Skill 列表"""
        while self._skills_layout.count():
            item = self._skills_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        try:
            from ...skills import list_skills
            skills = list_skills()
        except Exception as e:
            lbl = QtWidgets.QLabel(f"⚠ {tr('plugin.load_error')}: {e}")
            lbl.setObjectName("pmErrorLabel")
            self._skills_layout.addWidget(lbl)
            self._skills_layout.addStretch()
            return

        if not skills:
            # 空状态
            empty_frame = QtWidgets.QFrame()
            empty_frame.setObjectName("pmEmptyState")
            ev = QtWidgets.QVBoxLayout(empty_frame)
            ev.setContentsMargins(20, 40, 20, 40)
            ev.setSpacing(10)
            ev.setAlignment(QtCore.Qt.AlignCenter)

            icon_lbl = QtWidgets.QLabel("🧠")
            icon_lbl.setStyleSheet("font-size: 28px; background: transparent;")
            icon_lbl.setAlignment(QtCore.Qt.AlignCenter)
            ev.addWidget(icon_lbl)

            hint_lbl = QtWidgets.QLabel(tr('plugin.no_skills'))
            hint_lbl.setObjectName("pmEmptyHint")
            hint_lbl.setAlignment(QtCore.Qt.AlignCenter)
            ev.addWidget(hint_lbl)

            self._skills_layout.addWidget(empty_frame)
        else:
            for s in skills:
                row = self._create_skill_row(s)
                self._skills_layout.addWidget(row)

        self._skills_layout.addStretch()

        # 加载用户 Skill 目录
        try:
            from ...skills import _get_user_skill_dir
            user_dir = _get_user_skill_dir()
            if user_dir:
                self._skill_dir_edit.setText(str(user_dir))
        except Exception:
            pass

    def _create_skill_row(self, info: dict) -> QtWidgets.QWidget:
        """创建单个 Skill 行（卡片式）"""
        row = QtWidgets.QFrame()
        row.setObjectName("pmCard")

        h = QtWidgets.QHBoxLayout(row)
        h.setContentsMargins(12, 8, 12, 8)
        h.setSpacing(10)

        # 图标
        icon_lbl = QtWidgets.QLabel("🧠")
        icon_lbl.setFixedWidth(20)
        icon_lbl.setStyleSheet("font-size: 14px; background: transparent;")
        icon_lbl.setAlignment(QtCore.Qt.AlignCenter)
        h.addWidget(icon_lbl)

        left = QtWidgets.QVBoxLayout()
        left.setSpacing(3)

        name = info.get("name", "Unknown")
        name_lbl = QtWidgets.QLabel(
            f"<span style='font-weight:600; color:#e0d4c0'>{name}</span>"
        )
        name_lbl.setObjectName("pmCardName")
        left.addWidget(name_lbl)

        desc = info.get("description", "")
        if desc:
            desc_lbl = QtWidgets.QLabel(desc[:120])
            desc_lbl.setObjectName("pmCardDesc")
            desc_lbl.setWordWrap(True)
            left.addWidget(desc_lbl)

        params = info.get("parameters", {})
        if params:
            param_names = list(params.keys())[:5]
            tag_row = QtWidgets.QHBoxLayout()
            tag_row.setSpacing(4)
            for p in param_names:
                tag = QtWidgets.QLabel(p)
                tag.setObjectName("pmTagBadge")
                tag_row.addWidget(tag)
            tag_row.addStretch()
            left.addLayout(tag_row)

        h.addLayout(left, 1)

        # Skill 启用/禁用开关
        tool_name = f"skill:{name}"
        enabled = True
        try:
            from ...utils.tool_registry import get_tool_registry
            enabled = get_tool_registry().is_enabled(tool_name)
        except Exception:
            pass

        toggle = QtWidgets.QCheckBox()
        toggle.setChecked(enabled)
        toggle.setToolTip(tr('plugin.tool_toggle_tip'))
        toggle.stateChanged.connect(
            lambda state, n=tool_name: self._on_tool_toggle(n, state == QtCore.Qt.Checked))
        h.addWidget(toggle)

        return row

    def _browse_skill_dir(self):
        """浏览选择用户 Skill 目录"""
        dir_path = QtWidgets.QFileDialog.getExistingDirectory(
            self, tr('plugin.skill_dir_browse'), "")
        if dir_path:
            self._skill_dir_edit.setText(dir_path)
            # 保存到 config/houdini_ai.ini
            try:
                import configparser
                from pathlib import Path
                config_dir = Path(__file__).resolve().parent.parent.parent / "config"
                ini_path = config_dir / "houdini_ai.ini"
                cfg = configparser.ConfigParser()
                if ini_path.exists():
                    cfg.read(str(ini_path), encoding='utf-8')
                if not cfg.has_section("skills"):
                    cfg.add_section("skills")
                cfg.set("skills", "user_skill_dir", dir_path)
                with open(ini_path, 'w', encoding='utf-8') as f:
                    cfg.write(f)
                print(f"[Skills] 用户 Skill 目录已设置: {dir_path}")
            except Exception as e:
                print(f"[Skills] 保存 Skill 目录失败: {e}")

    def _open_settings(self, plugin_name: str, info: dict):
        """打开插件设置对话框"""
        dlg = PluginSettingsPage(
            plugin_name=plugin_name,
            settings_schema=info.get("settings", []),
            parent=self,
        )
        dlg.exec_()


class PluginSettingsPage(QtWidgets.QDialog):
    """插件设置页 — 根据 settings schema 自动生成配置表单

    settings schema 格式:
        [
            {"key": "log_level", "type": "string", "label": "Log Level", "default": "info", "options": [...]},
            {"key": "enable_x", "type": "bool", "label": "Enable X", "default": True},
        ]
    """

    def __init__(self, plugin_name: str, settings_schema: list, parent=None):
        super().__init__(parent)
        self.setObjectName("pluginSettingsDlg")
        self.setWindowTitle(f"{tr('plugin.settings')} — {plugin_name}")
        self.setMinimumWidth(420)
        self._plugin_name = plugin_name
        self._schema = settings_schema
        self._widgets: dict = {}  # key -> widget

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 标题栏
        header = QtWidgets.QFrame()
        header.setObjectName("pmHeader")
        header.setFixedHeight(40)
        header_lay = QtWidgets.QHBoxLayout(header)
        header_lay.setContentsMargins(14, 0, 14, 0)
        title = QtWidgets.QLabel(f"⚙  {plugin_name}")
        title.setObjectName("pmTitle")
        header_lay.addWidget(title)
        root.addWidget(header)

        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        # 读取当前设置值
        try:
            from ...utils.hooks import get_plugin_setting
        except ImportError:
            get_plugin_setting = lambda pn, k, d=None: d

        # 生成表单
        form = QtWidgets.QFormLayout()
        form.setSpacing(8)
        form.setContentsMargins(0, 0, 0, 0)

        for item in settings_schema:
            key = item.get("key", "")
            label = item.get("label", key)
            stype = item.get("type", "string")
            default = item.get("default")
            options = item.get("options")
            current_val = get_plugin_setting(plugin_name, key, default)

            if stype == "bool":
                cb = QtWidgets.QCheckBox()
                cb.setChecked(bool(current_val))
                form.addRow(label, cb)
                self._widgets[key] = cb

            elif stype == "string" and options:
                combo = QtWidgets.QComboBox()
                for opt in options:
                    combo.addItem(str(opt))
                if current_val and str(current_val) in [str(o) for o in options]:
                    combo.setCurrentText(str(current_val))
                form.addRow(label, combo)
                self._widgets[key] = combo

            else:
                # string / number
                le = QtWidgets.QLineEdit()
                le.setText(str(current_val) if current_val is not None else "")
                le.setPlaceholderText(str(default) if default is not None else "")
                form.addRow(label, le)
                self._widgets[key] = le

        layout.addLayout(form)
        layout.addStretch()

        root.addLayout(layout, 1)

        # 底部按钮栏
        footer = QtWidgets.QFrame()
        footer.setObjectName("pmFooter")
        footer.setFixedHeight(42)
        footer_lay = QtWidgets.QHBoxLayout(footer)
        footer_lay.setContentsMargins(14, 0, 14, 0)
        footer_lay.addStretch()

        btn_cancel = QtWidgets.QPushButton(tr('plugin.cancel'))
        btn_cancel.setObjectName("pmFooterBtn")
        btn_cancel.setCursor(QtCore.Qt.PointingHandCursor)
        btn_cancel.clicked.connect(self.reject)
        footer_lay.addWidget(btn_cancel)

        btn_save = QtWidgets.QPushButton(tr('plugin.save'))
        btn_save.setObjectName("pmBtnPrimary")
        btn_save.setCursor(QtCore.Qt.PointingHandCursor)
        btn_save.clicked.connect(self._save)
        footer_lay.addWidget(btn_save)

        root.addWidget(footer)

    def _save(self):
        """保存设置"""
        try:
            from ...utils.hooks import set_plugin_setting
        except ImportError:
            self.reject()
            return

        for item in self._schema:
            key = item.get("key", "")
            stype = item.get("type", "string")
            widget = self._widgets.get(key)
            if not widget:
                continue

            if stype == "bool":
                value = widget.isChecked()
            elif isinstance(widget, QtWidgets.QComboBox):
                value = widget.currentText()
            else:
                value = widget.text()

            set_plugin_setting(self._plugin_name, key, value)

        self.accept()
