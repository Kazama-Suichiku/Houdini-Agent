# -*- coding: utf-8 -*-
"""
Meshy REST 客户端 — 纯 HTTP，无 Qt / 无 hou 依赖。

约定（与官方 openapi 一致）：
  - 创建任务 POST  → {"result": "<task-id>"}
  - 轮询任务 GET .../:id → {id,status,progress,model_urls{...},texture_urls[...],
                            thumbnail_url,consumed_credits,task_error{message}}
  - status ∈ PENDING | IN_PROGRESS | SUCCEEDED | FAILED | CANCELED
"""

import os
import sys
import time

# 与 ai_client 一致：优先使用仓库内置 lib/ 的 requests
_lib_path = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "lib")
if os.path.exists(_lib_path) and _lib_path not in sys.path:
    sys.path.insert(0, _lib_path)

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    requests = None
    HAS_REQUESTS = False

from . import config


_TERMINAL = ("SUCCEEDED", "FAILED", "CANCELED")


def _friendly_task_error(status, raw_msg, kind=""):
    """把 Meshy 返回的晦涩 task_error 映射成可操作的中文提示。

    Meshy 对内容审核失败常常只回一句很泛的 "The input file or parameters could
    not be processed."，模型/用户无从判断原因。对文生图/图生图这类任务，这条错误
    绝大多数是提示词或图片被内容审核拒绝（最常见是包含受版权保护的角色/品牌/名人）。
    给出明确的修正方向，避免模型原样重试或乱猜。
    """
    msg = (raw_msg or "").strip()
    low = msg.lower()
    # 只对 FAILED 给内容审核猜测；CANCELED（用户/系统取消）不该被说成"被审核拒绝"。
    if str(status).upper() != "FAILED":
        return "任务%s: %s" % (status, msg or status)
    is_image_gen = any(k in (kind or "") for k in ("image", "concept", "text-to-image"))
    if ("could not be processed" in low or "invalid" in low or "policy" in low
            or "moderation" in low or "rejected" in low):
        hint = ("（提示：该错误通常表示提示词或输入图片被内容审核拒绝，"
                "最常见原因是包含受版权保护的角色/品牌/名人名称（如卡通形象、影视角色等），"
                "或图片不合规。请改用不含具体 IP 名称的通用描述后重试，不要原样重试。）")
        if is_image_gen or "could not be processed" in low:
            return "任务%s: %s %s" % (status, msg or status, hint)
    return "任务%s: %s" % (status, msg or status)


class MeshyError(RuntimeError):
    pass


class MeshyClient:
    def __init__(self, api_key=None, timeout=60):
        if not HAS_REQUESTS:
            raise MeshyError("requests 不可用，无法调用 Meshy API")
        self.api_key = api_key or config.get_api_key()
        if not self.api_key:
            raise MeshyError("未配置 Meshy API Key（设置环境变量 MESHY_API_KEY 或在应用内填写）")
        self.timeout = timeout

    # ---------- 底层 ----------
    def _headers(self):
        return {"Authorization": "Bearer %s" % self.api_key,
                "Content-Type": "application/json"}

    def _url(self, kind, suffix=""):
        return config.API_BASE + config.ENDPOINTS[kind] + suffix

    def _post(self, kind, body):
        r = requests.post(self._url(kind), headers=self._headers(),
                          json=body, timeout=self.timeout)
        self._raise(r)
        data = r.json()
        return data.get("result") if isinstance(data, dict) else data

    def get_task(self, kind, task_id):
        r = requests.get(self._url(kind, "/" + str(task_id)),
                        headers=self._headers(), timeout=self.timeout)
        self._raise(r)
        return r.json()

    # ---------- 列出账号下的历史任务（云资产库） ----------
    def list_tasks(self, kind, page_num=1, page_size=40, sort_by="-created_at"):
        """GET 分页列出某能力下账号的历史任务。返回任务对象列表。
        kind ∈ ENDPOINTS（image-to-3d / text-to-3d / retexture / remesh / text-to-image）。"""
        params = {"page_num": int(page_num), "page_size": int(page_size),
                  "sort_by": sort_by}
        r = requests.get(self._url(kind), headers=self._headers(),
                        params=params, timeout=self.timeout)
        self._raise(r)
        data = r.json()
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            # 不同端点字段名兼容
            for k in ("result", "data", "tasks", "items"):
                v = data.get(k)
                if isinstance(v, list):
                    return v
        return []

    @staticmethod
    def _raise(resp):
        if resp.status_code >= 400:
            msg = resp.text
            try:
                j = resp.json()
                msg = j.get("message") or j.get("error") or msg
            except Exception:
                pass
            raise MeshyError("Meshy API %d: %s" % (resp.status_code, str(msg)[:400]))

    # ---------- 创建任务（返回 task_id） ----------
    def create_text_to_image(self, prompt, ai_model="nano-banana",
                             aspect_ratio="1:1", generate_multi_view=False):
        body = {
            "ai_model": ai_model,          # 必填：nano-banana / nano-banana-2 / nano-banana-pro / gpt-image-2
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "generate_multi_view": bool(generate_multi_view),
        }
        return self._post("text-to-image", body)

    def create_image_to_image(self, prompt, reference_images, ai_model="nano-banana",
                              generate_multi_view=False):
        """图生图/图片编辑：基于 1–5 张参考图 + 提示词生成新图。
        reference_images：URL / base64 data URI 列表（已规整）。"""
        refs = reference_images if isinstance(reference_images, (list, tuple)) else [reference_images]
        refs = [r for r in refs if r][:5]
        if not refs:
            raise MeshyError("image-to-image 需要至少 1 张参考图")
        body = {
            "ai_model": ai_model,
            "prompt": prompt,
            "reference_image_urls": list(refs),
            "generate_multi_view": bool(generate_multi_view),
        }
        return self._post("image-to-image", body)

    def create_image_to_3d(self, image=None, input_task_id=None, should_texture=True,
                           enable_pbr=True, topology="triangle", target_polycount=30000,
                           ai_model="latest", target_formats=None):
        body = {
            "should_texture": bool(should_texture),
            "enable_pbr": bool(enable_pbr),
            "should_remesh": True,
            "topology": topology,
            "target_polycount": int(target_polycount),
            "ai_model": ai_model,
            "target_formats": target_formats or ["glb"],
        }
        # input_task_id 优先：直接接 text-to-image 的结果，免下载重传
        if input_task_id:
            body["input_task_id"] = input_task_id
        else:
            body["image_url"] = image
        return self._post("image-to-3d", body)

    def create_text_to_3d_preview(self, prompt, topology="triangle",
                                  target_polycount=30000, ai_model="latest",
                                  target_formats=None):
        body = {
            "mode": "preview",
            "prompt": prompt,
            "topology": topology,
            "target_polycount": int(target_polycount),
            "ai_model": ai_model,
            "target_formats": target_formats or ["glb"],
        }
        return self._post("text-to-3d", body)

    def create_text_to_3d_refine(self, preview_task_id, enable_pbr=True,
                                 texture_prompt=None, ai_model="latest",
                                 target_formats=None):
        body = {
            "mode": "refine",
            "preview_task_id": preview_task_id,
            "enable_pbr": bool(enable_pbr),
            "ai_model": ai_model,
            "target_formats": target_formats or ["glb"],
        }
        if texture_prompt:
            body["texture_prompt"] = texture_prompt
        return self._post("text-to-3d", body)

    def create_retexture(self, model_url, text_style_prompt=None,
                         image_style_url=None, enable_pbr=True,
                         enable_original_uv=False, ai_model="latest",
                         target_formats=None):
        body = {
            "model_url": model_url,
            "enable_pbr": bool(enable_pbr),
            "enable_original_uv": bool(enable_original_uv),
            "ai_model": ai_model,
            "target_formats": target_formats or ["glb"],
        }
        # image_style_url 优先级高于 text_style_prompt（官方约定）
        if image_style_url:
            body["image_style_url"] = image_style_url
        elif text_style_prompt:
            body["text_style_prompt"] = text_style_prompt
        return self._post("retexture", body)

    def create_remesh(self, model_url=None, input_task_id=None,
                      topology="quad", target_polycount=30000,
                      target_formats=None):
        body = {
            "topology": topology,
            "target_polycount": int(target_polycount),
            "target_formats": target_formats or ["glb"],
        }
        if input_task_id:
            body["input_task_id"] = input_task_id
        elif model_url:
            body["model_url"] = model_url
        return self._post("remesh", body)

    # ---------- 绑定 / 动画 ----------
    def create_rigging(self, input_task_id=None, model_url=None,
                       height_meters=1.7, texture_image_url=None):
        """对人形角色做自动绑定。input_task_id（Meshy 已生成的模型）优先，
        否则用 model_url（公网 GLB 或 base64 data URI）。约束：双足人形、带贴图、
        脸朝 +Z、≤30 万面。成功后返回 rig_task_id，并自带 walk/run 基础动画。"""
        body = {"height_meters": float(height_meters or 1.7)}
        if input_task_id:
            body["input_task_id"] = input_task_id
        elif model_url:
            body["model_url"] = model_url
        else:
            raise MeshyError("rigging 需要 input_task_id 或 model_url 之一")
        if texture_image_url:
            body["texture_image_url"] = texture_image_url
        return self._post("rigging", body)

    def create_animation(self, rig_task_id, action_id):
        """对一个已绑定的角色套一个预设动作。rig_task_id 来自绑定任务，
        action_id 是动作库里的整数编号（0–696）。每次只生成一个动作。"""
        if not rig_task_id:
            raise MeshyError("animation 需要 rig_task_id")
        body = {"rig_task_id": rig_task_id, "action_id": int(action_id)}
        return self._post("animation", body)

    # ---------- 余额 ----------
    def balance(self):
        r = requests.get(config.API_BASE + config.ENDPOINTS["balance"],
                        headers=self._headers(), timeout=self.timeout)
        self._raise(r)
        data = r.json()
        # -1 表示"无法解析余额"，与真实的 0 余额区分开（调用方/ UI 据此显示"未知"）。
        if not isinstance(data, dict):
            return -1
        try:
            return int(data.get("balance"))
        except (TypeError, ValueError):
            return -1

    # ---------- 轮询 ----------
    def wait(self, kind, task_id, on_progress=None, timeout=600, interval=3,
             should_stop=None):
        """阻塞轮询直到任务终态。on_progress(progress:int, status:str) 可选。
        should_stop() 返回 True 时中止。返回最终 task dict；失败抛 MeshyError。"""
        deadline = time.monotonic() + timeout
        last = -1
        while True:
            if should_stop and should_stop():
                raise MeshyError("已被用户中止（任务可能仍在 Meshy 云端继续运行并计费，"
                                 "稍后可在资产库中找回已生成的结果）")
            task = self.get_task(kind, task_id)
            status = str(task.get("status", "")).upper()
            prog = int(task.get("progress", 0) or 0)
            if on_progress and prog != last:
                try:
                    on_progress(prog, status)
                except Exception:
                    pass
                last = prog
            if status in _TERMINAL:
                if status != "SUCCEEDED":
                    err = (task.get("task_error") or {}).get("message") or status
                    raise MeshyError(_friendly_task_error(status, err, kind))
                # 用量埋点：所有计费任务都从这里终态返回（埋点绝不影响主流程）
                try:
                    from . import telemetry
                    telemetry.record_task(kind, task)
                except Exception:
                    pass
                return task
            if time.monotonic() > deadline:
                raise MeshyError("轮询超时（%ds），任务仍未完成（任务可能仍在 Meshy 云端"
                                 "继续运行并计费，稍后可在资产库中找回结果）" % timeout)
            time.sleep(interval)

    # ---------- 下载 ----------
    def download(self, url, dest_path):
        """原子下载：先写入 .part 临时文件，完整下载后再 rename 到目标路径。
        中途失败会删除半截临时文件，绝不在目标路径留下不完整文件——否则后续
        os.path.isfile() 的缓存探测会把半截文件当成有效缓存、永久复用损坏数据。"""
        os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
        tmp_path = dest_path + ".part"
        try:
            r = requests.get(url, timeout=self.timeout, stream=True)
            self._raise(r)
            with open(tmp_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
            os.replace(tmp_path, dest_path)   # 同盘原子替换
        except BaseException:
            try:
                if os.path.isfile(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
            raise
        return dest_path
