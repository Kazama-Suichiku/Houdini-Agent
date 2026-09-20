# -*- coding: utf-8 -*-
from __future__ import annotations

"""FastMCP 服务器与工具注册（OOP 拆分版，包名：utils.mcp）

架构说明：
    本模块负责 FastMCP HTTP 服务器，面向外部 MCP 客户端。
    底层 Houdini 操作尽量委托给 hou_core.py 共享层。
    对比 client.py（面向内部 AI Agent 的直接 Python 调用）。
"""

import asyncio
import glob
import logging
import os
import queue
import tempfile
import threading
import time
import uuid
import re
from typing import Any, Optional, Callable

try:
	import hou  # type: ignore
except Exception:
	hou = None  # type: ignore

try:
	import requests
except Exception:
	requests = None  # type: ignore

try:
	from bs4 import BeautifulSoup
except Exception:
	BeautifulSoup = None  # type: ignore

from .settings import read_settings
from .logger import get_logger
from . import hou_core

log: logging.Logger = get_logger()

# 运行时全局
mcp = None  # FastMCP 实例
mcp_thread_handle: Optional[threading.Thread] = None
stop_event = threading.Event()
_server_start_time: float | None = None

# 资源注册（用于 flipbook 图片）
_registered_flipbook_resources: set[str] = set()
_resource_lock = threading.RLock()

# 简单任务队列（UI event loop 消费）
task_queue: queue.Queue = queue.Queue()


def _fastmcp_available() -> bool:
	return mcp is not None


def register_image_resource(filepath: str) -> str:
	"""将图片注册为 MCP 资源，返回可通过 MCP HTTP 访问的路径。"""
	global _registered_flipbook_resources, mcp
	if mcp is None:
		return filepath
	resource_id = f"flipbook_{uuid.uuid4().hex}.png"
	resource_url = f"file://{resource_id}"
	http_url = f"/resources/{resource_id}"
	with _resource_lock:
		if resource_url in _registered_flipbook_resources:
			return http_url

		@mcp.resource(uri=resource_url, mime_type="image/png")  # type: ignore[attr-defined]
		def _res() -> bytes:
			with open(filepath, "rb") as f:
				return f.read()

		_registered_flipbook_resources.add(resource_url)
	try:
		log.info("[MCP] 注册资源成功：%s", http_url)
	except Exception:
		pass
	return http_url


def _setup_fastmcp_tools():
	"""在 FastMCP 实例上注册所有工具。"""
	global mcp
	if mcp is None:
		return

	def ok(message: str = "", data: Any | None = None) -> dict:
		return {"status": "success", "message": message, "data": data}

	def err(message: str, code: Optional[str] = None, data: Any | None = None) -> dict:
		payload = {"status": "error", "message": message}
		if code:
			payload["code"] = code
		if data is not None:
			payload["data"] = data
		return payload

	def tool_wrapper(fn: Callable[..., dict]) -> Callable[..., dict]:
		def _wrapped(*args, **kwargs) -> dict:
			try:
				return fn(*args, **kwargs)
			except Exception as e:
				log.exception("MCP tool error in %s", getattr(fn, "__name__", "<tool>"))
				return err(f"内部错误：{e}", code="internal_error")
		_wrapped.__name__ = getattr(fn, "__name__", "wrapped")
		return _wrapped

	@mcp.tool  # type: ignore[attr-defined]
	@tool_wrapper
	def check_node_errors(node_path: str | None = None) -> dict:
		"""检查节点错误（委托给 hou_core 共享层）"""
		success, msg, errors = hou_core.check_errors(node_path)
		return ok(msg, errors) if success else err(msg)

	@mcp.tool  # type: ignore[attr-defined]
	@tool_wrapper
	def display_node_result(node_path: str) -> dict:
		"""设置节点显示标志（委托给 hou_core 共享层）"""
		success, msg = hou_core.set_display_flag(node_path)
		return ok(msg) if success else err(msg)

	@mcp.tool  # type: ignore[attr-defined]
	@tool_wrapper
	def get_houdini_help(help_type: str, item_name: str) -> dict:
		if requests is None:
			return err("requests 未安装。")
		if BeautifulSoup is None:
			return err("bs4 未安装。请安装 beautifulsoup4。")
		base_url = "https://www.sidefx.com/docs/houdini/"
		url_mapping = {
			"obj": f"nodes/obj/{item_name}.html",
			"sop": f"nodes/sop/{item_name}.html",
			"vex_function": f"vex/functions/{item_name}.html",
			"vex_expression": "vex/snippets.html",
			"python_hou": f"hom/hou/{item_name}.html",
		}
		if help_type not in url_mapping:
			return err(f"Unsupported help type: {help_type}")
		full_url = base_url + url_mapping[help_type]
		s = read_settings()
		resp = requests.get(full_url, timeout=s.request_timeout)
		if resp.status_code != 200:
			return err(f"Failed to fetch help page. Status code: {resp.status_code}")
		soup = BeautifulSoup(resp.text, 'html.parser')
		h1 = soup.find('h1', class_='title')
		if h1:
			title_text = (h1.contents[0].strip() if h1.contents else "").strip()
			subtitle = h1.find('span', class_='subtitle')
			subtitle_text = subtitle.get_text(strip=True) if subtitle else ""
			title = f"{title_text} - {subtitle_text}" if subtitle_text else title_text
		else:
			title = "No title found"
		des = soup.find('p', class_="summary")
		description = des.get_text(strip=True) if des else ''
		parameters: list[dict[str, Any]] = []
		for param_div in soup.find_all("div", class_="parameter"):
			name_tag = param_div.find("p", class_="label")
			desc_tag = param_div.find("div", class_="content")
			if not name_tag or not desc_tag:
				continue
			param_name = name_tag.get_text(strip=True)
			param_desc_p = desc_tag.find("p")
			param_desc = param_desc_p.get_text(strip=True) if param_desc_p else ""
			options: list[dict[str, str]] = []
			defs = desc_tag.find("div", class_="defs")
			if defs:
				for def_item in defs.find_all("div", class_="def"):
					label = def_item.find("p", class_="label")
					d2 = def_item.find("div", class_="content")
					if label and d2:
						options.append({
							"name": label.get_text(strip=True),
							"description": d2.get_text(strip=True),
						})
			parameters.append({"name": param_name, "description": param_desc, "options": options or None})

		def extract_section(section_id: str) -> list[dict[str, str]]:
			items: list[dict[str, str]] = []
			inouts = soup.find("div", id=f'{section_id}-body')
			if inouts:
				for def_item in inouts.find_all("div", class_="def"):
					label = def_item.find("p", class_="label")
					desc_div = def_item.find("div", class_="content")
					if label and desc_div:
						items.append({"name": label.get_text(strip=True), "description": desc_div.get_text(strip=True)})
			return items

		return ok("Help content retrieved successfully.", {
			"title": title,
			"url": full_url,
			"description": description,
			"parameters": parameters,
			"inputs": extract_section("inputs"),
			"outputs": extract_section("outputs"),
		})

	@mcp.tool  # type: ignore[attr-defined]
	@tool_wrapper
	def get_available_node_types(parent_path: str) -> dict:
		if hou is None:
			return err("Houdini 环境不可用。")
		parent = hou.node(parent_path)
		if not parent:
			return err(f"父节点 {parent_path} 未找到！")
		node_type_category = parent.childTypeCategory()
		if not node_type_category:
			return err(f"节点 {parent_path} 不能包含子节点！")
		node_types = node_type_category.nodeTypes()
		type_names = list(node_types.keys())
		categories: dict[str, list[dict[str, Any]]] = {}
		for type_name, node_type in node_types.items():
			try:
				category = node_type.category().name()
				categories.setdefault(category, []).append({
					"name": type_name,
					"label": node_type.description() if hasattr(node_type, 'description') else type_name,
				})
			except Exception:
				continue
		return ok(
			f"成功获取节点 {parent_path} 的可用子节点类型，共 {len(type_names)} 种。",
			{
				"node_types": type_names,
				"categories": categories,
				"parent_category": node_type_category.name(),
				"total_count": len(type_names),
			},
		)

	@mcp.tool  # type: ignore[attr-defined]
	@tool_wrapper
	def set_node_parameter(node_path: str, param_name: str, value: Any) -> dict:
		if hou is None:
			return err("Houdini 环境不可用。")
		node = hou.node(node_path)
		if not node:
			return err(f"节点 {node_path} 未找到！")
		parm = node.parm(param_name)
		if not parm:
			parm_tuple = node.parmTuple(param_name)
			if not parm_tuple:
				return err(f"节点 {node_path} 中未找到参数 '{param_name}'！")
			if isinstance(value, (list, tuple)):
				parm_tuple.set(value)
				actual_value = parm_tuple.eval()
			else:
				return err(f"参数 '{param_name}' 是元组参数，需要提供列表或元组值！")
		else:
			if isinstance(value, (list, tuple)):
				return err(f"参数 '{param_name}' 是单个参数，不能设置列表值！")
			parm.set(value)
			actual_value = parm.eval()
		return ok(
			f"成功设置节点 {node_path} 的参数 '{param_name}'。",
			{"node_path": node_path, "parameter": param_name, "set_value": value, "actual_value": actual_value},
		)

	@mcp.tool  # type: ignore[attr-defined]
	@tool_wrapper
	def get_node_parameters(node_path: str, include_hidden: bool = False) -> dict:
		if hou is None:
			return err("Houdini 环境不可用。")
		node = hou.node(node_path)
		if not node:
			return err(f"节点 {node_path} 未找到！")
		parameters: dict[str, Any] = {}
		parm_groups: dict[str, list[str]] = {}
		for parm in node.parms():
			try:
				if not include_hidden and parm.isHidden():
					continue
				parm_template = parm.parmTemplate()
				group_name = "General"
				try:
					if hasattr(parm_template, 'folderName'):
						folder_name = parm_template.folderName()
						if folder_name:
							group_name = folder_name
				except Exception:
					pass
				parm_groups.setdefault(group_name, [])
				try:
					current_value = parm.eval()
				except Exception:
					current_value = "无法获取"
				default_value = None
				try:
					if hasattr(parm_template, 'defaultValue'):
						default_value = parm_template.defaultValue()
				except Exception:
					pass
				help_text = ""
				try:
					if hasattr(parm_template, 'help'):
						help_info = parm_template.help()
						if help_info:
							help_text = help_info
				except Exception:
					pass
				parameters[parm.name()] = {
					"name": parm.name(),
					"label": parm.description(),
					"type": parm_template.type().name() if hasattr(parm_template, 'type') else "unknown",
					"current_value": current_value,
					"default_value": default_value,
					"is_locked": parm.isLocked(),
					"has_keyframes": parm.isTimeDependent(),
					"help": help_text,
				}
				parm_groups[group_name].append(parm.name())
			except Exception:
				continue
		tuples: dict[str, Any] = {}
		for parm_tuple in node.parmTuples():
			try:
				if not include_hidden and parm_tuple.isHidden():
					continue
				try:
					current_value = parm_tuple.eval()
				except Exception:
					current_value = "无法获取"
				tuples[parm_tuple.name()] = {
					"name": parm_tuple.name(),
					"label": parm_tuple.description(),
					"size": len(parm_tuple),
					"current_value": current_value,
					"components": [p.name() for p in parm_tuple],
				}
			except Exception:
				continue
		return ok(
			f"成功获取节点 {node_path} 的参数信息。",
			{
				"node_path": node_path,
				"node_type": node.type().name(),
				"node_label": node.type().description(),
				"parameters": parameters,
				"parameter_tuples": tuples,
				"parameter_groups": parm_groups,
				"total_params": len(parameters),
			},
		)

	@mcp.tool  # type: ignore[attr-defined]
	@tool_wrapper
	def create_node(parent_path: str, node_type: str, node_name: str = "") -> dict:
		"""创建节点（委托给 hou_core 共享层）"""
		success, msg, node = hou_core.create_node(parent_path, node_type, node_name)
		if not success:
			return err(msg)
		return ok("Node created successfully.", {"node_path": node.path() if node else ""})

	@mcp.tool  # type: ignore[attr-defined]
	@tool_wrapper
	def delete_node(node_path: str) -> dict:
		"""删除节点（委托给 hou_core 共享层）"""
		success, msg = hou_core.delete_node(node_path)
		return ok(msg) if success else err(msg)

	@mcp.tool  # type: ignore[attr-defined]
	@tool_wrapper
	def execute_python_code(code: str) -> dict:
		import contextlib, io
		try:
			if hou is None:
				return err("Houdini 环境不可用。")
			exec_globals: dict[str, Any] = {"hou": hou}
			exec_locals: dict[str, Any] = {}
			stdout_buffer = io.StringIO()
			with contextlib.redirect_stdout(stdout_buffer):
				try:
					result = eval(code.strip(), exec_globals, exec_locals)
					return ok("表达式执行成功", {"result": repr(result), "stdout": stdout_buffer.getvalue(), "type": "expression"})
				except SyntaxError:
					stdout_buffer.seek(0); stdout_buffer.truncate(0)
					exec(code.strip(), exec_globals, exec_locals)
					result_hint = "代码执行完成"
					if exec_locals:
						local_vars = {k: v for k, v in exec_locals.items() if not k.startswith('__')}
						if local_vars:
							result_hint = f"局部变量: {list(local_vars.keys())}"
					return ok(result_hint, {"result": "执行成功", "stdout": stdout_buffer.getvalue(), "type": "statement", "local_variables": list(exec_locals.keys()) if exec_locals else []})
		except Exception as e:
			return err(f"执行失败：{e}")

	@mcp.tool  # type: ignore[attr-defined]
	@tool_wrapper
	def connect_nodes(output_node_path: str, input_node_path: str, input_index: int = 0) -> dict:
		"""连接节点（委托给 hou_core 共享层）"""
		success, msg = hou_core.connect_nodes(output_node_path, input_node_path, input_index)
		if not success:
			return err(msg)
		return ok(msg, {"output_node": output_node_path, "input_node": input_node_path, "input_index": input_index})

	@mcp.tool  # type: ignore[attr-defined]
	@tool_wrapper
	def get_node_info(node_path: str) -> dict:
		"""获取节点信息（委托给 hou_core 共享层）"""
		success, msg, info = hou_core.get_node_info(node_path)
		return ok(msg, info) if success else err(msg)

	@mcp.tool  # type: ignore[attr-defined]
	@tool_wrapper
	def create_node_network(network_config: dict) -> dict:
		if hou is None:
			return err("Houdini 环境不可用。")
		parent_path = network_config.get("parent_path")
		nodes_config = network_config.get("nodes", [])
		connections_config = network_config.get("connections", [])
		if not parent_path:
			return err("缺少 parent_path 参数！")
		parent = hou.node(parent_path)
		if not parent:
			return err(f"父节点 {parent_path} 未找到！")
		created_nodes: dict[str, dict[str, Any]] = {}
		created_connections: list[dict[str, Any]] = []
		errors: list[str] = []
		for node_config in nodes_config:
			try:
				node_type = node_config.get("type")
				node_name = node_config.get("name", "")
				# 支持 "parameters" 和 "parms" 两种写法
				parameters = node_config.get("parameters") or node_config.get("parms", {})
				if not node_type:
					errors.append(f"节点配置缺少 type: {node_config}")
					continue
				_nm = (str(node_name).strip() if node_name else None)
				if _nm:
					import re as _re
					_nm2 = _re.sub(r"[^A-Za-z0-9_]+", "_", _nm).strip("_")
					_nm = _nm2 if _nm2 else None
				node = parent.createNode(node_type, _nm)
				actual_name = node.name()
				created_nodes[actual_name] = {"path": node.path(), "type": node_type, "requested_name": node_name}
				for param_name, value in parameters.items():
					try:
						parm = node.parm(param_name)
						if parm:
							parm.set(value)
						else:
							parm_tuple = node.parmTuple(param_name)
							if parm_tuple:
								parm_tuple.set(value)
					except Exception as param_error:
						errors.append(f"设置节点 {actual_name} 参数 {param_name} 失败: {str(param_error)}")
				try:
					_t = node.type().name().lower()
					if _t == "attribwrangle":
						_sn = node.parm("snippet")
						if _sn and not parameters.get("snippet"):
							if not (_sn.eval() or "").strip():
								_sn.set("@pscale = fit01(rand(@ptnum + ch('seed')), 0.1, 0.5);")
					elif _t == "scatter":
						_np = node.parm("npts")
						if _np and not parameters.get("npts"):
							_np.set(200)
				except Exception:
					pass
			except Exception as node_error:
				errors.append(f"创建节点失败: {str(node_error)}")
		for conn_config in connections_config:
			try:
				from_name = conn_config.get("from") or conn_config.get("src")
				to_name = conn_config.get("to") or conn_config.get("dst")
				input_index = int(conn_config.get("input_index", conn_config.get("input", 0)) or 0)
				from_node = None
				to_node = None
				for name, info in created_nodes.items():
					if name == from_name or info["requested_name"] == from_name:
						from_node = hou.node(info["path"])
					if name == to_name or info["requested_name"] == to_name:
						to_node = hou.node(info["path"])
				if not from_node:
					errors.append(f"未找到源节点: {from_name}")
					continue
				if not to_node:
					errors.append(f"未找到目标节点: {to_name}")
					continue
				to_node.setInput(input_index, from_node)
				created_connections.append({"from": from_node.path(), "to": to_node.path(), "input_index": input_index})
			except Exception as conn_error:
				errors.append(f"建立连接失败: {str(conn_error)}")
		try:
			parent.layoutChildren()
		except Exception:
			pass
		# 注意：不再自动猜测连接关系。
		# 原因：自动连接（如按创建顺序串联、猜测 copytopoints 输入）
		# 会导致不可预测的结果。连接关系应由调用方通过 connections 配置显式指定。
		success_message = f"成功创建 {len(created_nodes)} 个节点，建立 {len(created_connections)} 个连接"
		if errors:
			success_message += f"，但有 {len(errors)} 个错误"
		try:
			if created_nodes:
				last_node = hou.node(list(created_nodes.values())[-1]["path"])
				if last_node:
					last_node.setDisplayFlag(True)
					last_node.setRenderFlag(True)
		except Exception:
			pass
		return {
			"status": ("success" if created_nodes else "error"),
			"message": success_message,
			"data": {
				"parent_path": parent_path,
				"created_nodes": created_nodes,
				"created_connections": created_connections,
				"errors": errors,
			},
		}

	@mcp.tool  # type: ignore[attr-defined]
	@tool_wrapper
	def auto_layout_nodes(parent_path: str, spacing: tuple = (2.0, 2.0)) -> dict:
		if hou is None:
			return err("Houdini 环境不可用。")
		parent = hou.node(parent_path)
		if not parent:
			return err(f"parent node {parent_path} not found!")
		parent.layoutChildren(horizontal_spacing=spacing[0], vertical_spacing=spacing[1])
		return ok(f"Auto-layout completed for {len(parent.children())} nodes.")

	@mcp.tool  # type: ignore[attr-defined]
	@tool_wrapper
	def list_children(parent_path: str) -> dict:
		if hou is None:
			return err("Houdini 环境不可用。")
		parent = hou.node(parent_path)
		if not parent:
			return err(f"parent node {parent_path} not found!")
		children = parent.children()
		child_info = [{"name": c.name(), "type": c.type().name()} for c in children]
		return ok("Children nodes retrieved successfully.", child_info)

	# ========================================
	# NetworkBox 工具（委托给 hou_core 共享层）
	# ========================================

	@mcp.tool  # type: ignore[attr-defined]
	@tool_wrapper
	def create_network_box(
		parent_path: str,
		name: str = "",
		comment: str = "",
		color_preset: str = "",
		node_paths: list | None = None,
	) -> dict:
		"""创建 NetworkBox 并可选地将节点加入其中"""
		success, msg, box = hou_core.create_network_box(
			parent_path, name, comment, color_preset, node_paths or []
		)
		if success:
			return ok(msg, {"box_name": box.name() if box else name})
		return err(msg)

	@mcp.tool  # type: ignore[attr-defined]
	@tool_wrapper
	def add_nodes_to_box(
		parent_path: str,
		box_name: str,
		node_paths: list,
		auto_fit: bool = True,
	) -> dict:
		"""将节点添加到已有的 NetworkBox"""
		success, msg = hou_core.add_nodes_to_box(parent_path, box_name, node_paths, auto_fit)
		return ok(msg) if success else err(msg)

	@mcp.tool  # type: ignore[attr-defined]
	@tool_wrapper
	def list_network_boxes(parent_path: str) -> dict:
		"""列出网络中所有 NetworkBox 及其内容"""
		success, msg, boxes_info = hou_core.list_network_boxes(parent_path)
		return ok(msg, boxes_info) if success else err(msg)

	@mcp.tool  # type: ignore[attr-defined]
	def get_task_queue_status() -> dict:
		return {"status": "success", "message": "Task queue status retrieved.", "data": {"tasks_in_queue": task_queue.qsize(), "recent_results": getattr(mcp, "recent_results", []) if mcp else []}}

	@mcp.tool(name="viewport_flipbook", description="渲染 Houdini 视口并返回图片资源链接", enabled=read_settings().enable_flipbook)  # type: ignore[attr-defined]
	@tool_wrapper
	def viewport_flipbook(start_frame: int = 1, end_frame: int = 24) -> dict:
		global _registered_flipbook_resources
		timeId = time.time_ns()
		if hou is None:
			return err("Houdini 环境不可用。")
		viewer = hou.ui.paneTabOfType(hou.paneTabType.SceneViewer)
		if viewer is None:
			return err("找不到 Scene Viewer，必须在 GUI 模式运行。")
		tmp_dir = tempfile.gettempdir()
		output_template = os.path.join(tmp_dir, f"flipbook_{timeId}.$F4.jpg")
		resolution = (100, 100)
		timeline = hou.playbar
		if not start_frame:
			start_frame = int(timeline.frameRange()[0])
		if not end_frame:
			end_frame = int(timeline.frameRange()[1])
		flip_settings = viewer.flipbookSettings().stash()
		flip_settings.output(output_template)
		flip_settings.frameRange((start_frame, end_frame))
		flip_settings.resolution(resolution)
		flip_settings.outputToMPlay(False)
		viewer.flipbook(viewer.curViewport(), flip_settings)
		glob_path = output_template.replace("$F4", "*").replace("$F", "*")
		image_files = sorted(glob.glob(glob_path))
		if not image_files:
			return err("没有生成图像，请检查场景设置。")
		frames = []
		for idx, filepath in enumerate(image_files, start=start_frame):
			url = register_image_resource(filepath)
			frames.append({"frame": idx, "path": url})
		with _resource_lock:
			_registered_flipbook_resources.clear()
		return ok(
			f"生成 {len(frames)} 帧图像。",
			{"image_width": resolution[0], "image_height": resolution[1], "frames": frames},
		)

	@mcp.tool(name="health", description="MCP 健康检查")  # type: ignore[attr-defined]
	def health() -> dict:
		now = time.time()
		uptime = (now - _server_start_time) if _server_start_time else None
		s = read_settings()
		return {
			"status": "success",
			"message": "OK",
			"data": {
				"hou_available": bool(hou is not None),
				"uptime_sec": uptime,
				"config": {
					"host": s.host,
					"port": s.port,
					"transport": s.transport,
					"flipbook_enabled": s.enable_flipbook,
				},
			},
		}


def _mcp_thread_runner():
	if not _fastmcp_available():
		return
	try:
		if os.name == 'nt' and hasattr(asyncio, 'WindowsProactorEventLoopPolicy'):
			asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
	except Exception:
		pass
	loop = asyncio.new_event_loop()
	asyncio.set_event_loop(loop)

	async def run_server_until_stopped():
		global mcp, _server_start_time
		if mcp is None:
			return
		s = read_settings()
		host = s.host or "127.0.0.1"
		port = s.port or 9000
		transport = s.transport or "streamable-http"
		try:
			server_task = asyncio.create_task(mcp.run_async(transport=transport, host=host, port=port))
		except Exception as e:
			log.exception("Failed to start MCP server: %s", e)
			return
		_server_start_time = time.time()
		log.info("MCP server started at http://%s:%s/mcp/", host, port)
		try:
			while not stop_event.is_set():
				await asyncio.sleep(0.1)
		finally:
			log.info("🛑 Shutdown requested. Cancelling server...")
			server_task.cancel()
			try:
				await server_task
			except asyncio.CancelledError:
				pass
			log.info("Server shutdown completed.")

	loop.run_until_complete(run_server_until_stopped())
	loop.close()


def ensure_mcp_running(auto_start: bool = True) -> tuple[bool, str]:
	global mcp, mcp_thread_handle
	try:
		from fastmcp import FastMCP  # type: ignore
	except Exception:
		return False, "fastmcp 未安装，跳过 MCP 服务器启动。"
	if hou is None:
		return False, "未检测到 Houdini 环境（hou），跳过 MCP 服务器启动。"
	s = read_settings()
	if not s.enabled:
		return False, "配置禁用了 MCP（mcp_enabled=false）。"
	if mcp_thread_handle and mcp_thread_handle.is_alive():
		return True, "MCP 服务器已在运行。"
	mcp = FastMCP("Houdini MCP Server")  # type: ignore
	_setup_fastmcp_tools()
	if auto_start:
		mcp_thread_handle = threading.Thread(target=_mcp_thread_runner, daemon=True)
		mcp_thread_handle.start()
		try:
			def _process_tasks():
				while not task_queue.empty():
					try:
						fn = task_queue.get_nowait()
						fn()
					except Exception as e:
						try:
							hou.ui.displayMessage(f"Task error: {e}")
						except Exception:
							pass
			if hasattr(hou, 'ui') and hou.ui is not None:
				hou.ui.addEventLoopCallback(_process_tasks)
		except Exception:
			pass
	return True, "MCP 服务器已启动。"


def stop_mcp_server(timeout: float = 3.0) -> tuple[bool, str]:
	global mcp_thread_handle, _server_start_time
	if not (mcp_thread_handle and mcp_thread_handle.is_alive()):
		return True, "MCP 服务器未运行。"
	stop_event.set()
	mcp_thread_handle.join(timeout=timeout)
	if mcp_thread_handle.is_alive():
		return False, "MCP 服务器未在超时时间内停止。"
	_server_start_time = None
	return True, "MCP 服务器已停止。"


def get_mcp_status() -> dict:
	s = read_settings()
	running = bool(mcp_thread_handle and mcp_thread_handle.is_alive())
	uptime = (time.time() - _server_start_time) if (_server_start_time) else None
	return {
		"running": running,
		"host": s.host,
		"port": s.port,
		"transport": s.transport,
		"uptime_sec": uptime,
	}


# ⚠️ 模块级自动启动已移除
# 原因：import 不应产生副作用（启动线程、注册回调等）。
# 请改为由调用方显式调用 ensure_mcp_running()。
# 示例：
#   from utils.mcp.server import ensure_mcp_running
#   ensure_mcp_running(auto_start=True)
