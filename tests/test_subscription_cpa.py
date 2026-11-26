# -*- coding: utf-8 -*-
"""ChatGPT 订阅（本机 CPA）管理逻辑：安装校验、配置生成、账号解析、退出登录、provider 路由。
全部离线：下载用假 requests，CPA 进程不启动。"""
import base64
import hashlib
import io
import json
import zipfile

import pytest

from houdini_agent.subscription import cpa


class Resp:
    def __init__(self, status=200, content=b""):
        self.status_code = status
        self._content = content
        self.headers = {"content-length": str(len(content))}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP %d" % self.status_code)

    def iter_content(self, chunk_size=65536):
        for i in range(0, len(self._content), chunk_size):
            yield self._content[i:i + chunk_size]


class FakeRequests:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, **kw):
        self.calls.append(url)
        r = self.routes.get(url)
        if r is None:
            raise ConnectionError("no route")
        return r


def _zip_bytes(exe_name="cli-proxy-api.exe"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("cli-proxy-api.exe" if exe_name.endswith(".exe") else exe_name, b"MZ-fake-binary")
        z.writestr("LICENSE", b"MIT")
        z.writestr("README.md", b"readme")
    return buf.getvalue()


def _jwt(claims):
    seg = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return "x." + seg + ".y"


@pytest.fixture
def mgr(tmp_path):
    return cpa.CPAManager(base=tmp_path / "cpa")


# ---------------------------------------------------------------- install
@pytest.mark.skipif(cpa.os.name != "nt", reason="windows asset layout")
class TestInstall:
    def test_mirror_down_falls_back_to_github_and_verifies(self, mgr, monkeypatch):
        data = _zip_bytes()
        asset = cpa._platform_asset()
        monkeypatch.setitem(cpa._SHA256, asset, hashlib.sha256(data).hexdigest())
        fake = FakeRequests({cpa._MIRROR_URL.format(asset=asset): Resp(404),
                             cpa._GITHUB_URL.format(ver=cpa.CPA_VERSION, asset=asset): Resp(200, data)})
        monkeypatch.setattr(cpa, "_requests", lambda: fake)
        progress = []
        mgr.install(progress=progress.append)
        assert mgr.installed() and mgr.exe.read_bytes() == b"MZ-fake-binary"
        assert (mgr.bin_dir / "LICENSE").is_file() and not (mgr.bin_dir / "README.md").exists()
        assert progress and progress[-1] == 100
        assert len(fake.calls) == 2

    def test_checksum_mismatch_rejected(self, mgr, monkeypatch):
        asset = cpa._platform_asset()
        monkeypatch.setitem(cpa._SHA256, asset, "0" * 64)
        fake = FakeRequests({cpa._MIRROR_URL.format(asset=asset): Resp(200, _zip_bytes()),
                             cpa._GITHUB_URL.format(ver=cpa.CPA_VERSION, asset=asset): Resp(200, _zip_bytes())})
        monkeypatch.setattr(cpa, "_requests", lambda: fake)
        with pytest.raises(cpa.CPAError) as e:
            mgr.install()
        assert "校验值不符" in str(e.value) and not mgr.installed()


def test_every_pinned_asset_is_for_current_version():
    assert all(cpa.CPA_VERSION in name for name in cpa._SHA256)
    assert cpa._platform_asset() in cpa._SHA256 or cpa.os.name != "nt"


def test_ensure_running_requires_install(mgr):
    with pytest.raises(cpa.CPAError):
        mgr.ensure_running(timeout=1)


# ---------------------------------------------------------------- config
def test_config_is_localhost_only_with_key_and_proxy(mgr, monkeypatch):
    monkeypatch.setattr(cpa, "system_proxy", lambda: "http://127.0.0.1:7890")
    mgr.write_config()
    text = mgr.config_path.read_text(encoding="utf-8")
    assert 'host: "127.0.0.1"' in text
    assert 'port: %d' % mgr.port in text
    assert json.dumps(mgr.client_key()) in text
    assert 'secret-key: ""' in text and "disable-control-panel: true" in text
    assert json.dumps(mgr.auth_dir.as_posix()) in text
    assert 'proxy-url: "http://127.0.0.1:7890"' in text
    # key / port persist across manager instances
    again = cpa.CPAManager(base=mgr.base)
    assert again.client_key() == mgr.client_key() and again.port == mgr.port


# ---------------------------------------------------------------- accounts
def test_accounts_parse_without_exposing_tokens(mgr):
    mgr.auth_dir.mkdir(parents=True)
    tok = _jwt({"email": "a@b.com", "https://api.openai.com/auth": {"chatgpt_plan_type": "pro"}})
    (mgr.auth_dir / "codex-a@b.com.json").write_text(json.dumps(
        {"type": "codex", "id_token": tok, "access_token": "SECRET", "refresh_token": "SECRET2"}), encoding="utf-8")
    (mgr.auth_dir / "claude-x.json").write_text(json.dumps({"type": "claude", "email": "x@y"}), encoding="utf-8")
    (mgr.auth_dir / "broken.json").write_text("{not json", encoding="utf-8")
    accs = mgr.accounts()
    assert accs == [{"file": "codex-a@b.com.json", "email": "a@b.com", "plan": "pro"}]
    assert "SECRET" not in json.dumps(accs)
    assert mgr.logged_in()
    assert mgr.logout() == 1 and not mgr.logged_in()
    assert (mgr.auth_dir / "claude-x.json").exists()      # 只删 Codex 凭据


def test_models_cache_defaults_to_codex_catalog(mgr):
    assert mgr.cached_models() == cpa.CODEX_MODELS


# ---------------------------------------------------------------- provider routing
def test_codex_provider_routes_to_local_cpa(mgr, monkeypatch):
    from houdini_agent.utils.ai_client_providers import AIClientProvidersMixin
    import houdini_agent.subscription as sub
    monkeypatch.setattr(sub, "manager", mgr)
    c = AIClientProvidersMixin()
    c._api_keys = {}
    assert c.has_api_key("codex") is False
    assert c._get_api_key("codex") == mgr.client_key()
    with pytest.raises(RuntimeError):
        c._get_api_url("codex", "gpt-6-sol")           # 未登录：明确报错，不去连本机服务
    mgr.auth_dir.mkdir(parents=True, exist_ok=True)
    (mgr.auth_dir / "codex-u.json").write_text(json.dumps({"type": "codex", "email": "u@x"}), encoding="utf-8")
    monkeypatch.setattr(mgr, "ensure_running", lambda timeout=20.0: True)
    assert c.has_api_key("codex") is True
    assert c._get_api_url("codex", "gpt-6-sol") == "http://127.0.0.1:%d/v1/chat/completions" % mgr.port
    assert c._get_vendor_name("codex") == "ChatGPT 订阅"
    assert c.get_masked_key("codex").startswith("u@x")


def test_image_generation_models_are_not_offered_for_chat(mgr):
    st = mgr._load_state()
    st["models"] = ["gpt-6.1-sol", "gpt-image-2", "gpt-image-2.5-flare", "gpt-reserve", "gpt-5.5"]
    assert mgr.cached_models() == ["gpt-6.1-sol", "gpt-5.5"]
    st["models"] = ["gpt-image-2"]
    assert mgr.cached_models() == cpa.CODEX_MODELS


# ---------------------------------------------------------------- reasoning summaries
class _CapturePost:
    def __init__(self):
        self.payloads = []

    def post(self, url, json=None, **kw):
        self.payloads.append(json)
        raise RuntimeError("stop after capture")


def _capture_payload(provider, model, enable_thinking, monkeypatch, mgr, effort=None, summary=True):
    from houdini_agent.utils.ai_client import AIClient
    import houdini_agent.subscription as sub
    monkeypatch.setattr(sub, "manager", mgr)
    monkeypatch.setattr(mgr, "logged_in", lambda *a, **k: True)
    monkeypatch.setattr(mgr, "ensure_running", lambda timeout=20.0: True)
    c = AIClient()
    c._api_keys["deepseek"] = "sk-test"
    if effort is not None:
        c.set_reasoning(effort, summary=summary)
    cap = _CapturePost()
    c._http_session = cap
    c._max_retries = 1
    list(c.chat_stream([{"role": "user", "content": "hi"}], model=model, provider=provider,
                       enable_thinking=enable_thinking))
    return cap.payloads[0] if cap.payloads else {}


def test_codex_requests_detailed_reasoning_summary_by_default(monkeypatch, mgr):
    p = _capture_payload("codex", "gpt-6.1-sol", True, monkeypatch, mgr)
    assert p.get("reasoning_effort") == "medium"
    assert p.get("reasoning") == {"summary": "detailed"}


def test_codex_uses_user_effort_and_skips_summary_when_think_off(monkeypatch, mgr):
    p = _capture_payload("codex", "gpt-6.1-sol", True, monkeypatch, mgr, effort="xhigh", summary=False)
    assert p.get("reasoning_effort") == "xhigh"
    assert p.get("reasoning") == {"summary": "none"}
    p = _capture_payload("codex", "gpt-6.1-sol", False, monkeypatch, mgr, effort="high")
    assert p.get("reasoning_effort") == "high" and p.get("reasoning") == {"summary": "none"}


def test_other_providers_unaffected(monkeypatch, mgr):
    p = _capture_payload("deepseek", "deepseek-v4-flash", True, monkeypatch, mgr, effort="xhigh")
    assert "reasoning" not in p and p.get("reasoning_effort") != "xhigh"


def test_effort_levels_per_model():
    assert cpa.codex_efforts("gpt-6.1-sol") == ["low", "medium", "high", "xhigh", "max", "ultra"]
    assert cpa.codex_efforts("gpt-6-luna")[-1] == "max"
    assert cpa.codex_efforts("gpt-5.5")[-1] == "xhigh"
    assert cpa.codex_efforts("some-new-model") == ["low", "medium", "high"]
    assert cpa.codex_default_effort("gpt-6.1-sol") == "low"
    assert cpa.codex_default_effort("gpt-6-astra") == "medium"
    assert all(m in cpa.CODEX_EFFORTS for m in cpa.CODEX_MODELS)


def test_sessions_can_skip_think_tag_prompt_and_controller_does_for_codex():
    # 纯文本检查：controller / 会话依赖 PySide6，CI 没装，不能 import
    import os
    import re
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "houdini_agent", "ui_qml")
    agent_src = open(os.path.join(root, "agent_session.py"), encoding="utf-8").read()
    assert re.search(r"def run\([^)]*think_tags=True", agent_src, re.S)
    assert "self.build_messages(enable_thinking and think_tags)" in agent_src
    bridge_src = open(os.path.join(root, "bridge_session.py"), encoding="utf-8").read()
    assert "class BridgeAgentSession(BaseAgentSession):" in bridge_src
    ctl = open(os.path.join(root, "controller.py"), encoding="utf-8").read()
    assert 'think_tags=(self._sub_kind(provider) is None)' in ctl


# ---------------------------------------------------------------- Claude subscription
def test_accounts_and_models_are_split_by_kind(mgr):
    mgr.auth_dir.mkdir(parents=True)
    (mgr.auth_dir / "codex-a.json").write_text(json.dumps({"type": "codex", "email": "a@x"}), encoding="utf-8")
    (mgr.auth_dir / "claude-b.json").write_text(json.dumps({"type": "claude", "email": "b@y",
                                                              "access_token": "SECRET"}), encoding="utf-8")
    assert [a["email"] for a in mgr.accounts("codex")] == ["a@x"]
    assert [a["email"] for a in mgr.accounts("claude")] == ["b@y"]
    assert "SECRET" not in json.dumps(mgr.accounts("claude"))
    assert mgr.logout(kind="claude") == 1
    assert mgr.logged_in("codex") and not mgr.logged_in("claude")
    st = mgr._load_state()
    st["models"] = ["gpt-6.1-sol", "claude-opus-5-5"]          # 混在一起也能分开
    st["models_claude"] = ["claude-sonnet-5-5", "gpt-5.5"]
    assert mgr.cached_models("codex") == ["gpt-6.1-sol"]
    assert mgr.cached_models("claude") == ["claude-sonnet-5-5"]


def test_kind_helpers():
    assert cpa.kind_of("codex") == "codex" and cpa.kind_of("claude_sub") == "claude"
    assert cpa.kind_of("deepseek") is None
    assert cpa.default_models("claude")[0] == "claude-opus-5-5"
    assert cpa.effort_levels("claude", "claude-opus-5-5") == ["low", "medium", "high", "xhigh", "max"]
    assert cpa.default_effort("claude", "claude-opus-5-5") == "medium"
    assert cpa.model_context("claude", "claude-opus-5-5") == 1000000
    assert cpa.model_context("codex", "gpt-6.1-sol") == 500000


def test_claude_sub_provider_routes_to_local_cpa(mgr, monkeypatch):
    from houdini_agent.utils.ai_client_providers import AIClientProvidersMixin
    import houdini_agent.subscription as sub
    monkeypatch.setattr(sub, "manager", mgr)
    c = AIClientProvidersMixin()
    c._api_keys = {}
    assert c.has_api_key("claude_sub") is False
    with pytest.raises(RuntimeError):
        c._get_api_url("claude_sub", "claude-opus-5-5")
    mgr.auth_dir.mkdir(parents=True, exist_ok=True)
    (mgr.auth_dir / "claude-u.json").write_text(json.dumps({"type": "claude", "email": "u@y"}), encoding="utf-8")
    monkeypatch.setattr(mgr, "ensure_running", lambda timeout=20.0: True)
    assert c.has_api_key("claude_sub") is True and c.has_api_key("codex") is False
    assert c._get_api_url("claude_sub", "claude-opus-5-5").endswith("/v1/chat/completions")
    assert c._get_vendor_name("claude_sub") == "Claude 订阅"


def test_claude_sub_requests_effort_and_thinking(monkeypatch, mgr):
    p = _capture_payload("claude_sub", "claude-opus-5-5", True, monkeypatch, mgr, effort="high")
    assert p.get("reasoning_effort") == "high" and p.get("reasoning") == {"summary": "detailed"}


# ---------------------------------------------------------------- Codex image models & generation
_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0"
    b"\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb0\x00\x00\x00\x00IEND\xaeB`\x82"
)


class _JsonResp:
    def __init__(self, status=200, payload=None, text="", content=b"", headers=None):
        self.status_code = status
        self._payload = payload
        self.text = text if text else (json.dumps(payload) if payload is not None else "")
        self.content = content
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP %d" % self.status_code)

    def json(self):
        return self._payload


def test_codex_image_model_detection_and_sorting():
    assert cpa.is_image_model("gpt-image-2.5")
    assert cpa.is_image_model("gpt-image-2.5-sunburst")
    assert cpa.is_image_model("gpt-image-2.6-flare")
    assert cpa.is_image_model("gpt-image-5.6")
    assert cpa.is_image_model("codex-image-1")
    assert not cpa.is_image_model("gpt-6.1-sol")
    assert not cpa.is_image_model("claude-opus-5-5")
    assert not cpa.is_image_model("gpt-reserve")
    assert not cpa._is_chat_model("gpt-image-2.6")
    assert not cpa._is_chat_model("codex-image-1")

    raw = ["gpt-image-1.5", "gpt-image-2.5-flare", "gpt-image-2", "gpt-image-2.6-sunburst",
           "gpt-image-2.5", "gpt-image-2.6", "gpt-image-2.5-sunburst", "gpt-image-5.6"]
    assert cpa._sort_image_models(raw) == [
        "gpt-image-5.6",
        "gpt-image-2.6",
        "gpt-image-2.6-sunburst",
        "gpt-image-2.5",
        "gpt-image-2.5-sunburst",
        "gpt-image-2.5-flare",
        "gpt-image-2",
        "gpt-image-1.5",
    ]


def test_aspect_ratio_to_size():
    assert cpa.aspect_ratio_to_size("1:1") == "1024x1024"
    assert cpa.aspect_ratio_to_size("16:9") == "1536x1024"
    assert cpa.aspect_ratio_to_size("4:3") == "1536x1024"
    assert cpa.aspect_ratio_to_size("9:16") == "1024x1536"
    assert cpa.aspect_ratio_to_size("3:4") == "1024x1536"
    assert cpa.aspect_ratio_to_size("1536x1024") == "1536x1024"
    assert cpa.aspect_ratio_to_size("") == "1024x1024"


def test_refresh_models_syncs_codex_image_models_dynamically(mgr, monkeypatch):
    assert mgr.cached_image_models("codex") == cpa.CODEX_IMAGE_MODELS
    assert mgr.cached_image_models("claude") == []
    assert mgr.image_models_stale() is True

    class _FakeReq:
        def get(self, url, **kw):
            assert url.endswith("/models")
            assert kw.get("proxies") == {"http": None, "https": None}
            return _JsonResp(200, {
                "data": [
                    {"id": "gpt-6.1-sol"},
                    {"id": "gpt-image-2.6-flare"},
                    {"id": "gpt-image-2.6"},
                    {"id": "gpt-image-2.5"},
                    {"id": "gpt-image-2"},
                    {"id": "claude-opus-5-5"},
                    {"id": "codex-auto-review"},
                ]
            })

    monkeypatch.setattr(cpa, "_requests", lambda: _FakeReq())
    chat_models = mgr.refresh_models("codex")
    assert chat_models == ["gpt-6.1-sol"]
    assert mgr.cached_image_models("codex") == [
        "gpt-image-2.6", "gpt-image-2.6-flare", "gpt-image-2.5", "gpt-image-2"
    ]
    assert mgr.image_models_stale() is False
    # 持久化到 state.json 后新实例也能读到
    again = cpa.CPAManager(base=mgr.base)
    assert again.cached_image_models("codex") == [
        "gpt-image-2.6", "gpt-image-2.6-flare", "gpt-image-2.5", "gpt-image-2"
    ]


def test_codex_generate_image_t2i_and_i2i(mgr, tmp_path, monkeypatch):
    # 未登录直接报错
    with pytest.raises(cpa.CPAError):
        mgr.generate_image("a red cube")

    mgr.auth_dir.mkdir(parents=True, exist_ok=True)
    (mgr.auth_dir / "codex-u.json").write_text(json.dumps({"type": "codex", "email": "u@x"}), encoding="utf-8")
    monkeypatch.setattr(mgr, "ensure_running", lambda timeout=20.0: True)

    calls = []
    b64_png = base64.b64encode(_TINY_PNG).decode("ascii")

    class _FakeReq:
        def post(self, url, **kw):
            calls.append((url, kw))
            assert kw.get("proxies") == {"http": None, "https": None}
            if url.endswith("/images/generations"):
                return _JsonResp(200, {"output_format": "png",
                                       "data": [{"b64_json": b64_png, "revised_prompt": "red cube"}]})
            return _JsonResp(200, {"data": [{"url": "data:image/png;base64," + b64_png}]})

    monkeypatch.setattr(cpa, "_requests", lambda: _FakeReq())

    # 1) 文生图
    res1 = mgr.generate_image("a red cube", model="gpt-image-2.5", aspect_ratio="16:9")
    assert res1["bytes"] == _TINY_PNG and res1["ext"] == ".png"
    assert res1["revised_prompt"] == "red cube"
    assert calls[0][0].endswith("/v1/images/generations")
    assert calls[0][1]["json"] == {"model": "gpt-image-2.5", "prompt": "a red cube", "size": "1536x1024"}

    # 2) 图生图（多张参考图：本地文件 + data URI）
    ref_file = tmp_path / "ref.png"
    ref_file.write_bytes(_TINY_PNG)
    data_uri = "data:image/png;base64," + b64_png
    res2 = mgr.generate_image("make it blue", model="gpt-image-2.5", aspect_ratio="9:16",
                              reference_images=[str(ref_file), data_uri])
    assert res2["bytes"] == _TINY_PNG and res2["ext"] == ".png"
    assert calls[1][0].endswith("/v1/images/edits")
    assert calls[1][1]["data"] == {"model": "gpt-image-2.5", "prompt": "make it blue", "size": "1024x1536"}
    assert [field for field, _ in calls[1][1]["files"]] == ["image[]", "image[]"]

    # 3) 鉴权过期给出可读中文错误
    class _ErrReq:
        def post(self, url, **kw):
            return _JsonResp(503, {"error": {"message": "auth_unavailable: no auth available"}})

    monkeypatch.setattr(cpa, "_requests", lambda: _ErrReq())
    with pytest.raises(cpa.CPAError) as err:
        mgr.generate_image("cube")
    assert "重新登录" in str(err.value)


def test_generate_concepts_routes_codex_without_meshy_key(mgr, tmp_path, monkeypatch):
    from houdini_agent.meshy import network_ops as nops, config as mcfg, schemas as mschemas
    import houdini_agent.subscription as sub

    # 同名 gpt-image-2 能准确区分 Meshy 与 Codex
    assert nops.parse_image_model("codex:gpt-image-2") == ("codex", "gpt-image-2")
    assert nops.parse_image_model("gpt-image-2") == ("meshy", "gpt-image-2")
    assert nops.parse_image_model("codex:gpt-image-2.5") == ("codex", "gpt-image-2.5")
    assert nops.parse_image_model("gpt-image-2.5") == ("codex", "gpt-image-2.5")
    assert nops.parse_image_model("nano-banana") == ("meshy", "nano-banana")

    # 工具 schema 不再把 ai_model 锁死为 Meshy enum
    for t in mschemas.MESHY_TOOLS:
        if t["function"]["name"] in ("meshy_text_to_image", "meshy_image_to_image", "meshy_concept_to_3d"):
            prop = t["function"]["parameters"]["properties"]["ai_model"]
            assert "enum" not in prop

    # Codex 生图路径绝不实例化 MeshyClient，也不要求 MESHY_API_KEY
    monkeypatch.setattr(sub, "manager", mgr)
    mgr.auth_dir.mkdir(parents=True, exist_ok=True)
    (mgr.auth_dir / "codex-u.json").write_text(json.dumps({"type": "codex", "email": "u@x"}), encoding="utf-8")
    monkeypatch.setattr(mgr, "ensure_running", lambda timeout=20.0: True)
    monkeypatch.setattr(mcfg, "assets_dir", lambda tid=None: str((tmp_path / str(tid or "root")).resolve()))

    def _boom_meshy(*a, **k):
        raise AssertionError("MeshyClient should not be constructed for Codex image generation")
    monkeypatch.setattr(nops, "MeshyClient", _boom_meshy)

    seen = []

    def _fake_gen(prompt, model="gpt-image-2.5", aspect_ratio="1:1", reference_images=None, **kw):
        seen.append((prompt, model, aspect_ratio, reference_images))
        return {"bytes": _TINY_PNG, "ext": ".png", "revised_prompt": "", "model": model}

    monkeypatch.setattr(mgr, "generate_image", _fake_gen)

    # assets_dir 需要目录存在
    def _mk_assets_dir(tid=None):
        p = tmp_path / str(tid or "root")
        p.mkdir(parents=True, exist_ok=True)
        return str(p)
    monkeypatch.setattr(mcfg, "assets_dir", _mk_assets_dir)

    emitted = []
    out = nops.generate_concepts(
        ["sci-fi helmet", "steampunk goggles"],
        ai_model="codex:gpt-image-2.5",
        aspect_ratio="16:9",
        on_image=emitted.append,
    )
    assert len(out) == 2
    assert len(emitted) == 2
    assert all(item["provider"] == "codex" and item["model"] == "gpt-image-2.5" for item in out)
    assert all(cpa.os.path.isfile(item["image"]) for item in out)
    assert {s[0] for s in seen} == {"sci-fi helmet", "steampunk goggles"}
    assert all(s[1] == "gpt-image-2.5" and s[2] == "16:9" for s in seen)


def test_claude_models_filter_retired_and_promote_newest(mgr, monkeypatch):
    assert "claude-fable-5-2" in cpa.CLAUDE_MODELS
    assert cpa.model_context("claude", "claude-fable-5-2") == 1000000
    assert "gpt-6.2-sol" in cpa.CODEX_MODELS
    assert cpa.model_context("codex", "gpt-6.2-sol") == 500000

    # 旧缓存里的已退役 Claude 3.x / 4.0 快照在读取时即被过滤
    st = mgr._load_state()
    st["models_claude"] = [
        "claude-opus-5-5", "claude-3-5-haiku-20241022", "claude-3-7-sonnet-20250219",
        "claude-opus-4-20250514", "claude-opus-4-1-20250805", "claude-sonnet-4-20250514",
        "claude-fable-5-2",
    ]
    assert mgr.cached_models("claude") == ["claude-opus-5-5", "claude-fable-5-2"]
    assert mgr.models_stale("claude") is True

    class _FakeReq:
        def get(self, url, **kw):
            return _JsonResp(200, {
                "data": [
                    {"id": "claude-3-5-haiku-20241022"},
                    {"id": "claude-3-7-sonnet-20250219"},
                    {"id": "claude-opus-4-20250514"},
                    {"id": "claude-sonnet-4-6"},
                    {"id": "claude-opus-4-7"},
                    {"id": "claude-opus-5-5"},
                    {"id": "claude-fable-5-2"},
                    {"id": "claude-sonnet-5-5"},
                    {"id": "claude-opus-6"},
                ]
            })

    monkeypatch.setattr(cpa, "_requests", lambda: _FakeReq())
    ordered = mgr.refresh_models("claude")
    # 未来更高版本置顶，推荐列表居中，较老但仍在役的按版本降序排后，已退役模型全部过滤
    assert ordered == [
        "claude-opus-6",
        "claude-opus-5-5",
        "claude-fable-5-2",
        "claude-sonnet-5-5",
        "claude-opus-4-7",
        "claude-sonnet-4-6",
    ]


def test_antigravity_provider_models_and_routing(mgr, monkeypatch):
    from houdini_agent.utils.ai_client_providers import AIClientProvidersMixin
    import houdini_agent.subscription as sub

    assert cpa.kind_of("antigravity") == "antigravity"
    assert "gemini-4-neon-xhigh" in cpa.default_models("antigravity")
    assert cpa.model_context("antigravity", "gemini-4-neon-xhigh") == 1048576
    assert cpa.effort_levels("antigravity", "gemini-4-neon-xhigh") == ["low", "medium", "high", "xhigh"]
    assert cpa.effort_levels("antigravity", "gemini-4-argon-high") == ["low", "medium", "high"]
    assert cpa.effort_levels("antigravity", "gpt-oss-120b-medium") == []

    # owned_by 能把 Antigravity 的 claude-opus-5-5-high / claude-sonnet-4-6 与 Claude 订阅彻底隔离，
    # 且过滤掉 gemini-3.1-flash-image 非对话图片模型
    class _FakeReq:
        def get(self, url, **kw):
            return _JsonResp(200, {
                "data": [
                    {"id": "gemini-4-neon-xhigh", "owned_by": "antigravity"},
                    {"id": "claude-opus-5-5-high", "owned_by": "antigravity"},
                    {"id": "claude-sonnet-4-6", "owned_by": "antigravity"},
                    {"id": "gemini-3.1-flash-image", "owned_by": "antigravity"},
                    {"id": "claude-opus-5-5", "owned_by": "anthropic"},
                    {"id": "gpt-6.2-sol", "owned_by": "openai"},
                ]
            })

    monkeypatch.setattr(cpa, "_requests", lambda: _FakeReq())
    ag_models = mgr.refresh_models("antigravity")
    assert ag_models == ["gemini-4-neon-xhigh", "claude-opus-5-5-high", "claude-sonnet-4-6"]
    assert mgr.refresh_models("claude") == ["claude-opus-5-5"]
    assert mgr.refresh_models("codex") == ["gpt-6.2-sol"]

    # 账号隔离与路由
    monkeypatch.setattr(sub, "manager", mgr)
    c = AIClientProvidersMixin()
    c._api_keys = {}
    assert c.has_api_key("antigravity") is False
    with pytest.raises(RuntimeError):
        c._get_api_url("antigravity", "gemini-4-neon-xhigh")
    mgr.auth_dir.mkdir(parents=True, exist_ok=True)
    (mgr.auth_dir / "antigravity-g@gmail.com.json").write_text(
        json.dumps({"type": "antigravity", "email": "g@gmail.com", "access_token": "SECRET"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(mgr, "ensure_running", lambda timeout=20.0: True)
    assert c.has_api_key("antigravity") is True
    assert c._get_api_url("antigravity", "gemini-4-neon-xhigh").endswith("/v1/chat/completions")
    assert c._get_vendor_name("antigravity") == "Google 反重力"
    assert c.get_masked_key("antigravity").startswith("g@gmail.com")
    assert "SECRET" not in json.dumps(mgr.accounts("antigravity"))
    assert mgr.logout(kind="antigravity") == 1 and not mgr.logged_in("antigravity")
