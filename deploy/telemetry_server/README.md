# Meshy 用量埋点后端 (ha-telemetry)

统计【通过 Houdini Agent 使用 Meshy API】累计消耗了多少 credits。

## 组成

| 文件 | 作用 |
|------|------|
| `server.py` | 纯标准库 HTTP 服务（http.server + sqlite3），**无任何 pip 依赖**。监听 `127.0.0.1:8000`。 |
| `ha-telemetry.service` | systemd 服务单元，开机自启 + 崩溃自动重启。数据库在 `/var/lib/ha-telemetry/telemetry.db`。 |
| `deploy_telemetry.py` | 一键部署：上传后端 + 装服务 + 更新 Nginx（启用 `/api/` 反代）+ 自检。 |

Nginx 把 `https://houdini-agent.com/api/` 反代到本地 `127.0.0.1:8000`（见 `../nginx-site.conf`）。

## 客户端

`houdini_agent/meshy/telemetry.py` 在每个 Meshy 任务成功时记录一条事件，本地 spool 后台批量上传。
默认端点 `https://houdini-agent.com/api/telemetry`，可用环境变量 `HAGENT_TELEMETRY_URL` 或
`houdini_ai.ini` 的 `telemetry_url` 覆盖。关闭：`HAGENT_TELEMETRY_OFF=1` 或 ini `telemetry_optout:1`。

上报字段：匿名安装 ID、时间戳、版本、能力类型(kind)、task_id、ai_model、mode、credits、prompt。
**不含** API key、不含账号。按 `event_id` 去重，重复上传不会重复计数。

## 部署

```bash
HA_SSH_PASS='服务器密码' python deploy/telemetry_server/deploy_telemetry.py
```

## 接口

- `POST /api/telemetry` — body `{"events":[ {...}, ... ]}`，返回 `{"ok":true,"accepted":N,"received":M}`
- `GET  /api/telemetry/stats` — `{"total_credits","total_events","distinct_installs","by_kind":{...}}`
- `GET  /api/health` — `{"ok":true}`

## 运维

```bash
sudo systemctl status ha-telemetry          # 状态
sudo journalctl -u ha-telemetry -f          # 日志
curl -s localhost:8000/api/telemetry/stats  # 当前累计
sqlite3 /var/lib/ha-telemetry/telemetry.db 'SELECT SUM(credits) FROM events;'
```
