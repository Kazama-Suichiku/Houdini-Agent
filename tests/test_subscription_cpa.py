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
