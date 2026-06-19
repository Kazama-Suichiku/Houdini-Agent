# -*- coding: utf-8 -*-
"""
Meshy 网络工具的执行编排（app 侧后台线程；无 Qt / 无 hou 依赖）。

每个 run 返回统一的工具结果 dict：
  {"success": bool, "result": "<给模型看的文字摘要>", "error": "", "data": {...}}
data 里含本地 glb 路径(glb)、贴图目录(texture_dir)、贴图字典(textures)、缩略图、消耗 credits。

进度通过 on_progress(stage:str, progress:int, status:str) 回调上报（可选）。
"""

import base64
import concurrent.futures
import os
import threading
import time

from . import config
from .client import MeshyClient, MeshyError


_TEX_KEYS = ("base_color", "normal", "roughness", "metallic", "emission")

_PROMPT_MAX = 600          # Meshy 提示词长度上限
_POLY_MIN, _POLY_MAX = 100, 300000
_VALID_TOPOLOGY = ("triangle", "quad")


def _clip_prompt(p):
    """规整提示词：strip + 截断到 600 字符（超长 Meshy 会直接拒绝）。"""
    p = (p or "").strip()
    return p[:_PROMPT_MAX] if len(p) > _PROMPT_MAX else p


def _clamp_poly(v, default=30000):
    """把 target_polycount 钳到 [100, 300000]；非数字回退默认。"""
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return max(_POLY_MIN, min(n, _POLY_MAX))


def _norm_topology(v, default="triangle"):
    v = str(v or "").strip().lower()
    return v if v in _VALID_TOPOLOGY else default


def _safe_call(fn, *a):
    """调用可选回调，吞掉其异常——回调（多为 UI）出错绝不能拖垮已成功的生成任务。"""
    if not fn:
        return
    try:
        fn(*a)
    except Exception:
        pass


def _is_url(s):
    return isinstance(s, str) and s.lower().startswith(("http://", "https://"))


def _is_data_uri(s):
    return isinstance(s, str) and s.startswith("data:")


def _image_to_arg(image):
    """把 image 参数规整成 Meshy 可接受的形式（URL / data URI）。"""
    if _is_url(image) or _is_data_uri(image):
        return image
    if os.path.isfile(image):
        ext = os.path.splitext(image)[1].lower().lstrip(".") or "png"
        mime = "jpeg" if ext in ("jpg", "jpeg") else ext
        with open(image, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        return "data:image/%s;base64,%s" % (mime, b64)
    raise MeshyError("无法识别的 image 参数（既不是 URL/data URI，也不是存在的本地文件）: %s" % image)


def _model_to_arg(model_path):
    """把本地模型文件规整成 base64 data URI；URL/data URI 原样返回。"""
    if _is_url(model_path) or _is_data_uri(model_path):
        return model_path
    if os.path.isfile(model_path):
        ext = os.path.splitext(model_path)[1].lower().lstrip(".")
        mime = {"glb": "model/gltf-binary", "gltf": "model/gltf+json",
                "obj": "model/obj", "fbx": "model/fbx",
                "stl": "model/stl"}.get(ext, "application/octet-stream")
        with open(model_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        return "data:%s;base64,%s" % (mime, b64)
    raise MeshyError("model_path 不是有效的本地文件或 URL: %s" % model_path)


def _download_assets(client, task, task_id):
    """下载 glb + 各 PBR 贴图到 cache/meshy/<task_id>/，返回 data 字典。"""
    dest = config.assets_dir(task_id)
    out = {"task_id": task_id, "asset_dir": dest, "glb": None,
           "texture_dir": None, "textures": {}, "thumbnail": None,
           "consumed_credits": task.get("consumed_credits")}

    model_urls = task.get("model_urls") or {}
    glb_url = model_urls.get("glb")
    if glb_url:
        try:
            out["glb"] = client.download(glb_url, os.path.join(dest, "model.glb"))
        except Exception as e:
            # 任务已 SUCCEEDED（credits 已消耗），但本地下载失败。明确告知用户，
            # 而不是笼统报"生成失败"——结果其实在云端，可稍后从资产库重新拉取。
            raise MeshyError(
                "模型已在云端生成成功（credits 已消耗，任务号 %s），但下载到本地失败：%s。"
                "可稍后在资产库中重新拉取该资产。" % (task_id, e))

    tex_list = task.get("texture_urls") or []
    if tex_list:
        tex = tex_list[0] or {}
        if len(tex_list) > 1:
            out["extra_texture_sets"] = len(tex_list) - 1   # 仅下首套；记录还有几套备查
        tex_dir = os.path.join(dest, "textures")
        os.makedirs(tex_dir, exist_ok=True)
        for key in _TEX_KEYS:
            url = tex.get(key)
            if url:
                ext = os.path.splitext(url.split("?")[0])[1] or ".png"
                try:
                    path = client.download(url, os.path.join(tex_dir, key + ext))
                    out["textures"][key] = path
                except Exception as e:
                    # 贴图下载失败不致命（几何已拿到）：跳过该贴图，记录到 data 供上层提示。
                    out.setdefault("texture_errors", []).append("%s: %s" % (key, e))
        if out["textures"]:
            out["texture_dir"] = tex_dir

    thumb = task.get("thumbnail_url")
    if thumb:
        try:
            out["thumbnail"] = client.download(thumb, os.path.join(dest, "thumb.png"))
        except Exception:
            pass
    return out


def _summary(verb, data):
    glb = data.get("glb")
    tex = data.get("texture_dir")
    credits = data.get("consumed_credits")
    lines = ["%s完成。" % verb]
    if glb:
        lines.append("本地 glb: %s" % glb)
    if tex:
        lines.append("贴图目录: %s" % tex)
    if data.get("texture_errors"):
        lines.append("注意：部分贴图下载失败（%s），材质可能不完整。"
                     % "; ".join(data["texture_errors"]))
    if data.get("note"):
        lines.append(data["note"])
    if credits is not None:
        lines.append("消耗 credits: %s" % credits)
    if glb:
        lines.append("下一步：调用 import_3d_asset(glb_path=\"%s\"%s) 导入 Houdini。"
                     % (glb, (", texture_dir=\"%s\"" % tex) if tex else ""))
    else:
        # 没有 glb 就别给空路径的导入指令，避免模型拿空字符串去调 import_3d_asset。
        lines.append("注意：本次未获得可导入的 glb 文件，暂时无法 import_3d_asset。")
    return "\n".join(lines)


def _ok(result_text, data):
    return {"success": True, "result": result_text, "error": "", "data": data}


def _err(msg, data=None):
    # 统一契约：失败也带 result/data 键，调用方无须分支判空（避免 KeyError）。
    return {"success": False, "result": "", "error": str(msg), "data": data or {}}


# ---------------- 各工具 ----------------

# 单次"产出一个资产"的核心（返回 data，失败抛 MeshyError）——可被单任务与并行复用。

def _produce_text_to_3d(client, kwargs, should_stop, on_progress=None):
    prompt = _clip_prompt(kwargs.get("prompt"))
    if not prompt:
        raise MeshyError("缺少 prompt")
    topo = _norm_topology(kwargs.get("topology"), "triangle")
    poly = _clamp_poly(kwargs.get("target_polycount"))
    enable_pbr = bool(kwargs.get("enable_pbr", True))
    preview_id = client.create_text_to_3d_preview(prompt, topology=topo,
                                                  target_polycount=poly)
    client.wait("text-to-3d", preview_id,
                on_progress=lambda p, s: on_progress and on_progress("生成几何", p, s),
                should_stop=should_stop)
    refine_id = client.create_text_to_3d_refine(preview_id, enable_pbr=enable_pbr)
    task = client.wait("text-to-3d", refine_id,
                       on_progress=lambda p, s: on_progress and on_progress("生成贴图", p, s),
                       should_stop=should_stop)
    return _download_assets(client, task, refine_id)


def _produce_image_to_3d(client, kwargs, should_stop, on_progress=None):
    image = kwargs.get("image")
    if not image:
        raise MeshyError("缺少 image")
    image_arg = _image_to_arg(image)
    topo = _norm_topology(kwargs.get("topology"), "triangle")
    poly = _clamp_poly(kwargs.get("target_polycount"))
    enable_pbr = bool(kwargs.get("enable_pbr", True))
    task_id = client.create_image_to_3d(image_arg, enable_pbr=enable_pbr,
                                        topology=topo, target_polycount=poly)
    task = client.wait("image-to-3d", task_id,
                       on_progress=lambda p, s: on_progress and on_progress("图生3D", p, s),
                       should_stop=should_stop)
    return _download_assets(client, task, task_id)


def _produce_retexture(client, kwargs, should_stop, on_progress=None):
    model_path = kwargs.get("model_path")
    if not model_path:
        raise MeshyError("缺少 model_path")
    text_prompt = _clip_prompt(kwargs.get("text_prompt")) or None
    image = kwargs.get("image")
    if not text_prompt and not image:
        raise MeshyError("retexture 需要 text_prompt 或 image 之一")
    model_arg = _model_to_arg(model_path)
    image_arg = _image_to_arg(image) if image else None
    task_id = client.create_retexture(
        model_arg, text_style_prompt=text_prompt, image_style_url=image_arg,
        enable_pbr=bool(kwargs.get("enable_pbr", True)),
        enable_original_uv=bool(kwargs.get("enable_original_uv", False)))
    task = client.wait("retexture", task_id,
                       on_progress=lambda p, s: on_progress and on_progress("重打材质", p, s),
                       should_stop=should_stop)
    data = _download_assets(client, task, task_id)
    if text_prompt and image:
        # 官方约定 image_style_url 优先，text_prompt 被忽略——明确告知，避免用户误以为两者都生效。
        data["note"] = "（同时提供了 image 和 text_prompt：本次以参考图为准，text_prompt 已忽略）"
    return data


def _produce_remesh(client, kwargs, should_stop, on_progress=None):
    model_path = kwargs.get("model_path")
    if not model_path:
        raise MeshyError("缺少 model_path")
    model_arg = _model_to_arg(model_path)
    task_id = client.create_remesh(
        model_url=model_arg, topology=_norm_topology(kwargs.get("topology"), "quad"),
        target_polycount=_clamp_poly(kwargs.get("target_polycount")))
    task = client.wait("remesh", task_id,
                       on_progress=lambda p, s: on_progress and on_progress("重拓扑", p, s),
                       should_stop=should_stop)
    return _download_assets(client, task, task_id)


# ---------------- 绑定 / 动画 ----------------

def _download_rig_assets(client, task, task_id):
    """下载绑定产物到 cache/meshy/<task_id>/：rigged FBX(优先) + GLB + 自带 walk/run。
    绑定/动画接口返回的是 result.*_url 结构（不同于生成类的 model_urls）。"""
    dest = config.assets_dir(task_id)
    res = task.get("result") or {}
    out = {"task_id": task_id, "rig_task_id": task_id, "asset_dir": dest,
           "fbx": None, "glb": None, "basic_animations": {},
           "consumed_credits": task.get("consumed_credits")}
    fbx_url = res.get("rigged_character_fbx_url")
    glb_url = res.get("rigged_character_glb_url")
    if fbx_url:
        out["fbx"] = client.download(fbx_url, os.path.join(dest, "rigged.fbx"))
    if glb_url:
        out["glb"] = client.download(glb_url, os.path.join(dest, "rigged.glb"))
    ba = res.get("basic_animations") or {}
    for key, fname in (("walking_fbx_url", "walking.fbx"),
                       ("running_fbx_url", "running.fbx")):
        u = ba.get(key)
        if u:
            try:
                out["basic_animations"][key[:-4]] = client.download(
                    u, os.path.join(dest, fname))
            except Exception:
                pass
    return out


def _download_anim_assets(client, task, task_id, label=""):
    """下载单个动作 clip 的 FBX(优先) + GLB。"""
    dest = config.assets_dir(task_id)
    res = task.get("result") or {}
    out = {"task_id": task_id, "label": label, "fbx": None, "glb": None,
           "consumed_credits": task.get("consumed_credits")}
    fbx_url = res.get("animation_fbx_url")
    glb_url = res.get("animation_glb_url")
    if fbx_url:
        out["fbx"] = client.download(fbx_url, os.path.join(dest, "anim.fbx"))
    if glb_url:
        out["glb"] = client.download(glb_url, os.path.join(dest, "anim.glb"))
    return out


def _produce_rig(client, kwargs, should_stop, on_progress=None):
    src = (kwargs.get("source_task_id") or "").strip()
    model_path = kwargs.get("model_path")
    height = kwargs.get("height_meters", 1.7)
    if src:
        rig_id = client.create_rigging(input_task_id=src, height_meters=height)
    elif model_path:
        rig_id = client.create_rigging(model_url=_model_to_arg(model_path),
                                       height_meters=height)
    else:
        raise MeshyError("meshy_rig 需要 source_task_id 或 model_path 之一")
    task = client.wait("rigging", rig_id, timeout=900,
                       on_progress=lambda p, s: on_progress and on_progress("自动绑定", p, s),
                       should_stop=should_stop)
    return _download_rig_assets(client, task, rig_id)


def _run_rig(client, kwargs, on_progress, should_stop):
    data = _produce_rig(client, kwargs, should_stop, on_progress=on_progress)
    rid = data.get("rig_task_id")
    lines = ["自动绑定完成。", "rig_task_id: %s" % rid]
    if data.get("fbx"):
        lines.append("绑定 FBX: %s" % data["fbx"])
    ba = data.get("basic_animations") or {}
    if ba:
        lines.append("自带基础动画: %s" % "、".join(ba.values()))
    if data.get("consumed_credits") is not None:
        lines.append("消耗 credits: %s" % data["consumed_credits"])
    if data.get("fbx"):
        lines.append("下一步：调用 import_rigged_character(fbx_path=\"%s\") 导入 Houdini。" % data["fbx"])
    lines.append("若要套更多动作：先用 meshy_search_animations 选动作并经用户确认，"
                 "再调用 meshy_animate(rig_task_id=\"%s\", actions=[...])。" % rid)
    return _ok("\n".join(lines), data)


def run_animations(rig_task_id, actions, on_progress=None, should_stop=None):
    """对一个已绑定角色并行套多个动作。actions=动作 id/名字列表。
    返回 (clips, unknown, errors, dropped)；clips 每个含 fbx/action_id/action_name；
    dropped 是因单次上限（10 个）被截掉的动作；全失败抛 MeshyError。"""
    from . import animation_lib
    resolved, unknown = animation_lib.resolve(actions)
    if not resolved:
        raise MeshyError("没有可识别的动作（无法解析：%s）" % (unknown or actions))
    dropped = []
    if len(resolved) > 10:
        # 官方上限：一次最多 10 个 clip。超出的明确回报给调用方，不静默丢弃。
        dropped = [a.get("name") or a.get("id") for a in resolved[10:]]
        resolved = resolved[:10]
    client = MeshyClient()
    total = len(resolved)
    results = [None] * total
    prog = [0] * total
    errors = []
    lock = threading.Lock()

    def report():
        if on_progress:
            with lock:
                avg = sum(prog) // total
                done = sum(1 for p in prog if p >= 100)
            _safe_call(on_progress, "套动作", avg, "%d/%d" % (done, total))

    def one(k, a):
        def wp(p, s):
            with lock:
                prog[k] = p
            report()
        tid = client.create_animation(rig_task_id, a["id"])
        print("[meshy] animation submitted: %s (action %s/%s)"
              % (tid, a["id"], a["name"]))
        task = client.wait("animation", tid, on_progress=wp,
                           should_stop=should_stop, timeout=900)
        data = _download_anim_assets(client, task, tid, label=a["name"])
        data["action_id"] = a["id"]
        data["action_name"] = a["name"]
        with lock:
            prog[k] = 100
        report()
        return data

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(total, MAX_PARALLEL)) as ex:
        futs = {ex.submit(one, k, a): k for k, a in enumerate(resolved)}
        for f in concurrent.futures.as_completed(futs):
            k = futs[f]
            try:
                results[k] = f.result()
            except Exception as e:
                errors.append(str(e))
                print("[meshy] animate failed:", e)
    out = [r for r in results if r]
    if not out:
        raise MeshyError("动作生成全部失败：" + (errors[0] if errors else "未知原因"))
    return out, unknown, errors, dropped


def _run_animate(client, kwargs, on_progress, should_stop):
    rig_task_id = (kwargs.get("rig_task_id") or "").strip()
    if not rig_task_id:
        return _err("meshy_animate 需要 rig_task_id（先调用 meshy_rig 取得）")
    actions = kwargs.get("actions") or []
    if not actions:
        return _err("meshy_animate 需要 actions（动作 id 或名字的列表）")
    clips, unknown, errors, dropped = run_animations(rig_task_id, actions,
                                            on_progress=on_progress, should_stop=should_stop)
    lines = ["套动作完成，共 %d 个：" % len(clips)]
    for c in clips:
        lines.append("- %s (action_id %s): %s"
                     % (c.get("action_name"), c.get("action_id"), c.get("fbx") or "(无 FBX)"))
    if unknown:
        lines.append("未能识别、已跳过：%s" % unknown)
    if dropped:
        # 单次最多 10 个动作；超出的没生成，明确告知用户而非静默丢弃。
        lines.append("注意：一次最多套 10 个动作，以下未处理（如需可分批再发）：%s" % dropped)
    if errors:
        # 部分动作生成失败（可能已消耗 credits）——明确告知，别让失败静默消失。
        lines.append("注意：有 %d 个动作生成失败（可能已消耗 credits）：%s"
                     % (len(errors), "; ".join(str(e)[:80] for e in errors)))
    total_credits = sum(int(c.get("consumed_credits") or 0) for c in clips)
    if total_credits:
        lines.append("合计消耗 credits: %d" % total_credits)
    lines.append("下一步：对每个 FBX 分别调用 import_rigged_character(fbx_path=...) 导入 Houdini。")
    return _ok("\n".join(lines), {"clips": clips, "unknown": unknown})


def search_animations(kwargs):
    """本地检索动作库（免费、不联网、瞬时）。返回统一工具结果。"""
    from . import animation_lib
    q = (kwargs or {}).get("query", "")
    try:
        limit = int((kwargs or {}).get("limit", 5) or 5)
    except (ValueError, TypeError):
        limit = 5
    limit = max(1, min(limit, 40))
    items = animation_lib.search(q, limit=limit)
    if not items:
        return {"success": True, "error": "",
                "result": "没匹配到动作，换个关键词试试（中英文均可）。",
                "data": {"actions": []}}
    lines = ["匹配到 %d 个候选动作（[]内为 action_id，供 meshy_animate 使用）：" % len(items)]
    for a in items:
        lines.append("- [%d] %s · %s" % (a["id"], a["name"], a.get("category", "")))
    lines.append("请你从中挑 3–5 个最贴合当前任务的：意图明确就直接定下来进入 meshy_animate；"
                 "若需用户拍板，再把这几个简短列给用户选。每个动作约 3 credits。")
    return {"success": True, "error": "", "result": "\n".join(lines),
            "data": {"actions": items}}


# 产出器（支持并行的 4 个独立任务）；标签用于摘要文案
_PRODUCERS = {
    "meshy_text_to_3d": (_produce_text_to_3d, "文生3D"),
    "meshy_image_to_3d": (_produce_image_to_3d, "图生3D"),
    "meshy_retexture": (_produce_retexture, "重打材质"),
    "meshy_remesh": (_produce_remesh, "重拓扑"),
}


def _run_single(tool_name, client, kwargs, on_progress, should_stop):
    producer, label = _PRODUCERS[tool_name]
    data = producer(client, kwargs, should_stop, on_progress=on_progress)
    return _ok(_summary(label, data), data)


def _run_balance(client, kwargs, on_progress, should_stop):
    bal = client.balance()
    # balance() 解析失败返回 -1（与真实 0 余额区分）。别把 -1 当数字播报给模型，
    # 否则它会告诉用户"余额 -1"。
    if bal is None or bal < 0:
        return _ok("Meshy 余额暂时无法获取（可稍后重试）。", {"balance": -1})
    return _ok("Meshy 剩余 credits: %d" % bal, {"balance": bal})


def _run_dispatch(tool_name, client, kwargs, on_progress, should_stop):
    if tool_name == "meshy_balance":
        return _run_balance(client, kwargs, on_progress, should_stop)
    if tool_name == "meshy_rig":
        return _run_rig(client, kwargs, on_progress, should_stop)
    if tool_name == "meshy_animate":
        return _run_animate(client, kwargs, on_progress, should_stop)
    if tool_name in _PRODUCERS:
        return _run_single(tool_name, client, kwargs, on_progress, should_stop)
    if tool_name in ("meshy_text_to_image", "meshy_image_to_image", "meshy_concept_to_3d"):
        # 这些是人在环交互工具（带画廊/多选），必须经 UI 控制器编排，不能走无 UI 的 run_network。
        return _err("%s 是交互式工具（需要画廊 UI），不能通过 run_network 直接执行；"
                    "请在应用内通过控制器调用。" % tool_name)
    return _err("未知的 Meshy 网络工具: %s" % tool_name)


# 支持并行 count>1 的工具（独立 endpoint，可并发提交）
BATCH_TOOLS = frozenset(_PRODUCERS.keys())
MAX_PARALLEL = 4


def run_network_parallel(tool_name, kwargs, count, on_item=None,
                         on_progress=None, should_stop=None, errors_out=None):
    """并行运行 count 个同类生成任务（同参数不同种子）。
    on_item(index, data) 每个完成时回调；返回 [data]，全失败抛 MeshyError。
    errors_out（可选 list）：把部分失败的错误信息回填给调用方，便于向用户汇报。"""
    if tool_name not in _PRODUCERS:
        raise MeshyError("该工具不支持并行: %s" % tool_name)
    producer = _PRODUCERS[tool_name][0]
    client = MeshyClient()
    count = max(1, min(int(count or 1), MAX_PARALLEL))
    results = [None] * count
    completed = [0]
    errors = []
    lock = threading.Lock()

    def one(i):
        data = producer(client, kwargs, should_stop)
        with lock:
            completed[0] += 1
            _safe_call(on_item, i, data)
            _safe_call(on_progress, completed[0] * 100 // count, completed[0], count)
        return data

    with concurrent.futures.ThreadPoolExecutor(max_workers=count) as ex:
        futs = {ex.submit(one, i): i for i in range(count)}
        for f in concurrent.futures.as_completed(futs):
            i = futs[f]
            try:
                results[i] = f.result()
            except Exception as e:
                errors.append(str(e))
                print("[meshy] parallel %s failed: %s" % (tool_name, e))
    out = [r for r in results if r]
    if errors_out is not None:
        errors_out.extend(errors)
    if not out:
        raise MeshyError("并行生成全部失败：" + (errors[0] if errors else "可能是 key/额度/网络问题"))
    return out


# ---------------- 概念图（文生图）并行能力 ----------------

def _download_image(client, task, task_id):
    dest = config.assets_dir(task_id)
    urls = task.get("image_urls") or []
    if not urls:
        raise MeshyError("text-to-image 未返回 image_urls")
    url = urls[0]
    ext = os.path.splitext(url.split("?")[0])[1] or ".png"
    return client.download(url, os.path.join(dest, "concept" + ext))


def generate_concepts(prompts, ai_model="nano-banana", aspect_ratio="1:1",
                      reference_images=None, per_prompt_refs=None,
                      on_progress=None, on_image=None, should_stop=None):
    """并行生成概念图。prompts = 每张图一个提示词的列表
    （元素可全相同=同一提示词的多个变体，也可各不相同=不同设计方向）。
    reference_images：共享参考图（所有输出都基于这组图，走【图生图】）。
    per_prompt_refs：与 prompts 一一对应的参考图列表——第 i 张输出只用 per_prompt_refs[i]
      作为参考（用于"对每张选中的图分别做二次编辑"）。优先级高于 reference_images。
    任一参考图非空 → 该输出走图生图端点；否则走文生图。
    on_image(concept) 每张完成时回调（UI 增量填槽，体现并行）。
    返回 [{index, task_id, image, prompt}]；全失败抛 MeshyError。"""
    client = MeshyClient()
    prompts = [str(p) for p in (prompts or []) if str(p).strip()][:MAX_PARALLEL]
    if not prompts:
        raise MeshyError("没有有效的提示词")
    shared_args = None
    if reference_images:
        shared_args = [_image_to_arg(r) for r in reference_images if r][:5] or None
    per_args = None
    if per_prompt_refs:
        per_args = []
        for lst in per_prompt_refs:
            a = [_image_to_arg(r) for r in (lst or []) if r][:5]
            per_args.append(a or None)
    total = len(prompts)
    results = [None] * total
    completed = [0]
    errors = []
    lock = threading.Lock()

    def one(i):
        refs_i = per_args[i] if (per_args is not None and i < len(per_args)) else None
        if refs_i is None:
            refs_i = shared_args
        if refs_i:
            tid = client.create_image_to_image(prompts[i], refs_i, ai_model=ai_model)
            kind = "image-to-image"
        else:
            tid = client.create_text_to_image(prompts[i], ai_model=ai_model,
                                              aspect_ratio=aspect_ratio)
            kind = "text-to-image"
        print("[meshy] %s submitted: %s (#%d)" % (kind, tid, i))
        task = client.wait(kind, tid, should_stop=should_stop)
        path = _download_image(client, task, tid)
        res = {"index": i, "task_id": tid, "image": path, "prompt": prompts[i]}
        with lock:
            completed[0] += 1
            _safe_call(on_image, res)
            _safe_call(on_progress, "生成概念图", completed[0] * 100 // total,
                       "%d/%d" % (completed[0], total))
        return res

    with concurrent.futures.ThreadPoolExecutor(max_workers=total) as ex:
        futs = {ex.submit(one, i): i for i in range(total)}
        for f in concurrent.futures.as_completed(futs):
            i = futs[f]
            try:
                results[i] = f.result()
            except Exception as e:
                errors.append(str(e))
                print("[meshy] text-to-image failed:", e)
    out = [r for r in results if r]
    if not out:
        raise MeshyError("概念图生成失败：" + (errors[0] if errors else "未知原因"))
    return out


def concepts_to_3d(concepts, enable_pbr=True, topology="triangle",
                   target_polycount=30000, on_progress=None, should_stop=None,
                   errors_out=None):
    """对选中的概念图并行做 image-to-3d（每张 3D 都会消耗 credits）。
    返回 [data]，每个含 glb/texture_dir/concept_index；全失败抛 MeshyError。
    errors_out（可选 list）：回填部分失败信息。"""
    concepts = list(concepts or [])
    if len(concepts) > MAX_PARALLEL:
        # 上限保护：每张概念图→3D 都计费，绝不能因异常输入提交几十上百个任务。
        concepts = concepts[:MAX_PARALLEL]
    topology = _norm_topology(topology, "triangle")
    target_polycount = _clamp_poly(target_polycount)
    client = MeshyClient()
    total = max(1, len(concepts))
    results = [None] * total
    completed = [0]
    lock = threading.Lock()

    prog = [0] * total
    errors = []

    def report():
        if on_progress:
            with lock:
                avg = sum(prog) // total
                done = sum(1 for p in prog if p >= 100)
            _safe_call(on_progress, "生成 3D", avg, "%d/%d" % (done, total))

    def one(k, c):
        def wp(p, s):
            with lock:
                prog[k] = p
            report()
        # 用已下载的概念图(base64)做 image-to-3d —— 比 input_task_id 跨产品串接更稳
        img = c.get("image")
        image_arg = None
        if img:
            try:
                image_arg = _image_to_arg(img)
            except Exception as ie:
                # 本地概念图丢失/不可读：不直接判这张失败，回退用云端 task_id 串接；
                # 两者都没有才放弃。
                if not c.get("task_id"):
                    raise
                print("[meshy] 概念图本地文件不可用，回退到 input_task_id：%s" % ie)
        tid = client.create_image_to_3d(
            image=image_arg,
            input_task_id=(None if image_arg else c.get("task_id")),
            enable_pbr=enable_pbr, topology=topology,
            target_polycount=target_polycount)
        print("[meshy] image-to-3d submitted: %s (concept %s)" % (tid, c.get("index")))
        task = client.wait("image-to-3d", tid, on_progress=wp, should_stop=should_stop)
        data = _download_assets(client, task, tid)
        data["concept_index"] = c.get("index")
        with lock:
            prog[k] = 100
        report()
        return data

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(total, MAX_PARALLEL)) as ex:
        futs = {ex.submit(one, k, c): k for k, c in enumerate(concepts)}
        for f in concurrent.futures.as_completed(futs):
            k = futs[f]
            try:
                results[k] = f.result()
            except Exception as e:
                errors.append(str(e))
                print("[meshy] concept->3d failed:", e)
    out = [r for r in results if r]
    if errors_out is not None:
        errors_out.extend(errors)
    if not out:
        raise MeshyError("选中概念图的 3D 生成全部失败：" + (errors[0] if errors else "未知原因"))
    return out


# ---------------- 云资产库（列出账号历史任务 + 拉取到本地） ----------------

# 默认纳入资产库的能力（产出 3D 模型的为主）
LIBRARY_KINDS = ("image-to-3d", "text-to-3d", "retexture", "remesh")


def _ts_label(ts):
    """把 Meshy 的时间戳（毫秒或秒）转成 YYYY-MM-DD；失败返回空串。"""
    if not ts:
        return ""
    try:
        v = float(ts)
        if v > 1e12:      # 毫秒
            v /= 1000.0
        return time.strftime("%Y-%m-%d", time.localtime(v))
    except Exception:
        return ""


def _normalize_task(kind, task):
    """把一条云端任务规整成 UI 统一字段；探测本地缓存与是否过期。"""
    tid = str(task.get("id") or task.get("task_id") or "")
    status = str(task.get("status", "")).upper()
    model_urls = task.get("model_urls") or {}
    glb_url = model_urls.get("glb") or ""

    # 本地缓存探测（不创建目录）
    local_glb = ""
    local_thumb = ""
    if tid:
        cand = config.asset_path(tid, "model.glb")
        if os.path.isfile(cand):
            local_glb = cand
        cand_t = config.asset_path(tid, "thumb.png")
        if os.path.isfile(cand_t):
            local_thumb = cand_t

    # 过期判断（预签名 URL 有时效）：已缓存到本地的永不算过期
    expired = False
    if not local_glb:
        exp = task.get("expires_at")
        if exp:
            try:
                v = float(exp)
                if v < 1e12:
                    v *= 1000.0
                expired = v < (time.time() * 1000.0)
            except Exception:
                expired = False

    created = task.get("created_at") or 0
    prompt = (task.get("prompt") or task.get("texture_prompt")
              or task.get("name") or "")
    importable = bool(status == "SUCCEEDED" and (local_glb or (glb_url and not expired)))

    return {
        "id": tid,
        "kind": kind,
        "status": status,
        "prompt": str(prompt),
        # 优先本地缩略图，否则用云端 URL（QML Image 可直接加载 http）
        "thumbnail": local_thumb or task.get("thumbnail_url") or "",
        "glb_url": glb_url,
        "local_glb": local_glb,
        "cached": bool(local_glb),
        "created_at": created,
        "created_label": _ts_label(created),
        "credits": task.get("consumed_credits"),
        "expired": bool(expired),
        "importable": importable,
    }


def list_library(kinds=None, page_num=1, page_size=40, should_stop=None):
    """列出账号下的云端资产（合并多个能力、按创建时间倒序）。
    每条带本地缓存/过期标记。需要已配置 API Key，否则抛 MeshyError。"""
    client = MeshyClient()
    kinds = kinds or LIBRARY_KINDS
    items = []
    errors = []
    attempted = 0
    for kind in kinds:
        if should_stop and should_stop():
            break
        if kind not in config.ENDPOINTS:
            continue
        attempted += 1
        try:
            tasks = client.list_tasks(kind, page_num=page_num, page_size=page_size)
        except Exception as e:
            errors.append("%s: %s" % (kind, e))
            print("[meshy] list %s failed: %s" % (kind, e))
            continue
        for t in tasks:
            try:
                items.append(_normalize_task(kind, t))
            except Exception as e:
                print("[meshy] normalize task failed: %s" % e)
    # 每个能力都拉取失败、且一条都没拿到 → 上抛，让 UI 显示"加载失败"而非误以为"库为空"。
    if attempted and not items and len(errors) == attempted:
        raise MeshyError("资产库加载失败：" + "; ".join(errors)[:300])
    items.sort(key=lambda x: x.get("created_at") or 0, reverse=True)
    return items


def fetch_library_asset(item, should_stop=None):
    """确保某资产的 glb(+贴图+缩略图)已落到本地缓存，返回 _download_assets 风格的 data。
    已缓存则零网络直接复用；未缓存则按 task_id 重新拉云端（过期会下载失败并抛错）。"""
    tid = str((item or {}).get("id") or "")
    if not tid:
        raise MeshyError("缺少任务 id")

    glb = config.asset_path(tid, "model.glb")
    if os.path.isfile(glb):
        tex_dir = config.asset_path(tid, "textures")
        textures = {}
        if os.path.isdir(tex_dir):
            for fn in os.listdir(tex_dir):
                key = os.path.splitext(fn)[0]
                if key in _TEX_KEYS:
                    textures[key] = os.path.join(tex_dir, fn)
        thumb = config.asset_path(tid, "thumb.png")
        return {"task_id": tid, "glb": glb,
                "texture_dir": (tex_dir if textures else None),
                "textures": textures,
                "thumbnail": (thumb if os.path.isfile(thumb) else None),
                "consumed_credits": item.get("credits")}

    # 未缓存：按 kind/task_id 拉云端再下载
    client = MeshyClient()
    kind = item.get("kind") or "image-to-3d"
    task = client.get_task(kind, tid)
    return _download_assets(client, task, tid)


def run_network(tool_name, kwargs, on_progress=None, should_stop=None):
    """统一入口：执行一个 Meshy 网络工具（单任务）。任何异常都收敛成 {success:False,error}。"""
    try:
        client = MeshyClient()
    except MeshyError as e:
        return _err(e)
    try:
        return _run_dispatch(tool_name, client, kwargs or {}, on_progress, should_stop)
    except MeshyError as e:
        return _err(e)
    except Exception as e:
        return _err("Meshy 调用失败: %s" % e)
