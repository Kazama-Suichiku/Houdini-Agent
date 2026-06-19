# -*- coding: utf-8 -*-
"""
Meshy 资产的 Houdini 落地（必须在 Houdini 主线程执行；依赖 hou）。

  - import_3d_asset：File SOP 导入几何 + 搭建 Principled Shader + 指派材质
  - export_node_to_glb：把某个节点的几何用 rop_gltf 导出成 glb（喂给 retexture）

这两个 handler 由 ToolRegistry 注册，HoudiniMCP.execute_tool 对不在派发表内的
工具会回退到这里。结果统一为 {"success", "result", "error"}。
"""

import os

from . import config


def _err(msg):
    return {"success": False, "result": "", "error": str(msg)}


def _ok(msg):
    return {"success": True, "result": msg, "error": ""}


def _find_texture(texture_dir, key):
    """在贴图目录里找以 key 开头的文件（base_color/normal/roughness/metallic）。"""
    if not texture_dir or not os.path.isdir(texture_dir):
        return None
    for fn in os.listdir(texture_dir):
        if fn.lower().startswith(key.lower()):
            return os.path.join(texture_dir, fn)
    return None


def _unique_name(parent, base):
    import hou
    name = base
    i = 1
    while parent.node(name) is not None:
        i += 1
        name = "%s%d" % (base, i)
    return name


def _build_principled(geo_node, texture_dir):
    """在 /mat 下建 principledshader::2.0，接上 PBR 贴图，返回材质节点路径。"""
    import hou
    mat = hou.node("/mat") or hou.node("/shop")
    if mat is None:
        mat = hou.node("/").createNode("mat", "mat")
    shader_name = _unique_name(mat, geo_node.name() + "_mat")
    try:
        shader = mat.createNode("principledshader::2.0", shader_name)
    except hou.OperationFailed:
        shader = mat.createNode("principledshader", shader_name)

    base = _find_texture(texture_dir, "base_color")
    rough = _find_texture(texture_dir, "roughness")
    metal = _find_texture(texture_dir, "metallic")
    normal = _find_texture(texture_dir, "normal")

    def _set(parm, val):
        p = shader.parm(parm)
        if p is not None:
            p.set(val)

    if base:
        _set("basecolor_useTexture", 1)
        _set("basecolor_texture", base)
    if rough:
        _set("rough_useTexture", 1)
        _set("rough_texture", rough)
    if metal:
        _set("metallic_useTexture", 1)
        _set("metallic_texture", metal)
    if normal:
        _set("baseBumpAndNormal_enable", 1)
        _set("baseBumpAndNormal_type", "normal")
        _set("baseNormal_texture", normal)

    try:
        shader.moveToGoodPosition()
    except Exception:
        pass
    return shader.path()


def import_3d_asset(args):
    try:
        import hou
    except Exception:
        return _err("未检测到 Houdini API")

    glb_path = (args.get("glb_path") or "").strip()
    if not glb_path:
        return _err("缺少 glb_path")
    if not os.path.isfile(glb_path):
        return _err("文件不存在: %s" % glb_path)

    texture_dir = args.get("texture_dir")
    build_material = bool(args.get("build_material", True))
    obj = hou.node("/obj")
    base_name = args.get("name") or os.path.splitext(os.path.basename(glb_path))[0]
    # 清洗成合法节点名
    base_name = "".join(c if (c.isalnum() or c == "_") else "_" for c in base_name) or "meshy_asset"

    ext = os.path.splitext(glb_path)[1].lower()
    try:
        geo = obj.createNode("geo", _unique_name(obj, base_name))
        if ext in (".glb", ".gltf"):
            # glTF/GLB 用专用 gltf SOP 读取（File SOP 对 glb 支持不可靠）
            imp = geo.createNode("gltf", "import")
            fp = imp.parm("filename") or imp.parm("file")
        else:
            # fbx / obj / bgeo 等走 File SOP
            imp = geo.createNode("file", "import")
            fp = imp.parm("file")
        if fp is None:
            # 找不到文件路径参数（不同 Houdini 版本 gltf SOP 参数名可能不同）：
            # 直接报错，而不是建一个没读到文件的空节点伪装成功。
            try:
                geo.destroy()
            except Exception:
                pass
            return _err("无法在导入 SOP 上找到文件路径参数（filename/file），"
                        "可能是 Houdini 版本的 gltf SOP 参数名不同；未导入。")
        fp.set(glb_path)
        imp.setDisplayFlag(True)
        imp.setRenderFlag(True)
        try:
            imp.moveToGoodPosition()
        except Exception:
            pass
    except Exception as e:
        return _err("导入失败: %s" % e)

    mat_path = None
    mat_warn = None
    if build_material:
        # 即使没有贴图目录，也建一个（空贴图的）Principled Shader 并指派，
        # 与 schema "build_material 默认 true 会搭建 Principled Shader" 的描述一致。
        try:
            mat_path = _build_principled(geo, texture_dir)
            mp = geo.parm("shop_materialpath")
            if mp is not None and mat_path:
                mp.set(mat_path)
        except Exception as e:
            # 材质失败不致命：几何已导入。用单独的告警字段，不要把错误串当材质路径。
            mat_warn = str(e)

    try:
        geo.setSelected(True, clear_all_selected=True)
    except Exception:
        pass

    msg = "已导入: %s" % geo.path()
    if mat_path:
        msg += "\n材质: %s" % mat_path
        if texture_dir is None:
            msg += "（未提供贴图目录，材质为空白 Principled Shader）"
    if mat_warn:
        msg += "\n注意：材质搭建失败（几何已正常导入）：%s" % mat_warn
    return _ok(msg)


def import_rigged_character(args):
    """把绑定/动画产出的 FBX(带骨架+蒙皮+动画)导入 Houdini（经典 Object 层 FBX 导入）。
    用 hou.hipFile.importFBX 在 /obj 下建出 subnet：bone 层级 + 蒙皮几何(Capture/Deform)
    + 关键帧动画，可直接在视口播放。"""
    try:
        import hou
    except Exception:
        return _err("未检测到 Houdini API")

    fbx_path = (args.get("fbx_path") or "").strip()
    if not fbx_path:
        return _err("缺少 fbx_path")
    if not os.path.isfile(fbx_path):
        return _err("文件不存在: %s" % fbx_path)
    if os.path.splitext(fbx_path)[1].lower() != ".fbx":
        return _err("import_rigged_character 仅接受 .fbx（带骨架/蒙皮/动画）；"
                    "普通静态模型请改用 import_3d_asset。")

    # 记录导入前 /obj 下已有节点，便于在 importFBX 不返回节点时反查新建的 subnet
    obj = hou.node("/obj")
    before = set(c.path() for c in obj.children()) if obj else set()

    try:
        result = hou.hipFile.importFBX(fbx_path)
    except Exception as e:
        return _err("FBX 导入失败: %s" % e)

    subnet = None
    if isinstance(result, (tuple, list)) and result:
        # importFBX 返回 (network_node, message)
        cand = result[0]
        if hasattr(cand, "path"):
            subnet = cand
    elif hasattr(result, "path"):
        subnet = result
    if subnet is None and obj is not None:
        # 兜底：用导入前后差集找新建的根节点
        new = [c for c in obj.children() if c.path() not in before]
        if new:
            subnet = new[0]
    if subnet is None:
        return _err("FBX 导入未返回节点（可能是空 FBX 或不含可导入内容）")

    nm = args.get("name")
    if nm:
        safe = "".join(c if (c.isalnum() or c == "_") else "_" for c in str(nm))
        if safe:
            try:
                subnet.setName(_unique_name(subnet.parent(), safe), unique_name=True)
            except Exception:
                pass

    try:
        subnet.moveToGoodPosition()
    except Exception:
        pass
    try:
        subnet.setSelected(True, clear_all_selected=True)
    except Exception:
        pass

    info = ""
    n_bone = -1
    try:
        kids = subnet.allSubChildren()
        n_bone = sum(1 for n in kids if "bone" in n.type().name().lower())
        info = "（%d 个子节点，含约 %d 根骨骼）" % (len(kids), n_bone)
    except Exception:
        pass
    if n_bone == 0:
        # 没有骨骼：这很可能是个静态网格而非绑定角色，别误导用户"可播放动画"。
        return _ok("已导入: %s %s\n注意：未检测到骨骼，这可能是静态模型而非绑定角色"
                   "（本工具用于带骨架/蒙皮/动画的 FBX；静态模型建议用 import_3d_asset）。"
                   % (subnet.path(), info))
    return _ok("已导入绑定角色: %s %s\n如 FBX 含关键帧，可直接在视口播放动画。"
               % (subnet.path(), info))


def export_node_to_glb(args):
    try:
        import hou
    except Exception:
        return _err("未检测到 Houdini API")

    node_path = (args.get("node_path") or "").strip()
    if not node_path:
        return _err("缺少 node_path")
    node = hou.node(node_path)
    if node is None:
        return _err("节点不存在: %s" % node_path)

    # 解析出一个 SOP 作为导出源
    sop = node
    try:
        if node.childTypeCategory() is not None and node.type().category().name() == "Object":
            disp = node.renderNode() or node.displayNode()
            if disp is not None:
                sop = disp
    except Exception:
        pass

    if sop.type().category().name() != "Sop":
        return _err("无法从 %s 解析出可导出的 SOP 几何" % node_path)

    # 几何为空检查：空几何会导出一个合法但空的 glb，下游 retexture 会白烧 credits。
    try:
        g = sop.geometry()
        if g is not None and len(g.iterPrims()) == 0 and len(g.points()) == 0:
            return _err("节点 %s 的几何为空（无点无面），无法导出有效模型；"
                        "请检查该节点是否已正确 cook 出几何。" % sop.path())
    except Exception:
        pass   # 取不到几何不阻断（可能需要 cook）；交给后续文件检查兜底

    safe = "".join(c if (c.isalnum() or c == "_") else "_" for c in node.name()) or "export"
    dest = os.path.join(config.assets_dir(), "export_%s.glb" % safe)
    # 先删掉同名旧文件：否则本次 cook 失败时，os.path.isfile(dest) 会命中旧文件、误判成功。
    try:
        if os.path.isfile(dest):
            os.remove(dest)
    except Exception:
        pass

    rop = None
    try:
        parent = sop.parent()
        rop = parent.createNode("rop_gltf", _unique_name(parent, "meshy_export"))
        rop.parm("soppath").set(sop.path())
        fp = rop.parm("file") or rop.parm("filename") or rop.parm("sopoutput")
        if fp is None:
            return _err("rop_gltf 缺少输出路径参数")
        fp.set(dest)
        rop.parm("execute").pressButton()
    except Exception as e:
        return _err("导出失败: %s" % e)
    finally:
        if rop is not None:
            try:
                rop.destroy()
            except Exception:
                pass

    if not os.path.isfile(dest):
        return _err("导出未生成文件: %s" % dest)
    return _ok("已导出 glb: %s\n可作为 meshy_retexture 的 model_path。" % dest)
