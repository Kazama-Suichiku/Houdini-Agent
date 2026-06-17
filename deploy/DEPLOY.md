# Houdini Agent 主页 — 腾讯云轻量服务器部署指南

环境：Tencent 轻量应用服务器 · Ubuntu 22.04/24.04 · 新加坡（免备案）
站点：纯静态（`website/index.html` + `assets/`，约 184 KB），已预留 `/api` 反代给将来的后端。

你已经有的：服务器、域名、SSL 证书、DNS 解析。下面把它们接起来。

---

## 需要先确认的 3 件事

1. **服务器公网 IP** —— 控制台「轻量应用服务器 → 实例」里看。
2. **SSH 登录用户名** —— 腾讯轻量 Linux 默认通常是 `ubuntu`（Ubuntu 镜像）或 `root`，以控制台「重置密码 / 管理密钥」页显示为准。下文统一写 `ubuntu`，按你的实际改。
3. **SSL 证书是 Nginx 格式** —— 在「SSL 证书」控制台下载，选 **Nginx** 类型，解压后有两个文件：
   - `xxx_bundle.crt`（完整证书链）
   - `xxx.key`（私钥）

> 域名下文用 `houdini-agent.com` 占位，全部替换成你的真实域名。

---

## 第 1 步：DNS 解析（在你买域名/解析的控制台做）

加两条记录，指向服务器公网 IP：

| 主机记录 | 类型 | 记录值 |
|---------|------|--------|
| `@`     | A    | 你的服务器公网 IP |
| `www`   | A    | 你的服务器公网 IP |

保存后等几分钟生效。验证（本机）：`ping houdini-agent.com` 看是否解析到你的 IP。

---

## 第 2 步：放行端口（关键，容易漏）

腾讯轻量有**自己的防火墙**（在控制台，不是只有系统 ufw）：
控制台 → 实例 → **防火墙** → 添加规则，放行：

- TCP **80**（HTTP）
- TCP **443**（HTTPS）
- TCP **22**（SSH，通常默认已开）

不开这两个端口，外网访问不到。

---

## 第 3 步：初始化服务器（装 Nginx + 写配置）

连服务器，两种方式任选其一：
- **控制台 OrcaTerm**：实例页点「登录」，直接在网页终端里操作（最省事，不用配 SSH）。
- **本机 SSH**：`ssh -i 你的密钥 ubuntu@你的IP`

然后把本仓库 `deploy/server-setup.sh` 拷上去运行（或直接把内容粘进去）：

```bash
# 域名已预填为 houdini-agent.com，直接运行即可
sudo bash server-setup.sh
```

脚本会装好 Nginx、建好目录 `/var/www/houdini-agent`、写好站点配置并启用。
**此时先别 reload**——证书还没上去。

---

## 第 4 步：上传 SSL 证书

把腾讯下载的 Nginx 证书改名上传到 `/etc/nginx/ssl/`，文件名要和域名对应：

**在本机 Git Bash 里**（scp 上传）：
```bash
scp -i 你的密钥 路径/xxx_bundle.crt  ubuntu@你的IP:/tmp/houdini-agent.com.crt
scp -i 你的密钥 路径/xxx.key         ubuntu@你的IP:/tmp/houdini-agent.com.key
```
**在服务器上**（移到位 + 收紧权限）：
```bash
sudo mv /tmp/houdini-agent.com.crt /etc/nginx/ssl/houdini-agent.com.crt
sudo mv /tmp/houdini-agent.com.key /etc/nginx/ssl/houdini-agent.com.key
sudo chmod 600 /etc/nginx/ssl/houdini-agent.com.key
```
> 没装 SSH 也行：用控制台 OrcaTerm 的「文件上传」传到 `/tmp`，再 mv。

---

## 第 5 步：上传网页文件

把本仓库 `website/` 里的内容传到 `/var/www/houdini-agent/`。

**在本机 Git Bash 里**：
```bash
cd /c/Users/Administrator/Desktop/Houdini-Agent
scp -i 你的密钥 -r website/* ubuntu@你的IP:/tmp/site/
```
**在服务器上**：
```bash
sudo rm -f /var/www/houdini-agent/index.html
sudo cp -r /tmp/site/* /var/www/houdini-agent/
sudo chown -R www-data:www-data /var/www/houdini-agent
```
传完目录应是：
```
/var/www/houdini-agent/index.html
/var/www/houdini-agent/assets/ui-main.png
```

---

## 第 6 步：生效 + 验证

```bash
sudo nginx -t                 # 配置语法检查，必须 OK
sudo systemctl reload nginx   # 生效
sudo systemctl enable nginx   # 开机自启（默认已开）
```

浏览器打开 `https://houdini-agent.com`：
- 地址栏是锁标（证书生效）
- `http://houdini-agent.com` 自动跳到 https
- `https://www.houdini-agent.com` 也能打开

---

## 常见问题

- **打不开** → 八成是第 2 步端口没放行；其次 DNS 没生效（`ping` 域名看 IP）。
- **`nginx -t` 报证书找不到** → 检查 `/etc/nginx/ssl/` 下文件名是否和配置里 `houdini-agent.com.crt/.key` 完全一致。
- **证书不受信任/不匹配** → 用的不是 Nginx 格式的 `_bundle.crt`（完整链），重下证书选 Nginx。
- **改完配置** → 永远先 `nginx -t` 再 `systemctl reload nginx`。
- 看日志：`sudo tail -f /var/log/nginx/error.log`

---

## 将来上后端时（已预留）

1. 后端跑在本地端口（如 `127.0.0.1:8000`），**不直接对外**，用 Docker 跑最干净。
2. 编辑 `/etc/nginx/sites-available/houdini-agent.conf`，取消 `location /api/ { ... }` 那段的注释。
3. `sudo nginx -t && sudo systemctl reload nginx`。
4. 前端调用 `https://houdini-agent.com/api/...`，和主页共用一个域名一张证书，无跨域。

栈建议（你说计算量不大）：FastAPI(Python) 或 Node/Express + SQLite/小型 Postgres，全部 Docker。到时告诉我要做什么功能，我帮你搭。
