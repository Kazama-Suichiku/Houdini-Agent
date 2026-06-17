# -*- coding: utf-8 -*-
"""
Houdini Agent - AI Client
OpenAI-compatible API client with Function Calling, streaming, and web search.
"""

import os
import sys
import json
import ssl
import time
import re
from typing import List, Dict, Optional, Any, Callable, Generator, Tuple
from urllib.parse import quote_plus

from shared.common_utils import load_config, save_config

# 强制使用本地 lib 目录中的依赖库
_lib_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'lib')
if os.path.exists(_lib_path):
    # 将 lib 目录添加到 sys.path 最前面，确保优先使用
    if _lib_path in sys.path:
        sys.path.remove(_lib_path)
    sys.path.insert(0, _lib_path)

# 导入 requests
HAS_REQUESTS = False
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    pass


# ============================================================
# 联网搜索功能（从 web_searcher 模块导入，保持向后兼容）
# ============================================================

from .web_searcher import WebSearcher


# ============================================================
# Houdini 工具定义
# ============================================================

HOUDINI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "create_wrangle_node",
            "description": "【优先使用】创建 Wrangle 节点并设置 VEX 代码。这是解决几何处理问题的首选方式，能用 VEX 解决的问题都应该用这个工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "vex_code": {
                        "type": "string",
                        "description": "VEX 代码内容。常用语法：@P（位置）, @N（法线）, @Cd（颜色）, @pscale（点大小）, addpoint(), addprim(), addvertex() 等"
                    },
                    "wrangle_type": {
                        "type": "string",
                        "enum": ["attribwrangle", "pointwrangle", "primitivewrangle", "volumewrangle", "vertexwrangle"],
                        "description": "Wrangle 类型。默认 'attribwrangle'（最通用）。pointwrangle 处理点，primitivewrangle 处理图元"
                    },
                    "node_name": {
                        "type": "string",
                        "description": "节点名称（可选）"
                    },
                    "run_over": {
                        "type": "string",
                        "enum": ["Points", "Vertices", "Primitives", "Detail", "Numbers"],
                        "description": "运行模式（与 Houdini class 一致）：Points=2（点，默认）, Vertices=3, Primitives=1, Detail=0（全局）, Numbers=4（指定迭代次数）"
                    },
                    "parent_path": {
                        "type": "string",
                        "description": "父网络路径（可选，留空使用当前网络）"
                    }
                },
                "required": ["vex_code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_network_structure",
            "description": "获取节点网络结构。如果网络中存在 NetworkBox 分组，默认返回 box 级别概览（名称+注释+节点数），大幅节省上下文；传入 box_name 可钻入查看该 box 内的详细节点。无 NetworkBox 时返回全部节点。",
            "parameters": {
                "type": "object",
                "properties": {
                    "network_path": {
                        "type": "string",
                        "description": "网络路径如 '/obj/geo1'，留空使用当前网络"
                    },
                    "box_name": {
                        "type": "string",
                        "description": "指定 NetworkBox 名称以查看其内部详细节点和连接。留空则显示概览（box 摘要 + 未分组节点）。"
                    },
                    "page": {
                        "type": "integer",
                        "description": "页码（从1开始），结果较多时翻页查看后续内容"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_node_parameters",
            "description": "获取节点的完整参数列表及概况信息：类型、状态标志(display/render/bypass)、错误信息、输入输出连接、以及每个参数的内部名称、类型(Float/Int/Menu等)、标签、默认值、当前值、菜单选项。设置参数前必须先调用此工具确认正确的参数名和类型，不要猜测。参数较多时支持分页。",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_path": {
                        "type": "string",
                        "description": "节点完整路径如 '/obj/geo1/box1'"
                    },
                    "page": {
                        "type": "integer",
                        "description": "页码（从1开始），参数较多时翻页查看后续参数"
                    }
                },
                "required": ["node_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_node_parameter",
            "description": "设置节点参数值。注意：调用前必须先用 get_node_parameters 确认参数名和类型，不要猜测参数名。",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_path": {"type": "string", "description": "节点路径"},
                    "param_name": {"type": "string", "description": "参数名（必须是 get_node_parameters 返回的有效参数名）"},
                    "value": {
                        "type": ["string", "number", "boolean", "array"],
                        "items": {"type": ["string", "number", "boolean"]},
                        "description": "参数值（单值或数组，如向量 [1, 0, 0]）"
                    }
                },
                "required": ["node_path", "param_name", "value"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_node",
            "description": "创建单个节点。节点类型格式：'box' 或 'sop/box'（推荐直接写节点名如'box'，系统会自动识别类别）。如果创建失败，必须调用 search_node_types 查找正确的节点类型名再重试，不要盲目重试。",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_type": {
                        "type": "string",
                        "description": "节点类型名称，如 'box', 'scatter', 'noise'。直接写节点名即可，系统会自动识别类别（sop/obj等）。"
                    },
                    "node_name": {"type": "string", "description": "节点名称（可选），如不提供会自动生成"},
                    "parameters": {"type": "object", "description": "初始参数字典（可选），如 {'size': 1.0}"},
                    "parent_path": {"type": "string", "description": "父网络路径（可选），如 '/obj/geo1'，留空使用当前网络"}
                },
                "required": ["node_type"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_nodes_batch",
            "description": "批量创建节点并自动连接。nodes 数组中每个元素需要 id（临时标识）和 type（节点类型）；connections 数组指定连接关系，from/to 使用 nodes 中的 id。",
            "parameters": {
                "type": "object",
                "properties": {
                    "nodes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "type": {"type": "string"},
                                "name": {"type": "string"},
                                "parms": {"type": "object"}
                            },
                            "required": ["id", "type"]
                        }
                    },
                    "connections": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "from": {"type": "string"},
                                "to": {"type": "string"},
                                "input": {"type": "integer"}
                            }
                        }
                    }
                },
                "required": ["nodes"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "connect_nodes",
            "description": "连接两个节点。连接前应先用 get_node_inputs 查询目标节点的输入端口含义。input_index: 0=第一输入, 1=第二输入(如copytopoints的目标点), 2=第三输入。",
            "parameters": {
                "type": "object",
                "properties": {
                    "from_path": {"type": "string", "description": "上游节点路径（提供数据的节点）"},
                    "to_path": {"type": "string", "description": "下游节点路径（接收数据的节点）"},
                    "input_index": {"type": "integer", "description": "目标节点的输入端口索引。0=主输入，1=第二输入（如copy的目标点），2=第三输入。默认0"}
                },
                "required": ["from_path", "to_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_node",
            "description": "删除指定路径的节点。",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_path": {"type": "string", "description": "要删除的节点完整路径，如 '/obj/geo1/box1'"}
                },
                "required": ["node_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_node_types",
            "description": "按关键词搜索 Houdini 可用的节点类型。用于精确查找节点。",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "搜索关键词，如 'scatter', 'copy'"},
                    "category": {"type": "string", "enum": ["sop", "obj", "dop", "vop", "cop", "all"], "description": "节点类别，默认 'all'"},
                    "limit": {"type": "integer", "description": "最大结果数，默认 10"}
                },
                "required": ["keyword"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "semantic_search_nodes",
            "description": "通过自然语言描述搜索合适的节点类型。例如：'我需要在表面上随机分布点'会找到 scatter 节点。当你不确定用什么节点时使用此工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "用自然语言描述你想要的功能，如 '在表面分布点'、'复制物体到点上'、'创建噪波变形'"
                    },
                    "category": {"type": "string", "enum": ["sop", "obj", "dop", "vop", "all"], "description": "节点类别，默认 'sop'"}
                },
                "required": ["description"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_children",
            "description": "列出网络下的所有子节点，类似文件系统的 ls 命令。显示节点名称、类型和状态。节点较多时支持分页。",
            "parameters": {
                "type": "object",
                "properties": {
                    "network_path": {"type": "string", "description": "网络路径，如 '/obj/geo1'。留空使用当前网络"},
                    "recursive": {"type": "boolean", "description": "是否递归列出子网络，默认 false"},
                    "show_flags": {"type": "boolean", "description": "是否显示节点标志（显示/渲染/旁路），默认 true"},
                    "page": {"type": "integer", "description": "页码（从1开始），节点较多时翻页查看"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_selection",
            "description": "读取当前选中节点的详细信息。不需要知道节点路径，直接读取用户选中的内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "include_params": {"type": "boolean", "description": "是否包含参数详情，默认 true"},
                    "include_geometry": {"type": "boolean", "description": "是否包含几何体信息，默认 false"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_display_flag",
            "description": "设置节点的显示标志。控制哪个节点在视口中显示。",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_path": {"type": "string", "description": "节点路径"},
                    "display": {"type": "boolean", "description": "是否设为显示节点"},
                    "render": {"type": "boolean", "description": "是否设为渲染节点"}
                },
                "required": ["node_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "copy_node",
            "description": "复制/克隆节点到新位置。可以复制到同一网络或其他网络。",
            "parameters": {
                "type": "object",
                "properties": {
                    "source_path": {"type": "string", "description": "源节点路径"},
                    "dest_network": {"type": "string", "description": "目标网络路径，留空则复制到同一网络"},
                    "new_name": {"type": "string", "description": "新节点名称（可选）"}
                },
                "required": ["source_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "batch_set_parameters",
            "description": "批量修改多个节点的参数。类似 search_replace，可以在多个节点中同时修改某个参数。",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "节点路径列表"
                    },
                    "param_name": {"type": "string", "description": "参数名"},
                    "value": {
                        "type": ["string", "number", "boolean", "array"],
                        "items": {"type": ["string", "number", "boolean"]},
                        "description": "新值（单值或数组，如向量 [1, 0, 0]）"
                    }
                },
                "required": ["node_paths", "param_name", "value"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "find_nodes_by_param",
            "description": "在网络中搜索具有特定参数值的节点。类似 grep 搜索。",
            "parameters": {
                "type": "object",
                "properties": {
                    "network_path": {"type": "string", "description": "搜索的网络路径，留空使用当前网络"},
                    "param_name": {"type": "string", "description": "参数名"},
                    "value": {"type": ["string", "number"], "description": "要匹配的值（可选，留空则列出所有有此参数的节点）"},
                    "recursive": {"type": "boolean", "description": "是否递归搜索子网络，默认 true"}
                },
                "required": ["param_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "save_hip",
            "description": "保存当前 HIP 文件。可以保存到当前路径或指定新路径。",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "保存路径（可选，留空则保存到当前文件）"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "undo_redo",
            "description": "执行撤销或重做操作。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["undo", "redo"], "description": "操作类型"}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "联网搜索任意信息。可搜索天气、新闻、技术文档、Houdini 帮助、编程问题、百科知识等任何内容。只要用户的问题涉及你不确定或需要最新数据的信息，都应主动调用此工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词"
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "最大结果数，默认 5"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_webpage",
            "description": "获取指定 URL 的网页正文内容（按行分页）。首次调用返回第 1 行起的内容；如结果末尾有 [分页提示]，可传入 start_line 获取后续行。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "网页 URL"
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "从第几行开始返回（默认 1）。用于翻页：如上次显示到第 80 行，传 81 获取后续内容"
                    }
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_local_doc",
            "description": "搜索本地 Houdini 文档索引（节点/VEX函数/HOM类）。常见信息已自动注入上下文，仅在需要更多细节时主动调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "关键词，如节点名'attribwrangle'、VEX函数名'addpoint'、HOM类'hou.Node'"
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "返回前k个结果（默认5）"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_houdini_node_doc",
            "description": "获取节点帮助文档（支持分页）。自动降级：本地帮助服务器->SideFX在线文档->节点类型信息。文档较长时会分页显示，返回结果中会提示总页数和下一页调用方式。优先使用 get_node_inputs 获取输入端口信息，本工具用于需要更详细文档时。",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_type": {
                        "type": "string",
                        "description": "节点类型名称"
                    },
                    "category": {
                        "type": "string",
                        "enum": ["sop", "obj", "dop", "vop", "cop", "rop"],
                        "description": "节点类别，默认 'sop'"
                    },
                    "page": {
                        "type": "integer",
                        "description": "页码（从1开始）。首次查询不传或传1，如果返回结果提示有更多页，传入对应页码查看后续内容"
                    }
                },
                "required": ["node_type"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "execute_python",
            "description": "在 Houdini Python Shell 中执行代码。可以执行任意 Python 代码，访问 hou 模块操作场景。执行结果（包括 print 输出和错误信息）会完整返回。输出较长时支持分页，用相同 code 和不同 page 翻页查看。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "要执行的 Python 代码"
                    },
                    "page": {
                        "type": "integer",
                        "description": "页码（从1开始），输出较长时翻页查看后续内容。翻页时必须传入与首次相同的 code"
                    }
                },
                "required": ["code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "execute_shell",
            "description": "在系统 Shell 中执行命令（非 Houdini Python Shell）。可运行 pip、git、dir/ls、ffmpeg、ssh、scp 等系统命令。工作目录默认为项目根目录。命令有超时限制（默认30秒，最大120秒）。危险命令（如 rm -rf、format、del /s）会被拦截。注意：1)必须生成可直接运行的完整命令，不用占位符；2)需交互的命令必须传非交互式参数；3)优先用精确命令减少输出量(如 find -maxdepth 2)；4)路径有空格需引号包裹；5)命令失败时分析stderr后修正重试，不要盲目重复。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的 Shell 命令"
                    },
                    "cwd": {
                        "type": "string",
                        "description": "工作目录（可选，默认为项目根目录）"
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "超时秒数（可选，默认 30，最大 120）"
                    },
                    "page": {
                        "type": "integer",
                        "description": "页码（从1开始），输出较长时翻页查看后续内容"
                    }
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_errors",
            "description": "检查Houdini节点的cooking错误和警告（仅用于节点cooking问题）。注意：如果工具调用返回了错误信息（如缺少参数），无需调用此工具，直接根据返回的错误信息修正参数即可。",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_path": {
                        "type": "string",
                        "description": "要检查的节点或网络路径。如果是网络路径，会检查其下所有节点。留空则检查当前网络。"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_node_inputs",
            "description": "【连接前必用】获取节点输入端口信息(210个常用节点已缓存,快速返回)。连接多输入节点前必用!常见节点已包含完整信息,优先使用此工具而非get_houdini_node_doc。",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_type": {
                        "type": "string",
                        "description": "节点类型名称，如 'copytopoints', 'boolean', 'scatter'"
                    },
                    "category": {
                        "type": "string",
                        "enum": ["sop", "obj", "dop", "vop"],
                        "description": "节点类别，默认 'sop'"
                    }
                },
                "required": ["node_type"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "add_todo",
            "description": "添加一个任务到 Todo 列表。在开始复杂任务前，先用这个工具列出计划的步骤。",
            "parameters": {
                "type": "object",
                "properties": {
                    "todo_id": {
                        "type": "string",
                        "description": "任务唯一 ID，如 'step1', 'task_create_box'"
                    },
                    "text": {
                        "type": "string",
                        "description": "任务描述"
                    },
                    "status": {
                        "type": "string",
                        "enum": ["pending", "in_progress", "done", "error"],
                        "description": "任务状态，默认 pending"
                    }
                },
                "required": ["todo_id", "text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_todo",
            "description": "更新 Todo 任务状态。每完成一个步骤必须立即调用此工具标记为 done，不要等到最后统一标记。",
            "parameters": {
                "type": "object",
                "properties": {
                    "todo_id": {
                        "type": "string",
                        "description": "要更新的任务 ID"
                    },
                    "status": {
                        "type": "string",
                        "enum": ["pending", "in_progress", "done", "error"],
                        "description": "新状态"
                    }
                },
                "required": ["todo_id", "status"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "verify_and_summarize",
            "description": "【任务结束前必调用】验证节点网络并生成总结。自动检测:1.孤立节点 2.错误节点 3.连接完整性 4.显示标志。已内置 get_network_structure，不需要在调用前单独查询网络。如果发现问题必须修复后重新调用，直到通过。",
            "parameters": {
                "type": "object",
                "properties": {
                    "check_items": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "要检查的项目列表(如节点名)"
                    },
                    "expected_result": {
                        "type": "string",
                        "description": "期望的结果描述"
                    }
                },
                "required": ["check_items", "expected_result"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_skill",
            "description": "执行预定义的 Skill（高级分析脚本）。Skill 是经过优化的专用脚本，比手写 execute_python 更可靠。用 list_skills 查看可用 skill。",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_name": {
                        "type": "string",
                        "description": "Skill 名称（用 list_skills 获取）"
                    },
                    "params": {
                        "type": "object",
                        "description": "传给 Skill 的参数（键值对）"
                    }
                },
                "required": ["skill_name", "params"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_skills",
            "description": "列出所有可用的 Skill 及其参数说明。在需要复杂分析（如几何属性统计、批量检查等）时，先调用此工具查看是否有现成 Skill。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    # ============================================================
    # 节点布局工具 — 自动整理节点位置
    # ============================================================
    {
        "type": "function",
        "function": {
            "name": "layout_nodes",
            "description": "自动布局节点位置。在 verify_and_summarize 通过后、创建 NetworkBox 之前调用，确保节点排列整齐。支持多种布局策略：auto（智能选择）、grid（网格排列）、columns（按拓扑深度分列）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "network_path": {
                        "type": "string",
                        "description": "父网络路径（如 /obj/geo1）。留空则使用当前活跃网络。"
                    },
                    "node_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "要布局的节点完整路径列表。留空则布局整个网络的所有子节点。"
                    },
                    "method": {
                        "type": "string",
                        "enum": ["auto", "grid", "columns"],
                        "description": "布局方法。auto=智能选择（推荐），grid=网格排列，columns=按拓扑深度分列。默认 auto。"
                    },
                    "spacing": {
                        "type": "number",
                        "description": "节点间距倍率，默认 1.0。增大则节点间距更宽松。"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_node_positions",
            "description": "获取节点的位置信息（坐标、类型），用于检查布局效果或在手动微调时查看当前状态。",
            "parameters": {
                "type": "object",
                "properties": {
                    "network_path": {
                        "type": "string",
                        "description": "父网络路径（如 /obj/geo1）。留空则使用当前活跃网络。"
                    },
                    "node_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "要查询的节点完整路径列表。留空则返回整个网络下所有子节点的位置。"
                    }
                },
                "required": []
            }
        }
    },
    # ============================================================
    # NetworkBox 工具 — 节点分组与可视化组织
    # ============================================================
    {
        "type": "function",
        "function": {
            "name": "create_network_box",
            "description": "创建 NetworkBox（节点分组框），用于将功能相关的节点组织在一起。支持设置名称、注释、颜色预设，并可在创建时直接包含指定节点。建议在每完成一个逻辑阶段后使用此工具将该阶段的节点打包。",
            "parameters": {
                "type": "object",
                "properties": {
                    "parent_path": {
                        "type": "string",
                        "description": "父网络路径（如 /obj/geo1）。留空则使用当前活跃网络。"
                    },
                    "name": {
                        "type": "string",
                        "description": "NetworkBox 名称（如 input_stage, deform_stage）。留空则自动生成。"
                    },
                    "comment": {
                        "type": "string",
                        "description": "注释文字，显示在 NetworkBox 标题栏上，描述这组节点的功能（如 '数据输入与基础几何', '噪波变形处理'）。"
                    },
                    "color_preset": {
                        "type": "string",
                        "enum": ["input", "processing", "deform", "output", "simulation", "utility"],
                        "description": "颜色预设: input(蓝/输入), processing(绿/处理), deform(橙/变形), output(红/输出), simulation(紫/模拟), utility(灰/辅助)。"
                    },
                    "node_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "要加入 box 的节点完整路径列表。创建后会自动调整 box 大小以包围这些节点。"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "add_nodes_to_box",
            "description": "将节点添加到已有的 NetworkBox 中。用于在创建新节点后将其归入对应的分组。",
            "parameters": {
                "type": "object",
                "properties": {
                    "parent_path": {
                        "type": "string",
                        "description": "父网络路径（如 /obj/geo1）。留空则使用当前活跃网络。"
                    },
                    "box_name": {
                        "type": "string",
                        "description": "目标 NetworkBox 的名称。"
                    },
                    "node_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "要添加的节点完整路径列表。"
                    },
                    "auto_fit": {
                        "type": "boolean",
                        "description": "是否自动调整 box 大小以包围所有节点。默认 true。"
                    }
                },
                "required": ["box_name", "node_paths"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_network_boxes",
            "description": "列出指定网络中所有 NetworkBox 及其包含的节点。用于了解当前网络的分组组织情况。",
            "parameters": {
                "type": "object",
                "properties": {
                    "parent_path": {
                        "type": "string",
                        "description": "要查询的网络路径（如 /obj/geo1）。留空则使用当前活跃网络。"
                    }
                },
                "required": []
            }
        }
    },
    # ============================================================
    # PerfMon 性能分析工具
    # ============================================================
    {
        "type": "function",
        "function": {
            "name": "perf_start_profile",
            "description": "启动 Houdini 性能 Profiling（基于 hou.perfMon）。用于详细分析 cook 耗时和内存增长。启动后需执行操作（如强制 cook），然后调用 perf_stop_and_report 获取分析报告。如果只需快速查看各节点 cook 时间排名，优先使用 run_skill(skill_name='analyze_cook_performance')。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Profile 标题（可选，默认 'AI Performance Analysis'）"
                    },
                    "force_cook_node": {
                        "type": "string",
                        "description": "启动 profile 后立即强制 cook 的节点路径（可选）。传入末端节点路径可触发整条链的 cook。"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "perf_stop_and_report",
            "description": "停止性能 Profiling 并返回分析报告。必须先调用 perf_start_profile 启动。报告包含各节点 cook 时间排名、内存统计等。可选保存 .hperf 文件到磁盘。",
            "parameters": {
                "type": "object",
                "properties": {
                    "save_path": {
                        "type": "string",
                        "description": "保存 .hperf profile 文件的路径（可选）。如 'C:/tmp/profile.hperf'"
                    },
                    "page": {
                        "type": "integer",
                        "description": "页码（从1开始），报告较长时翻页查看"
                    }
                },
                "required": []
            }
        }
    },
    # ★ 长期记忆主动搜索工具
    {
        "type": "function",
        "function": {
            "name": "search_memory",
            "description": "搜索长期记忆库。当你需要回忆过去的经验、用户偏好、踩坑记录、调试思路、常用命令时调用此工具。可按用途分类过滤。返回的记忆含置信度，仅供参考。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词或问题描述"
                    },
                    "category": {
                        "type": "string",
                        "enum": ["preference", "command", "debug", "pitfall", "workflow", "knowledge", "user_profile", "general"],
                        "description": "按用途分类过滤（可选）"
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "返回条数，默认5，最大10"
                    }
                },
                "required": ["query"]
            }
        }
    },
    # ★ 视口截图工具（视觉验证）
    {
        "type": "function",
        "function": {
            "name": "capture_viewport",
            "description": "截取当前 Houdini 3D 视口的快照图片。用于查验节点的运行结果、检查几何体是否正确、验证材质/灯光效果等。截取的图片会自动传给你进行视觉分析。建议在完成关键操作后调用此工具来验证效果。",
            "parameters": {
                "type": "object",
                "properties": {
                    "width": {
                        "type": "integer",
                        "description": "截图宽度(像素)，默认960，范围160-1920"
                    },
                    "height": {
                        "type": "integer",
                        "description": "截图高度(像素)，默认540，范围120-1080"
                    },
                    "output_path": {
                        "type": "string",
                        "description": "可选：保存截图到指定文件路径（如 $HIP/snapshot.jpg）。未指定时截图仅传给模型做视觉分析。"
                    }
                },
                "required": []
            }
        }
    }
]

# ★ 将核心工具注册到 ToolRegistry（模块加载时自动执行）
try:
    from .tool_registry import get_tool_registry as _get_reg
    _reg = _get_reg()
    if not _reg.initialized:
        _reg.register_core_tools(HOUDINI_TOOLS)
except Exception as _e:
    print(f"[AIClient] ToolRegistry 注册失败 (非致命): {_e}")


# ============================================================
# Mixin 导入
# ============================================================

from .ai_client_context import AIClientContextMixin
from .ai_client_providers import AIClientProvidersMixin
from .ai_client_streaming import AIClientStreamingMixin
from .ai_client_agent import AIClientAgentMixin


# ============================================================
# AI 客户端
# ============================================================

class AIClient(AIClientContextMixin, AIClientProvidersMixin, AIClientStreamingMixin, AIClientAgentMixin):
    """AI 客户端，支持流式传输、Function Calling、联网搜索"""

    def __init__(self, api_key: Optional[str] = None):
        self._api_keys: Dict[str, Optional[str]] = {
            'openai': api_key or self._read_api_key('openai'),
            'deepseek': self._read_api_key('deepseek'),
            'glm': self._read_api_key('glm'),
            'duojie': self._read_api_key('duojie'),
            'openrouter': self._read_api_key('openrouter'),
            'custom': self._read_api_key('custom'),
        }
        self._ssl_context = self._create_ssl_context()
        self._web_searcher = WebSearcher()
        self._tool_executor: Optional[Callable[[str, dict], dict]] = None
        self._batch_tool_executor: Optional[Callable[[list], list]] = None

        # 网络配置
        self._max_retries = 3
        self._retry_delay = 1.0
        self._chunk_timeout = 60  # 部分模型首字节较慢，留足超时

        # ★ 持久化 HTTP Session（连接池 + Keep-Alive，避免每轮重新 TLS 握手）
        self._http_session = requests.Session()
        self._http_session.headers.update({
            'Content-Type': 'application/json',
        })

        # 停止控制（使用 threading.Event 保证线程安全）
        import threading
        self._stop_event = threading.Event()

    def request_stop(self):
        """请求停止当前请求（线程安全）"""
        self._stop_event.set()

    def reset_stop(self):
        """重置停止标志（线程安全）"""
        self._stop_event.clear()

    def is_stop_requested(self) -> bool:
        """检查是否请求了停止（线程安全）"""
        return self._stop_event.is_set()

    def set_tool_executor(self, executor: Callable[..., dict]):
        """设置工具执行器

        executor 签名: (tool_name: str, **kwargs) -> dict
        """
        self._tool_executor = executor

    def set_batch_tool_executor(self, executor: Callable[[list], list]):
        """设置批量工具执行器（用于只读工具并行批处理）

        executor 签名: (batch: [(tool_name, kwargs), ...]) -> [result_dict, ...]
        如果未设置，批量执行会退化为逐个调用 _tool_executor。
        """
        self._batch_tool_executor = executor


# 兼容旧代码
OpenAIClient = AIClient
