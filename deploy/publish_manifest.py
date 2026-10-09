#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
发布官网版本清单 /download/latest.json —— 应用内更新检查的主来源。

必须在安装包（带版本号的那份）上传完成之后运行：脚本会先确认线上的
HoudiniAgent-Setup-<版本>.exe 存在且大小与本地一致，再写清单，保证清单
永远不会指向一个还不存在 / 不完整的文件。

用法：
    HA_SSH_PASS='密码' python deploy/publish_manifest.py "dist_installer/HoudiniAgent-Setup-2.0.17.exe" \\
        --notes "说明首行（会显示在客户端的检查更新对话框里）" [--name "v2.0.17 — 标题"]

版本号取安装包文件名里的版本，并与仓库根 VERSION 核对。
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.request

import paramiko

HOST = os.environ.get("HA_SSH_HOST", "43.160.222.28")
USER = os.environ.get("HA_SSH_USER", "ubuntu")
DLDIR = "/var/www/ha-downloads"
SITE = "https://houdini-agent.com/download/"
REPO_URL = "https://github.com/Kazama-Suichiku/Houdini-Agent"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("installer")
    ap.add_argument("--notes", required=True)
    ap.add_argument("--name", default="")
    a = ap.parse_args()

    path = a.installer
    if not os.path.isfile(path):
        sys.exit("找不到安装包: %s" % path)
    m = re.search(r"HoudiniAgent-Setup-(\d+(?:\.\d+)+)\.exe$", os.path.basename(path))
    if not m:
        sys.exit("文件名应形如 HoudiniAgent-Setup-<版本>.exe")
    ver = m.group(1)
    repo_ver = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "VERSION"),
                    encoding="utf-8").read().strip()
    if ver != repo_ver:
        sys.exit("安装包版本 %s 与仓库 VERSION %s 不一致" % (ver, repo_ver))

    size = os.path.getsize(path)
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    url = SITE + "HoudiniAgent-Setup-%s.exe" % ver

    # 线上安装包必须已就位且大小一致
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "ha-publish"})
    with urllib.request.urlopen(req, timeout=30) as r:
        remote_len = int(r.headers.get("Content-Length") or 0)
    if remote_len != size:
        sys.exit("线上 %s 大小 %d 与本地 %d 不一致，先上传安装包" % (url, remote_len, size))

    manifest = {
        "version": ver,
        "name": a.name or ("v" + ver),
        "notes": a.notes.strip(),
        "published": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "installer": {"url": url, "size": size, "sha256": h.hexdigest()},
        "source_zip": "%s/archive/refs/tags/v%s.zip" % (REPO_URL, ver),
        "release_page": "%s/releases/tag/v%s" % (REPO_URL, ver),
    }
    body = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, 22, USER, os.environ["HA_SSH_PASS"], timeout=20, look_for_keys=False, allow_agent=False)
    sftp = c.open_sftp()
    with sftp.open("/tmp/latest.json", "wb") as f:
        f.write(body)
    sftp.close()
    _i, o, e = c.exec_command("sudo mv /tmp/latest.json %s/latest.json && sudo chown www-data:www-data %s/latest.json"
                              % (DLDIR, DLDIR))
    rc = o.channel.recv_exit_status()
    err = e.read().decode(errors="replace").strip()
    c.close()
    if rc != 0:
        sys.exit("写入清单失败: %s" % err)

    # 回读确认
    with urllib.request.urlopen(SITE + "latest.json?t=%d" % time.time(), timeout=30) as r:
        live = json.loads(r.read().decode("utf-8"))
    if live.get("version") != ver or live.get("installer", {}).get("sha256") != h.hexdigest():
        sys.exit("回读清单不一致: %s" % live)
    print("已发布 %slatest.json -> v%s (%d bytes, sha256 %s…)" % (SITE, ver, size, h.hexdigest()[:12]))


if __name__ == "__main__":
    main()
