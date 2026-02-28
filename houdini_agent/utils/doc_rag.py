# -*- coding: utf-8 -*-
"""
Houdini 文档轻量级索引系统（重写版）

替代旧的全量向量化 RAG，采用 **dict 索引** 实现 O(1) 查找：
  - 节点名 → 文档  (from nodes.zip)
  - VEX 函数 → 签名+描述  (from vex.zip)
  - HOM 类/方法 → 签名+描述  (from hom.zip)
  - 知识库 → 分段检索  (from Doc/*.txt)

数据源：Houdini help 目录下的 ZIP 文件（wiki 标记格式） + Doc/*.txt 知识库
"""

import os
import re
import json
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass


# ============================================================
# 数据结构
# ============================================================

@dataclass
class NodeDoc:
    """节点文档"""
    node_type: str          # 内部名, e.g. "attribwrangle"
    context: str            # sop / dop / obj / cop2 / ...
    title: str              # 显示名称
    description: str        # 简要描述 (≤300 chars)
    parameters: list        # [[name, description], ...]  (≤15 条)


@dataclass
class VexDoc:
    """VEX 函数文档"""
    name: str               # 函数名
    signature: str          # 完整签名
    description: str        # 简要描述
    category: str           # 分类, e.g. "attrib", "geo"


@dataclass
class HomDoc:
    """HOM 类/方法文档"""
    name: str               # 完整名称, e.g. "hou.Node"
    doc_type: str           # class / method / function / homclass
    signature: str          # 方法签名
    description: str        # 简要描述


@dataclass
class KnowledgeChunk:
    """知识库文档片段"""
    title: str              # 小节标题
    content: str            # 小节内容 (≤2000 chars)
    source: str             # 来源文件名
    keywords: List[str]     # 关键词列表 (小写)


# ============================================================
# 核心：轻量级文档索引
# ============================================================

class HoudiniDocIndex:
    """Houdini 文档轻量级索引

    使用 dict 实现 O(1) 查找，替代全量向量化。
    索引来源：$HFS/houdini/help 目录下的 ZIP 文件。
    """

    def __init__(self, help_dir: Optional[str] = None):
        self._help_dir = self._resolve_help_dir(help_dir)

        # 三大索引
        self.node_index: Dict[str, NodeDoc] = {}
        self.vex_index: Dict[str, VexDoc] = {}
        self.hom_index: Dict[str, HomDoc] = {}

        # 知识库索引
        self.knowledge_chunks: List[KnowledgeChunk] = []

        # 辅助索引
        self._node_aliases: Dict[str, str] = {}         # 别名(小写) → node_type
        self._vex_categories: Dict[str, List[str]] = {}  # category → [func_names]
        self._all_node_types: Optional[set] = None       # 懒初始化

        # 缓存
        project_root = Path(__file__).parent.parent.parent
        self._cache_dir = project_root / "cache" / "doc_index"
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._doc_dir = project_root / "Doc"

        self._load_or_build()
        self._load_knowledge_base()

    # ==========================================================
    # 帮助目录发现
    # ==========================================================

    @staticmethod
    def _resolve_help_dir(help_dir: Optional[str]) -> Optional[Path]:
        """自动发现文档目录（含 ZIP 文件）

        查找顺序：
        1. 显式传入的路径
        2. 项目内置 Doc/ 目录（随项目分发，确保任何电脑可用）
        3. 环境变量 HFS / hou 模块
        4. 常见 Windows 安装路径
        """
        REQUIRED_ZIPS = ("nodes.zip", "vex.zip", "hom.zip")

        def _has_zips(d: Path) -> bool:
            return d.is_dir() and any((d / z).exists() for z in REQUIRED_ZIPS)

        # 0. 显式路径
        if help_dir:
            p = Path(help_dir)
            if _has_zips(p):
                return p

        # 1. 项目内置 Doc/ 目录（优先——保证跨机器可用）
        project_root = Path(__file__).parent.parent.parent
        bundled = project_root / "Doc"
        if _has_zips(bundled):
            return bundled

        # 2. 环境变量 HFS（Houdini 标准）
        hfs = os.environ.get("HFS")
        if hfs:
            p = Path(hfs) / "houdini" / "help"
            if _has_zips(p):
                return p

        # 3. hou 模块获取
        try:
            import hou  # type: ignore
            hfs_val = hou.getenv("HFS", "")
            if hfs_val:
                p = Path(hfs_val) / "houdini" / "help"
                if _has_zips(p):
                    return p
        except Exception:
            pass

        # 4. 常见 Windows 安装路径
        for drive in ("C", "D", "E"):
            base = Path(f"{drive}:/Program Files/Side Effects Software")
            if base.is_dir():
                for v in sorted(base.glob("Houdini*"), reverse=True):
                    p = v / "houdini" / "help"
                    if _has_zips(p):
                        return p

        return None

    # ==========================================================
    # 索引加载 / 构建 / 缓存
    # ==========================================================

    def _load_or_build(self):
        cache_file = self._cache_dir / "houdini_doc_index.json"

        if cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("help_dir") == str(self._help_dir) and data.get("version") == 2:
                    self._load_from_cache(data)
                    print(f"[DocIndex] 缓存加载: {len(self.node_index)} 节点, "
                          f"{len(self.vex_index)} VEX, {len(self.hom_index)} HOM")
                    return
            except Exception as e:
                print(f"[DocIndex] 缓存失败: {e}")

        if not self._help_dir:
            print("[DocIndex] 未找到 Houdini help 目录，文档索引为空")
            return

        print(f"[DocIndex] 构建索引: {self._help_dir} ...")
        self._build_indexes()

        try:
            self._save_to_cache(cache_file)
            print(f"[DocIndex] 已缓存: {len(self.node_index)} 节点, "
                  f"{len(self.vex_index)} VEX, {len(self.hom_index)} HOM")
        except Exception as e:
            print(f"[DocIndex] 缓存保存失败: {e}")

    def _build_indexes(self):
        """从 ZIP 文件构建所有索引"""
        for name, builder in [("nodes.zip", self._build_node_index),
                               ("vex.zip",   self._build_vex_index),
                               ("hom.zip",   self._build_hom_index)]:
            zp = self._help_dir / name
            if zp.exists():
                print(f"[DocIndex]   解析 {name} ...")
                builder(zp)
        self._build_aliases()

    # ==========================================================
    # 知识库加载（Doc/*.txt 文件）
    # ==========================================================

    def _load_knowledge_base(self):
        """从 Doc/ 目录递归加载 .txt 知识库文件，按 ## 标题分段

        改进:
        1. 递归加载子目录 Doc/**/*.txt
        2. 知识库缓存: 将解析结果序列化到 JSON 缓存
        3. 增量检测: 按文件修改时间判断是否需要重新解析
        """
        if not self._doc_dir or not self._doc_dir.is_dir():
            return

        # 递归发现所有 .txt 文件
        txt_files = sorted(self._doc_dir.rglob("*.txt"))
        if not txt_files:
            return

        kb_cache_file = self._cache_dir / "knowledge_base_cache.json"

        # 构建文件指纹 {相对路径: mtime}
        file_fingerprints = {}
        for txt_path in txt_files:
            rel = txt_path.relative_to(self._doc_dir)
            file_fingerprints[str(rel)] = txt_path.stat().st_mtime

        # 尝试增量加载缓存
        if kb_cache_file.exists():
            try:
                with open(kb_cache_file, "r", encoding="utf-8") as f:
                    cache_data = json.load(f)
                cached_fingerprints = cache_data.get("fingerprints", {})
                # 比较指纹: 完全一致则直接加载缓存
                if cached_fingerprints == file_fingerprints:
                    for chunk_data in cache_data.get("chunks", []):
                        self.knowledge_chunks.append(KnowledgeChunk(
                            title=chunk_data["title"],
                            content=chunk_data["content"],
                            source=chunk_data["source"],
                            keywords=chunk_data["keywords"],
                        ))
                    print(f"[DocIndex] 知识库缓存加载: {len(self.knowledge_chunks)} 个片段 "
                          f"(来自 {len(txt_files)} 个文件)")
                    return
                else:
                    print(f"[DocIndex] 知识库文件变更，重新解析...")
            except Exception as e:
                print(f"[DocIndex] 知识库缓存读取失败: {e}")

        # 全量解析
        for txt_path in txt_files:
            try:
                text = txt_path.read_text(encoding="utf-8")
                # 使用相对路径作为 source（保留子目录信息）
                rel = txt_path.relative_to(self._doc_dir)
                source = str(rel.with_suffix("")).replace("\\", "/")
                chunks = self._parse_txt_sections(text, source)
                self.knowledge_chunks.extend(chunks)
            except Exception as e:
                print(f"[DocIndex] 读取知识库 {txt_path.name} 失败: {e}")

        if self.knowledge_chunks:
            print(f"[DocIndex] 知识库加载: {len(self.knowledge_chunks)} 个片段 "
                  f"(来自 {len(txt_files)} 个文件)")

            # 保存缓存
            try:
                cache_data = {
                    "fingerprints": file_fingerprints,
                    "chunks": [
                        {
                            "title": c.title,
                            "content": c.content,
                            "source": c.source,
                            "keywords": c.keywords,
                        }
                        for c in self.knowledge_chunks
                    ],
                }
                with open(kb_cache_file, "w", encoding="utf-8") as f:
                    json.dump(cache_data, f, ensure_ascii=False, separators=(",", ":"))
                print(f"[DocIndex] 知识库缓存已保存")
            except Exception as e:
                print(f"[DocIndex] 知识库缓存保存失败: {e}")

    @staticmethod
    def _parse_txt_sections(text: str, source: str) -> List[KnowledgeChunk]:
        """将 .txt 文件按 ## 标题分段

        每个以 ## 开头的行作为一个新段落的标题。
        段落内容最多保留 2000 字符。
        """
        chunks: List[KnowledgeChunk] = []
        current_title = ""
        current_lines: List[str] = []

        def _flush():
            if current_title and current_lines:
                content = '\n'.join(current_lines).strip()
                if len(content) > 30:  # 跳过过短的段落
                    # 提取关键词：英文标识符 + 中文词组
                    keywords_en = [w.lower() for w in
                                   re.findall(r'[a-zA-Z_@][a-zA-Z0-9_@.]*', current_title + ' ' + content)
                                   if len(w) >= 2]
                    keywords_cn = re.findall(r'[\u4e00-\u9fff]{2,}', current_title)
                    all_kw = list(set(keywords_en + keywords_cn))
                    chunks.append(KnowledgeChunk(
                        title=current_title,
                        content=content[:2000],
                        source=source,
                        keywords=all_kw[:50],  # 最多50个关键词
                    ))

        for line in text.split('\n'):
            # 匹配 "## 标题" (二级标题)
            m = re.match(r'^##\s+(.+)', line)
            if m:
                title_text = m.group(1).strip()
                # 跳过装饰分隔线 (如 "## ========" 或 "## ------")
                if re.match(r'^[=\-#*~]{3,}$', title_text):
                    continue
                _flush()
                current_title = title_text
                current_lines = []
            else:
                current_lines.append(line)

        _flush()
        return chunks

    def search_knowledge(self, query: str, top_k: int = 3) -> List[dict]:
        """在知识库中搜索与查询匹配的片段"""
        if not self.knowledge_chunks:
            return []

        ql = query.lower()
        # 提取查询中的关键词
        query_words = set(re.findall(r'[a-zA-Z_@][a-zA-Z0-9_@.]*', ql))
        query_cn = set(re.findall(r'[\u4e00-\u9fff]{2,}', query))

        scored: List[tuple] = []
        for chunk in self.knowledge_chunks:
            score = 0.0
            # 英文关键词匹配
            chunk_kw_set = set(chunk.keywords)
            matched = query_words & chunk_kw_set
            score += len(matched) * 0.3
            # 中文关键词匹配
            for cn in query_cn:
                if cn in chunk.title or cn in chunk.content[:200]:
                    score += 0.5
            # 精确子串匹配（标题）
            for w in query_words:
                if len(w) >= 3 and w in chunk.title.lower():
                    score += 0.8
            if score > 0.2:
                scored.append((score, chunk))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = []
        for score, chunk in scored[:top_k]:
            # 截取内容摘要
            snippet = chunk.content[:300]
            if len(chunk.content) > 300:
                snippet += "..."
            results.append({
                "type": "knowledge",
                "name": chunk.title,
                "snippet": f"[知识库] {chunk.title}\n{snippet}",
                "score": min(score, 1.0),
                "source": chunk.source,
            })
        return results

    # --- 缓存序列化 ---

    def _save_to_cache(self, path: Path):
        data = {
            "help_dir": str(self._help_dir),
            "version": 2,
            "nodes": {
                k: {"node_type": v.node_type, "context": v.context,
                     "title": v.title, "description": v.description,
                     "parameters": v.parameters}
                for k, v in self.node_index.items() if "/" not in k
            },
            "vex": {
                k: {"name": v.name, "signature": v.signature,
                     "description": v.description, "category": v.category}
                for k, v in self.vex_index.items()
            },
            "hom": {
                k: {"name": v.name, "doc_type": v.doc_type,
                     "signature": v.signature, "description": v.description}
                for k, v in self.hom_index.items()
            },
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))

    def _load_from_cache(self, data: dict):
        for k, v in data.get("nodes", {}).items():
            doc = NodeDoc(**v)
            self.node_index[k] = doc
            if v.get("context"):
                self.node_index[f"{v['context']}/{k}"] = doc

        for k, v in data.get("vex", {}).items():
            self.vex_index[k] = VexDoc(**v)
            if v.get("category"):
                self._vex_categories.setdefault(v["category"], []).append(k)

        for k, v in data.get("hom", {}).items():
            self.hom_index[k] = HomDoc(**v)

        self._build_aliases()

    # ==========================================================
    # Wiki 格式解析器
    # ==========================================================

    @staticmethod
    def _parse_wiki(text: str) -> dict:
        """解析 Houdini wiki 标记格式文档

        格式概要::

            = Title =
            #type: homclass
            #context: sop
            #internal: nodename

            \\"\\"\\"Brief description\\"\\"\\"

            Body text ...

            @parameters
            Param Name:
                Description

            @methods
            ::`methodName(args)`:
                Description
        """
        doc: Dict[str, Any] = {
            "title": "", "type": "", "context": "", "internal": "",
            "description": "", "body": "", "sections": {},
        }

        lines = text.split("\n")
        i, n = 0, len(lines)

        # 跳过空行
        while i < n and not lines[i].strip():
            i += 1

        # = Title =
        if i < n:
            m = re.match(r"^=\s+(.+?)\s+=\s*$", lines[i])
            if m:
                doc["title"] = m.group(1).strip()
                i += 1

        # #key: value 元数据
        while i < n:
            line = lines[i].strip()
            if not line:
                i += 1
                continue
            m = re.match(r"^#(\w+):\s*(.*)", line)
            if m:
                key, val = m.group(1).lower(), m.group(2).strip()
                if key in doc:
                    doc[key] = val
                i += 1
            else:
                break

        # """description"""
        while i < n and not lines[i].strip():
            i += 1
        if i < n and lines[i].strip().startswith('"""'):
            dl = lines[i].strip()
            if dl.endswith('"""') and len(dl) > 6:
                doc["description"] = dl[3:-3].strip()
                i += 1
            else:
                parts = [dl[3:]]
                i += 1
                while i < n:
                    if '"""' in lines[i]:
                        parts.append(lines[i].split('"""')[0])
                        i += 1
                        break
                    parts.append(lines[i])
                    i += 1
                doc["description"] = "\n".join(parts).strip()

        # Body + @sections
        cur_sec = "_body"
        buf: List[str] = []
        while i < n:
            line = lines[i]
            s = line.strip()
            if s.startswith("@") and len(s) > 1 and s[1:].split()[0].isalpha():
                # 保存上一段
                text_block = "\n".join(buf).strip()
                if text_block:
                    if cur_sec == "_body":
                        doc["body"] = text_block
                    else:
                        doc["sections"][cur_sec] = text_block
                cur_sec = s[1:].split()[0]
                buf = []
            else:
                buf.append(line)
            i += 1

        text_block = "\n".join(buf).strip()
        if text_block:
            if cur_sec == "_body":
                doc["body"] = text_block
            else:
                doc["sections"][cur_sec] = text_block

        return doc

    # ==========================================================
    # 节点索引  (nodes.zip)
    # ==========================================================

    def _build_node_index(self, zip_path: Path):
        count = 0
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                for name in zf.namelist():
                    if not name.endswith(".txt") or "/_" in name or name.startswith("_"):
                        continue
                    try:
                        raw = zf.read(name).decode("utf-8", errors="ignore")
                        doc = self._parse_wiki(raw)

                        internal = doc.get("internal", "")
                        context = doc.get("context", "")
                        if not internal:
                            parts = name.replace("\\", "/").split("/")
                            internal = Path(parts[-1]).stem
                            if not context and len(parts) >= 3:
                                context = parts[-2] if parts[-2] != "nodes" else ""
                        if not internal:
                            continue

                        params = self._parse_parameters(
                            doc.get("sections", {}).get("parameters", "")
                        )

                        nd = NodeDoc(
                            node_type=internal,
                            context=context,
                            title=doc.get("title", internal),
                            description=doc.get("description", "")[:300],
                            parameters=params[:15],
                        )
                        # 短名(无context前缀)优先 SOP > OBJ > DOP > 其他
                        _CTX_PRIORITY = {"sop": 0, "obj": 1, "dop": 2, "cop2": 3}
                        existing = self.node_index.get(internal)
                        if existing is None or (
                            _CTX_PRIORITY.get(context, 99) <
                            _CTX_PRIORITY.get(existing.context, 99)
                        ):
                            self.node_index[internal] = nd
                        if context:
                            self.node_index[f"{context}/{internal}"] = nd
                        count += 1
                    except Exception:
                        continue
        except Exception as e:
            print(f"[DocIndex] nodes.zip 失败: {e}")
        print(f"[DocIndex]   -> {count} 节点文档")

    # ==========================================================
    # VEX 索引  (vex.zip)
    # ==========================================================

    def _build_vex_index(self, zip_path: Path):
        count = 0
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                for name in zf.namelist():
                    if not name.endswith(".txt") or "/_" in name:
                        continue
                    try:
                        raw = zf.read(name).decode("utf-8", errors="ignore")
                        doc = self._parse_wiki(raw)
                        func_name = doc.get("internal", "") or Path(name).stem
                        if not func_name or func_name.startswith("_"):
                            continue

                        # 从 body / usage section 提取签名
                        sig_src = (doc.get("body", "") + "\n"
                                   + doc.get("sections", {}).get("usage", ""))
                        sig = ""
                        sig_m = re.search(r"`([^`]+)`", sig_src)
                        if sig_m:
                            sig = sig_m.group(1)

                        parts = name.replace("\\", "/").split("/")
                        cat = parts[-2] if len(parts) >= 2 and parts[-2] != "vex" else ""

                        self.vex_index[func_name] = VexDoc(
                            name=func_name,
                            signature=sig[:200],
                            description=doc.get("description", "")[:200],
                            category=cat,
                        )
                        if cat:
                            self._vex_categories.setdefault(cat, []).append(func_name)
                        count += 1
                    except Exception:
                        continue
        except Exception as e:
            print(f"[DocIndex] vex.zip 失败: {e}")
        print(f"[DocIndex]   → {count} VEX 函数")

    # ==========================================================
    # HOM 索引  (hom.zip)
    # ==========================================================

    def _build_hom_index(self, zip_path: Path):
        count = 0
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                for name in zf.namelist():
                    if not name.endswith(".txt") or "/_" in name:
                        continue
                    try:
                        raw = zf.read(name).decode("utf-8", errors="ignore")
                        doc = self._parse_wiki(raw)
                        title = doc.get("title", "")
                        if not title:
                            title = "hou." + Path(name).stem

                        # 主条目
                        self.hom_index[title] = HomDoc(
                            name=title,
                            doc_type=doc.get("type", "") or "class",
                            signature="",
                            description=doc.get("description", "")[:300],
                        )
                        count += 1

                        # 提取方法
                        methods_text = doc.get("sections", {}).get("methods", "")
                        if methods_text:
                            count += self._extract_hom_methods(title, methods_text)
                    except Exception:
                        continue
        except Exception as e:
            print(f"[DocIndex] hom.zip 失败: {e}")
        print(f"[DocIndex]   → {count} HOM 条目")

    def _extract_hom_methods(self, parent: str, text: str) -> int:
        """从 @methods section 提取方法签名"""
        count = 0
        # 匹配  ::`methodName(self, arg1, arg2)`:  或类似格式
        for m in re.finditer(r"::`(\w+)\(([^)]*)\)`\s*:", text):
            mname = m.group(1)
            margs = m.group(2)
            full = f"{parent}.{mname}"

            # 取紧随其后的缩进行作为描述
            pos = m.end()
            desc_lines = []
            for line in text[pos:].split("\n"):
                stripped = line.strip()
                if not stripped:
                    continue
                if line.startswith("    ") or line.startswith("\t"):
                    desc_lines.append(stripped)
                    if len(desc_lines) >= 2:
                        break
                else:
                    break  # 非缩进行 = 描述结束

            self.hom_index[full] = HomDoc(
                name=full,
                doc_type="method",
                signature=f"{mname}({margs})",
                description=" ".join(desc_lines)[:200],
            )
            count += 1
        return count

    # ==========================================================
    # 参数解析
    # ==========================================================

    @staticmethod
    def _parse_parameters(text: str) -> list:
        """解析 @parameters 段落 → [[name, desc], ...]"""
        params: list = []
        if not text:
            return params
        cur_name = ""
        cur_desc: List[str] = []
        for line in text.split("\n"):
            s = line.strip()
            if not s:
                continue
            # 跳过 wiki include 指令
            if s.startswith(":include") or s.startswith("#include"):
                continue
            if s.endswith(":") and not line.startswith((" ", "\t")):
                if cur_name and not cur_name.startswith(":"):
                    params.append([cur_name, " ".join(cur_desc)[:150]])
                cur_name = s[:-1].strip()
                cur_desc = []
            elif cur_name and (line.startswith("    ") or line.startswith("\t")):
                cur_desc.append(s)
        if cur_name and not cur_name.startswith(":"):
            params.append([cur_name, " ".join(cur_desc)[:150]])
        return params

    # ==========================================================
    # 辅助索引
    # ==========================================================

    def _build_aliases(self):
        """构建别名（用于模糊匹配）"""
        self._node_aliases.clear()
        for ntype, doc in self.node_index.items():
            if "/" in ntype:
                continue
            low_title = doc.title.lower().replace(" ", "")
            self._node_aliases[low_title] = ntype
            self._node_aliases[ntype.lower()] = ntype
        self._all_node_types = {k for k in self.node_index if "/" not in k}

    # ==========================================================
    # 查询 API
    # ==========================================================

    def lookup_node(self, node_type: str) -> Optional[NodeDoc]:
        """精确查找节点"""
        doc = self.node_index.get(node_type)
        if doc:
            return doc
        alias = self._node_aliases.get(node_type.lower().replace(" ", ""))
        return self.node_index.get(alias) if alias else None

    def lookup_vex(self, func_name: str) -> Optional[VexDoc]:
        """精确查找 VEX 函数"""
        return self.vex_index.get(func_name) or self.vex_index.get(func_name.lower())

    def lookup_hom(self, name: str) -> Optional[HomDoc]:
        """精确查找 HOM 类/方法"""
        return self.hom_index.get(name)

    def search(self, query: str, top_k: int = 5, **_kw) -> List[dict]:
        """多策略搜索
        
        Returns:
            [{"type": "node"/"vex"/"hom", "name": str,
              "snippet": str, "score": float}, ...]
        """
        results: List[dict] = []
        ql = query.lower().strip()

        # --- 精确匹配 ---
        node = self.lookup_node(ql)
        if node:
            results.append({"type": "node", "name": node.node_type,
                            "snippet": self._fmt_node(node), "score": 1.0})
        vex = self.lookup_vex(ql)
        if vex:
            results.append({"type": "vex", "name": vex.name,
                            "snippet": self._fmt_vex(vex), "score": 1.0})
        hom = self.lookup_hom(query)
        if hom:
            results.append({"type": "hom", "name": hom.name,
                            "snippet": self._fmt_hom(hom), "score": 1.0})

        # --- 子串匹配 ---
        if len(results) < top_k:
            words = {w for w in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", ql)}
            seen = {r["name"] for r in results}

            for w in words:
                if len(results) >= top_k:
                    break
                for ntype in (self._all_node_types or set()):
                    if w in ntype.lower() and ntype not in seen:
                        d = self.node_index[ntype]
                        results.append({"type": "node", "name": ntype,
                                        "snippet": self._fmt_node(d), "score": 0.5})
                        seen.add(ntype)
                        if len(results) >= top_k:
                            break
                for fname, d in self.vex_index.items():
                    if w in fname.lower() and fname not in seen:
                        results.append({"type": "vex", "name": fname,
                                        "snippet": self._fmt_vex(d), "score": 0.4})
                        seen.add(fname)
                        if len(results) >= top_k:
                            break
                for hname, d in self.hom_index.items():
                    if w in hname.lower() and hname not in seen:
                        results.append({"type": "hom", "name": hname,
                                        "snippet": self._fmt_hom(d), "score": 0.4})
                        seen.add(hname)
                        if len(results) >= top_k:
                                    break

        # --- 知识库匹配 ---
        if len(results) < top_k:
            kb_results = self.search_knowledge(query, top_k=top_k - len(results))
            seen = {r["name"] for r in results}
            for kr in kb_results:
                if kr["name"] not in seen:
                    results.append(kr)
                    seen.add(kr["name"])

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]
    
    # ==========================================================
    # 自动检索（供 _run_agent 注入上下文）
    # ==========================================================

    # 常见英语单词（不应匹配节点/函数名）
    _STOP_WORDS = frozenset({
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "can", "shall", "to", "of", "in", "for",
        "on", "with", "at", "by", "from", "as", "into", "through", "about",
        "after", "before", "between", "under", "above", "up", "down", "out",
        "off", "over", "then", "than", "so", "no", "not", "only", "very",
        "just", "that", "this", "but", "and", "or", "if", "it", "its",
        "all", "each", "every", "both", "few", "more", "most", "some", "any",
        "how", "what", "which", "who", "when", "where", "why",
        "i", "you", "he", "she", "we", "they", "me", "him", "her", "us",
        "my", "your", "his", "our", "their",
        "new", "use", "get", "set", "run", "add", "create", "make", "node",
        "want", "need", "try", "like", "also", "now", "one", "two",
        "using", "used", "function", "point", "points", "value", "values",
        "type", "name", "input", "output", "result", "data", "file",
        "string", "int", "float", "vector", "matrix", "array", "list",
        "true", "false", "none", "null", "self", "return",
    })

    def auto_retrieve(self, user_message: str, max_chars: int = 1200) -> str:
        """从用户消息中自动提取关键词并检索相关文档

        返回一段紧凑的文档片段，用于注入 AI 上下文。
        设计原则：宁精勿滥，每次最多注入 ~300 token。
        """
        if not any((self.node_index, self.vex_index, self.hom_index)):
            return ""

        snippets: List[str] = []
        seen: set = set()
        total = 0

        def _add(s: str, key: str):
            nonlocal total
            if key in seen or total + len(s) > max_chars:
                return
            seen.add(key)
            snippets.append(s)
            total += len(s)

        # 1) hou.XXX 引用
        for ref in re.findall(r"hou\.([a-zA-Z_][a-zA-Z0-9_.]*)", user_message):
            full = f"hou.{ref}"
            doc = self.lookup_hom(full)
            if doc:
                _add(self._fmt_hom(doc), full)

        # 2) 提取英文单词（ASCII-only，避免 \w 匹配中文）
        words = set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", user_message))
        for w in words:
            wl = w.lower()
            if wl in self._STOP_WORDS or len(wl) < 3:
                continue
            # VEX 函数
            vdoc = self.vex_index.get(wl) or self.vex_index.get(w)
            if vdoc:
                _add(self._fmt_vex(vdoc), vdoc.name)
            # 节点
            ndoc = self.node_index.get(wl) or self.node_index.get(w)
            if ndoc:
                _add(self._fmt_node(ndoc), ndoc.node_type)

        # 3) 中文关键词 → 匹配节点标题
        for kw in re.findall(r"[\u4e00-\u9fff]{2,}", user_message)[:3]:
            for ntype, ndoc in self.node_index.items():
                if "/" in ntype:
                    continue
                if kw in ndoc.title or kw in ndoc.description:
                    _add(self._fmt_node(ndoc), ndoc.node_type)
                    break

        # 4) 知识库匹配 — 涉及已收录主题时注入
        if self.knowledge_chunks:
            _KB_HINTS = {
                # VEX / Wrangle
                "属性", "attribute", "vex", "@P", "@N", "@Cd", "pscale", "orient",
                "snippet", "代码", "函数", "语法", "wrangle", "噪波", "noise",
                "copy", "scatter", "颜色", "法线", "位置", "run over",
                "nearpoint", "pcfind", "addpoint", "setpointattrib",
                "hou.", "python", "表达式", "hscript", "变量",
                # Heightfields / Terrain
                "heightfield", "terrain", "地形", "height", "erosion", "侵蚀",
                "mask", "蒙版", "layer", "图层",
                # Copernicus / COP
                "copernicus", "cop", "图像", "image", "texture", "纹理",
                "composite", "合成", "filter", "滤镜", "gpu",
                # MPM
                "mpm", "物理", "模拟", "simulation", "solver", "求解器",
                "snow", "雪", "soil", "mud", "泥", "concrete", "混凝土",
                "rubber", "橡胶", "jello", "sand", "沙",
                # Machine Learning
                "machine learning", "ml", "机器学习", "train", "训练",
                "inference", "推理", "model", "dataset", "数据集", "onnx",
                # Labs
                "labs", "sidefx labs", "游戏", "game", "gamedev",
                "baker", "bake", "烘焙", "lod", "impostor", "flowmap",
                "osm", "photogrammetry", "摄影测量", "wfc", "wave function",
                "tree", "pivot painter", "unreal", "fbx",
                "trim texture", "triplanar", "texel", "mesh slice",
                "destruction", "niagara", "wang tile",
            }
            msg_lower = user_message.lower()
            if any(h in msg_lower for h in _KB_HINTS):
                kb_results = self.search_knowledge(user_message, top_k=2)
                for kr in kb_results:
                    if kr["score"] > 0.3:
                        _add(kr["snippet"], kr["name"])

        if not snippets:
            return ""
        return "[Houdini 文档参考]\n" + "\n".join(snippets)

    # ==========================================================
    # Labs 目录生成（供 system prompt 注入）
    # ==========================================================

    _labs_catalog_cache: Optional[str] = None

    def get_labs_catalog(self) -> str:
        """生成紧凑的 Labs 节点目录，供注入 system prompt

        从 labs_knowledge_base 的 chunk 标题中提取节点名，
        按功能分类输出，约 2000~3000 字符。
        """
        if self._labs_catalog_cache is not None:
            return self._labs_catalog_cache

        labs_chunks = [c for c in self.knowledge_chunks
                       if c.source == 'labs_knowledge_base']
        if not labs_chunks:
            self._labs_catalog_cache = ""
            return ""

        # 提取节点名 + 简短描述
        nodes = []
        for chunk in labs_chunks:
            name = chunk.title.strip()
            # 去掉 "geometry node", "render node" 等后缀
            name = re.sub(
                r'(geometry|render|object|compositing|sop|cop|top|rop|lop|dop|vop)\s*node\s*$',
                '', name, flags=re.IGNORECASE
            ).strip()
            # 去掉版本号 如 6.0, 1.0
            name = re.sub(r'\d+\.\d+$', '', name).strip()
            # 简短描述（取 content 前 60 字符）
            desc = chunk.content[:80].split('\n')[0].strip()
            if len(desc) > 60:
                desc = desc[:60] + '...'
            nodes.append((name, desc))

        if not nodes:
            self._labs_catalog_cache = ""
            return ""

        # Categorize by function
        categories: Dict[str, List[str]] = {
            'Game Dev/Optimization': [], 'Texture/UV': [], 'Terrain': [],
            'Photogrammetry': [], 'Procedural Generation': [], 'Modeling/Geometry': [],
            'FX/Visual': [], 'Import/Export': [], 'Flowmap': [],
            'Tree Generation': [], 'Utility': [],
        }

        for name, desc in nodes:
            nl = name.lower()
            dl = desc.lower()
            c = nl + ' ' + dl
            if 'av ' in nl or 'alicevision' in dl or 'photogramm' in dl:
                categories['Photogrammetry'].append(name)
            elif 'flowmap' in nl:
                categories['Flowmap'].append(name)
            elif 'tree' in nl and any(w in nl for w in ('branch', 'trunk', 'leaf', 'controller', 'simple leaf')):
                categories['Tree Generation'].append(name)
            elif any(w in c for w in ('game', 'lod', 'baker', 'bake', 'pivot painter',
                                       'impostor', 'niagara', 'unreal', 'fbx archive')):
                categories['Game Dev/Optimization'].append(name)
            elif any(w in c for w in ('texture', 'texel', 'trim', 'uv ', 'material',
                                       'triplanar', 'normal', 'detail mesh')):
                categories['Texture/UV'].append(name)
            elif any(w in c for w in ('terrain', 'height', 'slope', 'hf ')):
                categories['Terrain'].append(name)
            elif any(w in c for w in ('wfc', 'wave function', 'lightning', 'superformula',
                                       'wang tile', 'sci-fi', 'scifi')):
                categories['Procedural Generation'].append(name)
            elif any(w in c for w in ('mesh', 'edge', 'dissolve', 'thicken', 'border',
                                       'partition', 'skeleton', 'path deform', 'group',
                                       'poly', 'deform', 'resample', 'inside face')):
                categories['Modeling/Geometry'].append(name)
            elif any(w in c for w in ('destruction', 'color', 'shader', 'pbr', 'toon',
                                       'physics', 'fracture', 'pyro', 'rbd', 'smoke')):
                categories['FX/Visual'].append(name)
            elif any(w in c for w in ('export', 'import', 'osm', 'csv', 'xyz', 'goz',
                                       'obj import', 'substance', 'marmoset', 'vector field',
                                       'volume', 'sketchfab', 'pdg')):
                categories['Import/Export'].append(name)
            else:
                categories['Utility'].append(name)

        # Generate compact text
        lines = [f"SideFX Labs Available Nodes ({len(nodes)}) - MUST use search_local_doc before using any:"]
        for cat, items in categories.items():
            if not items:
                continue
            # 去重
            unique = sorted(set(items))
            lines.append(f"  [{cat}] {', '.join(unique)}")

        catalog = '\n'.join(lines)
        self._labs_catalog_cache = catalog
        return catalog

    # --- 格式化 ---

    @staticmethod
    def _fmt_node(d: NodeDoc) -> str:
        s = f"[doc] {d.title} ({d.context}/{d.node_type})"
        if d.description:
            s += f": {d.description[:120]}"
        if d.parameters:
            names = ", ".join(p[0] for p in d.parameters[:6])
            s += f"\n   Params: {names}"
        return s

    @staticmethod
    def _fmt_vex(d: VexDoc) -> str:
        s = f"[VEX] {d.name}"
        if d.signature:
            s += f": {d.signature}"
        elif d.description:
            s += f": {d.description[:120]}"
        return s

    @staticmethod
    def _fmt_hom(d: HomDoc) -> str:
        s = f"[HOM] {d.name}"
        if d.signature:
            s += f" -> {d.signature}"
        if d.description:
            s += f": {d.description[:120]}"
        return s


# ============================================================
# 全局单例
# ============================================================

_index_instance: Optional[HoudiniDocIndex] = None


def get_doc_index(help_dir: Optional[str] = None) -> HoudiniDocIndex:
    """获取全局文档索引实例（单例）"""
    global _index_instance
    if _index_instance is None:
        _index_instance = HoudiniDocIndex(help_dir)
    return _index_instance


# 兼容旧 API（client.py 中的 from ..doc_rag import get_doc_rag）
get_doc_rag = get_doc_index
