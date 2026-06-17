#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把安装程序上传到腾讯云 COS（对象存储），让下载走 COS / CDN，而不是被轻量服务器的
出口带宽峰值卡住。配合 CDN 域名，国内用户走边缘节点，比直连源站快很多。

依赖（仅本机需要，服务器不用装）：
    pip install cos-python-sdk-v5
    # 可选，若要顺带刷新 CDN 缓存：
    pip install tencentcloud-sdk-python-cdn

用法（密钥只走环境变量，绝不写进文件/命令历史里硬编码）：
    export COS_SECRET_ID=...        # 腾讯云访问密钥 SecretId
    export COS_SECRET_KEY=...       # 腾讯云访问密钥 SecretKey
    export COS_REGION=ap-singapore  # 存储桶地域，如 ap-singapore / ap-guangzhou
    export COS_BUCKET=ha-downloads-1300000000   # 存储桶名（含 APPID 后缀）
    # 可选：
    export HA_CDN_BASE=https://dl.houdini-agent.com   # 绑定到桶的 CDN 加速域名（带 https://）
    export HA_DL_KEY=download/HoudiniAgent-Setup.exe  # 对象键，默认即此
    python deploy/upload_installer_cos.py "dist_installer/HoudiniAgent-Setup-2.0.0.exe"

上传成功后会打印最终下载 URL —— 把它填到 website 的下载常量（window.HA_DOWNLOAD_URL）即可。
"""
import os
import sys

if len(sys.argv) < 2 or not os.path.isfile(sys.argv[1]):
    sys.exit("用法: COS_SECRET_ID=... COS_SECRET_KEY=... COS_REGION=... COS_BUCKET=... "
             "python deploy/upload_installer_cos.py <本地 setup.exe 路径>")

LOCAL = sys.argv[1]

try:
    SECRET_ID = os.environ["COS_SECRET_ID"]
    SECRET_KEY = os.environ["COS_SECRET_KEY"]
    REGION = os.environ["COS_REGION"]
    BUCKET = os.environ["COS_BUCKET"]
except KeyError as e:
    sys.exit(f"缺少环境变量 {e}. 需要 COS_SECRET_ID / COS_SECRET_KEY / COS_REGION / COS_BUCKET")

KEY = os.environ.get("HA_DL_KEY", "download/HoudiniAgent-Setup.exe")
CDN_BASE = os.environ.get("HA_CDN_BASE", "").rstrip("/")

try:
    from qcloud_cos import CosConfig, CosS3Client
except ImportError:
    sys.exit("未安装 COS SDK：请先 `pip install cos-python-sdk-v5`")

cfg = CosConfig(Region=REGION, SecretId=SECRET_ID, SecretKey=SECRET_KEY, Scheme="https")
client = CosS3Client(cfg)

size_mb = os.path.getsize(LOCAL) // 1024 // 1024
print(f"上传 {os.path.basename(LOCAL)} ({size_mb} MB) → cos://{BUCKET}/{KEY} ...")

# 分块上传，强制下载（attachment）+ 正确的二进制类型；公有读以便直链下载
client.upload_file(
    Bucket=BUCKET,
    Key=KEY,
    LocalFilePath=LOCAL,
    EnableMD5=False,
    ACL="public-read",
    ContentType="application/octet-stream",
    ContentDisposition="attachment",
)

cos_url = f"https://{BUCKET}.cos.{REGION}.myqcloud.com/{KEY}"
final_url = f"{CDN_BASE}/{KEY}" if CDN_BASE else cos_url
print("上传完成。")
print("  COS 直链 :", cos_url)
if CDN_BASE:
    print("  CDN 地址 :", final_url, "(把它填进 website 的 window.HA_DOWNLOAD_URL)")
else:
    print("  下载地址 :", final_url, "(把它填进 website 的 window.HA_DOWNLOAD_URL)")

# ---- 可选：刷新 CDN 缓存（覆盖同名文件后，让边缘节点拉新版本） ----
if CDN_BASE:
    try:
        from tencentcloud.common import credential
        from tencentcloud.cdn.v20180606 import cdn_client, models
        cred = credential.Credential(SECRET_ID, SECRET_KEY)
        cli = cdn_client.CdnClient(cred, "")
        req = models.PurgeUrlsCacheRequest()
        req.Urls = [final_url]
        cli.PurgeUrlsCache(req)
        print("  已提交 CDN 缓存刷新:", final_url)
    except ImportError:
        print("  (未装 tencentcloud-sdk-python-cdn，跳过 CDN 刷新；"
              "可在控制台手动刷新该 URL，或 `pip install tencentcloud-sdk-python-cdn`)")
    except Exception as e:
        print("  CDN 刷新失败（不影响上传），请在控制台手动刷新:", str(e)[:200])
