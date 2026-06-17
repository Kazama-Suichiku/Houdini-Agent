#!/usr/bin/env python3
"""
Houdini Agent 主页 — 远程部署脚本（通过 paramiko，密码从环境变量读取）

用法：
    全量（装 Nginx + 证书 + 配置 + 同步网页）：
        HA_SSH_PASS='密码' python deploy/remote_deploy.py
    仅更新网页内容（跳过 Nginx/证书/配置，快）：
        HA_SSH_PASS='密码' HA_DEPLOY_MODE=content python deploy/remote_deploy.py

密码只从环境变量 HA_SSH_PASS 读取，不写进文件。
网页目录 website/ 会被整体递归同步到服务器 webroot。
"""
import os, sys, paramiko, posixpath

HOST   = os.environ.get("HA_SSH_HOST", "43.160.222.28")
USER   = os.environ.get("HA_SSH_USER", "ubuntu")
PASS   = os.environ["HA_SSH_PASS"]
MODE   = os.environ.get("HA_DEPLOY_MODE", "full")   # full | content
DOMAIN = "houdini-agent.com"
WEBROOT= "/var/www/houdini-agent"
SSLDIR = "/etc/nginx/ssl"

HERE    = os.path.dirname(os.path.abspath(__file__))
REPO    = os.path.dirname(HERE)
WEBSITE = os.path.join(REPO, "website")
LOCAL_CONF = os.path.join(HERE, "nginx-site.conf")
CERT_DIR   = r"C:\Users\Administrator\Downloads\ha_cert\houdini-agent.com_nginx"
LOCAL_CRT  = os.path.join(CERT_DIR, "houdini-agent.com_bundle.crt")
LOCAL_KEY  = os.path.join(CERT_DIR, "houdini-agent.com.key")

if not os.path.isdir(WEBSITE):
    sys.exit("website/ not found")

c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, 22, USER, PASS, timeout=20, look_for_keys=False, allow_agent=False)
print(f"[connected] {USER}@{HOST}  mode={MODE}")

def run(cmd, timeout=600, allow_fail=False):
    i,o,e = c.exec_command(cmd, timeout=timeout)
    rc = o.channel.recv_exit_status()
    out = o.read().decode(errors="replace").strip()
    err = e.read().decode(errors="replace").strip()
    short = cmd if len(cmd) < 72 else cmd[:69] + "..."
    print(f"  [{'ok' if rc==0 else 'rc=%d'%rc}] {short}")
    if out: print("        " + out.replace("\n", "\n        "))
    if rc != 0 and err: print("    ERR " + err.replace("\n", "\n        "))
    if rc != 0 and not allow_fail:
        c.close(); sys.exit("command failed: " + cmd)
    return rc, out, err

sftp = c.open_sftp()
def put(local, remote):
    sftp.put(local, remote); print(f"  [up] {posixpath.basename(remote)}")

# ---------------- structural steps (full mode only) ----------------
if MODE != "content":
    print("== 1. 安装 Nginx ==")
    run("sudo apt-get update -y", timeout=300)
    run("sudo DEBIAN_FRONTEND=noninteractive apt-get install -y nginx", timeout=600)
    run(f"sudo mkdir -p {SSLDIR}")
    run("sudo mkdir -p /var/www/ha-downloads && sudo chown -R www-data:www-data /var/www/ha-downloads")

    print("== 2. 上传证书 + 站点配置 ==")
    for p in (LOCAL_CONF, LOCAL_CRT, LOCAL_KEY):
        if not os.path.isfile(p): sys.exit("missing local file: " + p)
    run("rm -rf /tmp/ha_cfg && mkdir -p /tmp/ha_cfg")
    put(LOCAL_CONF, "/tmp/ha_cfg/houdini-agent.conf")
    put(LOCAL_CRT,  "/tmp/ha_cfg/cert.crt")
    put(LOCAL_KEY,  "/tmp/ha_cfg/cert.key")
    run(f"sudo cp /tmp/ha_cfg/cert.crt {SSLDIR}/{DOMAIN}.crt")
    run(f"sudo cp /tmp/ha_cfg/cert.key {SSLDIR}/{DOMAIN}.key")
    run(f"sudo chmod 600 {SSLDIR}/{DOMAIN}.key")
    run("sudo cp /tmp/ha_cfg/houdini-agent.conf /etc/nginx/sites-available/houdini-agent.conf")
    run("sudo ln -sf /etc/nginx/sites-available/houdini-agent.conf /etc/nginx/sites-enabled/houdini-agent.conf")
    run("sudo rm -f /etc/nginx/sites-enabled/default")
    run("rm -rf /tmp/ha_cfg")

# ---------------- sync website/ recursively (both modes) ----------------
print("== 同步网页文件 (website/ -> webroot) ==")
files, dirs = [], set()
for dp, _, fns in os.walk(WEBSITE):
    for fn in fns:
        full = os.path.join(dp, fn)
        rel  = os.path.relpath(full, WEBSITE).replace("\\", "/")
        files.append((full, rel))
        d = posixpath.dirname(rel)
        if d: dirs.add(d)
run("rm -rf /tmp/ha_site && mkdir -p /tmp/ha_site")
for d in sorted(dirs):
    run(f"mkdir -p /tmp/ha_site/{d}")
for full, rel in files:
    put(full, f"/tmp/ha_site/{rel}")
run(f"sudo rm -rf {WEBROOT} && sudo mkdir -p {WEBROOT}")
run(f"sudo cp -r /tmp/ha_site/. {WEBROOT}/")
run(f"sudo chown -R www-data:www-data {WEBROOT}")
run("rm -rf /tmp/ha_site")
print(f"  synced {len(files)} files")

# ---------------- reload + verify ----------------
print("== 校验 + 生效 ==")
run("sudo nginx -t")
run("sudo systemctl reload nginx")
run("curl -skI -o /dev/null -w 'HTTPS -> %{http_code}\\n' https://" + DOMAIN + " --resolve " + DOMAIN + ":443:127.0.0.1", allow_fail=True)

sftp.close(); c.close()
print("\n部署完成。")
