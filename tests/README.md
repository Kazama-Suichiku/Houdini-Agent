# 自动化测试

离线单元测试套件，**不需要 Houdini(hou)、不需要 Meshy API Key、不发任何网络请求**。

## 运行

```bash
pip install pytest requests
pytest                 # 跑全部
pytest -q tests/test_meshy_telemetry.py    # 跑单个文件
pytest -k telemetry    # 按名字筛
```

CI 在 push / PR 时自动跑（`.github/workflows/tests.yml`，Python 3.11–3.13）。

## 隔离机制（见 `conftest.py`）

- **路径隔离**：把 `shared.common_utils.get_repo_root` 重定向到临时目录，
  所以 `config/` 与 `cache/` 的所有读写都落在 tmp，绝不碰真实文件。
- **环境隔离**：每个用例前清掉 `MESHY_API_KEY` / `*_TELEMETRY_*` 等环境变量。
- **禁网**：`telemetry_mod` 夹具把 `_post` 打桩为捕获器，并阻止后台上传线程。
- **hou 桩**：提供最小 `hou` 模块兜底，防止意外 import 失败。

## 覆盖范围

| 文件 | 覆盖 |
|------|------|
| `test_shared_config.py` | 配置/历史读写、路径隔离 |
| `test_meshy_config.py` | API Key 解析(env>ini)、脱敏、写入/清除 |
| `test_meshy_schemas.py` | 工具 schema 合法性、工具集合自洽 |
| `test_meshy_telemetry.py` | 事件构建、spool 往返、去重、opt-out、上传 |
| `test_telemetry_server.py` | 后端去重入库、统计聚合、HTTP 端到端 |
| `test_tool_registry.py` | 注册、模式访问控制、启用/禁用 |
| `test_bridge_protocol.py` | `_diff_children`、BridgeClient JSON-lines 协议 |

## 尚未覆盖（需 Houdini 环境，后续补）

`hou.*` 节点操作、QML UI、Meshy 真实 API 调用、controller 信号流——这些依赖
Houdini 运行时或外部服务，需用 Houdini 内的 `hython` 跑集成测试或引入 mock-hou 层。
