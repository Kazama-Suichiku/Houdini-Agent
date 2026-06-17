#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成 Houdini Agent 的应用图标 assets/houdini-agent.ico（多尺寸）。
品牌：Mono Editorial —— 近黑圆角底 #0d0d0d + 米色菱形钻石 #e8e2d4（呼应网站 ◇ mark）。
高分辨率渲染再下采样，得到平滑抗锯齿的小尺寸位图。
用法：python tools/make_app_icon.py
"""
import os
from PIL import Image, ImageDraw

S = 1024
BG = (13, 13, 13, 255)        # #0d0d0d
CREAM = (232, 226, 212, 255)  # #e8e2d4

img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# 圆角近黑底
r = int(S * 0.18)
d.rounded_rectangle([0, 0, S - 1, S - 1], radius=r, fill=BG)

# 米色钻石轮廓（粗菱形环，呼应网站 ◇ mark；环够粗，小尺寸也清晰）
c = S // 2
R = int(S * 0.34)
d.polygon([(c, c - R), (c + R, c), (c, c + R), (c - R, c)], fill=CREAM)
ri = int(R * 0.46)   # 中心挖一个小深菱形 → 形成菱形环
d.polygon([(c, c - ri), (c + ri, c), (c, c + ri), (c - ri, c)], fill=BG)

os.makedirs("assets", exist_ok=True)
out = os.path.join("assets", "houdini-agent.ico")
img.save(out, sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
print("wrote", out)
# 也存一张 PNG 预览
img.resize((256, 256), Image.LANCZOS).save(os.path.join("assets", "houdini-agent-256.png"))
print("wrote assets/houdini-agent-256.png")
