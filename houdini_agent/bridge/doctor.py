# -*- coding: utf-8 -*-
"""Bridge 连接医生：诊断与自愈 Houdini 连接。

场景工具坏掉时唯一的通道（Bridge）本身就是病灶，所以这里的一切都在 app 侧
独立完成，绝不依赖 Bridge：

  - scan_for_bridge()/adopt_port()  底层自愈原语：扫描候选端口找活着的 Bridge，
    找到后把端口固化到本进程（协调器心跳、工具调用门禁、agent 修复共用）
  - diagnose()                      结构化体检（进程/端口/集成包/日志）+ 中文修复建议
  - repair(action=...)              reconnect | reinstall_package | launch_houdini
  - CONNECTION_TOOLS                上述能力的 agent 工具声明（OpenAI schema），
    由 BridgeAgentSession 注册、controller 在 app 侧执行
"""

import concurrent.futures
import json
import os
import socket
import tempfile
import time
import uuid
from pathlib import Path

from .client import (
    BridgeClient, DEFAULT_HOST, _FALLBACK_PORT,
    bridges_dir, port_file, registered_ports, resolve_port,
)

# 服务器端口被占时自动顺延的范围（与 server.start_bridge 的 candidates 一致）
_PORT_SPAN = 21


CONNECTION_TOOLS = [
    {"type": "function", "function": {
        "name": "check_houdini_connection",
        "description": ("体检 Houdini Bridge 连接：Houdini 是否在运行、所有已连接实例列表、端口探测、集成包安装状态、"
                        "桥接/启动器日志摘要，并给出具体修复建议（advice 字段）。"
                        "场景工具报告 Houdini 未连接时先调用它定位原因。"
                        "聊天、Plan 和 Meshy 生成不依赖此连接，仅场景工具需要。"),
        "parameters": {"type": "object", "properties": {}, "required": []},
    }},
    {"type": "function", "function": {
        "name": "repair_houdini_connection",
        "description": ("修复 Houdini 连接。action='reconnect'：扫描候选端口重连"
                        "（Bridge 换端口/端口发现文件过期/环境变量指错时有效，几秒内完成）；"
                        "'reinstall_package'：重写 Houdini 集成包（应用被移动或包缺失时用，需重启 Houdini 生效）；"
                        "'launch_houdini'：启动 Houdini 并等待 Bridge 就绪（约 1-2 分钟，Houdini 已在运行时会拒绝）。"
                        "先用 check_houdini_connection 判断该用哪个动作。"),
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string",
                       "enum": ["reconnect", "reinstall_package", "launch_houdini"],
                       "description": "修复动作"},
            "version": {"type": "string",
                        "description": "launch_houdini 时可指定 Houdini 版本前缀（如 '21.0'），默认用最新版本"},
            "wait_seconds": {"type": "integer",
                             "description": "最长等待秒数：reconnect 默认 10，launch_houdini 默认 90"},
        }, "required": ["action"]},
    }},
    {"type": "function", "function": {
        "name": "list_houdini_instances",
        "description": ("列出当前本机所有正在运行且已接入 Bridge 的 Houdini 实例（支持同版本多开与不同版本同时打开）。"
                        "返回每个实例的 port、pid、Houdini 版本、当前打开的 .hip 文件名与路径、当前网络路径及是否为当前活跃目标。"
                        "当用户提到多个 Houdini 窗口、不同版本、或要跨文件读取/复制节点时，先调用本工具确认各实例身份。"),
        "parameters": {"type": "object", "properties": {}, "required": []},
    }},
    {"type": "function", "function": {
        "name": "switch_houdini_instance",
        "description": ("切换后续默认场景工具所指向的活跃 Houdini 实例。"
                        "instance 可填端口号（如 '45173'）、PID、.hip 文件名/路径关键词（如 'shot_010.hip'）、"
                        "唯一版本号（如 '20.5'）或 list_houdini_instances 返回的序号（如 '2'）。"),
        "parameters": {"type": "object", "properties": {
            "instance": {"type": "string",
                         "description": "目标实例标识：端口 / PID / .hip 文件名 / 版本号 / 序号"},
        }, "required": ["instance"]},
    }},
    {"type": "function", "function": {
        "name": "run_in_houdini_instance",
        "description": ("在指定的某个 Houdini 实例上直接执行任意场景工具（无需反复切换默认实例），"
                        "用于跨文件查看节点/参数/VEX、对比两个打开的 .hip 场景，或定向修改另一个 Houdini 窗口中的节点。"),
        "parameters": {"type": "object", "properties": {
            "instance": {"type": "string",
                         "description": "目标实例标识：端口 / PID / .hip 文件名 / 版本号 / 序号"},
            "tool_name": {"type": "string",
                          "description": "要在该实例执行的 Houdini 场景工具名（如 get_scene_info / inspect_node / create_node / execute_python 等）"},
            "arguments": {"type": "object",
                          "description": "传给该工具的参数字典（可选）"},
        }, "required": ["instance", "tool_name"]},
    }},
    {"type": "function", "function": {
        "name": "transfer_nodes_between_instances",
        "description": ("【跨文件 / 跨 Houdini 实例传输节点】把源 Houdini 实例中的一个或多个节点（含连线、参数、VEX Wrangle、子网等）"
                        "经 Houdini 原生 saveItemsToFile/loadItemsFromFile 无损复制到另一个已打开的 Houdini 实例（支持同版本多开与跨版本如 20.5 ↔ 21.0）。"
                        "同一批 node_paths 必须位于同一个父网络下；目标父网络若是 /obj/<name> 且不存在会自动创建 geo 容器。"),
        "parameters": {"type": "object", "properties": {
            "source_instance": {"type": "string",
                                "description": "源 Houdini 实例标识（端口 / PID / .hip 名 / 版本号 / 序号）"},
            "target_instance": {"type": "string",
                                "description": "目标 Houdini 实例标识（端口 / PID / .hip 名 / 版本号 / 序号）"},
            "node_paths": {"type": "array", "items": {"type": "string"},
                           "description": "要复制的源节点绝对路径列表（同一父网络下），如 ['/obj/geo1/grid1', '/obj/geo1/mountain1'] 或 ['/obj/hero_geo']"},
            "target_parent": {"type": "string",
                              "description": "目标实例里的父网络路径，默认 '/obj'（传 SOP 节点时可填 '/obj/geo1' 等）"},
            "create_parent": {"type": "boolean",
                              "description": "目标 /obj/<name> 容器不存在时是否自动创建 geo 节点，默认 true"},
        }, "required": ["source_instance", "target_instance", "node_paths"]},
    }},
]

CONNECTION_TOOL_NAMES = {t["function"]["name"] for t in CONNECTION_TOOLS}


# ---------------------------------------------------------------- primitives

def _state_dir():
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "HoudiniAgent"


def _log_tail(name, max_lines=12, max_chars=200):
    try:
        text = (_state_dir() / name).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    lines = [ln.strip()[:max_chars] for ln in text.splitlines() if ln.strip()]
    return lines[-max_lines:]


def _payload_root():
    """app 的 Python 载荷根：源码运行 = 仓库根；打包运行 = <安装目录>/_internal。
    与 external_app 的 repo_root、install_package 的 repo_root 参数语义一致。"""
    import houdini_agent
    return Path(houdini_agent.__file__).resolve().parents[1]


def _env_base():
    try:
        return int(os.environ.get("HAGENT_BRIDGE_PORT") or 0) or None
    except ValueError:
        return None


def _candidate_ports():
    """所有可能有 Bridge 的端口，去重保序：当前解析端口、发现文件端口、多实例登记端口、
    env 基址顺延段、默认基址顺延段（覆盖"env/文件过期指错、服务器顺延换端口、多实例并存"全部情况）。"""
    cands = [resolve_port()]
    try:
        cands.append(int(port_file().read_text(encoding="utf-8").strip()))
    except Exception:
        pass
    try:
        cands += registered_ports()
    except Exception:
        pass
    env = _env_base()
    if env:
        cands += [env + i for i in range(_PORT_SPAN)]
    cands += [_FALLBACK_PORT + i for i in range(_PORT_SPAN)]
    seen, out = set(), []
    for p in cands:
        if isinstance(p, int) and 0 < p < 65536 and p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _tcp_state(port, timeout=0.3):
    try:
        with socket.create_connection((DEFAULT_HOST, port), timeout=timeout):
            return "open"
    except socket.timeout:
        return "timeout"
    except OSError:
        return "closed"


def _ping(port):
    try:
        return BridgeClient(port=port, connect_timeout=0.3).ping()
    except Exception:
        return None


def _short_houdini_ver(ver):
    parts = str(ver or "").strip().split(".")
    if len(parts) >= 2 and parts[0].isdigit():
        return "H%s.%s" % (parts[0], parts[1])
    return ("H" + str(ver).strip()) if ver else "Houdini"


def format_instance_label(inst, include_port=False):
    ver = _short_houdini_ver(inst.get("houdini"))
    hip = str(inst.get("hip_name") or "").strip()
    if not hip and inst.get("hip_path"):
        hip = os.path.basename(str(inst["hip_path"]))
    base = "%s · %s" % (ver, hip) if hip else ver
    if include_port and inst.get("port"):
        return "%s (:%s)" % (base, inst["port"])
    return base


def _probe_instance(port, include_context=True):
    """探测单个端口：返回标准化实例字典，未开或非 Bridge 返回 None。"""
    if _tcp_state(port, timeout=0.25) != "open":
        return None
    cli = BridgeClient(port=port, connect_timeout=0.3)
    info = None
    if include_context:
        ctx = cli.scene_context()
        if isinstance(ctx, dict) and (ctx.get("houdini") or ctx.get("path")):
            info = ctx
    if not isinstance(info, dict):
        ping = cli.ping()
        if not (isinstance(ping, dict) and ping.get("status") == "ok"):
            return None
        info = ping
    hip_path = str(info.get("hip_path") or "")
    hip_name = str(info.get("hip_name") or "") or (os.path.basename(hip_path) if hip_path else "")
    inst = {
        "id": "port:%d" % port,
        "port": int(port),
        "pid": int(info.get("pid") or 0) or None,
        "houdini": str(info.get("houdini") or ""),
        "hip_path": hip_path,
        "hip_name": hip_name,
        "path": str(info.get("path") or "/obj"),
        "selection": str(info.get("selection") or ""),
    }
    inst["label"] = format_instance_label(inst, include_port=False)
    return inst


def _prune_stale_registrations(live_ports):
    """清理 bridges/ 下已退出实例遗留的登记文件（仅清理不在 live_ports 且端口不通的）。"""
    d = bridges_dir()
    if not d.is_dir():
        return
    live_set = set(int(p) for p in (live_ports or []))
    for f in d.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            p = int(data.get("port") or 0)
            if p and p not in live_set and _tcp_state(p, timeout=0.12) != "open":
                f.unlink()
        except Exception:
            continue


def discover_all_bridges(heartbeat=None, include_context=True):
    """并发扫描全部候选端口，发现所有活着的 Houdini Bridge 实例（支持同版本多开与跨版本）。
    返回按端口升序排列的实例列表，并标明当前 active 实例。"""
    cands = _candidate_ports()
    if not cands:
        return []
    active_port = resolve_port()
    found = []
    workers = min(12, len(cands))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_probe_instance, p, include_context): p for p in cands}
        for f in concurrent.futures.as_completed(futs):
            if heartbeat:
                try:
                    heartbeat()
                except Exception:
                    pass
            try:
                inst = f.result()
            except Exception:
                inst = None
            if inst is not None:
                found.append(inst)
    found.sort(key=lambda x: x["port"])
    for idx, inst in enumerate(found, 1):
        inst["index"] = idx
        inst["active"] = (inst["port"] == active_port)
    try:
        _prune_stale_registrations([x["port"] for x in found])
    except Exception:
        pass
    return found


def resolve_instance(query, instances=None, heartbeat=None):
    """按端口 / PID / 序号 / .hip 文件名 / 版本号解析目标 Houdini 实例。
    返回 (instance_dict, "") 或 (None, 可读中文错误说明)。"""
    if instances is None:
        instances = discover_all_bridges(heartbeat=heartbeat, include_context=True)
    if not instances:
        return None, "当前没有已连接的 Houdini 实例。请先启动 Houdini 或调用 check_houdini_connection 排查。"
    q = str(query or "").strip()
    if not q:
        for inst in instances:
            if inst.get("active"):
                return inst, ""
        return instances[0], ""

    low = q.lower()
    # 1) 显式 port:<n> 或精确端口匹配
    port_q = low[5:] if low.startswith("port:") else (low[1:] if low.startswith(":") else low)
    if port_q.isdigit():
        num = int(port_q)
        for inst in instances:
            if inst["port"] == num:
                return inst, ""
        for inst in instances:
            if inst.get("pid") == num:
                return inst, ""
        if 1 <= num <= len(instances):
            return instances[num - 1], ""

    # 2) 显式 pid:<n>
    if low.startswith("pid:") and low[4:].isdigit():
        pid = int(low[4:])
        for inst in instances:
            if inst.get("pid") == pid:
                return inst, ""

    # 3) 精确文件名/路径匹配
    exact = [inst for inst in instances if (
        (inst.get("hip_name") or "").lower() == low
        or (inst.get("hip_path") or "").lower() == low
        or (inst.get("houdini") or "").lower() == low
    )]
    if len(exact) == 1:
        return exact[0], ""
    if len(exact) > 1:
        desc = "；".join("#%d %s (:%d, pid=%s)" % (
            i["index"], i["label"], i["port"], i.get("pid") or "?") for i in exact)
        return None, "匹配到多个 Houdini 实例（%s），请用端口号或完整 .hip 路径指定：%s" % (q, desc)

    # 4) 子串模糊匹配（.hip 文件名、路径、版本号、显示标签）
    fuzzy = []
    for inst in instances:
        hay = " ".join([
            str(inst.get("hip_name") or ""),
            str(inst.get("hip_path") or ""),
            str(inst.get("houdini") or ""),
            str(inst.get("label") or ""),
            str(inst.get("port") or ""),
        ]).lower()
        if low in hay:
            fuzzy.append(inst)
    if len(fuzzy) == 1:
        return fuzzy[0], ""
    if len(fuzzy) > 1:
        desc = "；".join("#%d %s (:%d, pid=%s)" % (
            i["index"], i["label"], i["port"], i.get("pid") or "?") for i in fuzzy)
        return None, "关键词 %r 匹配到多个 Houdini 实例，请用端口号或更精确的 .hip 名区分：%s" % (q, desc)

    avail = "；".join("#%d %s (:%d)" % (i["index"], i["label"], i["port"]) for i in instances)
    return None, "未找到匹配的 Houdini 实例 %r。当前在线实例：%s" % (q, avail)


def list_instances_result(heartbeat=None):
    instances = discover_all_bridges(heartbeat=heartbeat, include_context=True)
    if not instances:
        return {
            "success": True,
            "count": 0,
            "instances": [],
            "result": "当前没有已连接的 Houdini 实例。请先打开 Houdini（可调用 check_houdini_connection 排查）。",
        }
    # 若当前活跃端口已不在在线列表里，自动切到第一个活实例
    if not any(i.get("active") for i in instances):
        adopt_port(instances[0]["port"])
        instances[0]["active"] = True
    lines = ["当前共发现 %d 个在线 Houdini 实例：" % len(instances)]
    for inst in instances:
        mark = " [当前默认]" if inst.get("active") else ""
        hip_info = inst.get("hip_path") or inst.get("hip_name") or "未命名场景"
        lines.append(
            "- #%d %s%s | port=%d | pid=%s | hip=%s | network=%s (%s)"
            % (inst["index"], inst["label"], mark, inst["port"],
               inst.get("pid") or "?", hip_info, inst.get("path") or "/obj",
               inst.get("selection") or "no selection"))
    return {
        "success": True,
        "count": len(instances),
        "instances": instances,
        "result": "\n".join(lines),
    }


def switch_instance(query, heartbeat=None):
    instances = discover_all_bridges(heartbeat=heartbeat, include_context=True)
    inst, err = resolve_instance(query, instances=instances)
    if err:
        return {"success": False, "error": err, "instances": instances}
    adopt_port(inst["port"])
    for item in instances:
        item["active"] = (item["port"] == inst["port"])
    return {
        "success": True,
        "reconnected": True,
        "switched": True,
        "port": inst["port"],
        "instance": inst,
        "instances": instances,
        "result": "已将当前活跃 Houdini 实例切换为 #%d %s（端口 %d，文件 %s）。后续场景工具将默认作用于此实例。"
                  % (inst["index"], inst["label"], inst["port"],
                     inst.get("hip_path") or inst.get("hip_name") or "untitled.hip"),
    }


def run_in_instance(instance_query, tool_name, arguments=None, heartbeat=None, read_only=False):
    tool_name = str(tool_name or "").strip()
    if not tool_name:
        return {"success": False, "error": "缺少 tool_name"}
    if tool_name in CONNECTION_TOOL_NAMES:
        return {"success": False, "error": "不能通过 run_in_houdini_instance 嵌套调用连接工具 %s" % tool_name}
    if read_only:
        from .server import _MUTATING
        if tool_name in _MUTATING or tool_name in ("save_hip", "delete_node", "execute_shell"):
            return {"success": False, "error": "当前处于只读模式（Ask/Plan），禁止在实例上执行写入工具 %s" % tool_name}
    inst, err = resolve_instance(instance_query, heartbeat=heartbeat)
    if err:
        return {"success": False, "error": err}
    try:
        cli = BridgeClient(port=inst["port"])
        res = cli.execute_tool(tool_name, arguments or {})
    except Exception as e:
        return {"success": False, "error": "在实例 %s (:%d) 上执行 %s 失败: %s"
                                           % (inst["label"], inst["port"], tool_name, e)}
    if isinstance(res, dict):
        res.setdefault("_instance", {
            "port": inst["port"], "pid": inst.get("pid"),
            "houdini": inst.get("houdini"), "hip_name": inst.get("hip_name"),
            "label": inst["label"],
        })
        if res.get("success") and res.get("result"):
            res["result"] = "[%s :%d]\n%s" % (inst["label"], inst["port"], res["result"])
        return res
    return {"success": False, "error": "工具返回了非字典结果"}


def transfer_nodes(source_instance, target_instance, node_paths, target_parent="/obj",
                   create_parent=True, heartbeat=None):
    """跨 Houdini 实例/文件复制节点（经 Houdini 原生 saveItemsToFile / loadItemsFromFile）。"""
    paths = [str(p).strip() for p in (node_paths or []) if str(p or "").strip()]
    if not paths:
        return {"success": False, "error": "node_paths 不能为空"}
    instances = discover_all_bridges(heartbeat=heartbeat, include_context=True)
    src, err = resolve_instance(source_instance, instances=instances)
    if err:
        return {"success": False, "error": "源实例解析失败：%s" % err}
    dst, err = resolve_instance(target_instance, instances=instances)
    if err:
        return {"success": False, "error": "目标实例解析失败：%s" % err}

    tmp_file = os.path.join(tempfile.gettempdir(), "hagent_xfer_%s.cpio" % uuid.uuid4().hex[:12])
    try:
        src_cli = BridgeClient(port=src["port"])
        try:
            exp = src_cli.export_items(paths, tmp_file)
        except Exception as e:
            if "unknown action" in str(e).lower():
                return {"success": False,
                        "error": "源实例 %s (:%d) 正在运行旧版 Bridge，尚未支持跨实例传输。"
                                 "请保存并重启该 Houdini 实例后重试。" % (src["label"], src["port"])}
            return {"success": False, "error": "从源实例 %s 导出节点失败: %s" % (src["label"], e)}
        if not isinstance(exp, dict) or not exp.get("success"):
            return {"success": False, "error": (exp or {}).get("error") or "导出源节点失败"}

        dst_cli = BridgeClient(port=dst["port"])
        try:
            imp = dst_cli.import_items(target_parent or "/obj", tmp_file, create_parent=create_parent)
        except Exception as e:
            if "unknown action" in str(e).lower():
                return {"success": False,
                        "error": "目标实例 %s (:%d) 正在运行旧版 Bridge，尚未支持跨实例传输。"
                                 "请保存并重启该 Houdini 实例后重试。" % (dst["label"], dst["port"])}
            return {"success": False, "error": "向目标实例 %s 导入节点失败: %s" % (dst["label"], e)}
        if not isinstance(imp, dict) or not imp.get("success"):
            return {"success": False, "error": (imp or {}).get("error") or "导入目标节点失败"}

        created = imp.get("created") or []
        summary = (
            "已从 [%s :%d] 跨文件传输 %d 个节点（%s）到 [%s :%d] 的 %s，新建路径：%s"
            % (src["label"], src["port"], exp.get("count", len(paths)),
               ", ".join(exp.get("names") or paths),
               dst["label"], dst["port"], imp.get("parent") or target_parent,
               ", ".join(created) if created else "(详见目标网络)")
        )
        out = {
            "success": True,
            "source": src,
            "target": dst,
            "exported": exp.get("paths") or paths,
            "created": created,
            "parent": imp.get("parent") or target_parent,
            "result": summary,
        }
        if imp.get("_node_changes"):
            out["_node_changes"] = imp["_node_changes"]
        return out
    finally:
        try:
            if os.path.isfile(tmp_file):
                os.remove(tmp_file)
        except OSError:
            pass


def scan_for_bridge(heartbeat=None):
    """在候选端口上找活着的 Bridge。返回 (port, ping_result)；找不到返回 (None, None)。
    localhost 上关闭端口是瞬时拒绝，整轮扫描通常 <1 秒。"""
    for p in _candidate_ports():
        if heartbeat:
            try:
                heartbeat()
            except Exception:
                pass
        if _tcp_state(p) != "open":
            continue
        info = _ping(p)
        if isinstance(info, dict) and info.get("status") == "ok":
            return p, info
    return None, None


def adopt_port(port):
    """把找到的活端口固化到本进程：env 在 resolve_port 里优先级最高，
    可覆盖过期的发现文件/指错的外部环境变量。只改本进程，不碰服务器写的发现文件。"""
    os.environ["HAGENT_BRIDGE_PORT"] = str(int(port))


def ensure_connected(heartbeat=None):
    """一步自愈：当前解析端口 ping 得通直接返回；不通就全端口扫描并采纳。
    返回 (port, ping_result) 或 (None, None)。协调器心跳与工具调用门禁共用。"""
    p = resolve_port()
    info = _ping(p)
    if isinstance(info, dict) and info.get("status") == "ok":
        return p, info
    found, info = scan_for_bridge(heartbeat)
    if found:
        if found != p:
            adopt_port(found)
        return found, info
    return None, None


# ----------------------------------------------------------------- diagnose

def diagnose():
    """结构化体检 + 修复建议。全部字段都给 agent 看，advice 是主结论。"""
    from houdini_agent.launcher.houdini_discovery import (
        _bridge_python_root, find_houdini_installs, is_houdini_running,
        known_pref_versions, user_pref_dirs)

    resolved = resolve_port()
    if _env_base():
        source = "env(HAGENT_BRIDGE_PORT)"
    elif port_file().is_file():
        source = "port_file"
    else:
        source = "default"

    ping_res = _ping(resolved)
    connected = isinstance(ping_res, dict) and ping_res.get("status") == "ok"

    installs = find_houdini_installs()
    running = is_houdini_running()
    expected_target = str(_bridge_python_root(str(_payload_root())))

    # 按「版本 × 候选用户目录」逐一体检：pref_dir_exists=True 而 exists=False
    # 就是"Houdini 实际使用的目录里没有集成包"（文档目录重定向时旧版装错位置的铁证）。
    versions = sorted({it["major_minor"] for it in installs} | known_pref_versions())
    packages = []
    for mm in versions:
        for pref in user_pref_dirs(mm):
            pkg = pref / "packages" / "HoudiniAgent.json"
            entry = {"houdini": mm, "pref_dir": str(pref),
                     "pref_dir_exists": pref.is_dir(),
                     "package_file": str(pkg), "exists": pkg.is_file(),
                     "target": "", "target_exists": False,
                     "points_to_current_app": False}
            if entry["exists"]:
                try:
                    data = json.loads(pkg.read_text(encoding="utf-8"))
                    repo = ""
                    for e in data.get("env", []) or []:
                        if isinstance(e, dict) and "HAGENT_REPO" in e:
                            repo = str(e["HAGENT_REPO"])
                    entry["target"] = repo
                    entry["target_exists"] = bool(repo) and os.path.isdir(repo)
                    if repo:
                        entry["points_to_current_app"] = (
                            Path(repo).resolve() == Path(expected_target).resolve())
                except Exception as e:
                    entry["parse_error"] = str(e)
            packages.append(entry)

    all_instances = discover_all_bridges(include_context=False)
    out = {
        "success": True,
        "bridge_connected": connected,
        "port": {"resolved": resolved, "source": source},
        "instance_count": len(all_instances),
        "instances": all_instances,
        "houdini_running": running,
        "houdini_installs": [{"version": i["version"], "path": i["path"]} for i in installs],
        "packages": packages,
        "app_payload_root": str(_payload_root()),
        "bridge_log_tail": _log_tail("bridge.log"),
        "launcher_log_tail": _log_tail("launcher.log"),
    }

    if connected:
        out["houdini"] = ping_res.get("houdini")
        extra = ("（当前共发现 %d 个在线 Houdini 实例，可用 list_houdini_instances / switch_houdini_instance / transfer_nodes_between_instances 跨实例协作）"
                 % len(all_instances)) if len(all_instances) > 1 else ""
        out["advice"] = ("Bridge 连接正常（端口 %d，Houdini %s），场景工具可用。%s"
                         % (resolved, ping_res.get("houdini"), extra))
        return out

    found, _info = scan_for_bridge()
    if found:
        out["bridge_found_on_port"] = found
        out["advice"] = ("端口 %d 上发现活着的 Bridge，但当前解析端口 %d（来源 %s）已失效——"
                         "端口发现文件过期或环境变量指错。调用 "
                         "repair_houdini_connection(action='reconnect') 即可切换过去。"
                         % (found, resolved, source))
        return out

    advice = []
    if not installs:
        advice.append("未找到已安装的 Houdini（需要 19.5 及以上）。请让用户确认 Houdini 已安装。")
    elif not running:
        advice.append("Houdini 未在运行。可调用 repair_houdini_connection(action='launch_houdini') "
                      "启动（自动安装集成包，启动约需 1 分钟），或让用户手动打开 Houdini。")
    else:
        # 只统计「目录真实存在」的候选：pref 目录在=Houdini 真用过它，里面没包才是问题
        gap = [p for p in packages if p["pref_dir_exists"] and not p["exists"]]
        stale = [p for p in packages if p["exists"] and not p["points_to_current_app"]]
        if gap:
            advice.append("发现 Houdini 实际使用的用户目录缺少集成包（常见原因：「文档」目录被"
                          "移动到其他盘/被 OneDrive 重定向，旧版本装错了位置）：%s。先调用 "
                          "repair_houdini_connection(action='reinstall_package')——新版会写入"
                          "全部候选目录——然后请用户重启 Houdini。"
                          % "; ".join(p["pref_dir"] for p in gap[:3]))
        elif stale:
            advice.append("集成包存在但指向旧安装目录（%d 处）。先调用 repair_houdini_connection"
                          "(action='reinstall_package')，然后请用户重启 Houdini 生效。" % len(stale))
        tail = "\n".join(out["bridge_log_tail"])
        if "无法启动 Bridge" in tail or "10013" in tail or "10048" in tail:
            advice.append("bridge.log 显示端口被占用导致 Bridge 启动失败。请用户重启 Houdini"
                          "（会自动顺延到空闲端口），仍失败则设置环境变量 HAGENT_BRIDGE_PORT 后重启。")
        elif "MCP import failed" in tail or "Failed to start bridge" in tail:
            advice.append("bridge.log 显示 Houdini 侧加载 Bridge 失败（详见 bridge_log_tail）。"
                          "通常是集成包指向的目录失效——先 reinstall_package 再请用户重启 Houdini。")
        elif not tail:
            advice.append("Houdini 在运行但 bridge.log 为空——Houdini 很可能在集成包安装之前启动、"
                          "没有加载集成包。请用户重启 Houdini。")
        else:
            advice.append("Houdini 在运行但未发现 Bridge 监听。最常见原因是 Houdini 在集成包安装之前"
                          "启动——请用户重启 Houdini；也可结合 bridge_log_tail 进一步判断。")
    out["advice"] = " ".join(advice)
    return out


# ------------------------------------------------------------------- repair

def repair(action, version=None, wait_seconds=None, heartbeat=None):
    action = (action or "").strip()
    if action == "reconnect":
        return _repair_reconnect(int(wait_seconds or 10), heartbeat)
    if action == "reinstall_package":
        return _repair_reinstall()
    if action == "launch_houdini":
        return _repair_launch(version, int(wait_seconds or 90), heartbeat)
    return {"success": False,
            "error": "未知修复动作: %r（可选 reconnect / reinstall_package / launch_houdini）" % action}


def _repair_reconnect(wait_seconds, heartbeat=None):
    deadline = time.monotonic() + max(2, min(wait_seconds, 60))
    while True:
        port, info = ensure_connected(heartbeat)
        if port:
            return {"success": True, "reconnected": True, "port": port,
                    "houdini": (info or {}).get("houdini"),
                    "result": "已连接 Houdini Bridge（端口 %d）。场景工具已恢复可用。" % port}
        if time.monotonic() >= deadline:
            return {"success": False, "reconnected": False,
                    "error": "扫描候选端口未发现 Bridge。请先 check_houdini_connection 定位原因"
                             "（Houdini 未运行 → launch_houdini；集成包异常 → reinstall_package 后重启 Houdini）。"}
        time.sleep(1.0)


def _repair_reinstall():
    from houdini_agent.launcher.houdini_discovery import install_all_packages
    result_map = install_all_packages(str(_payload_root()))
    written = [p for lst in result_map.values() for p in lst]
    if not written:
        return {"success": False,
                "error": "未找到已安装的 Houdini（需要 19.5 及以上）或用户目录全部不可写，无法安装集成包。"}
    return {"success": True, "package_files": written,
            "result": ("已为 %d 个 Houdini 版本重写集成包（共写入 %d 处候选用户目录，覆盖"
                       "「文档」目录被移动/重定向的情况）。注意：需要重启 Houdini 才会生效——"
                       "请用户保存工作并重启 Houdini，之后用 repair_houdini_connection"
                       "(action='reconnect') 验证连接。" % (len(result_map), len(written)))}


def _repair_launch(version, wait_seconds, heartbeat=None):
    from houdini_agent.launcher.houdini_discovery import (
        find_houdini_installs, is_houdini_running, launch_houdini)
    # 先看是不是其实已经能连上，免得白启动一个新实例
    port, info = ensure_connected(heartbeat)
    if port:
        return {"success": True, "reconnected": True, "port": port,
                "houdini": (info or {}).get("houdini"),
                "result": "Bridge 本来就在线（端口 %d），已直接连接，无需再启动 Houdini。" % port}
    installs = find_houdini_installs()
    if not installs:
        return {"success": False, "error": "未找到已安装的 Houdini（需要 19.5 及以上）。"}
    if is_houdini_running():
        return {"success": False,
                "error": "Houdini 已在运行但没有加载 Bridge，不宜再启动第二个实例（占用大量内存）。"
                         "正确做法：先 repair_houdini_connection(action='reinstall_package')，"
                         "然后请用户保存工作、关闭并重新打开 Houdini，Bridge 会自动加载。"}
    target = None
    if version:
        for it in installs:
            if it["version"].startswith(str(version)):
                target = it
                break
        if target is None:
            return {"success": False,
                    "error": "未找到版本 %r。可用版本: %s"
                             % (version, ", ".join(i["version"] for i in installs))}
    else:
        target = installs[0]
    try:
        launch_houdini(str(_payload_root()), target)
    except Exception as e:
        return {"success": False, "error": "启动 Houdini 失败: %s" % e}
    deadline = time.monotonic() + max(10, min(wait_seconds, 300))
    while time.monotonic() < deadline:
        if heartbeat:
            try:
                heartbeat()
            except Exception:
                pass
        time.sleep(2.0)
        port, info = ensure_connected(heartbeat)
        if port:
            return {"success": True, "reconnected": True, "port": port,
                    "houdini": (info or {}).get("houdini"),
                    "result": "已启动 Houdini %s 并连上 Bridge（端口 %d）。场景工具可用。"
                              % (target["version"], port)}
    return {"success": False, "reconnected": False,
            "error": "Houdini %s 已启动，但 %d 秒内 Bridge 尚未就绪（首次启动可能较慢）。"
                     "请稍等片刻后调用 repair_houdini_connection(action='reconnect') 再试。"
                     % (target["version"], wait_seconds)}
