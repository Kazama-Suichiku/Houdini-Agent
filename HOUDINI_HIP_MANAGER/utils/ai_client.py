# -*- coding: utf-8 -*-
"""
AI 调用客户端（Houdini 工具专用）
支持 OpenAI / DeepSeek / 智谱GLM（GLM-4, GLM-Z1推理, GLM-4V视觉）
支持 Function Calling、流式传输、联网搜索

安全提示：不要将密钥提交到版本库。
"""

import os
import sys
import json
import ssl
import time
import re
from typing import List, Dict, Optional, Any, Callable, Generator, Tuple
from urllib.parse import quote_plus

from shared.common_utils import load_config, save_config

# 强制使用本地 lib 目录中的依赖库
_lib_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'lib')
if os.path.exists(_lib_path):
    # 将 lib 目录添加到 sys.path 最前面，确保优先使用
    if _lib_path in sys.path:
        sys.path.remove(_lib_path)
    sys.path.insert(0, _lib_path)

# 导入 requests
HAS_REQUESTS = False
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    pass


# ============================================================
# 联网搜索功能
# ============================================================

class WebSearcher:
    """联网搜索工具 - 多引擎自动降级（Brave → DuckDuckGo）+ 缓存"""
    
    # Brave Search（免费 HTML 抓取，Svelte SSR，结果质量好）
    BRAVE_URL = "https://search.brave.com/search"
    
    # DuckDuckGo HTML 搜索（无需 API Key，备用）
    DUCKDUCKGO_URL = "https://html.duckduckgo.com/html/"

    # 通用请求头
    _HEADERS = {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/120.0.0.0 Safari/537.36'
        ),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate',
    }

    # 搜索结果缓存：key -> (timestamp, result)
    _search_cache: Dict[str, tuple] = {}
    _CACHE_TTL = 300  # 5 分钟

    # 网页正文缓存：url -> (timestamp, text_lines)
    _page_cache: Dict[str, tuple] = {}
    _PAGE_CACHE_TTL = 600  # 10 分钟

    # Trafilatura 可用性
    _HAS_TRAFILATURA = False
    
    def __init__(self):
        # 检测 trafilatura 可用性（只检测一次）
        if not WebSearcher._HAS_TRAFILATURA:
            try:
                import trafilatura  # noqa: F401
                WebSearcher._HAS_TRAFILATURA = True
            except ImportError:
                pass
    # ------------------------------------------------------------------
    # 编码修复：requests 默认 ISO-8859-1 会导致中文乱码
    # ------------------------------------------------------------------

    @staticmethod
    def _fix_encoding(response) -> str:
        """智能检测并修正 HTTP 响应的编码，避免中文乱码。

        优先级：
        1. Content-Type header 中明确声明的 charset（排除 ISO-8859-1 默认值）
        2. HTML <meta charset="..."> 标签
        3. requests.apparent_encoding（基于 chardet / charset_normalizer）
        4. 回退到 UTF-8
        """
        # 1) Content-Type 声明的 charset
        ct_enc = response.encoding
        if ct_enc and ct_enc.lower() not in ('iso-8859-1', 'latin-1', 'ascii'):
            return response.text

        # 2) HTML meta 标签
        raw = response.content[:8192]
        meta_match = re.search(
            rb'<meta[^>]*charset=["\']?\s*([a-zA-Z0-9_-]+)',
            raw, re.IGNORECASE,
        )
        if meta_match:
            declared = meta_match.group(1).decode('ascii', errors='ignore').strip()
            try:
                response.encoding = declared
                return response.text
            except (LookupError, UnicodeDecodeError):
                pass

        # 3) apparent_encoding (chardet)
        apparent = getattr(response, 'apparent_encoding', None)
        if apparent:
            try:
                response.encoding = apparent
                return response.text
            except (LookupError, UnicodeDecodeError):
                pass

        # 4) 回退 UTF-8
        response.encoding = 'utf-8'
        return response.text

    @staticmethod
    def _decode_entities(text: str) -> str:
        """解码 HTML 实体: &amp; &lt; &gt; &quot; &#xxxx; 等"""
        import html as _html
        try:
            return _html.unescape(text)
        except Exception:
            return text
    
    # ------------------------------------------------------------------
    # 搜索（带缓存 + 三级降级）
    # ------------------------------------------------------------------

    def search(self, query: str, max_results: int = 5, timeout: int = 10) -> Dict[str, Any]:
        """执行网络搜索（缓存 + 多引擎自动降级）
        
        优先级：缓存 → Brave 抓取 → DuckDuckGo 抓取
        任一引擎成功且有结果即返回，否则尝试下一个。
        """
        # --- 缓存查找 ---
        cache_key = f"{query}|{max_results}"
        cached = self._search_cache.get(cache_key)
        if cached:
            ts, cached_result = cached
            if (time.time() - ts) < self._CACHE_TTL:
                cached_result = dict(cached_result)
                cached_result['source'] = cached_result.get('source', '') + '(cached)'
                return cached_result

        errors = []
        
        # 1. Brave Search（免费 HTML 抓取，结果质量好）
        result = self._search_brave(query, max_results, timeout)
        if result.get('success') and result.get('results'):
            self._search_cache[cache_key] = (time.time(), result)
            return result
        errors.append(f"Brave: {result.get('error', 'no results')}")
        
        # 2. DuckDuckGo（备用）
        result = self._search_duckduckgo(query, max_results, timeout)
        if result.get('success') and result.get('results'):
            self._search_cache[cache_key] = (time.time(), result)
            return result
        errors.append(f"DDG: {result.get('error', 'no results')}")
        
        return {"success": False, "error": f"All engines failed: {'; '.join(errors)}", "results": []}

    # ---------- Brave Search ----------

    def _search_brave(self, query: str, max_results: int, timeout: int) -> Dict[str, Any]:
        """通过 Brave Search（HTML 抓取，无需 API Key，结果质量好）"""
        if not HAS_REQUESTS:
            return {"success": False, "error": "requests not installed", "results": []}
        try:
            params = {'q': query, 'source': 'web'}
            response = requests.get(
                self.BRAVE_URL, params=params, headers=self._HEADERS, timeout=timeout,
            )
            response.raise_for_status()
            page_html = self._fix_encoding(response)
            results = self._parse_brave_html(page_html, max_results)
            if results:
                return {"success": True, "query": query, "results": results, "source": "Brave"}
            return {"success": False, "error": "Brave returned page but no results parsed", "results": []}
        except Exception as e:
            return {"success": False, "error": str(e), "results": []}

    def _parse_brave_html(self, page_html: str, max_results: int) -> List[Dict[str, str]]:
        """解析 Brave Search 结果页（Svelte SSR 结构）
        
        Brave 结构:
          <div class="snippet svelte-..." data-type="web" data-pos="N">
            <a href="URL">
              <div class="title search-snippet-title ...">TITLE</div>
            </a>
            <div class="snippet-description ...">DESCRIPTION</div>
            或直接嵌入文本段落
          </div>
        """
        results: List[Dict[str, str]] = []
        
        block_starts = list(re.finditer(
            r'<div[^>]*class="snippet\b[^"]*"[^>]*data-type="web"[^>]*>',
            page_html, re.IGNORECASE,
        ))
        
        for i, match in enumerate(block_starts[:max_results + 5]):
            start = match.start()
            end = block_starts[i + 1].start() if i + 1 < len(block_starts) else start + 4000
            block = page_html[start:end]
            
            # URL: 第一个外部 <a href="https://...">
            url_m = re.search(r'<a[^>]*href="(https?://[^"]+)"', block, re.IGNORECASE)
            url = url_m.group(1) if url_m else ''
            if not url or 'brave.com' in url:
                continue
            
            # Title: class="title search-snippet-title ..."
            title = ''
            for title_pat in (
                r'class="title\b[^"]*search-snippet-title[^"]*"[^>]*>(.*?)</div>',
                r'class="[^"]*search-snippet-title[^"]*"[^>]*>(.*?)</(?:span|div)>',
                r'class="snippet-title[^"]*"[^>]*>(.*?)</(?:span|div)>',
            ):
                title_m = re.search(title_pat, block, re.DOTALL | re.IGNORECASE)
                if title_m:
                    title = re.sub(r'<[^>]+>', '', title_m.group(1)).strip()
                    # 去掉日期后缀（如 "Title 2025年11月6日 -"）
                    title = re.sub(r'\s*\d{4}年\d{1,2}月\d{1,2}日\s*-?\s*$', '', title)
                    break
            
            if not title:
                # 退而求其次：块内有意义文本（跳过网站名/URL片段）
                segments = re.findall(r'>([^<]{8,})<', block)
                for seg in segments:
                    seg = seg.strip()
                    if (seg and 'svg' not in seg.lower()
                            and 'path' not in seg.lower()
                            and not seg.startswith('›')
                            and '.' not in seg[:10]):  # 跳过 URL 片段
                        title = self._decode_entities(seg[:120])
                        break
            
            # Description: 各种可能的容器
            desc = ''
            for desc_pat in (
                r'class="[^"]*snippet-description[^"]*"[^>]*>(.*?)</(?:div|p|span)>',
                r'class="[^"]*snippet-content[^"]*"[^>]*>(.*?)</(?:div|p|span)>',
            ):
                desc_m = re.search(desc_pat, block, re.DOTALL | re.IGNORECASE)
                if desc_m:
                    desc = re.sub(r'<[^>]+>', '', desc_m.group(1)).strip()
                    desc = self._decode_entities(desc)
                    break
            
            # 如果没有 snippet-description，从文本段落中提取
            if not desc:
                segments = re.findall(r'>([^<]{20,})<', block)
                for seg in segments:
                    seg = seg.strip()
                    # 跳过标题本身、URL 面包屑、SVG 数据
                    if (seg and seg != title
                            and 'svg' not in seg.lower()
                            and not seg.startswith('›')
                            and not re.match(r'^[\d年月日\s\-]+$', seg)):
                        desc = self._decode_entities(seg[:300])
                        break
            
            results.append({
                'title': self._decode_entities(title) if title else '(no title)',
                'url': url,
                'snippet': desc[:300],
            })
            if len(results) >= max_results:
                break
        
        return results

    # ---------- DuckDuckGo ----------

    def _search_duckduckgo(self, query: str, max_results: int, timeout: int) -> Dict[str, Any]:
        """使用 DuckDuckGo 搜索（HTML lite 版本，备用）"""
        if not HAS_REQUESTS:
            return {"success": False, "error": "requests not installed", "results": []}
        
        try:
            response = requests.post(
                self.DUCKDUCKGO_URL,
                data={'q': query, 'b': '', 'kl': 'cn-zh'},
                headers=self._HEADERS,
                timeout=timeout,
            )
            response.raise_for_status()
            page_html = self._fix_encoding(response)
            results = self._parse_duckduckgo_html(page_html, max_results)
            
            if results:
                return {"success": True, "query": query, "results": results, "source": "DuckDuckGo"}
            return {"success": False, "error": "DDG returned page but no results parsed", "results": []}
        except Exception as e:
            return {"success": False, "error": str(e), "results": []}
    
    def _parse_duckduckgo_html(self, page_html: str, max_results: int) -> List[Dict[str, str]]:
        """解析 DuckDuckGo HTML 搜索结果（兼容多种页面结构）"""
        from urllib.parse import unquote, parse_qs, urlparse
        results = []
        
        # 模式 1: class="result__a"（经典版）
        pattern = r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>'
        matches = re.findall(pattern, page_html, re.IGNORECASE | re.DOTALL)
        
        # 模式 2: lite 版 <a rel="nofollow">
        if not matches:
            pattern = r'<a[^>]*rel="nofollow"[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>'
            matches = re.findall(pattern, page_html, re.IGNORECASE | re.DOTALL)
        
        for url, raw_title in matches[:max_results]:
            if not url or 'duckduckgo.com' in url:
                continue
            title = re.sub(r'<[^>]+>', '', raw_title).strip()
            title = self._decode_entities(title)
            if not title:
                continue
            
            real_url = url
            if 'uddg=' in url:
                try:
                    parsed = urlparse(url)
                    params = parse_qs(parsed.query)
                    if 'uddg' in params:
                        real_url = unquote(params['uddg'][0])
                except Exception:
                    pass
            
            results.append({"title": title, "url": real_url, "snippet": ""})
        
        # 提取摘要
        for pat in (r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
                    r'<td[^>]*class="result-snippet"[^>]*>(.*?)</td>'):
            snippet_matches = re.findall(pat, page_html, re.IGNORECASE | re.DOTALL)
            if snippet_matches:
                for i, raw in enumerate(snippet_matches[:len(results)]):
                    clean = re.sub(r'<[^>]+>', '', raw).strip()
                    clean = self._decode_entities(clean)
                    if clean:
                        results[i]["snippet"] = clean[:300]
                break
        
        return results
    
    # (Bing API 已移除 — 需要付费 Azure Key，不实用)

    # ------------------------------------------------------------------
    # 网页抓取（trafilatura 优先 → 正则降级 + 页面缓存）
    # ------------------------------------------------------------------

    def fetch_page_content(self, url: str, max_lines: int = 80,
                           start_line: int = 1, timeout: int = 15) -> Dict[str, Any]:
        """获取网页内容（trafilatura 正文提取 + 按行分页，支持翻页）
        
        Args:
            url: 网页 URL
            max_lines: 每页最大行数
            start_line: 从第几行开始（1-based），用于翻页
            timeout: 请求超时秒数
        """
        if not HAS_REQUESTS:
            return {"success": False, "error": "需要安装 requests 库"}

        try:
            # --- 页面缓存查找（翻页时复用已抓取的内容） ---
            cached = self._page_cache.get(url)
            if cached:
                ts, cached_lines = cached
                if (time.time() - ts) < self._PAGE_CACHE_TTL:
                    return self._paginate_lines(url, cached_lines, start_line, max_lines)

            response = requests.get(url, headers=self._HEADERS, timeout=timeout)
            response.raise_for_status()
            
            # 修正编码（防乱码核心）
            page_html = self._fix_encoding(response)

            # --- 正文提取：trafilatura 优先，正则降级 ---
            text = None
            if self._HAS_TRAFILATURA:
                try:
                    import trafilatura
                    text = trafilatura.extract(
                        page_html,
                        include_comments=False,
                        include_tables=True,
                        output_format='txt',
                        favor_recall=True,
                    )
                except Exception:
                    text = None

            if not text:
                # 降级到正则剥标签
                text = self._fallback_html_to_text(page_html)

            # 清理：每行合并多余空格，保留换行结构
            lines = []
            for line in text.split('\n'):
                cleaned = re.sub(r'[ \t]+', ' ', line).strip()
                if cleaned:
                    lines.append(cleaned)

            # 缓存此页面（翻页时复用）
            self._page_cache[url] = (time.time(), lines)
            # 限制缓存大小
            if len(self._page_cache) > 50:
                oldest_key = min(self._page_cache, key=lambda k: self._page_cache[k][0])
                del self._page_cache[oldest_key]

            return self._paginate_lines(url, lines, start_line, max_lines)

        except Exception as e:
            return {"success": False, "error": str(e), "url": url}

    def _fallback_html_to_text(self, page_html: str) -> str:
        """正则剥标签降级方案（trafilatura 不可用时）"""
        # 移除无用区块
        for tag in ('script', 'style', 'nav', 'footer', 'header', 'aside', 'noscript'):
            page_html = re.sub(
                rf'<{tag}[^>]*>.*?</{tag}>',
                '', page_html, flags=re.DOTALL | re.IGNORECASE,
            )
        # 块级标签 → 换行
        page_html = re.sub(r'<br\s*/?\s*>', '\n', page_html, flags=re.IGNORECASE)
        page_html = re.sub(
            r'</(?:p|div|li|tr|td|th|h[1-6]|blockquote|section|article)>',
            '\n', page_html, flags=re.IGNORECASE,
        )
        # 移除剩余 HTML 标签
        text = re.sub(r'<[^>]+>', ' ', page_html)
        # 解码 HTML 实体
        return self._decode_entities(text)

    @staticmethod
    def _paginate_lines(url: str, lines: List[str], start_line: int, max_lines: int) -> Dict[str, Any]:
        """对已提取的行列表做分页返回"""
        total_lines = len(lines)
        offset = max(0, start_line - 1)
        page_lines = lines[offset:offset + max_lines]
        end_line = offset + len(page_lines)

        if not page_lines:
            return {
                "success": True,
                "url": url,
                "content": f"[已到末尾] 该网页共 {total_lines} 行，start_line={start_line} 超出范围。"
            }

        content = '\n'.join(page_lines)

        if end_line < total_lines:
            next_start = end_line + 1
            content += (
                f"\n\n[分页提示] 当前显示第 {offset+1}-{end_line} 行，共 {total_lines} 行。"
                f"如需后续内容，请调用 fetch_webpage(url=\"{url}\", start_line={next_start})。"
            )
        else:
            content += f"\n\n[全部内容已显示] 第 {offset+1}-{end_line} 行，共 {total_lines} 行。"

        return {"success": True, "url": url, "content": content}


# ============================================================
# Houdini 工具定义
# ============================================================

HOUDINI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "create_wrangle_node",
            "description": "【优先使用】创建 Wrangle 节点并设置 VEX 代码。这是解决几何处理问题的首选方式，能用 VEX 解决的问题都应该用这个工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "vex_code": {
                        "type": "string",
                        "description": "VEX 代码内容。常用语法：@P（位置）, @N（法线）, @Cd（颜色）, @pscale（点大小）, addpoint(), addprim(), addvertex() 等"
                    },
                    "wrangle_type": {
                        "type": "string",
                        "enum": ["attribwrangle", "pointwrangle", "primitivewrangle", "volumewrangle", "vertexwrangle"],
                        "description": "Wrangle 类型。默认 'attribwrangle'（最通用）。pointwrangle 处理点，primitivewrangle 处理图元"
                    },
                    "node_name": {
                        "type": "string",
                        "description": "节点名称（可选）"
                    },
                    "run_over": {
                        "type": "string",
                        "enum": ["Points", "Vertices", "Primitives", "Detail"],
                        "description": "运行模式：Points（点，默认）, Vertices（顶点）, Primitives（图元）, Detail（全局）"
                    },
                    "parent_path": {
                        "type": "string",
                        "description": "父网络路径（可选，留空使用当前网络）"
                    }
                },
                "required": ["vex_code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_network_structure",
            "description": "获取当前网络编辑器中的节点网络结构，包括所有节点名称、类型和连接关系。轻量级操作，不包含详细参数。结果支持分页，大型网络可翻页查看。",
            "parameters": {
                "type": "object",
                "properties": {
                    "network_path": {
                        "type": "string",
                        "description": "网络路径如 '/obj/geo1'，留空使用当前网络"
                    },
                    "page": {
                        "type": "integer",
                        "description": "页码（从1开始），结果较多时翻页查看后续内容"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_node_details",
            "description": "获取节点概况：类型、状态标志(display/render/bypass)、错误信息、输入输出连接、以及用户修改过的非默认参数值。用于了解节点整体状况和连接关系，不含完整参数列表。如需查看所有可用参数请用 get_node_parameters。",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_path": {
                        "type": "string",
                        "description": "节点完整路径如 '/obj/geo1/box1'"
                    }
                },
                "required": ["node_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_node_parameters",
            "description": "获取节点的完整参数列表：每个参数的内部名称、类型(Float/Int/Menu等)、标签、默认值、当前值、菜单选项。设置参数前必须先调用此工具确认正确的参数名和类型，不要猜测。不含连接和错误信息，如需查看请用 get_node_details。参数较多时支持分页。",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_path": {
                        "type": "string",
                        "description": "节点完整路径如 '/obj/geo1/box1'"
                    },
                    "page": {
                        "type": "integer",
                        "description": "页码（从1开始），参数较多时翻页查看后续参数"
                    }
                },
                "required": ["node_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_node_parameter",
            "description": "设置节点参数值。注意：调用前必须先用 get_node_parameters 确认参数名和类型，不要猜测参数名。",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_path": {"type": "string", "description": "节点路径"},
                    "param_name": {"type": "string", "description": "参数名（必须是 get_node_parameters 返回的有效参数名）"},
                    "value": {"type": ["string", "number", "boolean", "array"], "description": "参数值"}
                },
                "required": ["node_path", "param_name", "value"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_node",
            "description": "创建单个节点。节点类型格式：'box' 或 'sop/box'（推荐直接写节点名如'box'，系统会自动识别类别）。如果创建失败，必须调用 search_node_types 查找正确的节点类型名再重试，不要盲目重试。",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_type": {
                        "type": "string", 
                        "description": "节点类型名称，如 'box', 'scatter', 'noise'。直接写节点名即可，系统会自动识别类别（sop/obj等）。"
                    },
                    "node_name": {"type": "string", "description": "节点名称（可选），如不提供会自动生成"},
                    "parameters": {"type": "object", "description": "初始参数字典（可选），如 {'size': 1.0}"},
                    "parent_path": {"type": "string", "description": "父网络路径（可选），如 '/obj/geo1'，留空使用当前网络"}
                },
                "required": ["node_type"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_nodes_batch",
            "description": "批量创建节点并自动连接。nodes 数组中每个元素需要 id（临时标识）和 type（节点类型）；connections 数组指定连接关系，from/to 使用 nodes 中的 id。",
            "parameters": {
                "type": "object",
                "properties": {
                    "nodes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "type": {"type": "string"},
                                "name": {"type": "string"},
                                "parms": {"type": "object"}
                            },
                            "required": ["id", "type"]
                        }
                    },
                    "connections": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "from": {"type": "string"},
                                "to": {"type": "string"},
                                "input": {"type": "integer"}
                            }
                        }
                    }
                },
                "required": ["nodes"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "connect_nodes",
            "description": "连接两个节点。连接前应先用 get_node_inputs 查询目标节点的输入端口含义。input_index: 0=第一输入, 1=第二输入(如copytopoints的目标点), 2=第三输入。",
            "parameters": {
                "type": "object",
                "properties": {
                    "from_path": {"type": "string", "description": "上游节点路径（提供数据的节点）"},
                    "to_path": {"type": "string", "description": "下游节点路径（接收数据的节点）"},
                    "input_index": {"type": "integer", "description": "目标节点的输入端口索引。0=主输入，1=第二输入（如copy的目标点），2=第三输入。默认0"}
                },
                "required": ["from_path", "to_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_node",
            "description": "删除指定路径的节点。",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_path": {"type": "string", "description": "要删除的节点完整路径，如 '/obj/geo1/box1'"}
                },
                "required": ["node_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_node_types",
            "description": "按关键词搜索 Houdini 可用的节点类型。用于精确查找节点。",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "搜索关键词，如 'scatter', 'copy'"},
                    "category": {"type": "string", "enum": ["sop", "obj", "dop", "vop", "cop", "all"], "description": "节点类别，默认 'all'"},
                    "limit": {"type": "integer", "description": "最大结果数，默认 10"}
                },
                "required": ["keyword"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "semantic_search_nodes",
            "description": "通过自然语言描述搜索合适的节点类型。例如：'我需要在表面上随机分布点'会找到 scatter 节点。当你不确定用什么节点时使用此工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "用自然语言描述你想要的功能，如 '在表面分布点'、'复制物体到点上'、'创建噪波变形'"
                    },
                    "category": {"type": "string", "enum": ["sop", "obj", "dop", "vop", "all"], "description": "节点类别，默认 'sop'"}
                },
                "required": ["description"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_children",
            "description": "列出网络下的所有子节点，类似文件系统的 ls 命令。显示节点名称、类型和状态。节点较多时支持分页。",
            "parameters": {
                "type": "object",
                "properties": {
                    "network_path": {"type": "string", "description": "网络路径，如 '/obj/geo1'。留空使用当前网络"},
                    "recursive": {"type": "boolean", "description": "是否递归列出子网络，默认 false"},
                    "show_flags": {"type": "boolean", "description": "是否显示节点标志（显示/渲染/旁路），默认 true"},
                    "page": {"type": "integer", "description": "页码（从1开始），节点较多时翻页查看"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_selection",
            "description": "读取当前选中节点的详细信息。不需要知道节点路径，直接读取用户选中的内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "include_params": {"type": "boolean", "description": "是否包含参数详情，默认 true"},
                    "include_geometry": {"type": "boolean", "description": "是否包含几何体信息，默认 false"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_display_flag",
            "description": "设置节点的显示标志。控制哪个节点在视口中显示。",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_path": {"type": "string", "description": "节点路径"},
                    "display": {"type": "boolean", "description": "是否设为显示节点"},
                    "render": {"type": "boolean", "description": "是否设为渲染节点"}
                },
                "required": ["node_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "copy_node",
            "description": "复制/克隆节点到新位置。可以复制到同一网络或其他网络。",
            "parameters": {
                "type": "object",
                "properties": {
                    "source_path": {"type": "string", "description": "源节点路径"},
                    "dest_network": {"type": "string", "description": "目标网络路径，留空则复制到同一网络"},
                    "new_name": {"type": "string", "description": "新节点名称（可选）"}
                },
                "required": ["source_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "batch_set_parameters",
            "description": "批量修改多个节点的参数。类似 search_replace，可以在多个节点中同时修改某个参数。",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "节点路径列表"
                    },
                    "param_name": {"type": "string", "description": "参数名"},
                    "value": {"type": ["string", "number", "boolean", "array"], "description": "新值"}
                },
                "required": ["node_paths", "param_name", "value"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "find_nodes_by_param",
            "description": "在网络中搜索具有特定参数值的节点。类似 grep 搜索。",
            "parameters": {
                "type": "object",
                "properties": {
                    "network_path": {"type": "string", "description": "搜索的网络路径，留空使用当前网络"},
                    "param_name": {"type": "string", "description": "参数名"},
                    "value": {"type": ["string", "number"], "description": "要匹配的值（可选，留空则列出所有有此参数的节点）"},
                    "recursive": {"type": "boolean", "description": "是否递归搜索子网络，默认 true"}
                },
                "required": ["param_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "save_hip",
            "description": "保存当前 HIP 文件。可以保存到当前路径或指定新路径。",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "保存路径（可选，留空则保存到当前文件）"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "undo_redo",
            "description": "执行撤销或重做操作。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["undo", "redo"], "description": "操作类型"}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "联网搜索任意信息。可搜索天气、新闻、技术文档、Houdini 帮助、编程问题、百科知识等任何内容。只要用户的问题涉及你不确定或需要最新数据的信息，都应主动调用此工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词"
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "最大结果数，默认 5"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_webpage",
            "description": "获取指定 URL 的网页正文内容（按行分页）。首次调用返回第 1 行起的内容；如结果末尾有 [分页提示]，可传入 start_line 获取后续行。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "网页 URL"
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "从第几行开始返回（默认 1）。用于翻页：如上次显示到第 80 行，传 81 获取后续内容"
                    }
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_local_doc",
            "description": "搜索本地 Houdini 文档索引（节点/VEX函数/HOM类）。常见信息已自动注入上下文，仅在需要更多细节时主动调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string", 
                        "description": "关键词，如节点名'attribwrangle'、VEX函数名'addpoint'、HOM类'hou.Node'"
                    },
                    "top_k": {
                        "type": "integer", 
                        "description": "返回前k个结果（默认5）"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_houdini_node_doc",
            "description": "获取节点帮助文档（支持分页）。自动降级：本地帮助服务器->SideFX在线文档->节点类型信息。文档较长时会分页显示，返回结果中会提示总页数和下一页调用方式。优先使用 get_node_inputs 获取输入端口信息，本工具用于需要更详细文档时。",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_type": {
                        "type": "string",
                        "description": "节点类型名称"
                    },
                    "category": {
                        "type": "string",
                        "enum": ["sop", "obj", "dop", "vop", "cop", "rop"],
                        "description": "节点类别，默认 'sop'"
                    },
                    "page": {
                        "type": "integer",
                        "description": "页码（从1开始）。首次查询不传或传1，如果返回结果提示有更多页，传入对应页码查看后续内容"
                    }
                },
                "required": ["node_type"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "execute_python",
            "description": "在 Houdini Python Shell 中执行代码。可以执行任意 Python 代码，访问 hou 模块操作场景。执行结果（包括 print 输出和错误信息）会完整返回。输出较长时支持分页，用相同 code 和不同 page 翻页查看。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "要执行的 Python 代码"
                    },
                    "page": {
                        "type": "integer",
                        "description": "页码（从1开始），输出较长时翻页查看后续内容。翻页时必须传入与首次相同的 code"
                    }
                },
                "required": ["code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "execute_shell",
            "description": "在系统 Shell 中执行命令（非 Houdini Python Shell）。可运行 pip、git、dir/ls、ffmpeg、ssh、scp 等系统命令。工作目录默认为项目根目录。命令有超时限制（默认30秒，最大120秒）。危险命令（如 rm -rf、format、del /s）会被拦截。注意：1)必须生成可直接运行的完整命令，不用占位符；2)需交互的命令必须传非交互式参数；3)优先用精确命令减少输出量(如 find -maxdepth 2)；4)路径有空格需引号包裹；5)命令失败时分析stderr后修正重试，不要盲目重复。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的 Shell 命令"
                    },
                    "cwd": {
                        "type": "string",
                        "description": "工作目录（可选，默认为项目根目录）"
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "超时秒数（可选，默认 30，最大 120）"
                    },
                    "page": {
                        "type": "integer",
                        "description": "页码（从1开始），输出较长时翻页查看后续内容"
                    }
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_errors",
            "description": "检查Houdini节点的cooking错误和警告（仅用于节点cooking问题）。注意：如果工具调用返回了错误信息（如缺少参数），无需调用此工具，直接根据返回的错误信息修正参数即可。",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_path": {
                        "type": "string",
                        "description": "要检查的节点或网络路径。如果是网络路径，会检查其下所有节点。留空则检查当前网络。"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_node_inputs",
            "description": "【连接前必用】获取节点输入端口信息(210个常用节点已缓存,快速返回)。连接多输入节点前必用!常见节点已包含完整信息,优先使用此工具而非get_houdini_node_doc。",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_type": {
                        "type": "string",
                        "description": "节点类型名称，如 'copytopoints', 'boolean', 'scatter'"
                    },
                    "category": {
                        "type": "string",
                        "enum": ["sop", "obj", "dop", "vop"],
                        "description": "节点类别，默认 'sop'"
                    }
                },
                "required": ["node_type"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "add_todo",
            "description": "添加一个任务到 Todo 列表。在开始复杂任务前，先用这个工具列出计划的步骤。",
            "parameters": {
                "type": "object",
                "properties": {
                    "todo_id": {
                        "type": "string",
                        "description": "任务唯一 ID，如 'step1', 'task_create_box'"
                    },
                    "text": {
                        "type": "string",
                        "description": "任务描述"
                    },
                    "status": {
                        "type": "string",
                        "enum": ["pending", "in_progress", "done", "error"],
                        "description": "任务状态，默认 pending"
                    }
                },
                "required": ["todo_id", "text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_todo",
            "description": "更新 Todo 任务状态。每完成一个步骤必须立即调用此工具标记为 done，不要等到最后统一标记。",
            "parameters": {
                "type": "object",
                "properties": {
                    "todo_id": {
                        "type": "string",
                        "description": "要更新的任务 ID"
                    },
                    "status": {
                        "type": "string",
                        "enum": ["pending", "in_progress", "done", "error"],
                        "description": "新状态"
                    }
                },
                "required": ["todo_id", "status"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "verify_and_summarize",
            "description": "【任务结束前必调用】验证节点网络并生成总结。自动检测:1.孤立节点 2.错误节点 3.连接完整性 4.显示标志。如果发现问题必须修复后重新调用，直到通过。在此之前应先用 get_network_structure 自行检查网络是否完整。",
            "parameters": {
                "type": "object",
                "properties": {
                    "check_items": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "要检查的项目列表(如节点名)"
                    },
                    "expected_result": {
                        "type": "string",
                        "description": "期望的结果描述"
                    }
                },
                "required": ["check_items", "expected_result"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_skill",
            "description": "执行预定义的 Skill（高级分析脚本）。Skill 是经过优化的专用脚本，比手写 execute_python 更可靠。用 list_skills 查看可用 skill。",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_name": {
                        "type": "string",
                        "description": "Skill 名称（用 list_skills 获取）"
                    },
                    "params": {
                        "type": "object",
                        "description": "传给 Skill 的参数（键值对）"
                    }
                },
                "required": ["skill_name", "params"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_skills",
            "description": "列出所有可用的 Skill 及其参数说明。在需要复杂分析（如几何属性统计、批量检查等）时，先调用此工具查看是否有现成 Skill。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    }
]


# ============================================================
# AI 客户端
# ============================================================

class AIClient:
    """AI 客户端，支持流式传输、Function Calling、联网搜索"""
    
    OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
    DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
    GLM_API_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    OLLAMA_API_URL = "http://localhost:11434/v1/chat/completions"  # Ollama OpenAI 兼容接口
    DUOJIE_API_URL = "https://api.duojie.games/v1/chat/completions"  # 拼好饭中转站

    def __init__(self, api_key: Optional[str] = None):
        self._api_keys: Dict[str, Optional[str]] = {
            'openai': api_key or self._read_api_key('openai'),
            'deepseek': self._read_api_key('deepseek'),
            'glm': self._read_api_key('glm'),
            'ollama': 'ollama',  # Ollama 不需要真正的 API key，但需要非空值
            'duojie': self._read_api_key('duojie'),
        }
        self._ssl_context = self._create_ssl_context()
        self._web_searcher = WebSearcher()
        self._tool_executor: Optional[Callable[[str, dict], dict]] = None
        
        # Ollama 配置
        self._ollama_base_url = "http://localhost:11434"
        
        # 网络配置
        self._max_retries = 3
        self._retry_delay = 1.0
        self._chunk_timeout = 60  # Ollama 本地模型可能较慢，增加超时
        
        # 停止控制（使用 threading.Event 保证线程安全）
        import threading
        self._stop_event = threading.Event()
    
    def request_stop(self):
        """请求停止当前请求（线程安全）"""
        self._stop_event.set()
    
    def reset_stop(self):
        """重置停止标志（线程安全）"""
        self._stop_event.clear()
    
    def is_stop_requested(self) -> bool:
        """检查是否请求了停止（线程安全）"""
        return self._stop_event.is_set()

    def set_tool_executor(self, executor: Callable[..., dict]):
        """设置工具执行器
        
        executor 签名: (tool_name: str, **kwargs) -> dict
        """
        self._tool_executor = executor

    # ----------------------------------------------------------
    # 工具结果分页：按行分段，让 AI 自主判断是否需要更多
    # ----------------------------------------------------------

    # 查询型工具 & 操作型工具分类（共用常量）
    _QUERY_TOOLS = frozenset({
        'get_network_structure', 'get_node_details', 'get_node_parameters',
        'list_children',
        'read_selection', 'search_node_types',
        'semantic_search_nodes', 'find_nodes_by_param', 'check_errors',
        'search_local_doc', 'get_houdini_node_doc', 'get_node_inputs',
        'execute_python', 'execute_shell', 'web_search', 'fetch_webpage',
        'run_skill', 'list_skills',
    })
    _OP_TOOLS = frozenset({
        'create_node', 'create_nodes_batch', 'connect_nodes',
        'set_node_parameter', 'create_wrangle_node',
    })

    @staticmethod
    def _paginate_result(text: str, max_lines: int = 50) -> str:
        """将工具结果按行分页，超出部分截断并附带分页提示。

        - 不超过 max_lines 行时原样返回
        - 超过时保留前 max_lines 行，并追加分页说明

        Args:
            text: 原始工具输出文本
            max_lines: 每页最大行数（默认 50）

        Returns:
            分页后的文本
        """
        if not text:
            return text
        lines = text.split('\n')
        total = len(lines)
        if total <= max_lines:
            return text
        page = '\n'.join(lines[:max_lines])
        return (
            f"{page}\n\n"
            f"[分页提示] 显示第 1-{max_lines} 行，共 {total} 行（已截断）。"
            f"当前信息如已足够请直接使用。"
            f"注意：用相同参数重复调用会得到相同结果。"
            f"如需更多信息请换用更精确的查询条件，或使用 fetch_webpage 获取特定 URL 的完整内容（支持 start_line 翻页）。"
        )

    # ------------------------------------------------------------------
    # 消息清洗：确保发送给 API 的消息格式正确
    # ------------------------------------------------------------------

    @staticmethod
    def _ensure_tool_call_ids(tool_calls: list) -> list:
        """确保每个 tool_call 都有有效的 id 字段
        
        代理 API（如 Duojie）有时不在第一个 chunk 提供 tool_call_id，
        导致后续 role:tool 消息的 tool_call_id 为空 → API 400 错误。
        """
        import uuid
        for tc in tool_calls:
            if not tc.get('id'):
                tc['id'] = f"call_{uuid.uuid4().hex[:24]}"
            # 确保 type 字段存在
            if not tc.get('type'):
                tc['type'] = 'function'
            # 确保 function 字段完整
            fn = tc.get('function', {})
            if not fn.get('name'):
                fn['name'] = 'unknown'
            if not fn.get('arguments', '').strip():
                fn['arguments'] = '{}'
            tc['function'] = fn
        return tool_calls

    # ----------------------------------------------------------
    # 智能摘要：提取工具结果的关键信息
    # ----------------------------------------------------------

    _PATH_RE = re.compile(r'/(?:obj|out|stage|tasks|ch|shop|img|mat|vex)/[\w/]+')
    _COUNT_RE = re.compile(r'(?:节点数量|点数量|错误数|警告数|count|total)[：:\s]*(\d+)', re.IGNORECASE)

    @classmethod
    def _summarize_tool_content(cls, content: str, max_len: int = 200) -> str:
        """智能摘要工具结果——提取关键信息而非简单截断

        提取优先级: 路径 > 数值统计 > 第一行摘要 > 截断
        """
        if not content or len(content) <= max_len:
            return content

        parts = []

        # 1. 提取节点路径
        paths = cls._PATH_RE.findall(content)
        if paths:
            unique_paths = list(dict.fromkeys(paths))[:5]  # 去重保留顺序
            parts.append("路径: " + ", ".join(unique_paths))

        # 2. 提取数量信息
        counts = cls._COUNT_RE.findall(content)
        if counts:
            parts.append("统计: " + ", ".join(counts[:4]))

        # 3. 检测成功/失败状态
        if '错误' in content[:100] or 'error' in content[:100].lower():
            # 错误信息——保留更多内容
            first_line = content.split('\n', 1)[0][:200]
            parts.append(first_line)
        elif not parts:
            # 没提取到结构化信息，保留第一行
            first_line = content.split('\n', 1)[0][:150]
            parts.append(first_line)

        summary = " | ".join(parts)
        if len(summary) > max_len:
            summary = summary[:max_len]
        return summary + '...[摘要]'

    # ----------------------------------------------------------
    # 渐进式裁剪
    # ----------------------------------------------------------

    def _progressive_trim(self, working_messages: list, tool_calls_history: list,
                          trim_level: int = 1) -> list:
        """渐进式裁剪上下文，根据 trim_level 逐步加大裁剪力度

        核心原则:
        - **永不截断 user 消息**（含任务目标）
        - 按「轮次」裁剪（assistant + 其 tool 结果 = 一轮）
        - tool 结果用智能摘要而非简单截断
        - 保留最近 N 轮完整对话

        trim_level=1: 轻度 - 智能压缩旧轮工具结果，保留最近 60% 消息
        trim_level=2: 中度 - 保留最近 5 轮完整对话
        trim_level=3+: 重度 - 保留最近 3 轮，激进压缩
        """
        if not working_messages:
            return working_messages

        sys_msg = working_messages[0] if working_messages[0].get('role') == 'system' else None
        body = working_messages[1:] if sys_msg else working_messages[:]

        if not body:
            return working_messages

        # --- 划分轮次：以 user 消息为分界 ---
        rounds = []  # [[msg, msg, ...], ...]
        current_round = []
        for m in body:
            if m.get('role') == 'user' and current_round:
                rounds.append(current_round)
                current_round = []
            current_round.append(m)
        if current_round:
            rounds.append(current_round)

        if trim_level <= 1:
            # 轻度：压缩非最近 40% 轮次的工具结果
            n_rounds = len(rounds)
            protect_n = max(3, int(n_rounds * 0.6))  # 保护最近 60%
            for r_idx, rnd in enumerate(rounds):
                if r_idx >= n_rounds - protect_n:
                    break  # 最近的轮次不压缩
                for m in rnd:
                    role = m.get('role', '')
                    if role == 'user':
                        continue  # 永不截断 user
                    c = m.get('content') or ''
                    if role == 'tool' and len(c) > 200:
                        m['content'] = self._summarize_tool_content(c, 200)
                    elif role == 'assistant' and len(c) > 400:
                        m['content'] = c[:400] + '...[已截断]'

            # 如果总消息仍然太多，移除最早的轮次
            keep_rounds = max(4, int(n_rounds * 0.6))
            if n_rounds > keep_rounds:
                rounds = rounds[-keep_rounds:]

        elif trim_level == 2:
            # 中度：保留最近 5 轮，工具结果用短摘要
            rounds = rounds[-5:] if len(rounds) > 5 else rounds
            for r_idx, rnd in enumerate(rounds):
                if r_idx >= len(rounds) - 2:
                    break  # 最近 2 轮不压缩
                for m in rnd:
                    role = m.get('role', '')
                    if role == 'user':
                        continue
                    c = m.get('content') or ''
                    if role == 'tool' and len(c) > 120:
                        m['content'] = self._summarize_tool_content(c, 120)
                    elif role == 'assistant' and len(c) > 250:
                        m['content'] = c[:250] + '...[已截断]'

        else:
            # 重度：保留最近 3 轮，激进压缩
            rounds = rounds[-3:] if len(rounds) > 3 else rounds
            for rnd in rounds[:-1]:  # 最后一轮不压缩
                for m in rnd:
                    role = m.get('role', '')
                    if role == 'user':
                        continue
                    c = m.get('content') or ''
                    if len(c) > 100:
                        m['content'] = self._summarize_tool_content(c, 100)

        # 重组
        body = [m for rnd in rounds for m in rnd]
        result = ([sys_msg] if sys_msg else []) + body

        # 恢复提示——只列操作型工具的摘要（跳过查询型）
        history_summary = ""
        if tool_calls_history:
            op_history = [h for h in tool_calls_history
                          if h['tool_name'] not in self._QUERY_TOOLS]
            if op_history:
                recent = op_history[-8:]
                lines = []
                for h in recent:
                    r = h.get('result', {})
                    status = 'ok' if (isinstance(r, dict) and r.get('success')) else 'err'
                    r_str = str(r.get('result', '') if isinstance(r, dict) else r)[:60]
                    lines.append(f"  [{status}] {h['tool_name']}: {r_str}")
                history_summary = "\n已完成的操作:\n" + "\n".join(lines)

        result.append({
            'role': 'system',
            'content': (
                f'[上下文管理] 已自动裁剪历史（级别 {trim_level}）。'
                f'{history_summary}'
                f'\n请继续完成当前任务。不要提及此裁剪。'
            )
        })

        print(f"[AI Client] 渐进式裁剪: level={trim_level}, "
              f"消息 {len(working_messages)} → {len(result)}, "
              f"轮次 {len(rounds)}")
        return result
    
    def _sanitize_working_messages(self, messages: list) -> list:
        """在发送给 API 之前清洗消息列表，修复常见格式问题
        
        修复项：
        1. assistant 消息中 tool_calls 的 id 为空
        2. role:tool 消息的 tool_call_id 与 assistant 中的 id 不匹配
        3. 移除无效的 tool 消息（没有对应 assistant tool_call）
        """
        # 收集所有有效的 tool_call_id
        valid_tc_ids = set()
        for msg in messages:
            if msg.get('role') == 'assistant' and 'tool_calls' in msg:
                self._ensure_tool_call_ids(msg['tool_calls'])
                for tc in msg['tool_calls']:
                    if tc.get('id'):
                        valid_tc_ids.add(tc['id'])
        
        # 修复 tool 消息的 tool_call_id
        sanitized = []
        for msg in messages:
            if msg.get('role') == 'tool':
                tc_id = msg.get('tool_call_id', '')
                if not tc_id or tc_id not in valid_tc_ids:
                    # 跳过孤儿 tool 消息（没有对应的 assistant tool_call）
                    continue
            sanitized.append(msg)
        return sanitized

    # 已自带分页的工具，不再二次截断
    _SELF_PAGED_TOOLS = frozenset({
        'get_houdini_node_doc', 'get_network_structure', 'get_node_parameters',
        'list_children', 'execute_python', 'execute_shell',
    })

    def _compress_tool_result(self, tool_name: str, result: dict) -> str:
        """统一工具结果压缩逻辑（供两种 agent loop 共用）

        策略：
        - 已自带分页的工具 → 直接返回（如 get_houdini_node_doc）
        - 查询工具 → 按行分页（默认 50 行）
        - 操作工具 → 提取路径，保留关键信息
        - 其他工具 → 适度截断
        - 失败 → 保留完整错误
        """
        if result.get('success'):
            content = result.get('result', '')
            # 已自带分页逻辑的工具，直接返回不再截断
            if tool_name in self._SELF_PAGED_TOOLS:
                return content
            if tool_name in self._QUERY_TOOLS:
                return self._paginate_result(content, max_lines=50)
            elif tool_name in self._OP_TOOLS:
                if len(content) > 300:
                    import re
                    paths = re.findall(r'[/\w]+(?:/[\w]+)+', content)
                    if paths:
                        content = ' '.join(paths[:5])
                        if len(content) > 300:
                            content = content[:300] + '...'
                    else:
                        content = content[:300]
                return content
            else:
                # 其他工具也按行分页，但更宽松
                return self._paginate_result(content, max_lines=80)
        else:
            error = result.get('error', '未知错误')
            return error[:500] if len(error) > 500 else error

    def _create_ssl_context(self):
        """创建 SSL 上下文。验证失败时回退到未验证模式（带警告）。"""
        try:
            context = ssl.create_default_context()
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            return context
        except Exception as e:
            print(f"[AI Client] ⚠️ SSL 证书验证失败 ({e})，回退到未验证模式。这可能存在安全风险。")
            try:
                return ssl._create_unverified_context()
            except Exception:
                return None

    def _read_api_key(self, provider: str) -> Optional[str]:
        provider = (provider or 'openai').lower()
        
        # Ollama 不需要 API key
        if provider == 'ollama':
            return 'ollama'
        
        env_map = {
            'openai': ['OPENAI_API_KEY', 'DCC_AI_OPENAI_API_KEY'],
            'deepseek': ['DEEPSEEK_API_KEY', 'DCC_AI_DEEPSEEK_API_KEY'],
            'glm': ['GLM_API_KEY', 'ZHIPU_API_KEY', 'DCC_AI_GLM_API_KEY'],
            'duojie': ['DUOJIE_API_KEY', 'DCC_AI_DUOJIE_API_KEY'],
        }
        for env_var in env_map.get(provider, []):
            key = os.environ.get(env_var)
            if key:
                return key
        cfg, _ = load_config('ai', dcc_type='houdini')
        if cfg:
            key_map = {
                'openai': 'openai_api_key', 'deepseek': 'deepseek_api_key',
                'glm': 'glm_api_key', 'duojie': 'duojie_api_key',
            }
            return cfg.get(key_map.get(provider, '')) or None
        return None

    def has_api_key(self, provider: str = 'openai') -> bool:
        provider = (provider or 'openai').lower()
        # Ollama 总是可用（本地服务）
        if provider == 'ollama':
            return True
        return bool(self._api_keys.get(provider))

    def _get_api_key(self, provider: str) -> Optional[str]:
        return self._api_keys.get((provider or 'openai').lower())

    def set_api_key(self, key: str, persist: bool = False, provider: str = 'openai') -> bool:
        provider = (provider or 'openai').lower()
        key = (key or '').strip()
        if not key:
            return False
        self._api_keys[provider] = key
        if persist:
            cfg, _ = load_config('ai', dcc_type='houdini')
            cfg = cfg or {}
            key_map = {'openai': 'openai_api_key', 'deepseek': 'deepseek_api_key', 'glm': 'glm_api_key'}
            cfg[key_map.get(provider, f'{provider}_api_key')] = key
            ok, _ = save_config('ai', cfg, dcc_type='houdini')
            return ok
        return True

    def get_masked_key(self, provider: str = 'openai') -> str:
        provider = (provider or 'openai').lower()
        # Ollama 显示本地状态
        if provider == 'ollama':
            return 'Local'
        key = self._get_api_key(provider)
        if not key:
            return ''
        if len(key) <= 10:
            return '*' * len(key)
        return key[:5] + '...' + key[-4:]

    def _get_api_url(self, provider: str) -> str:
        provider = (provider or 'openai').lower()
        if provider == 'deepseek':
            return self.DEEPSEEK_API_URL
        elif provider == 'glm':
            return self.GLM_API_URL
        elif provider == 'ollama':
            return self.OLLAMA_API_URL
        elif provider == 'duojie':
            return self.DUOJIE_API_URL
        return self.OPENAI_API_URL

    def _get_vendor_name(self, provider: str) -> str:
        names = {
            'openai': 'OpenAI', 'deepseek': 'DeepSeek',
            'glm': 'GLM（智谱AI）', 'ollama': 'Ollama',
            'duojie': '拼好饭',
        }
        return names.get(provider, provider)
    
    def set_ollama_url(self, base_url: str):
        """设置 Ollama 服务地址"""
        self._ollama_base_url = base_url.rstrip('/')
        self.OLLAMA_API_URL = f"{self._ollama_base_url}/v1/chat/completions"
    
    def get_ollama_models(self) -> List[str]:
        """获取 Ollama 可用的模型列表"""
        if not HAS_REQUESTS:
            return ['qwen2.5:14b']
        
        try:
            response = requests.get(
                f"{self._ollama_base_url}/api/tags",
                timeout=5
            )
            if response.status_code == 200:
                data = response.json()
                models = [m.get('name', '') for m in data.get('models', [])]
                return models if models else ['qwen2.5:14b']
        except Exception:
            pass
        
        return ['qwen2.5:14b']  # 默认模型

    def test_connection(self, provider: str = 'deepseek') -> Dict[str, Any]:
        """测试连接"""
        provider = (provider or 'deepseek').lower()
        
        # Ollama 特殊处理
        if provider == 'ollama':
            try:
                if HAS_REQUESTS:
                    response = requests.get(
                        f"{self._ollama_base_url}/api/tags",
                        timeout=5
                    )
                    if response.status_code == 200:
                        return {'ok': True, 'url': self._ollama_base_url, 'status': 200}
                    return {'ok': False, 'error': f'Ollama 服务响应异常: {response.status_code}'}
            except Exception as e:
                return {'ok': False, 'error': f'无法连接 Ollama 服务: {str(e)}'}
        
        api_key = self._get_api_key(provider)
        if not api_key:
            return {'ok': False, 'error': f'缺少 API Key'}
        
        try:
            if HAS_REQUESTS:
                response = requests.post(
                    self._get_api_url(provider),
                    json={'model': self._get_default_model(provider), 'messages': [{'role': 'user', 'content': 'hi'}], 'max_tokens': 1},
                    headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
                    timeout=15,
                    proxies={'http': None, 'https': None}
                )
                return {'ok': True, 'url': self._get_api_url(provider), 'status': response.status_code}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    def _get_default_model(self, provider: str) -> str:
        defaults = {
            'openai': 'gpt-4o-mini', 
            'deepseek': 'deepseek-chat', 
            'glm': 'glm-4.7',
            'ollama': 'qwen2.5:14b'
        }
        return defaults.get(provider, 'gpt-4o-mini')

    # ============================================================
    # 模型特性判断
    # ============================================================
    
    @staticmethod
    def is_reasoning_model(model: str) -> bool:
        """判断模型是否为原生推理模型（API 返回 reasoning_content 字段）
        
        仅限明确通过 reasoning_content 字段返回推理的模型：
        DeepSeek-R1/Reasoner, GLM-Z1 系列, GLM-4.7, 以及 -think 后缀的模型
        """
        m = model.lower()
        return (
            'reasoner' in m or 'r1' in m
            or m.startswith('glm-z1')
            or m == 'glm-4.7'
            or m.endswith('-think')  # Duojie/Factory think 变体
        )
    
    @staticmethod
    def is_glm47(model: str) -> bool:
        """判断是否为 GLM-4.7 模型"""
        return model.lower() == 'glm-4.7'
    
    # Duojie 模型 → think 变体映射
    # 当 Think 开关 ON 时，自动替换模型名以启用原生 Extended Thinking
    _DUOJIE_THINK_MAP = {
        'claude-opus-4-5-kiro': 'claude-opus-4-5-kiro',      # kiro 本身已含思考
        'claude-opus-4-5-max': 'claude-opus-4-5-max',         # 保持不变
        'claude-sonnet-4-5': 'claude-sonnet-4-5',             # 无已知 think 变体
        'claude-haiku-4-5': 'claude-haiku-4-5',               # 无已知 think 变体
    }
    
    @classmethod
    def _duojie_think_model(cls, model: str) -> str:
        """当 Think 开启时，将 Duojie 模型名映射到 think 变体"""
        return cls._DUOJIE_THINK_MAP.get(model, model)
    
    # ============================================================
    # Usage 解析
    # ============================================================
    
    @staticmethod
    def _parse_usage(usage: dict) -> dict:
        """解析 API 返回的 usage 数据为统一格式"""
        if not usage:
            return {}
        prompt_tokens = usage.get('prompt_tokens', 0)
        cache_hit = usage.get('prompt_cache_hit_tokens', 0)
        cache_miss = usage.get('prompt_cache_miss_tokens', 0)
        completion = usage.get('completion_tokens', 0)
        total = usage.get('total_tokens', 0) or (prompt_tokens + completion)
        return {
            'prompt_tokens': prompt_tokens,
            'completion_tokens': completion,
            'total_tokens': total,
            'cache_hit_tokens': cache_hit,
            'cache_miss_tokens': cache_miss,
            'cache_hit_rate': (cache_hit / prompt_tokens) if prompt_tokens > 0 else 0,
        }
    
    # ============================================================
    # 流式传输 Chat
    # ============================================================
    
    def chat_stream(self,
                    messages: List[Dict[str, str]],
                    model: str = 'gpt-4o-mini',
                    provider: str = 'openai',
                    temperature: float = 0.3,
                    max_tokens: Optional[int] = None,
                    tools: Optional[List[dict]] = None,
                    tool_choice: str = 'auto',
                    enable_thinking: bool = True) -> Generator[Dict[str, Any], None, None]:
        """流式 Chat API
        
        Yields:
            {"type": "content", "content": str}  # 内容片段
            {"type": "tool_call", "tool_call": dict}  # 工具调用
            {"type": "thinking", "content": str}  # 思考内容（DeepSeek）
            {"type": "done", "finish_reason": str}  # 完成
            {"type": "error", "error": str}  # 错误
        """
        if not HAS_REQUESTS:
            yield {"type": "error", "error": "需要安装 requests 库"}
            return
        
        provider = (provider or 'openai').lower()
        api_key = self._get_api_key(provider)
        
        # Ollama 不需要 API Key 验证
        if provider != 'ollama' and not api_key:
            yield {"type": "error", "error": f"缺少 {self._get_vendor_name(provider)} API Key"}
            return
        
        api_url = self._get_api_url(provider)
        
        payload = {
            'model': model,
            'messages': messages,
            'temperature': temperature,
            'stream': True,
            # 必须加 stream_options 才能在流式响应中获取 usage 统计
            'stream_options': {'include_usage': True},
        }
        if max_tokens:
            payload['max_tokens'] = max_tokens
        
        # GLM-4.7 专属参数（仅原生 GLM 接口）：深度思考 + 流式工具调用
        if self.is_glm47(model) and provider == 'glm' and enable_thinking:
            payload['thinking'] = {'type': 'enabled'}
            if tools:
                payload['tool_stream'] = True
        
        # Duojie 中转：当 Think 开启时，自动映射到 think 模型变体
        if provider == 'duojie' and enable_thinking:
            actual_model = self._duojie_think_model(model)
            if actual_model != model:
                payload['model'] = actual_model
                print(f"[AI Client] Duojie Think: {model} -> {actual_model}")
        
        # DeepSeek / OpenAI prompt caching 自动启用（保持前缀稳定即可命中）
        
        # Ollama 的工具调用支持（如果模型支持）
        if tools:
            payload['tools'] = tools
            payload['tool_choice'] = tool_choice
        
        # 构建请求头
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'text/event-stream',
        }
        
        # Ollama 不需要 Authorization 头
        if provider != 'ollama':
            headers['Authorization'] = f'Bearer {api_key}'
        
        # 重试逻辑
        print(f"[AI Client] Requesting {api_url} with model {model}")
        for attempt in range(self._max_retries):
            try:
                with requests.post(
                    api_url,
                    json=payload,
                    headers=headers,
                    stream=True,
                    timeout=(10, self._chunk_timeout),  # (连接超时, 读取超时)
                    proxies={'http': None, 'https': None}
                ) as response:
                    # 强制 UTF-8 编码（requests 对 text/event-stream 默认 ISO-8859-1，会导致中文乱码）
                    response.encoding = 'utf-8'
                    print(f"[AI Client] Response status: {response.status_code}")
                    
                    if response.status_code != 200:
                        try:
                            err = response.json()
                            err_msg = err.get('error', {}).get('message', response.text)
                        except:
                            err_msg = response.text
                        print(f"[AI Client] Error: {err_msg}")
                        
                        # 5xx 服务端错误（502/503/529 等）可重试
                        if response.status_code >= 500 and attempt < self._max_retries - 1:
                            wait = self._retry_delay * (attempt + 1)
                            print(f"[AI Client] Server error {response.status_code}, retrying in {wait}s...")
                            time.sleep(wait)
                            continue  # 重试
                        
                        yield {"type": "error", "error": f"HTTP {response.status_code}: {err_msg}"}
                        return
                    
                    # 解析 SSE 流
                    tool_calls_buffer = {}  # 缓存工具调用片段
                    pending_usage = {}  # 收集 usage 数据
                    last_finish_reason = None
                    
                    # ── 使用 iter_content + 增量解码器 + 手动分行 ──
                    # 比 iter_lines() 更健壮：
                    #   1. iter_content() 返回 HTTP body 原始字节块
                    #   2. 增量解码器正确处理跨 chunk 切断的多字节 UTF-8
                    #   3. 手动按 \n 分行，避免 requests 内部分行时的编码干扰
                    import codecs
                    _utf8_decoder = codecs.getincrementaldecoder('utf-8')(errors='ignore')
                    _line_buf = ""  # 解码后、尚未遇到 \n 的文本缓冲
                    
                    def _process_sse_line(line):
                        """处理单行 SSE data，返回要 yield 的 dict 列表"""
                        nonlocal tool_calls_buffer, pending_usage, last_finish_reason
                        results = []
                        
                        if not line.startswith('data: '):
                            return results
                        
                        data_str = line[6:]
                        
                        if data_str.strip() == '[DONE]':
                            print(f"[AI Client] Received [DONE], usage={pending_usage}")
                            results.append({"type": "done", "finish_reason": last_finish_reason or "stop", "usage": pending_usage})
                            return results
                        
                        try:
                            data = json.loads(data_str)
                        except json.JSONDecodeError:
                            return results
                        
                        choices = data.get('choices', [])
                        usage_data = data.get('usage')
                        
                        # usage-only chunk
                        if usage_data:
                            pending_usage = self._parse_usage(usage_data)
                        
                        if not choices:
                            return results
                        
                        choice = choices[0]
                        delta = choice.get('delta', {})
                        finish_reason = choice.get('finish_reason')
                        
                        # 思考内容
                        if 'reasoning_content' in delta and delta['reasoning_content']:
                            results.append({"type": "thinking", "content": delta['reasoning_content']})
                        
                        # 普通内容
                        if 'content' in delta and delta['content']:
                            results.append({"type": "content", "content": delta['content']})
                        
                        # 工具调用
                        if 'tool_calls' in delta:
                            for tc in delta['tool_calls']:
                                idx = tc.get('index', 0)
                                tc_id = tc.get('id', '')
                                
                                if tc_id and idx in tool_calls_buffer:
                                    existing_id = tool_calls_buffer[idx].get('id', '')
                                    if existing_id and existing_id != tc_id:
                                        idx = max(tool_calls_buffer.keys()) + 1
                                
                                if idx not in tool_calls_buffer:
                                    tool_calls_buffer[idx] = {
                                        'id': tc_id,
                                        'type': 'function',
                                        'function': {'name': '', 'arguments': ''}
                                    }
                                
                                if tc_id:
                                    tool_calls_buffer[idx]['id'] = tc_id
                                if 'function' in tc:
                                    fn = tc['function']
                                    if 'name' in fn and fn['name']:
                                        tool_calls_buffer[idx]['function']['name'] = fn['name']
                                    if 'arguments' in fn:
                                        tool_calls_buffer[idx]['function']['arguments'] += fn['arguments']
                        
                        # 完成（先发送工具调用，但不 return，等后续 usage chunk / [DONE]）
                        if finish_reason:
                            if tool_calls_buffer:
                                for idx_k in sorted(tool_calls_buffer.keys()):
                                    results.append({"type": "tool_call", "tool_call": tool_calls_buffer[idx_k]})
                                tool_calls_buffer = {}
                            last_finish_reason = finish_reason
                        
                        return results
                    
                    # ── 主循环：读取原始字节块 → 解码 → 分行 → 处理 ──
                    _should_return = False
                    for raw_chunk in response.iter_content(chunk_size=4096, decode_unicode=False):
                        if not raw_chunk:
                            continue
                        
                        if self._stop_event.is_set():
                            yield {"type": "stopped", "message": "用户停止了请求"}
                            return
                        
                        # 增量解码：跨 chunk 的多字节 UTF-8 字符在此正确拼合
                        decoded = _utf8_decoder.decode(raw_chunk)
                        _line_buf += decoded
                        
                        # 逐行分割并处理
                        while '\n' in _line_buf:
                            one_line, _line_buf = _line_buf.split('\n', 1)
                            one_line = one_line.rstrip('\r')
                            if not one_line:
                                continue
                            
                            for item in _process_sse_line(one_line):
                                yield item
                                if item.get('type') == 'done':
                                    _should_return = True
                        
                        if _should_return:
                            return
                    
                    # 处理缓冲区残留（流结束时没有以 \n 结尾的尾行）
                    _line_buf += _utf8_decoder.decode(b'', final=True)
                    if _line_buf.strip():
                        for item in _process_sse_line(_line_buf.strip()):
                            yield item
                            if item.get('type') == 'done':
                                return
                    
                    # 流结束但没有收到 [DONE]
                    if tool_calls_buffer:
                        for idx in sorted(tool_calls_buffer.keys()):
                            yield {"type": "tool_call", "tool_call": tool_calls_buffer[idx]}
                    yield {"type": "done", "finish_reason": last_finish_reason or "stop", "usage": pending_usage}
                    return
                    
            except requests.exceptions.Timeout:
                if attempt < self._max_retries - 1:
                    time.sleep(self._retry_delay * (attempt + 1))
                    continue
                yield {"type": "error", "error": f"请求超时（已重试 {self._max_retries} 次）"}
                return
            except requests.exceptions.ConnectionError as e:
                if attempt < self._max_retries - 1:
                    time.sleep(self._retry_delay * (attempt + 1))
                    continue
                yield {"type": "error", "error": f"连接错误: {str(e)}"}
                return
            except Exception as e:
                yield {"type": "error", "error": f"请求失败: {str(e)}"}
                return

    # ============================================================
    # 非流式 Chat（保留兼容性）
    # ============================================================
    
    def chat(self,
             messages: List[Dict[str, str]],
             model: str = 'gpt-4o-mini',
             provider: str = 'openai',
             temperature: float = 0.3,
             max_tokens: Optional[int] = None,
             timeout: int = 60,
             tools: Optional[List[dict]] = None,
             tool_choice: str = 'auto') -> Dict[str, Any]:
        """非流式 Chat（兼容旧接口）"""
        
        if not HAS_REQUESTS:
            return {'ok': False, 'error': '需要安装 requests 库'}
        
        provider = (provider or 'openai').lower()
        api_key = self._get_api_key(provider)
        if not api_key:
            return {'ok': False, 'error': f'缺少 API Key'}
        
        payload = {
            'model': model,
            'messages': messages,
            'temperature': temperature,
        }
        if max_tokens:
            payload['max_tokens'] = max_tokens
        
        # GLM-4.7 专属参数（仅原生 GLM 接口）
        if self.is_glm47(model) and provider == 'glm':
            payload['thinking'] = {'type': 'enabled'}
        
        # DeepSeek / OpenAI prompt caching 自动启用
        
        if tools:
            payload['tools'] = tools
            payload['tool_choice'] = tool_choice
        
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}',
        }
        
        for attempt in range(self._max_retries):
            try:
                response = requests.post(
                    self._get_api_url(provider),
                    json=payload,
                    headers=headers,
                    timeout=timeout,
                    proxies={'http': None, 'https': None}
                )
                response.raise_for_status()
                obj = response.json()
                
                choice = obj.get('choices', [{}])[0]
                message = choice.get('message', {})
                
                return {
                    'ok': True,
                    'content': message.get('content'),
                    'tool_calls': message.get('tool_calls'),
                    'finish_reason': choice.get('finish_reason'),
                    'usage': self._parse_usage(obj.get('usage', {})),
                    'raw': obj
                }
            except requests.exceptions.Timeout:
                if attempt < self._max_retries - 1:
                    time.sleep(self._retry_delay)
                    continue
                return {'ok': False, 'error': '请求超时'}
            except Exception as e:
                if attempt < self._max_retries - 1:
                    time.sleep(self._retry_delay)
                    continue
                return {'ok': False, 'error': str(e)}
        
        return {'ok': False, 'error': '请求失败'}

    # ============================================================
    # Agent Loop（流式版本）
    # ============================================================
    
    def agent_loop_stream(self,
                          messages: List[Dict[str, Any]],
                          model: str = 'gpt-4o-mini',
                          provider: str = 'openai',
                          max_iterations: int = 15,
                          temperature: float = 0.3,
                          max_tokens: Optional[int] = None,
                          enable_thinking: bool = True,
                          on_content: Optional[Callable[[str], None]] = None,
                          on_thinking: Optional[Callable[[str], None]] = None,
                          on_tool_call: Optional[Callable[[str, dict], None]] = None,
                          on_tool_result: Optional[Callable[[str, dict, dict], None]] = None) -> Dict[str, Any]:
        """流式 Agent Loop
        
        Args:
            enable_thinking: 是否启用思考模式（影响原生推理模型的 thinking 参数）
            on_content: 内容回调 (content) -> None
            on_thinking: 思考回调 (content) -> None
            on_tool_call: 工具调用开始回调 (name, args) -> None
            on_tool_result: 工具结果回调 (name, args, result) -> None
        
        Returns:
            {"ok": bool, "content": str, "tool_calls_history": list, "iterations": int}
        """
        if not self._tool_executor:
            return {'ok': False, 'error': '未设置工具执行器', 'content': '', 'tool_calls_history': [], 'iterations': 0}
        
        working_messages = list(messages)
        tool_calls_history = []
        full_content = ""
        iteration = 0
        
        # 累积 usage 统计（用于 cache 命中率统计）
        total_usage = {
            'prompt_tokens': 0,
            'completion_tokens': 0,
            'total_tokens': 0,
            'cache_hit_tokens': 0,
            'cache_miss_tokens': 0,
        }
        
        # 防止死循环：检测重复工具调用
        recent_tool_signatures = []  # 最近的工具调用签名
        max_tool_calls = 999  # 不限制总调用次数（仅保留连续重复检测）
        total_tool_calls = 0
        consecutive_same_calls = 0  # 连续相同调用计数
        last_call_signature = None
        server_error_retries = 0    # 连续服务端错误重试计数
        max_server_retries = 3      # 最多重试 3 次服务端错误
        
        while iteration < max_iterations:
            # 检查停止请求
            if self._stop_event.is_set():
                return {
                    'ok': False,
                    'error': '用户停止了请求',
                    'content': full_content,
                    'tool_calls_history': tool_calls_history,
                    'iterations': iteration,
                    'stopped': True,
                    'usage': total_usage
                }
            
            iteration += 1
            
            # 收集本轮的内容和工具调用
            round_content = ""
            round_thinking = ""
            round_tool_calls = []
            should_retry = False  # 错误恢复标志
            should_abort = False  # 不可恢复错误标志
            abort_error = ""
            
            # 发送前清洗消息（修复 tool_call_id 缺失等问题）
            working_messages = self._sanitize_working_messages(working_messages)
            
            # ⚠️ 主动防御：每轮迭代前检查 working_messages 大小
            # agent loop 多轮后工具结果会不断累积，如果不提前压缩会导致 API 报错
            if iteration > 1 and len(working_messages) > 20:
                # 保护最近 6 条消息不压缩（通常是当前轮次的完整交互）
                protect_start = max(1, len(working_messages) - 6)
                for i, m in enumerate(working_messages):
                    if i == 0 or i >= protect_start:  # 跳过 system + 最近消息
                        continue
                    role = m.get('role', '')
                    if role == 'user':  # 永不截断 user 消息
                        continue
                    c = m.get('content') or ''
                    if role == 'tool' and len(c) > 400:
                        m['content'] = self._summarize_tool_content(c, 400)
                    elif role == 'assistant' and len(c) > 600:
                        m['content'] = c[:600] + '...[已截断]'
            
            # 诊断：打印第二次及之后请求的消息结构
            if iteration > 1:
                print(f"[AI Client] === DEBUG iteration={iteration} messages ({len(working_messages)}) ===")
                for i, m in enumerate(working_messages):
                    role = m.get('role', '?')
                    tc = m.get('tool_calls')
                    tc_id = m.get('tool_call_id', '')
                    content = m.get('content')
                    content_repr = repr(content)[:120] if content else repr(content)
                    extras = {k: v for k, v in m.items() if k not in ('role', 'content', 'tool_calls', 'tool_call_id')}
                    if tc:
                        print(f"  [{i}] role={role}, content={content_repr}, tool_calls={json.dumps(tc, ensure_ascii=False)[:300]}, extras={extras}")
                    elif tc_id:
                        print(f"  [{i}] role={role}, tool_call_id={tc_id}, content={content_repr}, extras={extras}")
                    else:
                        print(f"  [{i}] role={role}, content={content_repr}, extras={extras}")
                print(f"[AI Client] === END DEBUG ===")
            
            # 流式请求
            for chunk in self.chat_stream(
                messages=working_messages,
                model=model,
                provider=provider,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=HOUDINI_TOOLS,
                tool_choice='auto',
                enable_thinking=enable_thinking
            ):
                # 检查停止请求
                if self._stop_event.is_set():
                    return {
                        'ok': False,
                        'error': '用户停止了请求',
                        'content': full_content + round_content,
                        'tool_calls_history': tool_calls_history,
                        'iterations': iteration,
                        'stopped': True,
                        'usage': total_usage
                    }
                
                chunk_type = chunk.get('type')
                
                if chunk_type == 'stopped':
                    return {
                        'ok': False,
                        'error': '用户停止了请求',
                        'content': full_content + round_content,
                        'tool_calls_history': tool_calls_history,
                        'iterations': iteration,
                        'stopped': True,
                        'usage': total_usage
                    }
                
                if chunk_type == 'content':
                    content = chunk.get('content', '')
                    # 清理XML标签（在流式输出时就清理，避免污染）
                    import re
                    cleaned_chunk = re.sub(r'</?tool_call[^>]*>', '', content)
                    cleaned_chunk = re.sub(r'<arg_key>([^<]+)</arg_key>\s*<arg_value>([^<]+)</arg_value>', '', cleaned_chunk)
                    cleaned_chunk = re.sub(r'</?arg_key[^>]*>', '', cleaned_chunk)
                    cleaned_chunk = re.sub(r'</?arg_value[^>]*>', '', cleaned_chunk)
                    cleaned_chunk = re.sub(r'</?redacted_reasoning[^>]*>', '', cleaned_chunk)
                    round_content += cleaned_chunk
                    if on_content and cleaned_chunk:
                        on_content(cleaned_chunk)
                
                elif chunk_type == 'thinking':
                    thinking_text = chunk.get('content', '')
                    round_thinking += thinking_text
                    if on_thinking and thinking_text:
                        on_thinking(thinking_text)
                
                elif chunk_type == 'tool_call':
                    tc = chunk.get('tool_call')
                    print(f"[AI Client] Tool call: {tc.get('function', {}).get('name', 'unknown')}")
                    round_tool_calls.append(tc)
                
                elif chunk_type == 'error':
                    error_msg = chunk.get('error', '')
                    error_lower = error_msg.lower()
                    print(f"[AI Client] Agent loop error at iteration {iteration}: {error_msg}")
                    
                    # ---- 精确分类错误类型 ----
                    # 1. 真正的上下文超限（API 明确告知 token 超限）
                    is_context_exceeded = any(k in error_lower for k in (
                        'context_length_exceeded', 'maximum context length',
                        'max_tokens', 'token limit', 'too many tokens',
                        'request too large', 'payload too large',
                        'context window', 'input too long',
                    )) or ('HTTP 413' in error_msg)
                    
                    # 2. 临时服务器错误（502/503/529 等，不一定与上下文大小有关）
                    is_server_transient = any(k in error_msg for k in (
                        'HTTP 502', 'HTTP 503', 'HTTP 529', 'no available'
                    ))
                    
                    # 3. 压缩/格式问题
                    is_format_error = ('HTTP 4' in error_msg and not is_context_exceeded and iteration > 1)
                    is_compress_fail = '压缩失败' in error_msg
                    
                    is_recoverable = is_context_exceeded or is_server_transient or is_format_error or is_compress_fail
                    
                    if is_recoverable:
                        server_error_retries += 1
                        
                        # 超过最大重试次数 → 停止
                        if server_error_retries > max_server_retries:
                            print(f"[AI Client] 错误已重试 {max_server_retries} 次，放弃")
                            if on_content:
                                on_content(f"\n[连续出错 {max_server_retries} 次，已停止重试。请稍后再试。]\n")
                            should_abort = True
                            abort_error = f"连续出错 {max_server_retries} 次: {error_msg}"
                            break
                        
                        cleanup_count = 0
                        
                        if is_context_exceeded:
                            # ---- 真正的上下文超限：渐进式裁剪 ----
                            print(f"[AI Client] 上下文超限，进行渐进式裁剪 (第{server_error_retries}次)")
                            if on_content:
                                on_content(f"\n[上下文超限，正在智能裁剪后重试 ({server_error_retries}/{max_server_retries})...]\n")
                            
                            old_len = len(working_messages)
                            working_messages = self._progressive_trim(
                                working_messages, tool_calls_history,
                                trim_level=server_error_retries  # 逐次加大裁剪力度
                            )
                            cleanup_count = old_len - len(working_messages)
                            
                        elif is_server_transient or is_compress_fail:
                            # ---- 临时服务器错误：先等待重试，不急着裁剪 ----
                            wait_seconds = 5 * server_error_retries
                            if on_content:
                                on_content(f"\n[服务端暂时不可用，{wait_seconds}秒后重试 ({server_error_retries}/{max_server_retries})...]\n")
                            time.sleep(wait_seconds)
                            
                            # 只在第2次及以后重试时才裁剪（第1次纯等待重试，给服务器恢复机会）
                            if server_error_retries >= 2:
                                print(f"[AI Client] 服务端连续出错，尝试轻度裁剪上下文")
                                old_len = len(working_messages)
                                working_messages = self._progressive_trim(
                                    working_messages, tool_calls_history,
                                    trim_level=server_error_retries - 1  # 比上下文超限更温和
                                )
                                cleanup_count = old_len - len(working_messages)
                            
                        else:
                            # ---- 4xx 格式问题 → 移除末尾可能有问题的消息 ----
                            while (working_messages and cleanup_count < 20 and
                                   working_messages[-1].get('role') in ('tool', 'system')
                                   and working_messages[-1] is not messages[0]):
                                working_messages.pop()
                                cleanup_count += 1
                            if working_messages and working_messages[-1].get('role') == 'assistant':
                                working_messages.pop()
                                cleanup_count += 1
                        
                        print(f"[AI Client] 重试 {server_error_retries}/{max_server_retries}, 移除了 {cleanup_count} 条消息")
                        should_retry = True
                        break  # 退出 for 循环，回到 while 循环重试
                    
                    # 无法恢复
                    should_abort = True
                    abort_error = error_msg
                    break  # 退出 for 循环
                
                elif chunk_type == 'done':
                    # 成功收到响应 → 重置服务端错误重试计数
                    server_error_retries = 0
                    # 收集 usage 信息（包含 cache 统计）
                    usage = chunk.get('usage', {})
                    if usage:
                        total_usage['prompt_tokens'] += usage.get('prompt_tokens', 0)
                        total_usage['completion_tokens'] += usage.get('completion_tokens', 0)
                        total_usage['total_tokens'] += usage.get('total_tokens', 0)
                        total_usage['cache_hit_tokens'] += usage.get('cache_hit_tokens', 0)
                        total_usage['cache_miss_tokens'] += usage.get('cache_miss_tokens', 0)
                    break
            
            # 错误恢复：跳过本轮剩余逻辑，重新请求 API
            if should_retry:
                full_content += round_content
                continue  # 正确地重新进入 while 循环
            
            # 不可恢复错误：返回
            if should_abort:
                return {
                    'ok': False,
                    'error': abort_error,
                    'content': full_content,
                    'tool_calls_history': tool_calls_history,
                    'iterations': iteration,
                    'usage': total_usage
                }
            
            # 如果没有工具调用，完成
            if not round_tool_calls:
                full_content += round_content
                # 计算 cache 命中率
                prompt_total = total_usage['cache_hit_tokens'] + total_usage['cache_miss_tokens']
                if prompt_total > 0:
                    total_usage['cache_hit_rate'] = total_usage['cache_hit_tokens'] / prompt_total
                else:
                    total_usage['cache_hit_rate'] = 0
                return {
                    'ok': True,
                    'content': full_content,
                    'tool_calls_history': tool_calls_history,
                    'iterations': iteration,
                    'usage': total_usage
                }
            
            # 添加助手消息（确保 tool_call ID 完整）
            self._ensure_tool_call_ids(round_tool_calls)
            assistant_msg = {'role': 'assistant', 'tool_calls': round_tool_calls}
            # content 为空时必须传 None（null）而非空字符串
            # Claude/Anthropic 兼容代理拒绝 content="" + tool_calls 共存
            assistant_msg['content'] = round_content or None
            # reasoning_content 仅 DeepSeek / 原生 GLM 需要（Duojie 等 OpenAI 兼容代理不支持）
            if self.is_reasoning_model(model) and provider in ('deepseek', 'glm'):
                assistant_msg['reasoning_content'] = round_thinking or ''
            working_messages.append(assistant_msg)
            
            # 执行工具调用（web 工具并行，Houdini 工具串行）
            # 预处理所有工具调用
            parsed_calls = []
            for tool_call in round_tool_calls:
                tool_id = tool_call.get('id', '')
                function = tool_call.get('function', {})
                tool_name = function.get('name', '')
                args_str = function.get('arguments', '{}')
                try:
                    arguments = json.loads(args_str)
                except:
                    arguments = {}
                parsed_calls.append((tool_id, tool_name, arguments, tool_call))

            # 分离可并行工具（web + shell）和 Houdini 工具（需主线程串行）
            _ASYNC_TOOL_NAMES = frozenset({'web_search', 'fetch_webpage', 'execute_shell'})
            async_calls = [(i, pc) for i, pc in enumerate(parsed_calls) if pc[1] in _ASYNC_TOOL_NAMES]
            houdini_calls = [(i, pc) for i, pc in enumerate(parsed_calls) if pc[1] not in _ASYNC_TOOL_NAMES]

            # 结果槽位：保持原始顺序
            results_ordered = [None] * len(parsed_calls)

            # --- 并行执行 async 工具（web + shell） ---
            if len(async_calls) > 1:
                import concurrent.futures
                def _exec_async(idx_pc):
                    idx, (tid, tname, targs, _tc) = idx_pc
                    if tname == 'web_search':
                        return idx, self._execute_web_search(targs)
                    elif tname == 'fetch_webpage':
                        return idx, self._execute_fetch_webpage(targs)
                    else:  # execute_shell
                        return idx, self._tool_executor(tname, **targs)
                with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(async_calls))) as pool:
                    for idx, result in pool.map(_exec_async, async_calls):
                        results_ordered[idx] = result
            elif len(async_calls) == 1:
                idx, (tid, tname, targs, _tc) = async_calls[0]
                if tname == 'web_search':
                    results_ordered[idx] = self._execute_web_search(targs)
                elif tname == 'fetch_webpage':
                    results_ordered[idx] = self._execute_fetch_webpage(targs)
                else:  # execute_shell
                    results_ordered[idx] = self._tool_executor(tname, **targs)

            # --- 串行执行 Houdini 工具（需主线程） ---
            for idx, (tid, tname, targs, _tc) in houdini_calls:
                results_ordered[idx] = self._tool_executor(tname, **targs)
                # Houdini 工具间延迟，防止 API 过快
                time.sleep(0.05)

            # --- 统一处理结果（保持原始顺序） ---
            should_break_tool_limit = False
            for i, (tool_id, tool_name, arguments, _tc) in enumerate(parsed_calls):
                result = results_ordered[i]

                # 防止死循环：检测重复工具调用
                total_tool_calls += 1
                call_signature = f"{tool_name}:{json.dumps(arguments, sort_keys=True)}"

                if total_tool_calls > max_tool_calls:
                    print(f"[AI Client] ⚠️ 达到最大工具调用次数限制 ({max_tool_calls})")
                    should_break_tool_limit = True
                    break

                if call_signature == last_call_signature:
                    consecutive_same_calls += 1
                else:
                    consecutive_same_calls = 1
                    last_call_signature = call_signature

                # 回调
                if on_tool_call:
                    on_tool_call(tool_name, arguments)

                tool_calls_history.append({
                    'tool_name': tool_name,
                    'arguments': arguments,
                    'result': result
                })

                if on_tool_result:
                    on_tool_result(tool_name, arguments, result)

                result_content = self._compress_tool_result(tool_name, result)

                working_messages.append({
                    'role': 'tool',
                    'tool_call_id': tool_id,
                    'content': result_content
                })

            if should_break_tool_limit:
                return {
                    'ok': True,
                    'content': full_content + f"\n\n已达到工具调用次数限制({max_tool_calls})，自动停止。",
                    'tool_calls_history': tool_calls_history,
                    'iterations': iteration,
                    'usage': total_usage
                }
            
            # 多轮思考引导：在最后一条工具结果后附加提示
            # 检测本轮是否有工具调用失败
            _round_failed = False
            for _ri, (_tid, _tn, _ta, _tc) in enumerate(parsed_calls):
                if not results_ordered[_ri].get('success'):
                    _round_failed = True
                    break

            if working_messages and working_messages[-1].get('role') == 'tool':
                if _round_failed:
                    working_messages[-1]['content'] += (
                        '\n\n[注意：上述工具调用返回了错误，这是工具调用层面的参数或执行错误，'
                        '不是Houdini节点cooking错误，无需调用check_errors。'
                        '请直接根据错误信息修正参数后重新调用该工具。]'
                    )
                if enable_thinking:
                    working_messages[-1]['content'] += (
                        '\n\n[请先在<think>标签内分析以上执行结果和当前进度，'
                        '检查 Todo 列表中哪些步骤已完成（用 update_todo 标记为 done），'
                        '确认下一步计划后再继续执行。]'
                    )
            
            # 保存当前轮次的内容
            full_content += round_content
        
        # 如果循环结束但内容为空，且有工具调用历史，强制要求生成总结
        if not full_content.strip() and tool_calls_history:
            print("[AI Client] ⚠️ Stream模式：工具调用完成但无回复内容，强制要求生成总结")
            # 最后一次请求，强制要求总结
            working_messages.append({
                'role': 'user',
                'content': '请生成最终总结，说明已完成的操作和结果。'
            })
            
            # 再次请求生成总结
            summary_content = ""
            for chunk in self.chat_stream(
                messages=working_messages,
                model=model,
                provider=provider,
                temperature=temperature,
                max_tokens=max_tokens or 500,  # 限制总结长度
                tools=None,  # 总结阶段不需要工具
                tool_choice=None
            ):
                if chunk.get('type') == 'content':
                    content = chunk.get('content', '')
                    summary_content += content
                    if on_content:
                        on_content(content)
                elif chunk.get('type') == 'done':
                    break
            
            full_content = summary_content if summary_content else full_content
        
        print(f"[AI Client] Reached max iterations ({iteration})")
        # 计算 cache 命中率
        prompt_total = total_usage['cache_hit_tokens'] + total_usage['cache_miss_tokens']
        if prompt_total > 0:
            total_usage['cache_hit_rate'] = total_usage['cache_hit_tokens'] / prompt_total
        else:
            total_usage['cache_hit_rate'] = 0
        return {
            'ok': True,
            'content': full_content if full_content.strip() else "(工具调用完成，但未生成回复)",
            'tool_calls_history': tool_calls_history,
            'iterations': iteration,
            'usage': total_usage
        }

    def _execute_web_search(self, arguments: dict) -> dict:
        """执行网络搜索（通用：天气/新闻/文档/任何话题）"""
        query = arguments.get('query', '')
        max_results = arguments.get('max_results', 5)
        
        if not query:
            return {"success": False, "error": "缺少搜索关键词"}
        
        result = self._web_searcher.search(query, max_results)
        
        if result.get('success'):
            items = result.get('results', [])
            if not items:
                return {"success": True, "result": f"搜索 '{query}' 未找到结果。可尝试换用不同关键词。"}
            
            # 格式化结果：标题 + URL + 摘要
            lines = [f"搜索 '{query}' 的结果（来源: {result.get('source', 'Unknown')}，共 {len(items)} 条）：\n"]
            for i, item in enumerate(items, 1):
                lines.append(f"{i}. {item.get('title', '无标题')}")
                lines.append(f"   URL: {item.get('url', '')}")
                snippet = item.get('snippet', '')
                if snippet:
                    lines.append(f"   摘要: {snippet[:300]}")
                lines.append("")
            
            lines.append("提示: 如需查看详细内容，请用 fetch_webpage(url=...) 获取网页正文。引用信息时务必在段落末标注 [来源: 标题](URL)。请勿用相同关键词重复搜索。")
            
            return {"success": True, "result": "\n".join(lines)}
        else:
            return {"success": False, "error": result.get('error', '搜索失败')}

    def _execute_fetch_webpage(self, arguments: dict) -> dict:
        """获取网页内容（分页返回，支持翻页）"""
        url = arguments.get('url', '')
        start_line = arguments.get('start_line', 1)
        
        if not url:
            return {"success": False, "error": "缺少 URL"}
        
        # 确保 start_line 合法
        try:
            start_line = max(1, int(start_line))
        except (TypeError, ValueError):
            start_line = 1
        
        result = self._web_searcher.fetch_page_content(url, max_lines=80, start_line=start_line)
        
        if result.get('success'):
            content = result.get('content', '')
            return {"success": True, "result": f"网页正文（{url}）：\n\n{content}"}
        else:
            return {"success": False, "error": result.get('error', '获取失败')}

    # 保持兼容性
    def agent_loop(self, *args, **kwargs):
        """兼容旧接口"""
        return self.agent_loop_stream(*args, **kwargs)

    # ============================================================
    # JSON 解析模式（用于不支持 Function Calling 的模型）
    # ============================================================
    
    def _supports_function_calling(self, provider: str, model: str) -> bool:
        """检查模型是否支持原生 Function Calling"""
        # Ollama 模型默认不支持
        if provider == 'ollama':
            return False
        # 其他云端模型都支持
        return True
    
    def _get_json_mode_system_prompt(self) -> str:
        """获取 JSON 模式的系统提示（执行器模式）"""
        # 构建工具列表说明
        tool_descriptions = []
        for tool in HOUDINI_TOOLS:
            func = tool['function']
            params = func.get('parameters', {}).get('properties', {})
            required = func.get('parameters', {}).get('required', [])
            
            param_desc = []
            for pname, pinfo in params.items():
                req_mark = "(必填)" if pname in required else "(可选)"
                param_desc.append(f"    - {pname} {req_mark}: {pinfo.get('description', '')}")
            
            tool_descriptions.append(f"""
**{func['name']}** - {func['description']}
参数:
{chr(10).join(param_desc) if param_desc else '    无'}
""")
        
        return f"""你是Houdini执行器。只执行，不思考，不解释。

严格禁止（违反会浪费token）:
-禁止生成任何思考过程、推理步骤、分析过程
-禁止说明"为什么"、"让我先"、"我需要"
-禁止逐步说明、分步解释
-禁止输出任何非执行性内容

只允许:
-直接调用工具执行操作
-直接给出执行结果(1句以内)
-不输出任何思考内容

工具调用参数规范（最高优先级）:
-调用前必须确认所有(必填)参数都已填写,缺少必填参数会导致调用失败
-node_path必须用完整绝对路径(如"/obj/geo1/box1"),不能只写节点名
-参数值类型必须正确:string/number/boolean/array,不要混用
-工具返回"缺少参数"错误时,直接修正参数重试,不要调用check_errors
-每次调用都要完整填写所有必填参数,不要假设系统记住上次参数

安全操作规则（必须遵守）:
-操作节点前先用get_network_structure确认节点存在
-了解节点状况(连接/错误/状态)用get_node_details
-设置参数前必须先用get_node_parameters查询正确的参数名和类型,不要猜测参数名
-execute_python中必须检查None:node=hou.node(path);if node:...
-创建节点后用返回的路径操作,不要猜测路径
-连接节点前确认两个节点都已存在

完成前必须检查（任务结束前强制执行）:
-调用get_network_structure检查节点网络
-确认所有节点已正确连接,无孤立节点
-检查是否有错误标记的节点,有则修复
-确认输出节点已设为显示
-只有检查通过才能结束任务

## 工具调用格式

```json
{{"tool": "工具名称", "args": {{"参数名": "参数值"}}}}
```

规则:
1.每次只调用一个工具
2.工具调用在独立JSON代码块中
3.调用后等待结果再继续
4.不解释，直接执行
5.先查询确认再操作
6.调用前检查所有(必填)参数是否已填写,不要遗漏node_path等必填参数
7.node_path必须写完整绝对路径(如"/obj/geo1/box1"),不能只写节点名

## 可用工具

{chr(10).join(tool_descriptions)}

## 示例

创建节点（不解释，直接执行）:
```json
{{"tool": "create_node", "args": {{"node_type": "box"}}}}
```
"""
    
    def _parse_json_tool_calls(self, content: str) -> List[Dict]:
        """从文本内容中解析 JSON 格式的工具调用（改进版：支持多种格式）"""
        import re
        
        tool_calls = []
        
        # 1. 清理XML标签（如果AI错误输出了XML格式）
        content = re.sub(r'</?tool_call[^>]*>', '', content)
        content = re.sub(r'<arg_key>([^<]+)</arg_key>\s*<arg_value>([^<]+)</arg_value>', r'"\1": "\2"', content)
        
        # 2. 匹配 ```json ... ``` 代码块
        json_blocks = re.findall(r'```(?:json)?\s*\n?({[^`]+})\s*\n?```', content, re.DOTALL)
        
        # 3. 如果没有代码块，尝试直接匹配JSON对象
        if not json_blocks:
            # 尝试匹配独立的JSON对象（不在代码块中）
            json_pattern = r'\{\s*"(?:tool|name)"\s*:\s*"[^"]+"\s*,\s*"(?:args|arguments)"\s*:\s*\{[^}]+\}\s*\}'
            json_blocks = re.findall(json_pattern, content, re.DOTALL)
        
        for block in json_blocks:
            try:
                # 清理可能的格式问题
                block = block.strip()
                # 修复常见的JSON格式错误
                block = re.sub(r',\s*}', '}', block)  # 移除末尾多余逗号
                block = re.sub(r',\s*]', ']', block)  # 移除数组末尾多余逗号
                
                data = json.loads(block)
                if 'tool' in data:
                    tool_calls.append({
                        'name': data['tool'],
                        'arguments': data.get('args', data.get('arguments', {}))
                    })
                elif 'name' in data:
                    # 兼容 {"name": "xxx", "arguments": {...}} 格式
                    tool_calls.append({
                        'name': data['name'],
                        'arguments': data.get('arguments', data.get('args', {}))
                    })
            except (json.JSONDecodeError, KeyError) as e:
                # 记录解析失败但不中断
                print(f"[AI Client] JSON解析失败: {e}, 内容: {block[:100]}")
                continue
        
        return tool_calls
    
    def agent_loop_json_mode(self,
                              messages: List[Dict[str, Any]],
                              model: str = 'qwen2.5:14b',
                              provider: str = 'ollama',
                              max_iterations: int = 15,
                              temperature: float = 0.3,
                              max_tokens: Optional[int] = None,
                              enable_thinking: bool = True,
                              on_content: Optional[Callable[[str], None]] = None,
                              on_thinking: Optional[Callable[[str], None]] = None,
                              on_tool_call: Optional[Callable[[str, dict], None]] = None,
                              on_tool_result: Optional[Callable[[str, dict, dict], None]] = None) -> Dict[str, Any]:
        """JSON 模式 Agent Loop（用于不支持 Function Calling 的模型）"""
        
        if not self._tool_executor:
            return {'ok': False, 'error': '未设置工具执行器', 'content': '', 'tool_calls_history': [], 'iterations': 0}
        
        # 添加 JSON 模式系统提示
        json_system_prompt = self._get_json_mode_system_prompt()
        working_messages = []
        
        # 处理消息，在第一个 system 消息后追加 JSON 模式说明
        system_found = False
        for msg in messages:
            if msg.get('role') == 'system' and not system_found:
                working_messages.append({
                    'role': 'system',
                    'content': msg.get('content', '') + '\n\n' + json_system_prompt
                })
                system_found = True
            else:
                working_messages.append(msg)
        
        if not system_found:
            working_messages.insert(0, {'role': 'system', 'content': json_system_prompt})
        
        tool_calls_history = []
        full_content = ""
        iteration = 0
        self._json_thinking_buffer = ""  # 初始化思考缓冲区
        
        # 累积 usage 统计（用于 cache 命中率统计）
        total_usage = {
            'prompt_tokens': 0,
            'completion_tokens': 0,
            'total_tokens': 0,
            'cache_hit_tokens': 0,
            'cache_miss_tokens': 0,
        }
        
        # 防止死循环：检测重复工具调用
        max_tool_calls = 999  # 不限制总调用次数（仅保留连续重复检测）
        total_tool_calls = 0
        consecutive_same_calls = 0
        last_call_signature = None
        server_error_retries = 0    # 连续服务端错误重试计数
        max_server_retries = 3      # 最多重试 3 次服务端错误
        
        while iteration < max_iterations:
            if self._stop_event.is_set():
                return {
                    'ok': False, 'error': '用户停止了请求',
                    'content': full_content, 'tool_calls_history': tool_calls_history,
                    'iterations': iteration, 'stopped': True, 'usage': total_usage
                }
            
            iteration += 1
            round_content = ""
            
            # ⚠️ 主动防御：每轮迭代前压缩过长的消息内容
            if iteration > 1 and len(working_messages) > 20:
                protect_start = max(1, len(working_messages) - 6)
                for i, m in enumerate(working_messages):
                    if i == 0 or i >= protect_start:
                        continue
                    role = m.get('role', '')
                    if role == 'user':
                        continue
                    c = m.get('content') or ''
                    if role == 'tool' and len(c) > 400:
                        m['content'] = self._summarize_tool_content(c, 400)
                    elif role == 'assistant' and len(c) > 600:
                        m['content'] = c[:600] + '...[已截断]'
            
            # 流式请求（不传 tools 参数）
            for chunk in self.chat_stream(
                messages=working_messages,
                model=model,
                provider=provider,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=None,  # JSON 模式不使用原生工具
                tool_choice=None
            ):
                if self._stop_event.is_set():
                    return {
                        'ok': False, 'error': '用户停止了请求',
                        'content': full_content + round_content,
                        'tool_calls_history': tool_calls_history,
                        'iterations': iteration, 'stopped': True, 'usage': total_usage
                    }
                
                chunk_type = chunk.get('type')
                
                if chunk_type == 'content':
                    content = chunk.get('content', '')
                    round_content += content
                    if on_content:
                        on_content(content)
                
                elif chunk_type == 'thinking':
                    thinking_text = chunk.get('content', '')
                    if on_thinking and thinking_text:
                        on_thinking(thinking_text)
                
                elif chunk_type == 'error':
                    err_msg = chunk.get('error', '')
                    err_lower = err_msg.lower()
                    
                    # 精确分类错误
                    is_context_exceeded = any(k in err_lower for k in (
                        'context_length_exceeded', 'maximum context length',
                        'max_tokens', 'token limit', 'too many tokens',
                        'request too large', 'payload too large',
                        'context window', 'input too long',
                    )) or ('HTTP 413' in err_msg)
                    is_server_transient = any(k in err_msg for k in (
                        'HTTP 502', 'HTTP 503', 'HTTP 529', '压缩失败', 'no available'
                    ))
                    
                    if is_context_exceeded or is_server_transient:
                        server_error_retries += 1
                        if server_error_retries > max_server_retries:
                            if on_content:
                                on_content(f"\n[连续出错 {max_server_retries} 次，已停止重试。]\n")
                            return {
                                'ok': False, 'error': f"连续出错: {err_msg}",
                                'content': full_content, 'tool_calls_history': tool_calls_history,
                                'iterations': iteration, 'usage': total_usage
                            }
                        
                        if is_context_exceeded:
                            # 上下文超限：立即裁剪
                            if on_content:
                                on_content(f"\n[上下文超限，智能裁剪后重试 ({server_error_retries}/{max_server_retries})...]\n")
                            working_messages = self._progressive_trim(
                                working_messages, tool_calls_history,
                                trim_level=server_error_retries
                            )
                        else:
                            # 临时服务器错误：等待，第2次开始才裁剪
                            wait_seconds = 5 * server_error_retries
                            if on_content:
                                on_content(f"\n[服务端暂时不可用，{wait_seconds}秒后重试 ({server_error_retries}/{max_server_retries})...]\n")
                            time.sleep(wait_seconds)
                            if server_error_retries >= 2:
                                working_messages = self._progressive_trim(
                                    working_messages, tool_calls_history,
                                    trim_level=server_error_retries - 1
                                )
                        break  # 退出 for，回到 while 重试
                    return {
                        'ok': False, 'error': err_msg,
                        'content': full_content, 'tool_calls_history': tool_calls_history,
                        'iterations': iteration, 'usage': total_usage
                    }
                
                elif chunk_type == 'done':
                    # 成功收到响应 → 重置服务端错误重试计数
                    server_error_retries = 0
                    # 收集 usage 信息（包含 cache 统计）
                    usage = chunk.get('usage', {})
                    if usage:
                        total_usage['prompt_tokens'] += usage.get('prompt_tokens', 0)
                        total_usage['completion_tokens'] += usage.get('completion_tokens', 0)
                        total_usage['total_tokens'] += usage.get('total_tokens', 0)
                        total_usage['cache_hit_tokens'] += usage.get('cache_hit_tokens', 0)
                        total_usage['cache_miss_tokens'] += usage.get('cache_miss_tokens', 0)
                    break
            
            # 清理内容中的XML标签和格式问题（更彻底的清理）
            import re
            cleaned_content = round_content
            # 清理所有XML标签
            cleaned_content = re.sub(r'</?tool_call[^>]*>', '', cleaned_content)
            cleaned_content = re.sub(r'<arg_key>([^<]+)</arg_key>\s*<arg_value>([^<]+)</arg_value>', '', cleaned_content)
            cleaned_content = re.sub(r'</?arg_key[^>]*>', '', cleaned_content)
            cleaned_content = re.sub(r'</?arg_value[^>]*>', '', cleaned_content)
            cleaned_content = re.sub(r'</?redacted_reasoning[^>]*>', '', cleaned_content)
            # 清理其他可能的XML标签
            cleaned_content = re.sub(r'<[^>]+>', '', cleaned_content)  # 清理所有剩余的XML标签
            
            # 解析 JSON 工具调用
            tool_calls = self._parse_json_tool_calls(cleaned_content)
            
            # 如果没有工具调用，检查是否完成
            if not tool_calls:
                # 清理后的内容添加到full_content（只添加一次，避免重复）
                if cleaned_content.strip():
                    # 检查是否与已有内容重复（避免重复添加）
                    if cleaned_content.strip() not in full_content:
                        full_content += cleaned_content
                # 如果内容为空或只有空白，检查是否需要继续
                if not cleaned_content.strip() and tool_calls_history:
                    # 有工具调用历史但无内容，继续循环等待总结
                    continue
                # 计算 cache 命中率
                prompt_total = total_usage['cache_hit_tokens'] + total_usage['cache_miss_tokens']
                if prompt_total > 0:
                    total_usage['cache_hit_rate'] = total_usage['cache_hit_tokens'] / prompt_total
                else:
                    total_usage['cache_hit_rate'] = 0
                return {
                    'ok': True,
                    'content': full_content,
                    'tool_calls_history': tool_calls_history,
                    'iterations': iteration,
                    'usage': total_usage
                }
            
            # 添加助手消息（使用清理后的内容，但不要重复添加到full_content）
            json_assistant_msg = {'role': 'assistant', 'content': cleaned_content}
            # reasoning_content 仅 DeepSeek / 原生 GLM 需要
            if self.is_reasoning_model(model) and provider in ('deepseek', 'glm'):
                json_assistant_msg['reasoning_content'] = ''
            working_messages.append(json_assistant_msg)
            
            # 执行工具调用（web 工具并行，Houdini 工具串行）
            tool_results = []

            _ASYNC_TOOL_NAMES_JSON = frozenset({'web_search', 'fetch_webpage', 'execute_shell'})
            async_tc = [(i, tc) for i, tc in enumerate(tool_calls) if tc['name'] in _ASYNC_TOOL_NAMES_JSON]
            houdini_tc = [(i, tc) for i, tc in enumerate(tool_calls) if tc['name'] not in _ASYNC_TOOL_NAMES_JSON]

            # 结果槽位
            exec_results = [None] * len(tool_calls)

            # 并行 async 工具（web + shell）
            if len(async_tc) > 1:
                import concurrent.futures
                def _exec_async_json(idx_tc):
                    idx, tc = idx_tc
                    tname, targs = tc['name'], tc['arguments']
                    if tname == 'web_search':
                        return idx, self._execute_web_search(targs)
                    elif tname == 'fetch_webpage':
                        return idx, self._execute_fetch_webpage(targs)
                    else:  # execute_shell
                        return idx, self._tool_executor(tname, **targs)
                with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(async_tc))) as pool:
                    for idx, res in pool.map(_exec_async_json, async_tc):
                        exec_results[idx] = res
            elif len(async_tc) == 1:
                idx, tc = async_tc[0]
                tname, targs = tc['name'], tc['arguments']
                if tname == 'web_search':
                    exec_results[idx] = self._execute_web_search(targs)
                elif tname == 'fetch_webpage':
                    exec_results[idx] = self._execute_fetch_webpage(targs)
                else:  # execute_shell
                    exec_results[idx] = self._tool_executor(tname, **targs)

            # 串行 Houdini 工具
            for idx, tc in houdini_tc:
                tname, targs = tc['name'], tc['arguments']
                if not self._tool_executor:
                    exec_results[idx] = {"success": False, "error": f"工具执行器未设置，无法执行工具: {tname}"}
                else:
                    try:
                        exec_results[idx] = self._tool_executor(tname, **targs)
                    except Exception as e:
                        import traceback
                        exec_results[idx] = {"success": False, "error": f"工具执行异常: {str(e)}\n{traceback.format_exc()[:200]}"}
                time.sleep(0.05)

            # 统一处理结果
            should_break_limit = False
            for i, tc in enumerate(tool_calls):
                tool_name = tc['name']
                arguments = tc['arguments']
                result = exec_results[i]

                total_tool_calls += 1
                call_signature = f"{tool_name}:{json.dumps(arguments, sort_keys=True)}"

                if total_tool_calls > max_tool_calls:
                    print(f"[AI Client] ⚠️ JSON模式：达到最大工具调用次数限制 ({max_tool_calls})")
                    should_break_limit = True
                    break

                if call_signature == last_call_signature:
                    consecutive_same_calls += 1
                else:
                    consecutive_same_calls = 1
                    last_call_signature = call_signature

                if on_tool_call:
                    on_tool_call(tool_name, arguments)

                tool_calls_history.append({
                    'tool_name': tool_name,
                    'arguments': arguments,
                    'result': result
                })

                if not result.get('success'):
                    error_detail = result.get('error', '未知错误')
                    print(f"[AI Client] ⚠️ 工具执行失败: {tool_name}")
                    print(f"[AI Client]   错误详情: {error_detail[:200]}")

                if on_tool_result:
                    on_tool_result(tool_name, arguments, result)

                compressed = self._compress_tool_result(tool_name, result)
                if result.get('success'):
                    tool_results.append(f"{tool_name}:{compressed}")
                else:
                    tool_results.append(f"{tool_name}:错误:{compressed}")

            if should_break_limit:
                return {
                    'ok': True,
                    'content': full_content + f"\n\n已达到工具调用次数限制({max_tool_calls})，自动停止。",
                    'tool_calls_history': tool_calls_history,
                    'iterations': iteration
                }
            
            # 极简格式：工具结果，继续或总结
            # 收集失败的工具详情（明确指出哪个工具、什么错误）
            failed_tool_details = []
            for r in tool_results:
                if ':错误:' in r:
                    failed_tool_details.append(r)
            has_failed_tools = len(failed_tool_details) > 0
            # 检查是否有未完成的todo（通过检查工具调用历史）
            has_pending_todos = False
            for tc in tool_calls_history:
                if tc.get('tool_name') == 'add_todo':
                    # 如果有add_todo但没有对应的update_todo done，说明还有未完成的任务
                    has_pending_todos = True
                    break
            
            # 构造提示（带多轮思考引导）
            think_hint = '先在<think>标签内分析执行结果和当前进度，再决定下一步。' if enable_thinking else ''
            
            todo_hint = '已完成的步骤请立即用 update_todo 标记为 done。'
            if has_failed_tools:
                # 明确列出失败的工具及错误原因，避免AI误解为需要调用check_errors
                fail_summary = '; '.join(failed_tool_details)
                prompt = ('|'.join(tool_results)
                          + f'|⚠️ 以下工具调用返回了错误（这是工具调用层面的参数/执行错误，不是Houdini节点错误，'
                          + f'无需调用check_errors，请直接根据错误原因修正参数后重试）: {fail_summary}'
                          + f'|{think_hint}{todo_hint}请根据上述错误原因修正后继续完成任务。不要因为失败就提前结束。')
            elif has_pending_todos and iteration < max_iterations - 2:
                prompt = '|'.join(tool_results) + f'|检测到还有未完成的任务，{think_hint}{todo_hint}请继续执行。'
            elif iteration >= max_iterations - 1:
                prompt = '|'.join(tool_results) + f'|{todo_hint}请生成最终总结，说明已完成的操作'
            else:
                prompt = '|'.join(tool_results) + f'|{think_hint}{todo_hint}继续或总结'
            
            # 使用 system 角色传递工具结果，避免与用户消息混淆
            # 注意：部分模型不支持多个 system 消息，此处使用明确的 [TOOL_RESULT] 标记
            working_messages.append({
                'role': 'user',
                'content': f'[TOOL_RESULT]\n{prompt}'
            })
            
            # 保存当前轮次的内容（清理XML标签，避免重复）
            import re
            cleaned_round = round_content
            # 更彻底的XML标签清理
            cleaned_round = re.sub(r'</?tool_call[^>]*>', '', cleaned_round)
            cleaned_round = re.sub(r'<arg_key>([^<]+)</arg_key>\s*<arg_value>([^<]+)</arg_value>', '', cleaned_round)
            cleaned_round = re.sub(r'</?arg_key[^>]*>', '', cleaned_round)
            cleaned_round = re.sub(r'</?arg_value[^>]*>', '', cleaned_round)
            cleaned_round = re.sub(r'</?redacted_reasoning[^>]*>', '', cleaned_round)
            cleaned_round = re.sub(r'<[^>]+>', '', cleaned_round)  # 清理所有剩余的XML标签
            # 只添加非空且不重复的内容
            if cleaned_round.strip():
                # 检查是否与已有内容重复（简单去重：如果内容完全相同，跳过）
                if cleaned_round.strip() not in full_content:
                    full_content += cleaned_round
                else:
                    # 如果内容重复，只添加一次（避免多次重复）
                    pass
        
        # 如果循环结束但内容为空，且有工具调用历史，强制要求生成总结
        if not full_content.strip() and tool_calls_history:
            print("[AI Client] ⚠️ JSON模式：工具调用完成但无回复内容，强制要求生成总结")
            # 最后一次请求，强制要求总结
            working_messages.append({
                'role': 'user',
                'content': '请生成最终总结，说明已完成的操作和结果。'
            })
            
            # 再次请求生成总结
            summary_content = ""
            for chunk in self.chat_stream(
                messages=working_messages,
                model=model,
                provider=provider,
                temperature=temperature,
                max_tokens=max_tokens or 500,  # 限制总结长度
                tools=None,
                tool_choice=None
            ):
                if chunk.get('type') == 'content':
                    content = chunk.get('content', '')
                    summary_content += content
                    if on_content:
                        on_content(content)
                elif chunk.get('type') == 'done':
                    break
            
            full_content = summary_content if summary_content else full_content
        
        # 计算 cache 命中率
        prompt_total = total_usage['cache_hit_tokens'] + total_usage['cache_miss_tokens']
        if prompt_total > 0:
            total_usage['cache_hit_rate'] = total_usage['cache_hit_tokens'] / prompt_total
        else:
            total_usage['cache_hit_rate'] = 0
        return {
            'ok': True,
            'content': full_content if full_content.strip() else "(工具调用完成，但未生成回复)",
            'tool_calls_history': tool_calls_history,
            'iterations': iteration,
            'usage': total_usage
        }
    
    def agent_loop_auto(self,
                        messages: List[Dict[str, Any]],
                        model: str = 'gpt-4o-mini',
                        provider: str = 'openai',
                        **kwargs) -> Dict[str, Any]:
        """自动选择合适的 Agent Loop 模式"""
        if self._supports_function_calling(provider, model):
            return self.agent_loop_stream(messages=messages, model=model, provider=provider, **kwargs)
        else:
            return self.agent_loop_json_mode(messages=messages, model=model, provider=provider, **kwargs)


# 兼容旧代码
OpenAIClient = AIClient
