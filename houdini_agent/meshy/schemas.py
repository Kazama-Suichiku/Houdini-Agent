# -*- coding: utf-8 -*-
"""
Meshy 工具的 function-calling schema + 工具名集合。

工具分两类：
  - 网络工具（NETWORK_TOOLS）：在 app 侧后台线程执行，调用 Meshy API，烧 credits
    → 需要确认门（CONFIRM_TOOLS，balance 除外）
  - Houdini 工具（HOUDINI_TOOLS）：在 Houdini 主线程执行，纯本地、免费
"""

# ---- 网络工具（生成类，烧 credits，需确认） ----

_TEXT_TO_3D = {
    "type": "function",
    "function": {
        "name": "meshy_text_to_3d",
        "description": (
            "【生成 3D 模型/网格，不是图片】用 Meshy 由文字生成带 PBR 贴图的 3D 模型（会消耗 credits）。"
            "注意：若用户要的是 2D 概念图/参考图/图片，请改用 meshy_text_to_image，不要用本工具。"
            "内部自动跑完 preview→refine 两阶段并把 glb 与贴图下载到本地缓存。"
            "返回本地 glb 路径与贴图目录——生成成功后应紧接着调用 import_3d_asset 把它导入 Houdini。"
            "count>1 时并行生成多个变体。适合凭空起一个 3D 资产作为程序化管线的种子。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "模型的文字描述，最多 600 字符"},
                "count": {"type": "integer",
                          "description": "并行生成的模型变体数量，默认 1，最多 4"},
                "topology": {"type": "string", "enum": ["quad", "triangle"],
                             "description": "拓扑类型，默认 triangle；需要干净四边面布线选 quad"},
                "target_polycount": {"type": "integer",
                                     "description": "目标面数，100–300000，默认 30000"},
                "enable_pbr": {"type": "boolean",
                               "description": "是否生成 PBR 贴图(金属度/粗糙度/法线)，默认 true"},
            },
            "required": ["prompt"],
        },
    },
}

_IMAGE_TO_3D = {
    "type": "function",
    "function": {
        "name": "meshy_image_to_3d",
        "description": (
            "用 Meshy 由一张图片生成带 PBR 贴图的 3D 模型（会消耗 Meshy credits）。"
            "image 可传：公网图片 URL、base64 data URI（data:image/png;base64,...）、或本地图片路径。"
            "若用户在对话里附了概念图，把它作为 image 传入。"
            "返回本地 glb 路径与贴图目录——成功后应紧接着调用 import_3d_asset 导入 Houdini。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "image": {"type": "string",
                          "description": "图片 URL / base64 data URI / 本地路径"},
                "count": {"type": "integer",
                          "description": "并行生成的模型变体数量，默认 1，最多 4"},
                "topology": {"type": "string", "enum": ["quad", "triangle"],
                             "description": "拓扑类型，默认 triangle"},
                "target_polycount": {"type": "integer",
                                     "description": "目标面数，100–300000，默认 30000"},
                "enable_pbr": {"type": "boolean", "description": "是否生成 PBR 贴图，默认 true"},
            },
            "required": ["image"],
        },
    },
}

_RETEXTURE = {
    "type": "function",
    "function": {
        "name": "meshy_retexture",
        "description": (
            "用 Meshy 给一个已有模型重打 PBR 材质/贴图（会消耗 Meshy credits）。"
            "若要给 Houdini 场景里已有的几何重打材质：先调用 export_node_to_glb 把节点导出成 glb，"
            "再把得到的 glb 本地路径作为 model_path 传入这里。"
            "用 text_prompt 描述目标风格（如 '风化的青铜，带绿锈'），或用 image 传风格参考图。"
            "返回带新贴图的 glb 与贴图目录——成功后调用 import_3d_asset 导入。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "model_path": {"type": "string",
                               "description": "本地模型文件路径(.glb/.gltf/.obj/.fbx/.stl)或公网 URL"},
                "text_prompt": {"type": "string", "description": "目标材质风格描述，最多 600 字符"},
                "image": {"type": "string",
                          "description": "可选：风格参考图(URL/data URI/本地路径)，优先级高于 text_prompt"},
                "enable_pbr": {"type": "boolean", "description": "是否生成 PBR 贴图，默认 true"},
                "enable_original_uv": {"type": "boolean",
                                       "description": "是否沿用原模型 UV，默认 false"},
                "count": {"type": "integer",
                          "description": "并行生成的材质变体数量，默认 1，最多 4"},
            },
            "required": ["model_path"],
        },
    },
}

_REMESH = {
    "type": "function",
    "function": {
        "name": "meshy_remesh",
        "description": (
            "用 Meshy 对一个模型做重拓扑/改面数（会消耗 Meshy credits）。"
            "用于把生成出来的高面三角网格转成干净的四边面布线或指定面数。"
            "传入本地 glb 路径作为 model_path。返回重拓扑后的 glb——成功后调用 import_3d_asset 导入。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "model_path": {"type": "string", "description": "本地模型文件路径或公网 URL"},
                "topology": {"type": "string", "enum": ["quad", "triangle"],
                             "description": "目标拓扑，默认 quad"},
                "target_polycount": {"type": "integer",
                                     "description": "目标面数，100–300000，默认 30000"},
                "count": {"type": "integer",
                          "description": "并行生成的变体数量，默认 1，最多 4"},
            },
            "required": ["model_path"],
        },
    },
}

_TEXT_TO_IMAGE = {
    "type": "function",
    "function": {
        "name": "meshy_text_to_image",
        "description": (
            "【2D 出图工具】用 Meshy 由文字并行生成概念图/参考图（产出的是图片，不是 3D 模型，会消耗 credits）。"
            "只要用户提到'概念图''参考图''图片''画几张''出几张图看看''方案图'等 2D 图像需求，"
            "一律用本工具——不要用 meshy_text_to_3d（那个出的是 3D 模型，不是图）。"
            "会弹出画廊：可多选、可改提示词重新生成、可把选中的图直接升级成 3D，或只保留图片完成。"
            "返回所选图片的本地路径——之后若用户想把某张做成 3D，可调用 meshy_image_to_3d(image=该路径)。"
            "【多方向】若用户想要几个不同方向/风格的概念图，用 prompts 数组——每个元素一张、并行生成、各不相同；"
            "若只是同一描述的多个变体，用 prompt + count。两者二选一。"
            "【纯文字出图用本工具；若用户给了参考图要在其基础上改/生成新图，请改用 meshy_image_to_image】"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "单一描述（配合 count 出同词变体），最多 600 字符"},
                "prompts": {"type": "array", "items": {"type": "string"},
                            "description": "多个不同描述，每个并行出一张（最多 4 个）。要不同方向时优先用它"},
                "count": {"type": "integer", "description": "用 prompt 时并行生成几张变体，默认 2，最多 4"},
                "ai_model": {"type": "string",
                             "enum": ["nano-banana", "nano-banana-2", "nano-banana-pro", "gpt-image-2"],
                             "description": "文生图模型，默认 nano-banana"},
                "aspect_ratio": {"type": "string",
                                 "description": "画幅比例，如 1:1 / 16:9 / 9:16 / 4:3，默认 1:1"},
            },
            "required": [],
        },
    },
}

_IMAGE_TO_IMAGE = {
    "type": "function",
    "function": {
        "name": "meshy_image_to_image",
        "description": (
            "【2D 图生图 / 图片编辑工具】用 Meshy 基于一张或多张参考图 + 文字提示生成一张新图片"
            "（产出图片，不是 3D 模型，会消耗 credits）。"
            "适用场景：用户上传/给出了一张图，说'在这张图基础上改成…''换个风格''把背景换掉'"
            "'生成一张类似但是…的图'等——把用户的图作为 image 传入、把想要的改动写进 prompt 即可。"
            "image 接受：公网 URL / base64 data URI / 本地路径；要多张参考图用 images（1–5 张）。"
            "【重要】若用户是在对话里上传/附带了图片，不要自己编造 image 的 URL 或路径——"
            "直接只传 prompt 调用即可，系统会自动用用户最近附带的图片作为参考图。"
            "只有当你确实知道一个真实存在的本地路径/公网 URL（如之前生成的某张概念图）时，才填 image。"
            "会弹画廊：可多选、改提示词重生、对选中的图再做二次图生图编辑、把选中的升级成 3D，或仅保留图片。"
            "纯文字凭空出图请改用 meshy_text_to_image；要 3D 模型请用 meshy_image_to_3d / meshy_text_to_3d。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "想要的改动/目标描述，最多 600 字符"},
                "image": {"type": "string",
                          "description": "单张参考图：URL / base64 data URI / 本地路径"},
                "images": {"type": "array", "items": {"type": "string"},
                           "description": "多张参考图(1–5)，与 image 二选一"},
                "count": {"type": "integer", "description": "并行生成几张变体，默认 2，最多 4"},
                "ai_model": {"type": "string",
                             "enum": ["nano-banana", "nano-banana-2", "nano-banana-pro", "gpt-image-2"],
                             "description": "图生图模型，默认 nano-banana"},
            },
            "required": ["prompt"],
        },
    },
}

_CONCEPT_TO_3D = {
    "type": "function",
    "function": {
        "name": "meshy_concept_to_3d",
        "description": (
            "概念图驱动的 3D 生成（人在环交互，会消耗 Meshy credits）。"
            "由文字先并行生成若干张概念图，弹出画廊让用户多选满意的，"
            "再对选中的图分别生成 3D 模型（自动串接，免重传）；用户也可改提示词重新生成。"
            "当用户想'先看几个方案再决定做哪个'、或希望对生成结果有更强的美术把控时，优先用这个而不是 meshy_text_to_3d。"
            "返回每个选中方案的本地 glb 路径——成功后应对每个结果调用 import_3d_asset 导入 Houdini。"
            "多方向：用 prompts 数组让每张概念图用不同提示词并行生成；或用 prompt + count 出同词变体。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "单一描述（配合 count 出同词变体），最多 600 字符"},
                "prompts": {"type": "array", "items": {"type": "string"},
                            "description": "多个不同描述，每个并行出一张概念图（最多 4 个）"},
                "count": {"type": "integer",
                          "description": "用 prompt 时并行生成的数量，默认 2，最多 4"},
                "ai_model": {"type": "string",
                             "enum": ["nano-banana", "nano-banana-2", "nano-banana-pro", "gpt-image-2"],
                             "description": "文生图模型，默认 nano-banana"},
                "enable_pbr": {"type": "boolean", "description": "3D 阶段是否生成 PBR 贴图，默认 true"},
                "topology": {"type": "string", "enum": ["quad", "triangle"],
                             "description": "3D 阶段拓扑，默认 triangle"},
                "target_polycount": {"type": "integer",
                                     "description": "3D 阶段目标面数，默认 30000"},
            },
            "required": [],
        },
    },
}

_RIG = {
    "type": "function",
    "function": {
        "name": "meshy_rig",
        "description": (
            "用 Meshy 给一个【人形角色】自动绑定骨骼/蒙皮（会消耗约 5 credits）。"
            "硬性约束（不满足极可能失败、白烧 credits，调用前务必先跟用户确认角色满足）："
            "①必须是双足人形且四肢/身体结构清晰；②必须带贴图（无贴图模型不支持）；"
            "③朝向必须脸朝 +Z（glTF 正向）；④面数 ≤300000（超了先用 meshy_remesh 降面）。"
            "来源二选一：source_task_id=之前某个 Meshy 生成任务的 task_id（最稳，免重传）；"
            "或 model_path=本地 glb 路径/公网 URL。"
            "若要绑定 Houdini 场景里已有的几何，请先调用 export_node_to_glb 导出再把路径传给 model_path"
            "（但仍需自检是否人形/带贴图/朝向）。"
            "成功返回 rig_task_id（用于后续 meshy_animate 套动作）和绑定好的 FBX 本地路径"
            "（自带 walk/run 基础动画）——成功后应调用 import_rigged_character 导入 Houdini。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "source_task_id": {"type": "string",
                                   "description": "Meshy 已生成模型的 task_id（与 model_path 二选一，优先用它）"},
                "model_path": {"type": "string",
                               "description": "本地模型 glb 路径或公网 URL（与 source_task_id 二选一）"},
                "height_meters": {"type": "number",
                                  "description": "角色身高（米），用于缩放估计，默认 1.7"},
            },
            "required": [],
        },
    },
}

_SEARCH_ANIM = {
    "type": "function",
    "function": {
        "name": "meshy_search_animations",
        "description": (
            "【免费、不消耗 credits、不联网】在 Meshy 预设动作库（约 600 个动作，分 5 类："
            "DailyActions/WalkAndRun/Fighting/Dancing/BodyMovements）里按自然语言检索候选动作。"
            "这是套动画的第一步：先用本工具把用户的意图（如'走路、挥手、跳一段舞、拔剑攻击'）"
            "匹配成若干候选动作，把结果（名称+编号+类别）列给用户看、让用户确认/挑选要哪些，"
            "再调用 meshy_animate 真正生成。支持中英文查询。"
            "返回 [{id, name, category}]——id 就是 meshy_animate 要用的 action_id。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "动作意图，中英文均可（如'跳舞''attack''坐下'）；留空则返回一组常用基础动作"},
                "limit": {"type": "integer", "description": "返回候选数量，默认 12，最多 40"},
            },
            "required": [],
        },
    },
}

_ANIMATE = {
    "type": "function",
    "function": {
        "name": "meshy_animate",
        "description": (
            "用 Meshy 给一个【已绑定的角色】套上一个或多个预设动作（每个动作约消耗 3 credits）。"
            "前置：必须先有 meshy_rig 返回的 rig_task_id；动作编号建议先用 meshy_search_animations "
            "检索并经用户确认后再传入。"
            "actions 传 action_id 整数数组（也接受动作名字符串）；会逐个并行生成，每个动作产出一个带动画的 FBX。"
            "返回每个动作对应的本地 FBX 路径——成功后应对每个结果调用 import_rigged_character 导入 Houdini。"
            "注意：动作数量直接决定花费（N 个动作≈3N credits），生成前请把动作清单和预计花费告诉用户。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "rig_task_id": {"type": "string",
                                "description": "绑定任务返回的 rig_task_id（必填）"},
                "actions": {"type": "array",
                            "items": {"type": ["integer", "string"]},
                            "description": "要生成的动作列表：action_id 整数（0–696）或动作名；一次最多 10 个"},
            },
            "required": ["rig_task_id", "actions"],
        },
    },
}

_BALANCE = {
    "type": "function",
    "function": {
        "name": "meshy_balance",
        "description": "查询当前 Meshy 账户剩余 credits（免费、不消耗）。在生成前想确认额度时调用。",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

_TASK_STATUS = {
    "type": "function",
    "function": {
        "name": "meshy_task_status",
        "description": (
            "查询【转入后台】运行中的 Meshy 生成任务的进度（免费、不消耗 credits）。"
            "当你之前发起的某个生成任务被用户转入后台、你想确认它好了没有时调用。"
            "不带参数=列出全部后台任务；用 op 指定某个任务号。"
            "注意：后台任务完成后，其结果会【自动作为一条新消息发给你】，通常无需主动轮询——"
            "除非用户追问进度，否则不必反复调用本工具。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "op": {"type": "string", "description": "可选：任务号（发起时返回的 op）"},
            },
            "required": [],
        },
    },
}

# ---- Houdini 工具（本地、免费、主线程） ----

_IMPORT_ASSET = {
    "type": "function",
    "function": {
        "name": "import_3d_asset",
        "description": (
            "把本地 3D 资产文件(.glb/.gltf/.fbx/.obj)导入 Houdini：在 /obj 下新建一个 geo，"
            "用 File SOP 读入几何；若提供了贴图目录，则自动搭建 Principled Shader "
            "(basecolor/normal/roughness/metallic)并指派到几何。"
            "通常紧跟在某个 Meshy 生成工具之后调用，用其返回的 glb_path 与 texture_dir。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "glb_path": {"type": "string", "description": "本地模型文件路径"},
                "texture_dir": {"type": "string",
                                "description": "可选：贴图目录(含 basecolor/normal/roughness/metallic)；提供则建材质"},
                "name": {"type": "string", "description": "可选：新建 geo 节点名"},
                "build_material": {"type": "boolean",
                                   "description": "是否搭建 Principled Shader，默认 true"},
            },
            "required": ["glb_path"],
        },
    },
}

_IMPORT_RIGGED = {
    "type": "function",
    "function": {
        "name": "import_rigged_character",
        "description": (
            "把 Meshy 绑定/动画产出的【带骨架+蒙皮(+动画)的 FBX】导入 Houdini："
            "在 /obj 下用 FBX 导入建出一个 subnet，含骨骼(bone 层级)、蒙皮几何(Capture/Deform)"
            "与关键帧动画——可直接在视口播放。通常紧跟 meshy_rig / meshy_animate 之后调用，"
            "用其返回的 fbx_path。每个动作 clip 各调用一次（会各建一个独立 subnet）。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "fbx_path": {"type": "string", "description": "本地 FBX 文件路径（绑定或动画产出）"},
                "name": {"type": "string", "description": "可选：新建 subnet 的名字（如动作名）"},
            },
            "required": ["fbx_path"],
        },
    },
}

_EXPORT_GLB = {
    "type": "function",
    "function": {
        "name": "export_node_to_glb",
        "description": (
            "把 Houdini 里某个 SOP/OBJ 节点的几何导出成 glb 到本地缓存(免费、本地)。"
            "主要用于把场景里已有的几何喂给 meshy_retexture 重打材质。返回导出的 glb 本地路径。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "node_path": {"type": "string",
                              "description": "要导出的节点绝对路径(如 /obj/geo1/OUT 或 /obj/geo1)"},
            },
            "required": ["node_path"],
        },
    },
}

# ---- 汇总 ----

MESHY_TOOLS = [_TEXT_TO_3D, _IMAGE_TO_3D, _TEXT_TO_IMAGE, _IMAGE_TO_IMAGE,
               _CONCEPT_TO_3D, _RETEXTURE, _REMESH, _RIG, _SEARCH_ANIM, _ANIMATE,
               _BALANCE, _TASK_STATUS, _IMPORT_ASSET, _IMPORT_RIGGED, _EXPORT_GLB]

# 在 app 侧后台线程执行（调用 Meshy API，绝不进 Houdini/bridge）
NETWORK_TOOLS = frozenset({
    "meshy_text_to_3d", "meshy_image_to_3d", "meshy_text_to_image",
    "meshy_image_to_image", "meshy_concept_to_3d", "meshy_retexture",
    "meshy_remesh", "meshy_rig", "meshy_animate", "meshy_balance",
})

# 纯本地、免费、瞬时返回（不联网、不烧 credits、不弹进度卡）。controller 直接内联处理。
LOCAL_TOOLS = frozenset({"meshy_search_animations"})

# 人在环交互工具（controller 特殊编排，带画廊/多选/重生/二次编辑）
INTERACTIVE_TOOLS = frozenset({
    "meshy_concept_to_3d", "meshy_text_to_image", "meshy_image_to_image",
})

# 在 Houdini 主线程执行（hou.*）
HOUDINI_TOOLS = frozenset({"import_3d_asset", "import_rigged_character", "export_node_to_glb"})

# 会消耗 credits → 需要确认门（balance/search 免费，不在内）
CONFIRM_TOOLS = frozenset({
    "meshy_text_to_3d", "meshy_image_to_3d", "meshy_retexture", "meshy_remesh",
    "meshy_rig", "meshy_animate",
})

# 会修改场景（导入会建节点）→ 需 undo 分组 + 节点变更快照
MUTATING_TOOLS = frozenset({"import_3d_asset", "import_rigged_character"})

ALL_TOOL_NAMES = frozenset(
    t["function"]["name"] for t in MESHY_TOOLS)
