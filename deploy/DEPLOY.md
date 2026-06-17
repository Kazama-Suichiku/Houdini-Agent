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

---

## 安装包下载走 COS + CDN（提速、卸掉源站带宽压力）

**为什么**：安装包目前由轻量服务器的 Nginx 从 `/var/www/ha-downloads/` 直出，受这台机器的**出口带宽峰值**（实测约 38 Mbps / 4.5 MB/s）封顶——所有下载共享这一个上限，包越大越慢。把安装包放到**对象存储 COS + CDN**，源站不再被带宽卡住，国内用户走边缘节点，明显更快。

> 已做的另一半优化：安装包已从 ~181 MB 瘦身到 ~70 MB（排掉 PyInstaller 打进来但用不到的 WebEngine/Multimedia/FFmpeg/Pdf/Quick3D 等 Qt 模块），下载时间直接少一多半。

### 一次性：在腾讯云控制台准备资源

1. **建存储桶（COS）**：对象存储 → 创建存储桶，地域就近（如 `ap-singapore` 或 `ap-guangzhou`），访问权限选**公有读私有写**。记下桶名（含 APPID 后缀，如 `ha-downloads-1300000000`）和地域。
2. **拿访问密钥**：访问管理 CAM → API 密钥，创建 `SecretId` / `SecretKey`（建议用子账号，只授 COS 该桶 + CDN 刷新权限）。**密钥只放环境变量，绝不写进代码或提交到 git。**
3. **绑 CDN 加速域名（可选但推荐）**：内容分发 CDN → 添加域名（如 `dl.houdini-agent.com`），源站类型选**「COS 源」指向上面的桶**；按提示在 DNS 加一条 CNAME 指到 CDN 给的地址；给该子域配 HTTPS 证书。

### 每次发版：上传安装包

本机装一次依赖（服务器不用）：
```bash
pip install cos-python-sdk-v5
pip install tencentcloud-sdk-python-cdn   # 可选，用于自动刷新 CDN 缓存
```
设环境变量后运行（密钥走 env，不进命令历史）：
```bash
export COS_SECRET_ID=...   COS_SECRET_KEY=...
export COS_REGION=ap-singapore
export COS_BUCKET=ha-downloads-1300000000
export HA_CDN_BASE=https://dl.houdini-agent.com   # 没绑 CDN 就不设，脚本会给 COS 直链
python deploy/upload_installer_cos.py "dist_installer/HoudiniAgent-Setup-2.0.0.exe"
```
脚本会上传（公有读 + 强制下载头）、可选刷新 CDN 缓存，并打印**最终下载 URL**。

### 把主页下载按钮切到 CDN

把上一步打印的 URL 填进 `website/index.html` 顶部脚本里的这一行（**全站只改这一处**），再重新部署网页即可：
```js
window.HA_DOWNLOAD_URL = "https://dl.houdini-agent.com/download/HoudiniAgent-Setup.exe";
```
> 不接 CDN 时保持默认 `"/download/HoudiniAgent-Setup.exe"`（继续走源站 Nginx，照常可用）。两种方式并存，随时切换、可回退。

---

## 安装时的 SmartScreen「发布者未知」警告

Windows 弹「已保护你的电脑 / 无法识别的应用 / 发布者未知」，是因为安装包**没有用受信任的代码签名证书签名**，SmartScreen 对没声誉的文件一律拦一下。这不是病毒报警，但确实会吓退用户。按效果/成本排序的解决方案：

1. **EV 代码签名证书（最佳，开箱即信）**：扩展验证（EV）证书签名后，SmartScreen **从第一份就不再弹警告**。需公司主体（Meshy 有），约 ¥2000–4000/年 + 硬件 U 盾。签名后 publisher 显示成「Meshy」而不是「未知」。
2. **Azure Trusted Signing（现代、便宜，推荐先看这个）**：微软自家的云签名服务，约 $9.99/月，同样能获得 SmartScreen 声誉，无需买硬件 U 盾。要求公司主体满足验证条件。性价比最高。
3. **OV 证书（便宜些，但需要养声誉）**：组织验证（OV）证书约 ¥1000+/年，签名后**初期仍可能弹警告**，随下载量累积、同一证书的声誉上升后逐渐消失。比 EV 差在没有即时声誉。
4. **不签名时的缓解**：在主页/下载页放 **SHA256 校验值**让用户自检完整性；写一句引导「点『更多信息 → 仍要运行』」；走 GitHub Releases 分发。注意：**自签名证书对 SmartScreen 无效**（不受信任），别浪费精力。

> 建议：公司产品直接上 **Azure Trusted Signing 或 EV 证书**。拿到证书后告诉我，我把 `signtool` 签名步骤加进 `build_installer.ps1`（对 exe 和最终安装包各签一次），以后每次构建自动签。
