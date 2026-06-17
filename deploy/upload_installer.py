#!/usr/bin/env python3
"""
把安装程序上传到网站的 /download/ 目录（独立于网页，部署网页不会清空它）。
用法：
    HA_SSH_PASS='密码' python deploy/upload_installer.py "dist_installer/HoudiniAgent-Setup-1.5.7.exe"
默认在服务器上重命名为 HoudiniAgent-Setup.exe（稳定下载链接），可用 HA_DL_NAME 覆盖。
下载地址：https://houdini-agent.com/download/HoudiniAgent-Setup.exe
"""
import os, sys, paramiko

if len(sys.argv) < 2 or not os.path.isfile(sys.argv[1]):
    sys.exit("用法: HA_SSH_PASS=... python deploy/upload_installer.py <本地 setup.exe 路径>")

LOCAL = sys.argv[1]
HOST  = os.environ.get("HA_SSH_HOST", "43.160.222.28")
USER  = os.environ.get("HA_SSH_USER", "ubuntu")
PASS  = os.environ["HA_SSH_PASS"]
NAME  = os.environ.get("HA_DL_NAME", "HoudiniAgent-Setup.exe")
DLDIR = "/var/www/ha-downloads"

c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, 22, USER, PASS, timeout=20, look_for_keys=False, allow_agent=False)

def run(cmd):
    i,o,e = c.exec_command(cmd); rc = o.channel.recv_exit_status()
    err = e.read().decode(errors="replace").strip()
    if rc != 0 and err: print("  ERR", err)
    return rc

run(f"sudo mkdir -p {DLDIR}")
sftp = c.open_sftp()
size = os.path.getsize(LOCAL)
print(f"上传 {os.path.basename(LOCAL)} ({size//1024//1024} MB) ...")
sftp.put(LOCAL, f"/tmp/{NAME}")
run(f"sudo mv /tmp/{NAME} {DLDIR}/{NAME} && sudo chown www-data:www-data {DLDIR}/{NAME}")
sftp.close(); c.close()
print(f"完成：https://houdini-agent.com/download/{NAME}")
