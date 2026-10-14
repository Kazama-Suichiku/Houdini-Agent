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
    monkeypatch.setattr(mgr, "logged_in", lambda: True)
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
    for mod in ("agent_session.py", "bridge_session.py"):
        src = open(os.path.join(root, mod), encoding="utf-8").read()
        assert re.search(r"def run\([^)]*think_tags=True", src, re.S), mod
        assert "self.build_messages(enable_thinking and think_tags)" in src, mod
    ctl = open(os.path.join(root, "controller.py"), encoding="utf-8").read()
    assert 'think_tags=(provider != "codex")' in ctl
