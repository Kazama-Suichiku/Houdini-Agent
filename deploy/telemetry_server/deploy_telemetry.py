#!/usr/bin/env python3
"""
部署 Meshy 用量埋点后端到主站服务器（paramiko，密码从环境变量读取）。

做的事：
  1. 上传 server.py 到 /opt/ha-telemetry/
  2. 安装 systemd 服务 ha-telemetry 并 enable --now（监听 127.0.0.1:8000）
  3. 上传更新后的 nginx-site.conf（已启用 /api/ 反代），nginx -t 后 reload
  4. 自检：本地 curl /api/health 与 /api/telemetry/stats

用法：
    HA_SSH_PASS='密码' python deploy/telemetry_server/deploy_telemetry.py

密码只从环境变量 HA_SSH_PASS 读取，不写进文件。
"""
import os
import sys
import posixpath
import paramiko

HOST = os.environ.get("HA_SSH_HOST", "43.160.222.28")
USER = os.environ.get("HA_SSH_USER", "ubuntu")
PASS = os.environ["HA_SSH_PASS"]
DOMAIN = "houdini-agent.com"

HERE = os.path.dirname(os.path.abspath(__file__))
DEPLOY = os.path.dirname(HERE)
LOCAL_SERVER = os.path.join(HERE, "server.py")
LOCAL_UNIT = os.path.join(HERE, "ha-telemetry.service")
LOCAL_NGINX = os.path.join(DEPLOY, "nginx-site.conf")

for p in (LOCAL_SERVER, LOCAL_UNIT, LOCAL_NGINX):
    if not os.path.isfile(p):
        sys.exit("missing local file: " + p)

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, 22, USER, PASS, timeout=20, look_for_keys=False, allow_agent=False)
print("[connected] %s@%s" % (USER, HOST))


def run(cmd, timeout=300, allow_fail=False):
    i, o, e = c.exec_command(cmd, timeout=timeout)
    rc = o.channel.recv_exit_status()
    out = o.read().decode(errors="replace").strip()
    err = e.read().decode(errors="replace").strip()
    short = cmd if len(cmd) < 72 else cmd[:69] + "..."
    print("  [%s] %s" % ("ok" if rc == 0 else "rc=%d" % rc, short))
    if out:
        print("        " + out.replace("\n", "\n        "))
    if rc != 0 and err:
        print("    ERR " + err.replace("\n", "\n        "))
    if rc != 0 and not allow_fail:
        c.close()
        sys.exit("command failed: " + cmd)
    return rc, out, err


sftp = c.open_sftp()


def put(local, remote):
    sftp.put(local, remote)
    print("  [up] " + posixpath.basename(remote))


print("== 1. 上传后端 server.py ==")
run("sudo mkdir -p /opt/ha-telemetry && sudo chown -R %s:%s /opt/ha-telemetry" % (USER, USER))
put(LOCAL_SERVER, "/opt/ha-telemetry/server.py")

print("== 2. 安装 systemd 服务 ==")
run("rm -rf /tmp/ha_tel && mkdir -p /tmp/ha_tel")
put(LOCAL_UNIT, "/tmp/ha_tel/ha-telemetry.service")
run("sudo cp /tmp/ha_tel/ha-telemetry.service /etc/systemd/system/ha-telemetry.service")
run("sudo systemctl daemon-reload")
run("sudo systemctl enable --now ha-telemetry")
run("sleep 1 && systemctl is-active ha-telemetry", allow_fail=True)
run("curl -s -o /dev/null -w 'backend /api/health -> %{http_code}\\n' "
    "http://127.0.0.1:8000/api/health", allow_fail=True)

print("== 3. 更新 Nginx（启用 /api/ 反代）==")
put(LOCAL_NGINX, "/tmp/ha_tel/houdini-agent.conf")
run("sudo cp /tmp/ha_tel/houdini-agent.conf /etc/nginx/sites-available/houdini-agent.conf")
run("sudo nginx -t")
run("sudo systemctl reload nginx")
run("rm -rf /tmp/ha_tel")

print("== 4. 自检 ==")
# 注意：curl -w 的 %{http_code} 是 curl 字面量，不能用 Python % 格式化，故用字符串拼接
run("curl -sk -o /dev/null -w 'https /api/health -> %{http_code}\\n' "
    "https://" + DOMAIN + "/api/health --resolve " + DOMAIN + ":443:127.0.0.1",
    allow_fail=True)
run("curl -sk https://" + DOMAIN + "/api/telemetry/stats --resolve "
    + DOMAIN + ":443:127.0.0.1", allow_fail=True)

sftp.close()
c.close()
print("\n埋点后端部署完成。客户端默认上报到 https://%s/api/telemetry" % DOMAIN)
