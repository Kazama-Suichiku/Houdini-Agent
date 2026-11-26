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
    ap.add_argument("--app-zip", default="", help="可选：全量静默更新包 HoudiniAgent-App-<ver>.zip 路径")
    ap.add_argument("--patch-zip", default="", help="可选：轻量化增量补丁包 HoudiniAgent-Patch-<ver>.zip 路径")
    ap.add_argument("--runtime-hash", default="", help="可选：冻结运行时指纹（默认从 dist/Houdini Agent/_internal/RUNTIME_HASH 读取）")
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

    # 若同目录存在静默更新包 HoudiniAgent-App-<ver>.zip 且线上已上传同大小文件，则写入 app_zip 字段
    app_zip_path = a.app_zip or os.path.join(os.path.dirname(os.path.abspath(path)),
                                             "HoudiniAgent-App-%s.zip" % ver)
    if app_zip_path and os.path.isfile(app_zip_path):
        zsize = os.path.getsize(app_zip_path)
        zh = hashlib.sha256()
        with open(app_zip_path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                zh.update(chunk)
        zurl = SITE + "HoudiniAgent-App-%s.zip" % ver
        try:
            zreq = urllib.request.Request(zurl, method="HEAD", headers={"User-Agent": "ha-publish"})
            with urllib.request.urlopen(zreq, timeout=30) as zr:
                zremote_len = int(zr.headers.get("Content-Length") or 0)
            if zremote_len == zsize:
                manifest["app_zip"] = {"url": zurl, "size": zsize, "sha256": zh.hexdigest()}
                print("附带静默更新包 %s (%d bytes, sha256 %s…)" % (zurl, zsize, zh.hexdigest()[:12]))
            else:
                print("警告：线上 %s 大小 %d 与本地 %d 不一致，跳过 app_zip" % (zurl, zremote_len, zsize))
        except Exception as ze:
            print("提示：线上未检测到 %s（%s），本次清单仅写 installer" % (zurl, ze))

    # 若同目录存在增量补丁包 HoudiniAgent-Patch-<ver>.zip 且线上已上传，则写入 patch_zip（含 runtime_hash）
    patch_zip_path = a.patch_zip or os.path.join(os.path.dirname(os.path.abspath(path)),
                                                 "HoudiniAgent-Patch-%s.zip" % ver)
    runtime_hash = (a.runtime_hash or "").strip()
    if not runtime_hash:
        rh_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "dist", "Houdini Agent", "_internal", "RUNTIME_HASH")
        if os.path.isfile(rh_file):
            runtime_hash = open(rh_file, encoding="utf-8").read().strip()
    if patch_zip_path and os.path.isfile(patch_zip_path) and runtime_hash:
        psize = os.path.getsize(patch_zip_path)
        ph = hashlib.sha256()
        with open(patch_zip_path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                ph.update(chunk)
        purl = SITE + "HoudiniAgent-Patch-%s.zip" % ver
        try:
            preq = urllib.request.Request(purl, method="HEAD", headers={"User-Agent": "ha-publish"})
            with urllib.request.urlopen(preq, timeout=30) as pr:
                premote_len = int(pr.headers.get("Content-Length") or 0)
            if premote_len == psize:
                manifest["patch_zip"] = {
                    "url": purl,
                    "size": psize,
                    "sha256": ph.hexdigest(),
                    "runtime_hash": runtime_hash,
                }
                print("附带增量补丁包 %s (%d bytes, sha256 %s…, rt %s…)"
                      % (purl, psize, ph.hexdigest()[:12], runtime_hash[:12]))
            else:
                print("警告：线上 %s 大小 %d 与本地 %d 不一致，跳过 patch_zip" % (purl, premote_len, psize))
        except Exception as pe:
            print("提示：线上未检测到 %s（%s），跳过 patch_zip" % (purl, pe))

    # 可选 Ed25519 非对称签名（设置环境变量 HA_MANIFEST_SIGN_KEY 时自动签署）
    sign_key = (os.environ.get("HA_MANIFEST_SIGN_KEY") or "").strip()
    if sign_key:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)
        from houdini_agent.utils.updater import sign_manifest
        manifest["signature"] = sign_manifest(manifest, sign_key)
        print("已使用 Ed25519 私钥签署 latest.json")

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
