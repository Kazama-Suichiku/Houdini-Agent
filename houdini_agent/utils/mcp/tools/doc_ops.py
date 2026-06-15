from __future__ import annotations

import re
from typing import Any, Optional, Dict, Tuple

try:
    import hou  # type: ignore
except Exception:
    hou = None  # type: ignore

try:
    import requests
except ImportError:
    requests = None  # type: ignore

from ..settings import read_settings


class DocOpsMixin:
    """Documentation search and retrieval."""

    # Houdini nodeTypeCategories() 的 key 与 AI 传入的 category 映射
    _CATEGORY_MAP: Dict[str, str] = {
        "sop": "Sop", "obj": "Object", "dop": "Dop", "vop": "Vop",
        "cop": "Cop2", "cop2": "Cop2", "rop": "Driver", "driver": "Driver",
        "chop": "Chop", "shop": "Shop", "lop": "Lop", "top": "Top",
    }

    # 文档分页缓存：key = "category/node_type" → 完整纯文本
    _doc_page_cache: Dict[str, str] = {}
    _DOC_PAGE_SIZE = 2500  # 每页字符数

    def search_documentation(self, node_type: str, category: str = "sop") -> Tuple[bool, str]:
        """查询节点文档"""
        if requests is None:
            return False, "requests 模块未安装"

        import time

        base_url = "https://www.sidefx.com/docs/houdini/nodes"
        doc_node_type = node_type.replace("::", "--")
        doc_url = f"{base_url}/{category}/{doc_node_type}.html"

        settings = read_settings()
        tries = max(1, settings.request_retries + 1)

        for _ in range(tries):
            try:
                response = requests.get(doc_url, timeout=settings.request_timeout)
                if response.status_code == 404:
                    return False, f"未找到文档: {category}/{node_type}"
                response.raise_for_status()

                content = response.text
                title_match = re.search(r'<title>(.*?)</title>', content, re.IGNORECASE)
                title = title_match.group(1) if title_match else f"{node_type} node"

                summary = ""
                summary_match = re.search(r'<div[^>]*class="[^"]*summary[^"]*"[^>]*>(.*?)</div>', content, re.DOTALL | re.IGNORECASE)
                if summary_match:
                    summary = re.sub(r'<[^>]+>', '', summary_match.group(1)).strip()

                result = f"## {title}\n\n**文档链接**: {doc_url}\n\n"
                if summary:
                    result += f"**描述**: {summary}\n"

                return True, result
            except Exception as e:
                time.sleep(settings.request_backoff)

        return False, f"查询失败: {doc_url}"

    def _get_houdini_local_doc(self, node_type: str, category: str = "sop", page: int = 1) -> Tuple[bool, str]:
        """获取节点文档（多重降级策略，支持分页）

        优先级：
        1. 分页缓存（之前已获取的文档直接分页返回）
        2. Houdini 本地帮助服务器（http://127.0.0.1:{port}）
        3. SideFX 在线文档（https://www.sidefx.com/docs/houdini/）
        4. hou.NodeType.description() + 参数列表 作为最低限度的文档

        Args:
            node_type: 节点类型名
            category: 节点类别
            page: 页码（从 1 开始），大于 1 时优先从缓存读取

        Returns:
            (success, doc_text)
        """
        if hou is None:
            return False, "未检测到 Houdini API"

        type_name_lower = node_type.lower().strip()

        # ---------- 分页快速路径：缓存中已有完整文档 ----------
        cache_key = f"{category}/{node_type}".lower()
        if page > 1 and cache_key in self._doc_page_cache:
            return True, self._paginate_doc(self._doc_page_cache[cache_key], node_type, category, page)

        # ---------- 查找节点类型对象 ----------
        node_type_obj = None
        try:
            categories = hou.nodeTypeCategories()
            hou_cat_name = self._CATEGORY_MAP.get(category.lower(), category.capitalize())
            cat_obj = categories.get(hou_cat_name)
            # 如果精确匹配失败，遍历所有分类
            if cat_obj is None:
                for cname, cobj in categories.items():
                    if cname.lower() == category.lower():
                        cat_obj = cobj
                        break

            if cat_obj:
                for name, nt in cat_obj.nodeTypes().items():
                    name_low = name.lower()
                    if name_low == type_name_lower or name_low.endswith(f"::{type_name_lower}"):
                        node_type_obj = nt
                        break
            # 如果指定类别未找到，搜索全部类别
            if node_type_obj is None:
                for cname, cobj in categories.items():
                    for name, nt in cobj.nodeTypes().items():
                        name_low = name.lower()
                        if name_low == type_name_lower or name_low.endswith(f"::{type_name_lower}"):
                            node_type_obj = nt
                            # 更新 category 为实际找到的
                            for k, v in self._CATEGORY_MAP.items():
                                if v == cname:
                                    category = k
                                    break
                            break
                    if node_type_obj:
                        break
        except Exception as e:
            print(f"[MCP] 查找节点类型失败: {e}")

        # ---------- 策略 1: 本地帮助服务器 ----------
        local_result = self._fetch_local_help(node_type, category, node_type_obj, page)
        if local_result is not None:
            return True, local_result

        # ---------- 策略 2: SideFX 在线文档 ----------
        online_result = self._fetch_online_help(node_type, category, page)
        if online_result is not None:
            return True, online_result

        # ---------- 策略 3: 从 hou.NodeType 提取基本信息 ----------
        if node_type_obj is not None:
            return self._extract_type_info(node_type_obj, node_type)

        return False, f"找不到节点类型 '{node_type}' 的文档。请用 search_node_types 确认正确的节点名。"

    def _html_to_text(self, html: str) -> str:
        """将 HTML 转为可读纯文本"""
        try:
            from bs4 import BeautifulSoup as BS
            soup = BS(html, 'html.parser')
            # 移除不需要的部分
            for tag in soup.find_all(['script', 'style', 'nav', 'header', 'footer']):
                tag.decompose()
            text = soup.get_text(separator='\n', strip=True)
        except Exception:
            # 无 bs4 时用正则
            text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r'<nav[^>]*>.*?</nav>', '', text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r'<header[^>]*>.*?</header>', '', text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r'<footer[^>]*>.*?</footer>', '', text, flags=re.DOTALL | re.IGNORECASE)
            # 块级标签换行
            text = re.sub(r'<(?:br|p|div|h[1-6]|li|tr)[^>]*>', '\n', text, flags=re.IGNORECASE)
            text = re.sub(r'<[^>]+>', ' ', text)
        # 清理多余空行
        lines = [l.strip() for l in text.split('\n')]
        lines = [l for l in lines if l]
        text = '\n'.join(lines)
        return text

    def _paginate_doc(self, text: str, node_type: str, category: str, page: int = 1) -> str:
        """将文档按页返回，支持分页查看完整内容

        Args:
            text: 完整的纯文本文档
            node_type: 节点类型名
            category: 节点类别
            page: 页码（从 1 开始）
        """
        cache_key = f"{category}/{node_type}".lower()
        self._doc_page_cache[cache_key] = text

        total_chars = len(text)
        page_size = self._DOC_PAGE_SIZE
        total_pages = max(1, (total_chars + page_size - 1) // page_size)

        # 限制页码范围
        page = max(1, min(page, total_pages))

        start = (page - 1) * page_size
        end = min(start + page_size, total_chars)
        page_text = text[start:end]

        header = f"[{node_type} 节点文档] (第 {page}/{total_pages} 页, 共 {total_chars} 字符)\n\n"

        if total_pages == 1:
            return header + page_text

        if page < total_pages:
            footer = f"\n\n[第 {page}/{total_pages} 页] 还有更多内容，调用 get_houdini_node_doc(node_type=\"{node_type}\", category=\"{category}\", page={page + 1}) 查看下一页"
        else:
            footer = f"\n\n[第 {page}/{total_pages} 页 - 最后一页]"

        return header + page_text + footer

    def _fetch_local_help(self, node_type: str, category: str, node_type_obj: Any, page: int = 1) -> Optional[str]:
        """从 Houdini 本地帮助服务器获取文档"""
        # 先检查分页缓存（避免重复请求）
        cache_key = f"{category}/{node_type}".lower()
        if cache_key in self._doc_page_cache and page > 1:
            return self._paginate_doc(self._doc_page_cache[cache_key], node_type, category, page)

        if not requests:
            return None
        settings = read_settings()
        help_port = getattr(settings, "help_server_port", 48626)
        help_server = f"http://127.0.0.1:{help_port}"

        # 构建 URL（优先 helpUrl，否则用标准路径）
        url_path = f"/nodes/{category.lower()}/{node_type.lower()}"
        if node_type_obj:
            try:
                help_url = node_type_obj.helpUrl()
                if help_url and not help_url.startswith(('http://', 'https://')):
                    url_path = help_url
            except Exception:
                pass
        full_url = f"{help_server}{url_path}"

        try:
            response = requests.get(full_url, timeout=5)
            if response.status_code == 200:
                text = self._html_to_text(response.text)
                if text and len(text) > 50:
                    return self._paginate_doc(text, node_type, category, page)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            pass  # 本地服务器不可用，降级到在线
        except Exception as e:
            print(f"[MCP] 本地帮助获取失败: {e}")
        return None

    def _fetch_online_help(self, node_type: str, category: str, page: int = 1) -> Optional[str]:
        """从 SideFX 在线文档获取"""
        # 先检查分页缓存
        cache_key = f"{category}/{node_type}".lower()
        if cache_key in self._doc_page_cache and page > 1:
            return self._paginate_doc(self._doc_page_cache[cache_key], node_type, category, page)

        if not requests:
            return None
        base_url = "https://www.sidefx.com/docs/houdini/"
        full_url = f"{base_url}nodes/{category.lower()}/{node_type.lower()}.html"
        try:
            response = requests.get(full_url, timeout=8)
            if response.status_code == 200:
                text = self._html_to_text(response.text)
                if text and len(text) > 50:
                    return self._paginate_doc(text, node_type, category, page)
        except Exception:
            pass
        return None

    def _extract_type_info(self, node_type_obj: Any, node_type: str) -> Tuple[bool, str]:
        """从 hou.NodeType 对象提取基本文档信息（最后降级）"""
        try:
            label = node_type_obj.description() or node_type
            # 输入信息
            inputs = []
            try:
                input_labels = node_type_obj.inputLabels()
                for i, lbl in enumerate(input_labels):
                    inputs.append(f"  输入 {i}: {lbl}")
            except Exception:
                pass
            # 参数摘要（前 20 个）
            parms = []
            try:
                parm_templates = node_type_obj.parmTemplates()
                for pt in parm_templates[:20]:
                    parms.append(f"  {pt.name()}: {pt.label()} ({pt.type().name()})")
            except Exception:
                pass

            doc = [f"[{node_type} 节点基本信息]", f"名称: {label}"]
            if inputs:
                doc.append("输入端口:\n" + '\n'.join(inputs))
            if parms:
                doc.append(f"参数 (前{min(20, len(parms))}个):\n" + '\n'.join(parms))
            return True, '\n'.join(doc)
        except Exception as e:
            return False, f"提取节点信息失败: {e}"
