# -*- coding: utf-8 -*-
"""ControllerManagementMixin — extracted from controller.py."""

import json
import os
import re
import time
import uuid
from pathlib import Path
from ..controller_shared import Slot, _TPL_HOU, _TPL_MESHY


class ControllerManagementMixin:
    def _tpl_config_path(self):
        try:
            from shared.common_utils import get_config_dir
            return os.path.join(str(get_config_dir()), "templates.json")
        except Exception:
            return os.path.join(os.path.dirname(__file__), "templates.json")

    def _load_my_templates(self):
        try:
            p = self._tpl_config_path()
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    self._my_templates = [x for x in data
                                          if isinstance(x, dict) and x.get("body")]
        except Exception:
            self._my_templates = []

    def _save_my_templates(self):
        try:
            p = self._tpl_config_path()
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(self._my_templates, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _tpl_loc(self, row):
        """把一条内置模板按当前语言取出（ty/cat/t/body/note）。"""
        en = self._lang == "en"
        sfx = "_en" if en else "_zh"
        def pick(k):
            return row.get(k + sfx) or row.get(k + "_zh", "")
        return {"ty": pick("ty"), "cat": pick("cat"), "t": pick("t"),
                "body": pick("b"), "note": pick("note"), "scratch": bool(row.get("sc"))}

    @Slot(result=str)
    def starterMeshy(self):
        """空会话主推：可从零开始的 Meshy 起手式。"""
        return json.dumps([self._tpl_loc(r) for r in _TPL_MESHY if r.get("sc")],
                          ensure_ascii=False)

    @Slot(result=str)
    def starterHou(self):
        """空会话配角：可靠的 Houdini 程序化起手式。"""
        return json.dumps([self._tpl_loc(r) for r in _TPL_HOU], ensure_ascii=False)

    @Slot(result=str)
    def templateLibrary(self):
        """模板库全量数据：Meshy(全)/Houdini(全)/我的模板。"""
        meshy = [self._tpl_loc(r) for r in _TPL_MESHY]
        hou = [self._tpl_loc(r) for r in _TPL_HOU]
        mine = [{"id": x.get("id", ""), "t": x.get("name", ""), "body": x.get("body", "")}
                for x in self._my_templates]
        return json.dumps({"meshy": meshy, "hou": hou, "mine": mine}, ensure_ascii=False)

    def _tpl_plain(self, body):
        """把 {{名称:默认值}} 渲染成纯文本（取默认值；没默认值就取名称）。"""
        return re.sub(r"\{\{([^:}]+):?([^}]*)\}\}",
                      lambda m: (m.group(2) or m.group(1)), str(body or ""))

    @Slot()
    def openTemplateLibrary(self):
        self.openTemplates.emit()

    @Slot(str)
    def insertTemplate(self, body):
        """把模板填进输入框（占位符取默认值），复用 prefillComposer。"""
        try:
            self.prefillComposer.emit(self._tpl_plain(body))
        except Exception:
            pass

    @Slot(str, result=str)
    def saveTemplate(self, payload_json):
        """新增/更新「我的模板」。payload: {name, body, id?}。返回 {ok, id} 或 {ok:false, error}。"""
        try:
            p = json.loads(payload_json or "{}")
        except Exception:
            return json.dumps({"ok": False, "error": self.tr("内容格式有误")})
        name = str(p.get("name", "")).strip()
        body = str(p.get("body", "")).strip()
        if not body:
            return json.dumps({"ok": False, "error": self.tr("请填写模板内容")})
        if not name:
            name = self.tr("未命名模板")
        tid = str(p.get("id", "")).strip() or uuid.uuid4().hex[:8]
        row = {"id": tid, "name": name, "body": body}
        for i, x in enumerate(self._my_templates):
            if x.get("id") == tid:
                self._my_templates[i] = row
                break
        else:
            self._my_templates.append(row)
        self._save_my_templates()
        self.templatesChanged.emit()
        return json.dumps({"ok": True, "id": tid})

    @Slot(str)
    def deleteTemplate(self, tid):
        tid = str(tid or "")
        n = len(self._my_templates)
        self._my_templates = [x for x in self._my_templates if x.get("id") != tid]
        if len(self._my_templates) != n:
            self._save_my_templates()
            self.templatesChanged.emit()

    @Slot(result="QVariantList")
    def rulesItems(self):
        try:
            from houdini_agent.utils.rules_manager import get_all_rules
            out = []
            for r in get_all_rules(force_reload=True):
                src = r.get("source", "ui")
                out.append({
                    "id": r.get("id", ""),
                    "title": r.get("title", "") or self.tr("未命名"),
                    "content": r.get("content", ""),
                    "enabled": bool(r.get("enabled", True)),
                    "source": src,
                    "readonly": src == "file",
                    "path": r.get("file_path", ""),
                })
            return out
        except Exception as e:
            return [{"id": "", "title": "Load failed", "content": str(e), "enabled": False,
                     "source": "error", "readonly": True, "path": ""}]

    @Slot(result="QVariantMap")
    def addRule(self):
        try:
            from houdini_agent.utils.rules_manager import add_rule
            r = add_rule(self.tr("未命名"), "")
            return {"id": r.get("id", ""), "ok": True}
        except Exception as e:
            self._info("规则编辑器", "新增失败：%s" % e)
            return {"id": "", "ok": False}

    @Slot(str, str, str, bool, result=bool)
    def saveRule(self, rule_id, title, content, enabled):
        try:
            from houdini_agent.utils.rules_manager import update_rule
            if not rule_id.startswith("file:"):
                update_rule(rule_id, title=title, content=content, enabled=bool(enabled))
                self.toast.emit("规则已保存")
                self.managementChanged.emit()
                return True
        except Exception as e:
            self._info("规则编辑器", "保存失败：%s" % e)
        return False

    @Slot(str)
    def deleteRule(self, rule_id):
        if not rule_id:
            return
        self._request_confirm("删除规则", "确定要删除这条规则吗？", lambda rid=rule_id: self._delete_rule_now(rid))

    def _delete_rule_now(self, rule_id):
        try:
            from houdini_agent.utils.rules_manager import delete_rule
            if not rule_id.startswith("file:"):
                delete_rule(rule_id)
                self.toast.emit("规则已删除")
                self.managementChanged.emit()
        except Exception as e:
            self._info("规则编辑器", "删除失败：%s" % e)

    @Slot()
    def openRulesFolder(self):
        try:
            from houdini_agent.utils.rules_manager import get_rules_dir, ensure_rules_dir
            ensure_rules_dir()
            self._open_path(str(get_rules_dir()))
        except Exception as e:
            self._info("规则编辑器", "打开目录失败：%s" % e)

    @staticmethod
    def _open_path(path):
        import os
        import subprocess
        import sys as _sys
        if _sys.platform == "win32":
            os.startfile(path)
        elif _sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])

    @Slot(result="QVariantList")
    def pluginItems(self):
        try:
            from houdini_agent.utils.hooks import list_plugins
            return [{
                "name": p.get("name", ""),
                "version": p.get("version", ""),
                "author": p.get("author", ""),
                "description": p.get("description", ""),
                "enabled": bool(p.get("_enabled", False)),
                "settings": bool(p.get("settings")),
            } for p in list_plugins()]
        except Exception as e:
            return [{"name": "Load failed", "version": "", "author": "",
                     "description": str(e), "enabled": False, "settings": False}]

    @Slot(str, result="QVariantList")
    def pluginSettings(self, name):
        try:
            from houdini_agent.utils.hooks import list_plugins, get_plugin_setting
            for p in list_plugins():
                if p.get("name") != name:
                    continue
                out = []
                for item in (p.get("settings") or []):
                    key = item.get("key", "")
                    if not key:
                        continue
                    default = item.get("default")
                    out.append({
                        "key": key,
                        "label": item.get("label", key),
                        "type": item.get("type", "string"),
                        "value": get_plugin_setting(name, key, default),
                        "default": default,
                        "options": item.get("options") or [],
                    })
                return out
        except Exception as e:
            self._info("插件设置", "加载失败：%s" % e)
        return []

    @Slot(str, str)
    def savePluginSettings(self, name, settings_json):
        try:
            from houdini_agent.utils.hooks import set_plugin_setting
            settings = json.loads(settings_json or "[]")
            for item in settings:
                key = item.get("key")
                if not key:
                    continue
                value = item.get("value")
                if item.get("type") == "bool":
                    value = bool(value)
                set_plugin_setting(name, key, value)
            self.toast.emit("插件设置已保存")
        except Exception as e:
            self._info("插件设置", "保存失败：%s" % e)

    @Slot(str, bool)
    def setPluginEnabled(self, name, enabled):
        try:
            from houdini_agent.utils.hooks import enable_plugin, disable_plugin
            (enable_plugin if enabled else disable_plugin)(name)
            self.toast.emit(("已启用 " if enabled else "已禁用 ") + name)
        except Exception as e:
            self._info("插件管理", "切换失败：%s" % e)

    @Slot(str)
    def reloadPlugin(self, name):
        try:
            from houdini_agent.utils.hooks import reload_plugin
            reload_plugin(name)
            self.toast.emit("已重载 " + name)
        except Exception as e:
            self._info("插件管理", "重载失败：%s" % e)

    @Slot()
    def reloadAllPlugins(self):
        try:
            from houdini_agent.utils.hooks import reload_all_plugins
            reload_all_plugins()
            self.toast.emit("已重载全部插件")
        except Exception as e:
            self._info("插件管理", "重载失败：%s" % e)

    @Slot()
    def openPluginsFolder(self):
        try:
            from houdini_agent.utils.hooks import get_plugins_dir
            p = get_plugins_dir()
            p.mkdir(parents=True, exist_ok=True)
            self._open_path(str(p))
        except Exception as e:
            self._info("插件管理", "打开目录失败：%s" % e)

    @Slot(result="QVariantList")
    def toolItems(self):
        try:
            from houdini_agent.utils.tool_registry import get_tool_registry
            return get_tool_registry().list_all()
        except Exception as e:
            return [{"name": "Load failed", "description": str(e), "source": "error",
                     "enabled": False, "modes": [], "tags": []}]

    @Slot(str, bool)
    def setToolEnabled(self, name, enabled):
        try:
            from houdini_agent.utils.tool_registry import get_tool_registry
            reg = get_tool_registry()
            reg.set_enabled(name, bool(enabled))
            reg.save_disabled_to_config()
        except Exception as e:
            self._info("插件管理", "工具切换失败：%s" % e)

    @Slot(result="QVariantList")
    def skillItems(self):
        try:
            from houdini_agent.skills import list_skills
            return list_skills()
        except Exception as e:
            return [{"name": "Load failed", "description": str(e), "parameters": {}}]

    @Slot(result=str)
    def userSkillDir(self):
        try:
            from houdini_agent.skills import _get_user_skill_dir
            p = _get_user_skill_dir()
            return str(p) if p else ""
        except Exception:
            return ""

    @Slot(str)
    def setUserSkillDir(self, path):
        try:
            if path.startswith("file:///"):
                path = path[8:] if os.name == "nt" else path[7:]
            elif path.startswith("file://"):
                path = path[7:]
            import configparser
            config_dir = Path(__file__).resolve().parents[2] / "config"
            config_dir.mkdir(parents=True, exist_ok=True)
            ini_path = config_dir / "houdini_ai.ini"
            cfg = configparser.ConfigParser()
            if ini_path.exists():
                cfg.read(str(ini_path), encoding="utf-8")
            if not cfg.has_section("skills"):
                cfg.add_section("skills")
            cfg.set("skills", "user_skill_dir", path)
            with open(ini_path, "w", encoding="utf-8") as f:
                cfg.write(f)
            try:
                from houdini_agent.skills import reload_skills
                reload_skills()
            except Exception:
                pass
            self.toast.emit("用户脚本目录已设置")
        except Exception as e:
            self._info("插件管理", "设置脚本目录失败：%s" % e)

    @Slot(result="QVariantMap")
    def memoryStats(self):
        try:
            from houdini_agent.utils.memory_store import get_memory_store
            s = get_memory_store()
            return {"episodic": s.count_episodic(), "semantic": s.count_semantic(),
                    "procedural": s.count_procedural()}
        except Exception as e:
            return {"episodic": 0, "semantic": 0, "procedural": 0, "error": str(e)}

    @Slot(str, result="QVariantList")
    def memoryItems(self, kind):
        try:
            from houdini_agent.utils.memory_store import get_memory_store
            s = get_memory_store()
            out = []
            if kind == "semantic":
                for r in s.get_all_semantic()[:80]:
                    out.append({"id": r.id, "title": r.category or "Semantic",
                                "body": r.rule, "meta": "confidence %.2f · level %s" % (r.confidence, r.abstraction_level)})
            elif kind == "procedural":
                for r in s.get_all_procedural()[:80]:
                    out.append({"id": r.id, "title": r.strategy_name,
                                "body": r.description, "meta": "priority %.2f · success %.0f%% · %d conditions" % (r.priority, r.success_rate * 100, len(r.conditions or []))})
            else:
                for r in s.get_recent_episodic(80):
                    out.append({"id": r.id, "title": r.task_description,
                                "body": r.result_summary, "meta": time.strftime("%Y-%m-%d %H:%M", time.localtime(r.timestamp))})
            return out
        except Exception as e:
            return [{"id": "", "title": "Load failed", "body": str(e), "meta": ""}]

    @Slot(str, str)
    def deleteMemory(self, kind, record_id):
        if not record_id:
            return
        self._request_confirm("删除记忆", "确定要删除这条长期记忆吗？", lambda k=kind, rid=record_id: self._delete_memory_now(k, rid))

    def _delete_memory_now(self, kind, record_id):
        try:
            from houdini_agent.utils.memory_store import get_memory_store
            s = get_memory_store()
            if kind == "semantic":
                s.delete_semantic(record_id)
            elif kind == "procedural":
                s.delete_procedural(record_id)
            else:
                s.delete_episodic(record_id)
            self.toast.emit("记忆已删除")
            self.managementChanged.emit()
        except Exception as e:
            self._info("记忆管理", "删除失败：%s" % e)

