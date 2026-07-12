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

import json
import os
import socket
import time
from pathlib import Path

from .client import BridgeClient, DEFAULT_HOST, _FALLBACK_PORT, port_file, resolve_port

# 服务器端口被占时自动顺延的范围（与 server.start_bridge 的 candidates 一致）
_PORT_SPAN = 21


CONNECTION_TOOLS = [
    {"type": "function", "function": {
        "name": "check_houdini_connection",
        "description": ("体检 Houdini Bridge 连接：Houdini 是否在运行、端口探测、集成包安装状态、"
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
]

CONNECTION_TOOL_NAMES = {"check_houdini_connection", "repair_houdini_connection"}


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
    """所有可能有 Bridge 的端口，去重保序：当前解析端口、发现文件端口、
    env 基址顺延段、默认基址顺延段（覆盖"env/文件过期指错、服务器顺延换端口"全部情况）。"""
    cands = [resolve_port()]
    try:
        cands.append(int(port_file().read_text(encoding="utf-8").strip()))
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
        _bridge_python_root, find_houdini_installs, is_houdini_running, user_pref_dir)

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

    packages = []
    for it in installs:
        pkg = user_pref_dir(it["major_minor"]) / "packages" / "HoudiniAgent.json"
        entry = {"houdini": it["version"], "package_file": str(pkg),
                 "exists": pkg.is_file(), "target": "", "target_exists": False,
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

    out = {
        "success": True,
        "bridge_connected": connected,
        "port": {"resolved": resolved, "source": source},
        "houdini_running": running,
        "houdini_installs": [{"version": i["version"], "path": i["path"]} for i in installs],
        "packages": packages,
        "app_payload_root": str(_payload_root()),
        "bridge_log_tail": _log_tail("bridge.log"),
        "launcher_log_tail": _log_tail("launcher.log"),
    }

    if connected:
        out["houdini"] = ping_res.get("houdini")
        out["advice"] = ("Bridge 连接正常（端口 %d，Houdini %s），场景工具可用。"
                         % (resolved, ping_res.get("houdini")))
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
        missing = [p for p in packages if not p["exists"]]
        stale = [p for p in packages if p["exists"] and not p["points_to_current_app"]]
        if missing or stale:
            advice.append("集成包异常（缺失 %d 个 / 指向旧安装目录 %d 个）。先调用 "
                          "repair_houdini_connection(action='reinstall_package')，"
                          "然后请用户重启 Houdini 生效。" % (len(missing), len(stale)))
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
    from houdini_agent.launcher.houdini_discovery import find_houdini_installs, install_package
    installs = find_houdini_installs()
    if not installs:
        return {"success": False, "error": "未找到已安装的 Houdini（需要 19.5 及以上），无法安装集成包。"}
    written, errors = [], []
    for it in installs:
        try:
            written.append(install_package(str(_payload_root()), it))
        except Exception as e:
            errors.append("%s: %s" % (it["version"], e))
    if not written:
        return {"success": False, "errors": errors,
                "error": "集成包安装全部失败: %s" % "; ".join(errors)}
    return {"success": True, "package_files": written, "errors": errors,
            "result": ("已为 %d 个 Houdini 版本重写集成包。注意：需要重启 Houdini 才会生效——"
                       "请用户保存工作并重启 Houdini，之后用 repair_houdini_connection"
                       "(action='reconnect') 验证连接。" % len(written))}


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
