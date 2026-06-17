# -*- coding: utf-8 -*-
"""
全局测试夹具（pytest fixtures）。

目标：让所有测试 **完全离线、与真实环境隔离**——
  - 不读写真实的 <repo>/config、<repo>/cache（重定向到临时目录）
  - 不依赖 Houdini 的 hou 模块（提供桩）
  - 不发任何真实网络请求
"""
import os
import sys
import types
from pathlib import Path

import pytest

# 让 `import houdini_agent` / `import shared` 在 pytest 下可用（仓库根加入 sys.path）
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


@pytest.fixture(autouse=True)
def isolated_paths(tmp_path, monkeypatch):
    """把 shared.common_utils 的仓库根重定向到临时目录，
    从而 config/cache 的所有读写都落在 tmp_path，绝不碰真实文件。

    因为 get_config_dir() / get_cache_dir() 在调用时都经由 get_repo_root()，
    只需打这一个点即可覆盖 load_config/save_config/get_cache_dir 全链路。"""
    import shared.common_utils as cu
    root = tmp_path / "repo"
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "cache").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cu, "get_repo_root", lambda *a, **k: str(root))
    return root


@pytest.fixture(autouse=True)
def clean_meshy_env(monkeypatch):
    """清掉可能影响 Meshy/埋点 行为的环境变量，保证每个测试从干净状态开始。"""
    for var in ("MESHY_API_KEY", "DCC_AI_MESHY_API_KEY",
                "HAGENT_TELEMETRY_OFF", "DCC_AI_TELEMETRY_OFF",
                "HAGENT_TELEMETRY_URL", "DCC_AI_TELEMETRY_URL"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _hou_stub():
    """提供一个最小 hou 桩，避免任何意外 `import hou` 在收集阶段失败。
    （被测的离线模块本身不需要 hou，这只是兜底。）"""
    if "hou" not in sys.modules:
        stub = types.ModuleType("hou")
        sys.modules["hou"] = stub
        created = True
    else:
        created = False
    yield
    if created:
        sys.modules.pop("hou", None)


@pytest.fixture
def telemetry_mod(monkeypatch):
    """返回隔离好的 telemetry 模块：
    - requests 设为真值哨兵（让 is_enabled 不因测试环境缺 requests 而误判为关闭）
    - _post 打桩为捕获器，绝不发真实请求
    - 重置进程级状态（_seen_tasks / install id 缓存 / 上传线程）
    - 阻止后台上传线程启动
    """
    from houdini_agent.meshy import telemetry as t

    monkeypatch.setattr(t, "requests", object())     # 真值即可，_post 已被打桩
    t._seen_tasks.clear()
    t._install_id_cache[0] = None
    t._uploader[0] = None
    monkeypatch.setattr(t, "_ensure_uploader", lambda: None)

    posted = []
    monkeypatch.setattr(t, "_post", lambda url, events: (posted.append((url, list(events))) or True))
    t.posted = posted        # 方便测试断言
    return t
