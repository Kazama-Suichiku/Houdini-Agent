# -*- coding: utf-8 -*-
"""
Houdini Agent - AI Client
OpenAI-compatible API client with Function Calling, streaming, and web search.
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
            "description": "获取节点网络结构。如果网络中存在 NetworkBox 分组，默认返回 box 级别概览（名称+注释+节点数），大幅节省上下文；传入 box_name 可钻入查看该 box 内的详细节点。无 NetworkBox 时返回全部节点。",
            "parameters": {
                "type": "object",
                "properties": {
                    "network_path": {
                        "type": "string",
                        "description": "网络路径如 '/obj/geo1'，留空使用当前网络"
                    },
                    "box_name": {
                        "type": "string",
                        "description": "指定 NetworkBox 名称以查看其内部详细节点和连接。留空则显示概览（box 摘要 + 未分组节点）。"
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
            "name": "get_node_parameters",
            "description": "获取节点的完整参数列表及概况信息：类型、状态标志(display/render/bypass)、错误信息、输入输出连接、以及每个参数的内部名称、类型(Float/Int/Menu等)、标签、默认值、当前值、菜单选项。设置参数前必须先调用此工具确认正确的参数名和类型，不要猜测。参数较多时支持分页。",
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
                    "value": {
                        "type": ["string", "number", "boolean", "array"],
                        "items": {"type": ["string", "number", "boolean"]},
                        "description": "参数值（单值或数组，如向量 [1, 0, 0]）"
                    }
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
                    "value": {
                        "type": ["string", "number", "boolean", "array"],
                        "items": {"type": ["string", "number", "boolean"]},
                        "description": "新值（单值或数组，如向量 [1, 0, 0]）"
                    }
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
            "description": "【任务结束前必调用】验证节点网络并生成总结。自动检测:1.孤立节点 2.错误节点 3.连接完整性 4.显示标志。已内置 get_network_structure，不需要在调用前单独查询网络。如果发现问题必须修复后重新调用，直到通过。",
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
    },
    # ============================================================
    # 节点布局工具 — 自动整理节点位置
    # ============================================================
    {
        "type": "function",
        "function": {
            "name": "layout_nodes",
            "description": "自动布局节点位置。在 verify_and_summarize 通过后、创建 NetworkBox 之前调用，确保节点排列整齐。支持多种布局策略：auto（智能选择）、grid（网格排列）、columns（按拓扑深度分列）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "network_path": {
                        "type": "string",
                        "description": "父网络路径（如 /obj/geo1）。留空则使用当前活跃网络。"
                    },
                    "node_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "要布局的节点完整路径列表。留空则布局整个网络的所有子节点。"
                    },
                    "method": {
                        "type": "string",
                        "enum": ["auto", "grid", "columns"],
                        "description": "布局方法。auto=智能选择（推荐），grid=网格排列，columns=按拓扑深度分列。默认 auto。"
                    },
                    "spacing": {
                        "type": "number",
                        "description": "节点间距倍率，默认 1.0。增大则节点间距更宽松。"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_node_positions",
            "description": "获取节点的位置信息（坐标、类型），用于检查布局效果或在手动微调时查看当前状态。",
            "parameters": {
                "type": "object",
                "properties": {
                    "network_path": {
                        "type": "string",
                        "description": "父网络路径（如 /obj/geo1）。留空则使用当前活跃网络。"
                    },
                    "node_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "要查询的节点完整路径列表。留空则返回整个网络下所有子节点的位置。"
                    }
                },
                "required": []
            }
        }
    },
    # ============================================================
    # NetworkBox 工具 — 节点分组与可视化组织
    # ============================================================
    {
        "type": "function",
        "function": {
            "name": "create_network_box",
            "description": "创建 NetworkBox（节点分组框），用于将功能相关的节点组织在一起。支持设置名称、注释、颜色预设，并可在创建时直接包含指定节点。建议在每完成一个逻辑阶段后使用此工具将该阶段的节点打包。",
            "parameters": {
                "type": "object",
                "properties": {
                    "parent_path": {
                        "type": "string",
                        "description": "父网络路径（如 /obj/geo1）。留空则使用当前活跃网络。"
                    },
                    "name": {
                        "type": "string",
                        "description": "NetworkBox 名称（如 input_stage, deform_stage）。留空则自动生成。"
                    },
                    "comment": {
                        "type": "string",
                        "description": "注释文字，显示在 NetworkBox 标题栏上，描述这组节点的功能（如 '数据输入与基础几何', '噪波变形处理'）。"
                    },
                    "color_preset": {
                        "type": "string",
                        "enum": ["input", "processing", "deform", "output", "simulation", "utility"],
                        "description": "颜色预设: input(蓝/输入), processing(绿/处理), deform(橙/变形), output(红/输出), simulation(紫/模拟), utility(灰/辅助)。"
                    },
                    "node_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "要加入 box 的节点完整路径列表。创建后会自动调整 box 大小以包围这些节点。"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "add_nodes_to_box",
            "description": "将节点添加到已有的 NetworkBox 中。用于在创建新节点后将其归入对应的分组。",
            "parameters": {
                "type": "object",
                "properties": {
                    "parent_path": {
                        "type": "string",
                        "description": "父网络路径（如 /obj/geo1）。留空则使用当前活跃网络。"
                    },
                    "box_name": {
                        "type": "string",
                        "description": "目标 NetworkBox 的名称。"
                    },
                    "node_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "要添加的节点完整路径列表。"
                    },
                    "auto_fit": {
                        "type": "boolean",
                        "description": "是否自动调整 box 大小以包围所有节点。默认 true。"
                    }
                },
                "required": ["box_name", "node_paths"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_network_boxes",
            "description": "列出指定网络中所有 NetworkBox 及其包含的节点。用于了解当前网络的分组组织情况。",
            "parameters": {
                "type": "object",
                "properties": {
                    "parent_path": {
                        "type": "string",
                        "description": "要查询的网络路径（如 /obj/geo1）。留空则使用当前活跃网络。"
                    }
                },
                "required": []
            }
        }
    },
    # ============================================================
    # PerfMon 性能分析工具
    # ============================================================
    {
        "type": "function",
        "function": {
            "name": "perf_start_profile",
            "description": "启动 Houdini 性能 Profiling（基于 hou.perfMon）。用于详细分析 cook 耗时和内存增长。启动后需执行操作（如强制 cook），然后调用 perf_stop_and_report 获取分析报告。如果只需快速查看各节点 cook 时间排名，优先使用 run_skill(skill_name='analyze_cook_performance')。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Profile 标题（可选，默认 'AI Performance Analysis'）"
                    },
                    "force_cook_node": {
                        "type": "string",
                        "description": "启动 profile 后立即强制 cook 的节点路径（可选）。传入末端节点路径可触发整条链的 cook。"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "perf_stop_and_report",
            "description": "停止性能 Profiling 并返回分析报告。必须先调用 perf_start_profile 启动。报告包含各节点 cook 时间排名、内存统计等。可选保存 .hperf 文件到磁盘。",
            "parameters": {
                "type": "object",
                "properties": {
                    "save_path": {
                        "type": "string",
                        "description": "保存 .hperf profile 文件的路径（可选）。如 'C:/tmp/profile.hperf'"
                    },
                    "page": {
                        "type": "integer",
                        "description": "页码（从1开始），报告较长时翻页查看"
                    }
                },
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
    DUOJIE_API_URL = "https://api.duojie.games/v1/chat/completions"  # 拼好饭中转站（OpenAI 协议）
    DUOJIE_ANTHROPIC_API_URL = "https://api.duojie.games/v1/messages"  # 拼好饭中转站（Anthropic 协议）
    
    # 使用 Anthropic 协议的 Duojie 模型
    _DUOJIE_ANTHROPIC_MODELS = frozenset({'glm-4.7', 'glm-5'})

    # ★ 预编译流式内容清洗正则（避免每个 SSE chunk 都重新编译）
    _RE_CLEAN_PATTERNS = [
        re.compile(r'</?tool_call[^>]*>'),
        re.compile(r'<arg_key>([^<]+)</arg_key>\s*<arg_value>([^<]+)</arg_value>'),
        re.compile(r'</?arg_key[^>]*>'),
        re.compile(r'</?arg_value[^>]*>'),
        re.compile(r'</?redacted_reasoning[^>]*>'),
    ]

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
        
        # ★ 持久化 HTTP Session（连接池 + Keep-Alive，避免每轮重新 TLS 握手）
        self._http_session = requests.Session()
        self._http_session.headers.update({
            'Content-Type': 'application/json',
        })
        
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
        'get_network_structure', 'get_node_parameters',
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
    # 图片内容剥离
    # ----------------------------------------------------------

    @staticmethod
    def _strip_image_content(messages: list, keep_recent_user: int = 0) -> int:
        """就地剥离消息中的 image_url 内容，将多模态 content 转为纯文本

        Args:
            messages: 消息列表（就地修改）
            keep_recent_user: 保留最近 N 条 user 消息的图片（0 = 全部剥离）

        Returns:
            剥离的图片数量
        """
        stripped = 0

        # 找出最近 N 条 user 消息的索引（从后往前）
        protected_indices: set = set()
        if keep_recent_user > 0:
            count = 0
            for i in range(len(messages) - 1, -1, -1):
                if messages[i].get('role') == 'user':
                    protected_indices.add(i)
                    count += 1
                    if count >= keep_recent_user:
                        break

        for idx, msg in enumerate(messages):
            content = msg.get('content')
            if not isinstance(content, list):
                continue
            if idx in protected_indices:
                continue

            # 多模态 content: [{"type":"text","text":"..."},{"type":"image_url",...}]
            text_parts = []
            has_image = False
            for part in content:
                if isinstance(part, dict):
                    if part.get('type') == 'text':
                        text_parts.append(part.get('text', ''))
                    elif part.get('type') == 'image_url':
                        has_image = True
                        stripped += 1
                elif isinstance(part, str):
                    text_parts.append(part)

            if has_image:
                combined = '\n'.join(t for t in text_parts if t)
                if combined:
                    combined += '\n[图片已移除以节省上下文空间]'
                else:
                    combined = '[图片已移除]'
                msg['content'] = combined

        return stripped

    # ----------------------------------------------------------
    # 渐进式裁剪
    # ----------------------------------------------------------

    def _progressive_trim(self, working_messages: list, tool_calls_history: list,
                          trim_level: int = 1, supports_vision: bool = True) -> list:
        """渐进式裁剪上下文，根据 trim_level 逐步加大裁剪力度

        Cursor 风格核心原则:
        - **永不截断 user 消息的文本部分**
        - **永不截断 assistant 消息**（保留完整回复——这是 Cursor 的关键设计）
        - 只压缩 tool 结果（role='tool'）
        - 剥离旧轮次中的图片（base64 图片是 body 膨胀的主因）
        - 按「轮次」裁剪，保留最近 N 轮完整对话
        - 最早的轮次优先删除

        trim_level=1: 轻度 - 压缩旧轮 tool 结果，保留最近 70% 轮次，剥离旧轮图片
        trim_level=2: 中度 - 保留最近 3 轮，较短的 tool 摘要，剥离所有旧图片
        trim_level=3+: 重度 - 保留最近 2 轮，激进压缩 tool 结果，剥离全部图片
        """
        if not working_messages:
            return working_messages

        # ── 第 0 步：剥离图片（base64 图片是 413 的主因）──
        if not supports_vision or trim_level >= 3:
            # 非视觉模型 或 重度裁剪：剥离所有图片
            n_stripped = self._strip_image_content(working_messages, keep_recent_user=0)
        elif trim_level == 2:
            # 中度裁剪：只保留最近 1 条 user 消息的图片
            n_stripped = self._strip_image_content(working_messages, keep_recent_user=1)
        else:
            # 轻度裁剪：保留最近 2 条 user 消息的图片
            n_stripped = self._strip_image_content(working_messages, keep_recent_user=2)

        if n_stripped > 0:
            print(f"[AI Client] 裁剪: 剥离了 {n_stripped} 张图片")

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
            # 轻度：只压缩非最近 30% 轮次的 tool 结果
            n_rounds = len(rounds)
            protect_n = max(3, int(n_rounds * 0.7))  # 保护最近 70%
            for r_idx, rnd in enumerate(rounds):
                if r_idx >= n_rounds - protect_n:
                    break
                for m in rnd:
                    c = m.get('content') or ''
                    if m.get('role') == 'tool' and isinstance(c, str) and len(c) > 300:
                        m['content'] = self._summarize_tool_content(c, 300)
                    # ★ assistant 和 user 文本完全保留 ★

            keep_rounds = max(5, int(n_rounds * 0.7))
            if n_rounds > keep_rounds:
                rounds = rounds[-keep_rounds:]

        elif trim_level == 2:
            # 中度：保留最近 3 轮（而非 5 轮，避免 level 1 → level 2 无效裁剪）
            rounds = rounds[-3:] if len(rounds) > 3 else rounds
            for r_idx, rnd in enumerate(rounds):
                if r_idx >= len(rounds) - 2:
                    break  # 最近 2 轮的 tool 结果不压缩
                for m in rnd:
                    c = m.get('content') or ''
                    if m.get('role') == 'tool' and isinstance(c, str) and len(c) > 150:
                        m['content'] = self._summarize_tool_content(c, 150)
                    # ★ assistant 和 user 文本完全保留 ★

        else:
            # 重度：保留最近 2 轮，激进压缩 tool 结果
            rounds = rounds[-2:] if len(rounds) > 2 else rounds
            for rnd in rounds[:-1]:  # 最后一轮不压缩
                for m in rnd:
                    c = m.get('content') or ''
                    if m.get('role') == 'tool' and isinstance(c, str) and len(c) > 100:
                        m['content'] = self._summarize_tool_content(c, 100)
                    # ★ assistant 和 user 文本完全保留 ★

        # 重组
        body = [m for rnd in rounds for m in rnd]
        result = ([sys_msg] if sys_msg else []) + body

        # 恢复提示
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

    def _is_anthropic_protocol(self, provider: str, model: str) -> bool:
        """判断是否应使用 Anthropic Messages 协议（而非 OpenAI 协议）"""
        return provider == 'duojie' and model.lower() in self._DUOJIE_ANTHROPIC_MODELS

    def _get_api_url(self, provider: str, model: str = '') -> str:
        provider = (provider or 'openai').lower()
        if provider == 'deepseek':
            return self.DEEPSEEK_API_URL
        elif provider == 'glm':
            return self.GLM_API_URL
        elif provider == 'ollama':
            return self.OLLAMA_API_URL
        elif provider == 'duojie':
            if model and self._is_anthropic_protocol(provider, model):
                return self.DUOJIE_ANTHROPIC_API_URL
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
            response = self._http_session.get(
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
                    response = self._http_session.get(
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
                response = self._http_session.post(
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
            'openai': 'gpt-5.2', 
            'deepseek': 'deepseek-chat', 
            'glm': 'glm-4.7',
            'ollama': 'qwen2.5:14b'
        }
        return defaults.get(provider, 'gpt-5.2')

    # ============================================================
    # 模型特性判断
    # ============================================================
    
    @staticmethod
    def is_reasoning_model(model: str) -> bool:
        """判断模型是否为原生推理模型（API 返回 reasoning_content 字段）
        
        仅限明确通过 reasoning_content 字段返回推理的模型：
        DeepSeek-R1/Reasoner, GLM-4.7
        注：Duojie 模型思考模式通过系统提示词 <think> 标签实现，不依赖 API 参数
        """
        m = model.lower()
        return (
            'reasoner' in m or 'r1' in m
            or m == 'glm-4.7'
        )
    
    @staticmethod
    def is_glm47(model: str) -> bool:
        """判断是否为 GLM-4.7 模型"""
        return model.lower() == 'glm-4.7'
    
    # Duojie 思考模式说明：
    # 经测试 thinking/reasoningEffort API 参数对 Duojie 均无效（reasoning_tokens 始终 0）
    # 思考通过系统提示词中的 <think> 标签指令实现，模型名保持不变
    
    # ============================================================
    # Usage 解析
    # ============================================================
    
    _usage_keys_logged = False  # 类变量：只打印一次原始 usage 完整结构

    @staticmethod
    def _parse_usage(usage: dict) -> dict:
        """解析 API 返回的 usage 数据为统一格式（含 reasoning tokens 和缓存指标）
        
        缓存字段兼容多种 API 返回格式：
        - DeepSeek/OpenAI: prompt_cache_hit_tokens / prompt_cache_miss_tokens
        - Anthropic 原生: cache_read_input_tokens / cache_creation_input_tokens
        - Factory/Duojie 代理: claude_cache_creation_*_tokens, input_tokens_details 内嵌
        """
        if not usage:
            return {}
        
        # 诊断：首次收到 usage 时打印完整结构（含嵌套 details）
        if not AIClient._usage_keys_logged:
            AIClient._usage_keys_logged = True
            print(f"[AI Client] Raw usage keys (首次): {sorted(usage.keys())}")
            for k in ('input_tokens_details', 'prompt_tokens_details', 'completion_tokens_details'):
                v = usage.get(k)
                if v:
                    print(f"[AI Client]   {k}: {v}")
        
        prompt_tokens = usage.get('prompt_tokens', 0) or usage.get('input_tokens', 0)
        
        # ── 缓存读取（hit）：从多级来源查找 ──
        # 优先从 details 子字段中提取（Factory/Anthropic 风格）
        input_details = usage.get('input_tokens_details') or usage.get('prompt_tokens_details') or {}
        if isinstance(input_details, dict):
            cache_hit = (
                input_details.get('cached_tokens')           # OpenAI 新格式
                or input_details.get('cache_read_input_tokens')  # Anthropic
                or input_details.get('cache_read_tokens')
                or 0
            )
        else:
            cache_hit = 0
        # 顶级字段后备
        if not cache_hit:
            cache_hit = (
                usage.get('prompt_cache_hit_tokens')
                or usage.get('cache_read_input_tokens')
                or usage.get('cache_read_tokens')
                or usage.get('cache_hit_tokens')
                or 0
            )
        
        # ── 缓存写入（miss/creation） ──
        # Factory 特有: claude_cache_creation_1_h_tokens / claude_cache_creation_5_m_tokens
        cache_write_1h = usage.get('claude_cache_creation_1_h_tokens', 0) or 0
        cache_write_5m = usage.get('claude_cache_creation_5_m_tokens', 0) or 0
        factory_cache_write = cache_write_1h + cache_write_5m
        
        if isinstance(input_details, dict):
            cache_miss_from_details = (
                input_details.get('cache_creation_input_tokens')
                or input_details.get('cache_creation_tokens')
                or 0
            )
        else:
            cache_miss_from_details = 0
        
        cache_miss = (
            cache_miss_from_details
            or usage.get('prompt_cache_miss_tokens')
            or usage.get('cache_creation_input_tokens')
            or usage.get('cache_write_tokens')
            or usage.get('cache_miss_tokens')
            or factory_cache_write
            or 0
        )
        
        completion = usage.get('completion_tokens', 0) or usage.get('output_tokens', 0)
        total = usage.get('total_tokens', 0) or (prompt_tokens + completion)
        
        # ── 提取 reasoning / thinking tokens ──
        # OpenAI/DeepSeek: completion_tokens_details.reasoning_tokens
        # Anthropic: 可能在 output_tokens_details.thinking 中
        reasoning_tokens = 0
        comp_details = usage.get('completion_tokens_details') or {}
        if isinstance(comp_details, dict):
            reasoning_tokens = comp_details.get('reasoning_tokens', 0) or 0
        if not reasoning_tokens:
            reasoning_tokens = usage.get('reasoning_tokens', 0) or 0
        
        return {
            'prompt_tokens': prompt_tokens,
            'completion_tokens': completion,
            'reasoning_tokens': reasoning_tokens,
            'total_tokens': total,
            'cache_hit_tokens': cache_hit,
            'cache_miss_tokens': cache_miss,
            'cache_hit_rate': (cache_hit / prompt_tokens) if prompt_tokens > 0 else 0,
        }
    
    # ============================================================
    # Anthropic Messages 协议适配层
    # ============================================================

    @staticmethod
    def _convert_messages_to_anthropic(messages: List[Dict[str, Any]]) -> tuple:
        """将 OpenAI 格式的消息列表转换为 Anthropic Messages API 格式。
        
        Returns:
            (system_text, anthropic_messages)
            - system_text: 系统提示（Anthropic 要求单独传 system 参数）
            - anthropic_messages: Anthropic 格式的 messages 列表
        """
        system_text = ""
        anthropic_msgs: List[Dict[str, Any]] = []
        
        for msg in messages:
            role = msg.get('role', '')
            
            if role == 'system':
                # Anthropic 的 system 不在 messages 里，单独传
                system_text += (("\n\n" if system_text else "") + (msg.get('content', '') or ''))
                continue
            
            if role == 'user':
                content = msg.get('content', '')
                # 支持 OpenAI 多模态格式: content 可能是 list
                if isinstance(content, list):
                    # 转换 OpenAI 多模态格式 → Anthropic 格式
                    anth_content = []
                    for part in content:
                        if part.get('type') == 'text':
                            anth_content.append({'type': 'text', 'text': part['text']})
                        elif part.get('type') == 'image_url':
                            url = part.get('image_url', {}).get('url', '')
                            if url.startswith('data:'):
                                # data:image/png;base64,xxxx
                                import re as _re
                                m = _re.match(r'data:(image/\w+);base64,(.+)', url, _re.DOTALL)
                                if m:
                                    anth_content.append({
                                        'type': 'image',
                                        'source': {
                                            'type': 'base64',
                                            'media_type': m.group(1),
                                            'data': m.group(2),
                                        }
                                    })
                            else:
                                anth_content.append({
                                    'type': 'image',
                                    'source': {'type': 'url', 'url': url}
                                })
                    anthropic_msgs.append({'role': 'user', 'content': anth_content})
                else:
                    anthropic_msgs.append({'role': 'user', 'content': str(content or '')})
                continue
            
            if role == 'assistant':
                content_blocks: List[Dict[str, Any]] = []
                text = msg.get('content')
                if text:
                    content_blocks.append({'type': 'text', 'text': str(text)})
                # tool_calls → tool_use blocks
                for tc in (msg.get('tool_calls') or []):
                    func = tc.get('function', {})
                    try:
                        input_obj = json.loads(func.get('arguments', '{}'))
                    except (json.JSONDecodeError, ValueError):
                        input_obj = {}
                    content_blocks.append({
                        'type': 'tool_use',
                        'id': tc.get('id', ''),
                        'name': func.get('name', ''),
                        'input': input_obj,
                    })
                if not content_blocks:
                    content_blocks.append({'type': 'text', 'text': ''})
                anthropic_msgs.append({'role': 'assistant', 'content': content_blocks})
                continue
            
            if role == 'tool':
                # OpenAI tool result → Anthropic tool_result (放在 user 消息中)
                tool_result_block = {
                    'type': 'tool_result',
                    'tool_use_id': msg.get('tool_call_id', ''),
                    'content': str(msg.get('content', '')),
                }
                # 如果上一条也是 user（连续的 tool results），合并到同一条 user 消息
                if anthropic_msgs and anthropic_msgs[-1]['role'] == 'user':
                    last_content = anthropic_msgs[-1]['content']
                    if isinstance(last_content, list):
                        last_content.append(tool_result_block)
                    else:
                        anthropic_msgs[-1]['content'] = [
                            {'type': 'text', 'text': last_content},
                            tool_result_block,
                        ]
                else:
                    anthropic_msgs.append({
                        'role': 'user',
                        'content': [tool_result_block],
                    })
                continue
        
        # Anthropic 要求消息以 user 开头，如果第一条是 assistant 则补一条 user
        if anthropic_msgs and anthropic_msgs[0]['role'] == 'assistant':
            anthropic_msgs.insert(0, {'role': 'user', 'content': '请继续。'})
        
        # Anthropic 要求角色严格交替（user/assistant/user/...）
        # 合并连续相同角色的消息
        merged: List[Dict[str, Any]] = []
        for m in anthropic_msgs:
            if merged and merged[-1]['role'] == m['role']:
                # 合并内容
                prev_content = merged[-1]['content']
                curr_content = m['content']
                # 统一为 list 格式
                if isinstance(prev_content, str):
                    prev_content = [{'type': 'text', 'text': prev_content}]
                if isinstance(curr_content, str):
                    curr_content = [{'type': 'text', 'text': curr_content}]
                if not isinstance(prev_content, list):
                    prev_content = [prev_content]
                if not isinstance(curr_content, list):
                    curr_content = [curr_content]
                merged[-1]['content'] = prev_content + curr_content
            else:
                merged.append(m)
        
        return system_text, merged

    @staticmethod
    def _convert_tools_to_anthropic(tools: List[dict]) -> List[dict]:
        """将 OpenAI Function Calling 格式的工具列表转换为 Anthropic 格式。
        
        OpenAI:  {"type": "function", "function": {"name": ..., "description": ..., "parameters": {...}}}
        Anthropic: {"name": ..., "description": ..., "input_schema": {...}}
        """
        if not tools:
            return []
        anthropic_tools = []
        for tool in tools:
            func = tool.get('function', tool)  # 兼容裸 function dict
            anthropic_tools.append({
                'name': func.get('name', ''),
                'description': func.get('description', ''),
                'input_schema': func.get('parameters', {'type': 'object', 'properties': {}}),
            })
        return anthropic_tools

    def _chat_stream_anthropic(self,
                                messages: List[Dict[str, Any]],
                                model: str,
                                provider: str,
                                temperature: float = 0.17,
                                max_tokens: Optional[int] = None,
                                tools: Optional[List[dict]] = None,
                                tool_choice: str = 'auto',
                                enable_thinking: bool = True,
                                api_key: str = '') -> Generator[Dict[str, Any], None, None]:
        """Anthropic Messages 协议的流式 Chat。
        
        将 OpenAI 格式的输入转换为 Anthropic 格式，调用 /v1/messages，
        解析 Anthropic SSE 事件流，yield 与 OpenAI 分支相同的内部 chunk 格式。
        """
        api_url = self._get_api_url(provider, model)
        
        # 消息转换
        system_text, anth_messages = self._convert_messages_to_anthropic(messages)
        
        payload: Dict[str, Any] = {
            'model': model,
            'messages': anth_messages,
            'max_tokens': max_tokens or 16384,
            'stream': True,
        }
        # temperature（Anthropic 范围 0-1）
        if temperature is not None:
            payload['temperature'] = min(max(temperature, 0.0), 1.0)
        
        if system_text:
            payload['system'] = system_text
        
        # 思考模式
        if enable_thinking:
            payload['thinking'] = {'type': 'enabled', 'budget_tokens': min(max_tokens or 16384, 10000)}
        
        # 工具
        if tools:
            payload['tools'] = self._convert_tools_to_anthropic(tools)
            if tool_choice == 'auto':
                payload['tool_choice'] = {'type': 'auto'}
            elif tool_choice == 'none':
                payload['tool_choice'] = {'type': 'none'}
            elif tool_choice == 'required':
                payload['tool_choice'] = {'type': 'any'}
        
        # 请求头（Anthropic 格式）
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'text/event-stream',
            'x-api-key': api_key,
            'anthropic-version': '2023-06-01',
        }
        
        print(f"[AI Client] Anthropic protocol: {api_url} model={model}")
        
        for attempt in range(self._max_retries):
            try:
                with self._http_session.post(
                    api_url,
                    json=payload,
                    headers=headers,
                    stream=True,
                    timeout=(10, self._chunk_timeout),
                    proxies={'http': None, 'https': None}
                ) as response:
                    response.encoding = 'utf-8'
                    print(f"[AI Client] Anthropic response status: {response.status_code}")
                    
                    if response.status_code != 200:
                        try:
                            err = response.json()
                            err_msg = err.get('error', {}).get('message', response.text)
                        except Exception:
                            err_msg = response.text
                        print(f"[AI Client] Anthropic error: {err_msg}")
                        
                        if response.status_code >= 500 and attempt < self._max_retries - 1:
                            wait = self._retry_delay * (attempt + 1)
                            print(f"[AI Client] Anthropic server error {response.status_code}, retrying in {wait}s...")
                            time.sleep(wait)
                            continue
                        
                        yield {"type": "error", "error": f"HTTP {response.status_code}: {err_msg}"}
                        return
                    
                    # ── 解析 Anthropic SSE 事件流 ──
                    # 状态
                    _content_blocks: Dict[int, Dict[str, Any]] = {}  # index → block info
                    _tool_args_acc: Dict[int, str] = {}  # index → accumulated JSON args
                    _pending_usage: Dict[str, Any] = {}
                    _last_stop_reason = None
                    _got_thinking = False
                    _enable_thinking_flag = enable_thinking  # 闭包变量
                    
                    import codecs
                    _utf8_decoder = codecs.getincrementaldecoder('utf-8')(errors='ignore')
                    _line_buf = ""
                    _event_type = ""  # 当前 SSE event 类型
                    
                    def _process_anthropic_event(event_type: str, data_str: str):
                        """处理单个 Anthropic SSE 事件，返回要 yield 的 dict 列表"""
                        nonlocal _content_blocks, _tool_args_acc, _pending_usage, _last_stop_reason, _got_thinking
                        results = []
                        
                        try:
                            data = json.loads(data_str)
                        except json.JSONDecodeError:
                            return results
                        
                        ev_type = data.get('type', event_type)
                        
                        if ev_type == 'message_start':
                            msg = data.get('message', {})
                            usage = msg.get('usage', {})
                            if usage:
                                _pending_usage = self._parse_usage(usage)
                        
                        elif ev_type == 'content_block_start':
                            idx = data.get('index', 0)
                            block = data.get('content_block', {})
                            _content_blocks[idx] = {
                                'type': block.get('type', 'text'),
                                'id': block.get('id', ''),
                                'name': block.get('name', ''),
                            }
                            if block.get('type') == 'tool_use':
                                _tool_args_acc[idx] = ''
                        
                        elif ev_type == 'content_block_delta':
                            idx = data.get('index', 0)
                            delta = data.get('delta', {})
                            delta_type = delta.get('type', '')
                            block_info = _content_blocks.get(idx, {})
                            
                            if delta_type == 'text_delta':
                                text = delta.get('text', '')
                                if text:
                                    results.append({"type": "content", "content": text})
                            
                            elif delta_type == 'thinking_delta':
                                thinking = delta.get('thinking', '')
                                if thinking:
                                    if not _got_thinking:
                                        _got_thinking = True
                                        print(f"[AI Client] 🧠 Anthropic thinking (首个 chunk, len={len(thinking)}, enable={_enable_thinking_flag})")
                                    if _enable_thinking_flag:
                                        results.append({"type": "thinking", "content": thinking})
                            
                            elif delta_type == 'input_json_delta':
                                partial = delta.get('partial_json', '')
                                if partial and idx in _tool_args_acc:
                                    _tool_args_acc[idx] += partial
                                    # 广播 tool_args_delta → UI 流式预览
                                    tool_name = block_info.get('name', '')
                                    if tool_name:
                                        results.append({
                                            "type": "tool_args_delta",
                                            "index": idx,
                                            "name": tool_name,
                                            "delta": partial,
                                            "accumulated": _tool_args_acc[idx],
                                        })
                        
                        elif ev_type == 'content_block_stop':
                            idx = data.get('index', 0)
                            block_info = _content_blocks.get(idx, {})
                            if block_info.get('type') == 'tool_use':
                                # 工具调用完成 → 转换为 OpenAI 格式的 tool_call
                                tool_id = block_info.get('id', '')
                                tool_name = block_info.get('name', '')
                                args_str = _tool_args_acc.get(idx, '{}')
                                results.append({
                                    "type": "tool_call",
                                    "tool_call": {
                                        'id': tool_id,
                                        'type': 'function',
                                        'function': {
                                            'name': tool_name,
                                            'arguments': args_str,
                                        }
                                    }
                                })
                        
                        elif ev_type == 'message_delta':
                            delta = data.get('delta', {})
                            _last_stop_reason = delta.get('stop_reason')
                            usage = data.get('usage', {})
                            if usage:
                                # 合并 usage
                                parsed = self._parse_usage(usage)
                                for k, v in parsed.items():
                                    if isinstance(v, (int, float)):
                                        _pending_usage[k] = _pending_usage.get(k, 0) + v
                        
                        elif ev_type == 'message_stop':
                            # 映射 stop_reason: end_turn → stop, tool_use → tool_calls
                            finish = 'stop'
                            if _last_stop_reason == 'tool_use':
                                finish = 'tool_calls'
                            elif _last_stop_reason == 'max_tokens':
                                finish = 'length'
                            results.append({
                                "type": "done",
                                "finish_reason": finish,
                                "usage": _pending_usage,
                            })
                        
                        elif ev_type == 'error':
                            err_msg = data.get('error', {}).get('message', str(data))
                            results.append({"type": "error", "error": err_msg})
                        
                        return results
                    
                    # ── 主循环 ──
                    _should_return = False
                    for raw_chunk in response.iter_content(chunk_size=4096, decode_unicode=False):
                        if not raw_chunk:
                            continue
                        if self._stop_event.is_set():
                            yield {"type": "stopped", "message": "用户停止了请求"}
                            return
                        
                        decoded = _utf8_decoder.decode(raw_chunk)
                        _line_buf += decoded
                        
                        while '\n' in _line_buf:
                            one_line, _line_buf = _line_buf.split('\n', 1)
                            one_line = one_line.rstrip('\r')
                            
                            if not one_line:
                                continue
                            
                            # Anthropic SSE: "event: xxx" 行后跟 "data: {...}" 行
                            if one_line.startswith('event: '):
                                _event_type = one_line[7:].strip()
                                continue
                            
                            if one_line.startswith('data: '):
                                data_str = one_line[6:]
                                for item in _process_anthropic_event(_event_type, data_str):
                                    yield item
                                    if item.get('type') in ('done', 'error'):
                                        _should_return = True
                                _event_type = ""  # 重置
                        
                        if _should_return:
                            return
                    
                    # 处理残留
                    _line_buf += _utf8_decoder.decode(b'', final=True)
                    if _line_buf.strip():
                        for line in _line_buf.strip().split('\n'):
                            line = line.strip()
                            if line.startswith('event: '):
                                _event_type = line[7:].strip()
                            elif line.startswith('data: '):
                                for item in _process_anthropic_event(_event_type, line[6:]):
                                    yield item
                                    if item.get('type') in ('done', 'error'):
                                        return
                    
                    # 流结束但未收到 message_stop
                    if not _should_return:
                        yield {"type": "done", "finish_reason": _last_stop_reason or "stop", "usage": _pending_usage}
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
                err_str = str(e)
                is_transient = any(k in err_str for k in (
                    'InvalidChunkLength', 'ChunkedEncodingError',
                    'Connection broken', 'IncompleteRead',
                    'ConnectionReset', 'RemoteDisconnected',
                ))
                if is_transient and attempt < self._max_retries - 1:
                    wait = self._retry_delay * (attempt + 1)
                    print(f"[AI Client] Anthropic 连接中断 ({err_str[:80]}), {wait}s 后重试")
                    time.sleep(wait)
                    continue
                yield {"type": "error", "error": f"请求失败: {err_str}"}
                return

    def _chat_anthropic(self,
                        messages: List[Dict[str, Any]],
                        model: str,
                        provider: str,
                        temperature: float = 0.17,
                        max_tokens: int = 4096,
                        tools: Optional[List[dict]] = None,
                        tool_choice: str = 'auto',
                        api_key: str = '',
                        timeout: int = 60) -> Dict[str, Any]:
        """Anthropic Messages 协议的非流式 Chat。"""
        api_url = self._get_api_url(provider, model)
        system_text, anth_messages = self._convert_messages_to_anthropic(messages)
        
        payload: Dict[str, Any] = {
            'model': model,
            'messages': anth_messages,
            'max_tokens': max_tokens,
        }
        if temperature is not None:
            payload['temperature'] = min(max(temperature, 0.0), 1.0)
        if system_text:
            payload['system'] = system_text
        if tools:
            payload['tools'] = self._convert_tools_to_anthropic(tools)
            if tool_choice == 'auto':
                payload['tool_choice'] = {'type': 'auto'}
        
        headers = {
            'Content-Type': 'application/json',
            'x-api-key': api_key,
            'anthropic-version': '2023-06-01',
        }
        
        for attempt in range(self._max_retries):
            try:
                response = self._http_session.post(
                    api_url, json=payload, headers=headers,
                    timeout=timeout, proxies={'http': None, 'https': None}
                )
                response.raise_for_status()
                obj = response.json()
                
                # 解析 Anthropic 响应 → OpenAI 统一格式
                content_text = ''
                tool_calls_list = []
                for block in obj.get('content', []):
                    if block.get('type') == 'text':
                        content_text += block.get('text', '')
                    elif block.get('type') == 'tool_use':
                        tool_calls_list.append({
                            'id': block.get('id', ''),
                            'type': 'function',
                            'function': {
                                'name': block.get('name', ''),
                                'arguments': json.dumps(block.get('input', {}), ensure_ascii=False),
                            }
                        })
                
                stop_reason = obj.get('stop_reason', 'end_turn')
                finish = 'stop' if stop_reason == 'end_turn' else ('tool_calls' if stop_reason == 'tool_use' else stop_reason)
                
                return {
                    'ok': True,
                    'content': content_text or None,
                    'tool_calls': tool_calls_list or None,
                    'finish_reason': finish,
                    'usage': self._parse_usage(obj.get('usage', {})),
                    'raw': obj,
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
    # 流式传输 Chat
    # ============================================================
    
    def chat_stream(self,
                    messages: List[Dict[str, str]],
                    model: str = 'gpt-5.2',
                    provider: str = 'openai',
                    temperature: float = 0.17,
                    max_tokens: Optional[int] = None,
                    tools: Optional[List[dict]] = None,
                    tool_choice: str = 'auto',
                    enable_thinking: bool = True) -> Generator[Dict[str, Any], None, None]:
        """流式 Chat API
        
        Yields:
            {"type": "content", "content": str}  # 内容片段
            {"type": "tool_call", "tool_call": dict}  # 工具调用
            {"type": "thinking", "content": str}  # 思考内容（DeepSeek / GLM 原生 reasoning_content）
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
        
        # ★ Anthropic 协议分支（Duojie GLM 等）
        if self._is_anthropic_protocol(provider, model):
            yield from self._chat_stream_anthropic(
                messages=messages, model=model, provider=provider,
                temperature=temperature, max_tokens=max_tokens,
                tools=tools, tool_choice=tool_choice,
                enable_thinking=enable_thinking, api_key=api_key,
            )
            return
        
        api_url = self._get_api_url(provider, model)
        
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
        
        # Duojie 中转：思考模式通过系统提示词中的 <think> 标签实现
        # 经测试 thinking/reasoningEffort 参数对 Duojie API 无效（reasoning_tokens 始终为 0）
        # 且 thinking 参数偶尔导致 403，因此不发送任何额外参数
        
        # DeepSeek / OpenAI prompt caching 自动启用（保持前缀稳定即可命中）
        
        # 工具调用（所有支持 function calling 的 provider 通用）
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
                with self._http_session.post(
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
                    _got_reasoning = False  # 诊断：本轮是否收到 reasoning_content
                    _enable_thinking = enable_thinking  # 闭包变量，供 _process_sse_line 使用
                    
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
                        nonlocal tool_calls_buffer, pending_usage, last_finish_reason, _got_reasoning, _enable_thinking
                        results = []
                        
                        if not line.startswith('data: '):
                            return results
                        
                        data_str = line[6:]
                        
                        if data_str.strip() == '[DONE]':
                            _reason_tokens = pending_usage.get('reasoning_tokens', 0)
                            print(f"[AI Client] Received [DONE], reasoning={'YES' if _got_reasoning else 'NO'}(tokens={_reason_tokens}), usage={pending_usage}")
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
                        
                        # 思考内容（仅在 enable_thinking=True 时显示）
                        # 不同代理可能使用不同字段名：reasoning_content / thinking_content / reasoning
                        # 统一拦截，Think 关闭时全部静默丢弃
                        _thinking_text = (
                            delta.get('reasoning_content')
                            or delta.get('thinking_content')
                            or delta.get('reasoning')
                            or ''
                        )
                        if _thinking_text:
                            if not _got_reasoning:
                                _got_reasoning = True
                                # 诊断：记录字段名以便排查
                                _field = ('reasoning_content' if 'reasoning_content' in delta
                                          else 'thinking_content' if 'thinking_content' in delta
                                          else 'reasoning')
                                print(f"[AI Client] 🧠 收到 {_field}（首个 chunk，len={len(_thinking_text)}，enable_thinking={_enable_thinking}）")
                            if _enable_thinking:
                                results.append({"type": "thinking", "content": _thinking_text})
                        
                        # 普通内容
                        if 'content' in delta and delta['content']:
                            results.append({"type": "content", "content": delta['content']})
                        
                        # 工具调用
                        if delta.get('tool_calls'):
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
                                        # ★ 广播 tool_call 参数增量 → UI 流式预览
                                        _tname = tool_calls_buffer[idx]['function'].get('name', '')
                                        if _tname:
                                            results.append({
                                                "type": "tool_args_delta",
                                                "index": idx,
                                                "name": _tname,
                                                "delta": fn['arguments'],
                                                "accumulated": tool_calls_buffer[idx]['function']['arguments'],
                                            })
                        
                        # 完成（先发送工具调用，但不 return，等后续 usage chunk / [DONE]）
                        if finish_reason:
                            if tool_calls_buffer:
                                # ★ 修复：检测并拆分被代理错误拼接的 arguments（如 duojie Claude 代理）
                                # 某些代理在流式传输时对多个工具调用使用相同 index，导致 arguments 被拼接为 {...}{...}
                                import uuid as _uuid
                                fixed_buffer = {}
                                next_fix_idx = max(tool_calls_buffer.keys()) + 1
                                for idx_k in sorted(tool_calls_buffer.keys()):
                                    tc_entry = tool_calls_buffer[idx_k]
                                    args_str = tc_entry['function']['arguments'].strip()
                                    if args_str.startswith('{'):
                                        try:
                                            json.loads(args_str)
                                            fixed_buffer[idx_k] = tc_entry
                                        except (json.JSONDecodeError, ValueError):
                                            # 尝试拆分拼接的多个 JSON 对象: {...}{...}
                                            split_parts = []
                                            depth = 0
                                            start = -1
                                            for ci, ch in enumerate(args_str):
                                                if ch == '{':
                                                    if depth == 0:
                                                        start = ci
                                                    depth += 1
                                                elif ch == '}':
                                                    depth -= 1
                                                    if depth == 0 and start >= 0:
                                                        part = args_str[start:ci+1]
                                                        try:
                                                            json.loads(part)
                                                            split_parts.append(part)
                                                        except:
                                                            pass
                                                        start = -1
                                            if split_parts:
                                                print(f"[AI Client] 修复拼接的 tool_call arguments: 拆分为 {len(split_parts)} 个独立调用")
                                                tc_entry['function']['arguments'] = split_parts[0]
                                                fixed_buffer[idx_k] = tc_entry
                                                for extra_args in split_parts[1:]:
                                                    fixed_buffer[next_fix_idx] = {
                                                        'id': f"call_{_uuid.uuid4().hex[:24]}",
                                                        'type': 'function',
                                                        'function': {
                                                            'name': tc_entry['function']['name'],
                                                            'arguments': extra_args
                                                        }
                                                    }
                                                    next_fix_idx += 1
                                            else:
                                                fixed_buffer[idx_k] = tc_entry
                                    else:
                                        fixed_buffer[idx_k] = tc_entry
                                tool_calls_buffer = fixed_buffer

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
                        # ★ 同样需要修复拼接的 arguments（与 finish_reason 分支保持一致）
                        import uuid as _uuid2
                        fixed_buffer2 = {}
                        next_fix_idx2 = max(tool_calls_buffer.keys()) + 1
                        for idx_k2 in sorted(tool_calls_buffer.keys()):
                            tc_entry2 = tool_calls_buffer[idx_k2]
                            args_str2 = tc_entry2['function']['arguments'].strip()
                            if args_str2.startswith('{'):
                                try:
                                    json.loads(args_str2)
                                    fixed_buffer2[idx_k2] = tc_entry2
                                except (json.JSONDecodeError, ValueError):
                                    split_parts2 = []
                                    depth2 = 0
                                    start2 = -1
                                    for ci2, ch2 in enumerate(args_str2):
                                        if ch2 == '{':
                                            if depth2 == 0:
                                                start2 = ci2
                                            depth2 += 1
                                        elif ch2 == '}':
                                            depth2 -= 1
                                            if depth2 == 0 and start2 >= 0:
                                                part2 = args_str2[start2:ci2+1]
                                                try:
                                                    json.loads(part2)
                                                    split_parts2.append(part2)
                                                except:
                                                    pass
                                                start2 = -1
                                    if split_parts2:
                                        print(f"[AI Client] 修复拼接的 tool_call arguments (尾部): 拆分为 {len(split_parts2)} 个独立调用")
                                        tc_entry2['function']['arguments'] = split_parts2[0]
                                        fixed_buffer2[idx_k2] = tc_entry2
                                        for extra_args2 in split_parts2[1:]:
                                            fixed_buffer2[next_fix_idx2] = {
                                                'id': f"call_{_uuid2.uuid4().hex[:24]}",
                                                'type': 'function',
                                                'function': {
                                                    'name': tc_entry2['function']['name'],
                                                    'arguments': extra_args2
                                                }
                                            }
                                            next_fix_idx2 += 1
                                    else:
                                        fixed_buffer2[idx_k2] = tc_entry2
                            else:
                                fixed_buffer2[idx_k2] = tc_entry2
                        tool_calls_buffer = fixed_buffer2
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
                err_str = str(e)
                # InvalidChunkLength / ChunkedEncodingError 等连接中断可重试
                is_transient = any(k in err_str for k in (
                    'InvalidChunkLength', 'ChunkedEncodingError',
                    'Connection broken', 'IncompleteRead',
                    'ConnectionReset', 'RemoteDisconnected',
                ))
                if is_transient and attempt < self._max_retries - 1:
                    wait = self._retry_delay * (attempt + 1)
                    print(f"[AI Client] 连接中断 ({err_str[:80]}), {wait}s 后重试 ({attempt+1}/{self._max_retries})")
                    time.sleep(wait)
                    continue
                yield {"type": "error", "error": f"请求失败: {err_str}"}
                return

    # ============================================================
    # 非流式 Chat（保留兼容性）
    # ============================================================
    
    def chat(self,
             messages: List[Dict[str, str]],
             model: str = 'gpt-5.2',
             provider: str = 'openai',
             temperature: float = 0.17,
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
        
        # 注意：非流式 chat() 不包含 enable_thinking 参数，不做 think 模型映射
        # 思考模式仅在流式 chat_stream() / agent_loop_stream() 中通过 enable_thinking 控制
        
        # DeepSeek / OpenAI prompt caching 自动启用
        
        if tools:
            payload['tools'] = tools
            payload['tool_choice'] = tool_choice
        
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}',
        }
        
        # ★ Anthropic 协议分支（非流式）
        if self._is_anthropic_protocol(provider, model):
            return self._chat_anthropic(
                messages=messages, model=model, provider=provider,
                temperature=temperature, max_tokens=max_tokens or 4096,
                tools=tools, tool_choice=tool_choice, api_key=api_key,
                timeout=timeout,
            )
        
        for attempt in range(self._max_retries):
            try:
                response = self._http_session.post(
                    self._get_api_url(provider, model),
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
                          model: str = 'gpt-5.2',
                          provider: str = 'openai',
                          max_iterations: int = 999,
                          temperature: float = 0.17,
                          max_tokens: Optional[int] = None,
                          enable_thinking: bool = True,
                          supports_vision: bool = True,
                          tools_override: Optional[List[dict]] = None,
                          on_content: Optional[Callable[[str], None]] = None,
                          on_thinking: Optional[Callable[[str], None]] = None,
                          on_tool_call: Optional[Callable[[str, dict], None]] = None,
                          on_tool_result: Optional[Callable[[str, dict, dict], None]] = None,
                          on_tool_args_delta: Optional[Callable[[str, str, str], None]] = None,
                          on_iteration_start: Optional[Callable[[int], None]] = None) -> Dict[str, Any]:
        """流式 Agent Loop
        
        Args:
            enable_thinking: 是否启用思考模式（影响原生推理模型的 thinking 参数）
            supports_vision: 模型是否支持图片输入（False 时自动剥离 image_url 内容）
            on_content: 内容回调 (content) -> None
            on_thinking: 思考回调 (content) -> None
            on_tool_call: 工具调用开始回调 (name, args) -> None
            on_tool_result: 工具结果回调 (name, args, result) -> None
            on_iteration_start: 每轮 API 请求开始时的回调 (iteration) -> None
                                用于 UI 显示 "Generating..." 等待状态
        
        Returns:
            {"ok": bool, "content": str, "final_content": str,
             "new_messages": list, "tool_calls_history": list, "iterations": int}
        """
        if not self._tool_executor:
            return {'ok': False, 'error': '未设置工具执行器', 'content': '', 'tool_calls_history': [], 'iterations': 0}
        
        working_messages = list(messages)
        
        # ── 预处理：非视觉模型剥离所有 image_url 内容 ──
        if not supports_vision:
            n_stripped = self._strip_image_content(working_messages, keep_recent_user=0)
            if n_stripped > 0:
                print(f"[AI Client] 非视觉模型 ({model})：已剥离 {n_stripped} 张图片")
        
        initial_msg_count = len(working_messages)  # 跟踪初始消息数量，用于提取新消息链
        tool_calls_history = []
        call_records = []  # 每次 API 调用的详细记录（对齐 Cursor）
        full_content = ""
        iteration = 0
        
        # ★ 工具列表：支持外部覆盖（用于 Ask 模式等场景）
        effective_tools = tools_override if tools_override is not None else HOUDINI_TOOLS
        
        # 累积 usage 统计（用于 cache 命中率统计）
        total_usage = {
            'prompt_tokens': 0,
            'completion_tokens': 0,
            'reasoning_tokens': 0,
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
        
        # ★ Cursor 风格：同轮去重缓存
        # 如果 AI 在同一 turn 中用相同参数调用相同工具，直接返回缓存结果
        # key: "tool_name:sorted_args_json" → value: result dict
        _turn_dedup_cache: Dict[str, dict] = {}
        
        # ★ 消息清洗 dirty 标志（避免每轮都 O(n) 遍历消息列表）
        _needs_sanitize = True
        
        while iteration < max_iterations:
            # 检查停止请求
            if self._stop_event.is_set():
                return {
                    'ok': False,
                    'error': '用户停止了请求',
                    'content': full_content,
                    'final_content': '',
                    'new_messages': working_messages[initial_msg_count:],
                    'tool_calls_history': tool_calls_history,
                    'call_records': call_records,
                    'iterations': iteration,
                    'stopped': True,
                    'usage': total_usage
                }
            
            iteration += 1
            _call_start = time.time()  # 记录本次 API 调用起始时间（对齐 Cursor 延迟统计）
            
            # 收集本轮的内容和工具调用
            round_content = ""
            round_thinking = ""
            round_tool_calls = []
            should_retry = False  # 错误恢复标志
            should_abort = False  # 不可恢复错误标志
            abort_error = ""
            _round_content_started = False  # ★ 标记本轮是否已发出首个 content chunk
            
            # 发送前清洗消息（仅在新增 tool 消息后才需要，避免无谓的 O(n) 遍历）
            if _needs_sanitize:
                working_messages = self._sanitize_working_messages(working_messages)
                _needs_sanitize = False
            
            # ⚠️ Cursor 风格：同一轮 agent turn 内，所有消息完整保留
            # - assistant 消息永不截断
            # - tool 结果也完整保留（AI 需要完整的查询结果来做决策）
            # - 只有在 context_length_exceeded 错误时才由 _progressive_trim 处理
            # 不做任何同轮内压缩——这是 Cursor 的核心设计
            
            # 诊断：仅打印消息数量摘要（完整内容通过"导出训练数据"功能获取）
            if iteration > 1:
                from collections import Counter
                role_counts = Counter(m.get('role', '?') for m in working_messages)
                summary = ', '.join(f"{r}={c}" for r, c in role_counts.items())
                print(f"[AI Client] iteration={iteration}, messages={len(working_messages)} ({summary})")
            
            # ★ 通知 UI 新一轮 API 请求即将开始（用于显示 "Generating..." 状态）
            if on_iteration_start:
                on_iteration_start(iteration)
            
            # 流式请求
            for chunk in self.chat_stream(
                messages=working_messages,
                model=model,
                provider=provider,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=effective_tools,
                tool_choice='auto',
                enable_thinking=enable_thinking
            ):
                # 检查停止请求
                if self._stop_event.is_set():
                    return {
                        'ok': False,
                        'error': '用户停止了请求',
                        'content': full_content + round_content,
                        'final_content': round_content,
                        'new_messages': working_messages[initial_msg_count:],
                        'tool_calls_history': tool_calls_history,
                        'call_records': call_records,
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
                        'final_content': round_content,
                        'new_messages': working_messages[initial_msg_count:],
                        'tool_calls_history': tool_calls_history,
                        'call_records': call_records,
                        'iterations': iteration,
                        'stopped': True,
                        'usage': total_usage
                    }
                
                if chunk_type == 'content':
                    content = chunk.get('content', '')
                    # 清理XML标签（使用预编译正则，避免每 chunk 重复编译）
                    cleaned_chunk = content
                    for _pat in self._RE_CLEAN_PATTERNS:
                        cleaned_chunk = _pat.sub('', cleaned_chunk)
                    
                    # ★ 修复多轮 iteration 内容粘连：
                    # 如果上一轮已有 content（full_content 非空），且本轮是首个 content chunk，
                    # 自动注入 \n\n 段落分隔符，避免 AI 跨 iteration 的文字粘在一起
                    if cleaned_chunk and not _round_content_started and full_content:
                        # 检查 full_content 末尾是否已有足够换行
                        if not full_content.endswith('\n\n'):
                            sep = '\n\n' if not full_content.endswith('\n') else '\n'
                            round_content += sep
                            if on_content:
                                on_content(sep)
                        _round_content_started = True
                    elif cleaned_chunk:
                        _round_content_started = True
                    
                    round_content += cleaned_chunk
                    if on_content and cleaned_chunk:
                        on_content(cleaned_chunk)
                
                elif chunk_type == 'thinking':
                    thinking_text = chunk.get('content', '')
                    round_thinking += thinking_text
                    if on_thinking and thinking_text:
                        on_thinking(thinking_text)
                
                elif chunk_type == 'tool_args_delta':
                    if on_tool_args_delta:
                        on_tool_args_delta(
                            chunk.get('name', ''),
                            chunk.get('delta', ''),
                            chunk.get('accumulated', ''),
                        )
                
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
                    
                    # 2. 临时服务器错误 / 连接中断（502/503/529 / InvalidChunkLength 等）
                    is_server_transient = any(k in error_msg for k in (
                        'HTTP 502', 'HTTP 503', 'HTTP 529', 'no available',
                        'InvalidChunkLength', 'ChunkedEncodingError',
                        'Connection broken', 'IncompleteRead',
                        'ConnectionReset', 'RemoteDisconnected',
                        '连接错误', '连接中断',
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
                                trim_level=server_error_retries,  # 逐次加大裁剪力度
                                supports_vision=supports_vision
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
                                    trim_level=server_error_retries - 1,  # 比上下文超限更温和
                                    supports_vision=supports_vision
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
                        total_usage['reasoning_tokens'] += usage.get('reasoning_tokens', 0)
                        total_usage['total_tokens'] += usage.get('total_tokens', 0)
                        total_usage['cache_hit_tokens'] += usage.get('cache_hit_tokens', 0)
                        total_usage['cache_miss_tokens'] += usage.get('cache_miss_tokens', 0)
                    
                    # ---- 记录本次 API 调用详情（对齐 Cursor） ----
                    import datetime as _dt
                    _call_latency = time.time() - _call_start
                    _rec_inp = usage.get('prompt_tokens', 0)
                    _rec_out = usage.get('completion_tokens', 0)
                    _rec_reason = usage.get('reasoning_tokens', 0)
                    _rec_chit = usage.get('cache_hit_tokens', 0)
                    _rec_cmiss = usage.get('cache_miss_tokens', 0)
                    try:
                        from houdini_agent.utils.token_optimizer import calculate_cost as _calc_cost
                        _rec_cost = _calc_cost(model, _rec_inp, _rec_out, _rec_chit, _rec_cmiss, _rec_reason)
                    except Exception:
                        _rec_cost = 0.0
                    call_records.append({
                        'timestamp': _dt.datetime.now().isoformat(),
                        'model': model,
                        'iteration': iteration,
                        'input_tokens': _rec_inp,
                        'output_tokens': _rec_out,
                        'reasoning_tokens': _rec_reason,
                        'cache_hit': _rec_chit,
                        'cache_miss': _rec_cmiss,
                        'total_tokens': usage.get('total_tokens', 0),
                        'latency': round(_call_latency, 2),
                        'has_tool_calls': len(round_tool_calls) > 0,
                        'estimated_cost': _rec_cost,
                    })
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
                    'final_content': '',
                    'new_messages': working_messages[initial_msg_count:],
                    'tool_calls_history': tool_calls_history,
                    'call_records': call_records,
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
                    'final_content': round_content,  # 最后一轮的回复（不含中间轮次）
                    'new_messages': working_messages[initial_msg_count:],  # 原生工具交互链
                    'tool_calls_history': tool_calls_history,
                    'call_records': call_records,
                    'iterations': iteration,
                    'usage': total_usage
                }
            
            # 添加助手消息（确保 tool_call ID 完整）
            self._ensure_tool_call_ids(round_tool_calls)
            
            # ★ 防御性修复：确保每个 tool_call 的 arguments 是合法 JSON
            # 某些代理（如 duojie）可能产生拼接的无效 JSON，存入历史后会导致下一轮 API 400 错误
            for _tc in round_tool_calls:
                _args_str = _tc.get('function', {}).get('arguments', '{}')
                try:
                    json.loads(_args_str)
                except (json.JSONDecodeError, ValueError):
                    # arguments 不是合法 JSON，尝试提取第一个完整 JSON 对象
                    _depth = 0
                    _start = -1
                    _fixed = None
                    for _ci, _ch in enumerate(_args_str):
                        if _ch == '{':
                            if _depth == 0:
                                _start = _ci
                            _depth += 1
                        elif _ch == '}':
                            _depth -= 1
                            if _depth == 0 and _start >= 0:
                                _candidate = _args_str[_start:_ci+1]
                                try:
                                    json.loads(_candidate)
                                    _fixed = _candidate
                                except:
                                    pass
                                break
                    _tc['function']['arguments'] = _fixed if _fixed else '{}'
                    print(f"[AI Client] 修正了无效的 tool_call arguments -> {_tc['function']['arguments'][:80]}")
            
            assistant_msg = {'role': 'assistant', 'tool_calls': round_tool_calls}
            # content 为空时必须传 None（null）而非空字符串
            # Claude/Anthropic 兼容代理拒绝 content="" + tool_calls 共存
            assistant_msg['content'] = round_content or None
            # reasoning_content 仅在回传消息时对 DeepSeek / 原生 GLM 有效
            # Duojie 的 reasoning_content 无需在后续请求中回传
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

            # ★ 同轮去重：纯查询类工具用相同参数重复调用时直接返回缓存
            # 只对无副作用的查询工具去重（execute_python/run_skill/web_search 等有副作用的不去重）
            _DEDUP_TOOLS = frozenset({
                'get_network_structure', 'get_node_parameters', 'list_children',
                'read_selection', 'search_node_types', 'semantic_search_nodes',
                'find_nodes_by_param', 'check_errors', 'search_local_doc',
                'get_houdini_node_doc', 'get_node_inputs', 'list_skills',
                'perf_stop_and_report',
            })
            
            # 分离可并行工具（web + shell）和 Houdini 工具（需主线程串行）
            _ASYNC_TOOL_NAMES = frozenset({'web_search', 'fetch_webpage', 'execute_shell'})
            async_calls = [(i, pc) for i, pc in enumerate(parsed_calls) if pc[1] in _ASYNC_TOOL_NAMES]
            houdini_calls = [(i, pc) for i, pc in enumerate(parsed_calls) if pc[1] not in _ASYNC_TOOL_NAMES]

            # 结果槽位：保持原始顺序
            results_ordered = [None] * len(parsed_calls)
            dedup_flags = [False] * len(parsed_calls)  # 标记哪些是缓存命中

            # --- 先检查去重缓存 ---
            for idx, (tid, tname, targs, _tc) in enumerate(parsed_calls):
                dedup_key = f"{tname}:{json.dumps(targs, sort_keys=True)}"
                if tname in _DEDUP_TOOLS and dedup_key in _turn_dedup_cache:
                    # ★ 缓存命中：直接返回之前的结果
                    results_ordered[idx] = _turn_dedup_cache[dedup_key]
                    dedup_flags[idx] = True
                    print(f"[AI Client] ♻️ 同轮去重命中: {tname}({json.dumps(targs, ensure_ascii=False)[:80]})")

            # 分离未缓存的调用
            uncached_async = [(i, pc) for i, pc in enumerate(parsed_calls) 
                             if pc[1] in _ASYNC_TOOL_NAMES and not dedup_flags[i]]
            uncached_houdini = [(i, pc) for i, pc in enumerate(parsed_calls) 
                               if pc[1] not in _ASYNC_TOOL_NAMES and not dedup_flags[i]]

            # --- 并行执行未缓存的 async 工具（web + shell） ---
            if len(uncached_async) > 1:
                import concurrent.futures
                def _exec_async(idx_pc):
                    idx, (tid, tname, targs, _tc) = idx_pc
                    if tname == 'web_search':
                        return idx, self._execute_web_search(targs)
                    elif tname == 'fetch_webpage':
                        return idx, self._execute_fetch_webpage(targs)
                    else:  # execute_shell
                        return idx, self._tool_executor(tname, **targs)
                with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(uncached_async))) as pool:
                    for idx, result in pool.map(_exec_async, uncached_async):
                        results_ordered[idx] = result
            elif len(uncached_async) == 1:
                idx, (tid, tname, targs, _tc) = uncached_async[0]
                if tname == 'web_search':
                    results_ordered[idx] = self._execute_web_search(targs)
                elif tname == 'fetch_webpage':
                    results_ordered[idx] = self._execute_fetch_webpage(targs)
                else:  # execute_shell
                    results_ordered[idx] = self._tool_executor(tname, **targs)

            # --- 串行执行未缓存的 Houdini 工具（需主线程） ---
            for idx, (tid, tname, targs, _tc) in uncached_houdini:
                results_ordered[idx] = self._tool_executor(tname, **targs)
            
            # --- 缓存维护 ---
            # 如果本轮有操作类工具（创建/删除/连接节点等），清除网络结构相关缓存
            # 因为操作改变了网络状态，之前缓存的查询结果可能已过期
            _NETWORK_MUTATING_TOOLS = frozenset({
                'create_node', 'create_nodes_batch', 'delete_node', 'connect_nodes',
                'create_wrangle_node', 'copy_node', 'set_display_flag', 'undo_redo',
            })
            has_mutation = any(
                pc[1] in _NETWORK_MUTATING_TOOLS 
                for idx_m, pc in enumerate(parsed_calls) 
                if not dedup_flags[idx_m]
            )
            if has_mutation:
                # 清除 get_network_structure / list_children / check_errors 的缓存
                keys_to_remove = [k for k in _turn_dedup_cache 
                                  if k.startswith(('get_network_structure:', 'list_children:', 'check_errors:'))]
                for k in keys_to_remove:
                    del _turn_dedup_cache[k]
            
            # 将新执行的查询工具结果写入去重缓存
            for idx, (tid, tname, targs, _tc) in enumerate(parsed_calls):
                if not dedup_flags[idx] and tname in _DEDUP_TOOLS and results_ordered[idx]:
                    dedup_key = f"{tname}:{json.dumps(targs, sort_keys=True)}"
                    _turn_dedup_cache[dedup_key] = results_ordered[idx]

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
                
                # ★ 去重命中时追加提示，引导 AI 不要再重复调用
                if dedup_flags[i]:
                    result_content = f"[缓存] 本轮已用相同参数调用过此工具，以下是之前的结果（无需再次调用）:\n{result_content}"

                working_messages.append({
                    'role': 'tool',
                    'tool_call_id': tool_id,
                    'content': result_content
                })
                _needs_sanitize = True  # 新增 tool 消息，下轮需要清洗

            if should_break_tool_limit:
                return {
                    'ok': True,
                    'content': full_content + f"\n\n已达到工具调用次数限制({max_tool_calls})，自动停止。",
                    'final_content': f"\n\n已达到工具调用次数限制({max_tool_calls})，自动停止。",
                    'new_messages': working_messages[initial_msg_count:],
                    'tool_calls_history': tool_calls_history,
                    'call_records': call_records,
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
                        '\n\n[重要：你的下一条回复必须以 <think> 标签开头。'
                        '在标签内分析以上执行结果和当前进度，'
                        '检查 Todo 列表中哪些步骤已完成（用 update_todo 标记为 done），'
                        '确认下一步计划后再继续执行。不要跳过 <think> 标签。]'
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
            'final_content': '',  # max iterations 时无明确的最终回复
            'new_messages': working_messages[initial_msg_count:],
            'tool_calls_history': tool_calls_history,
            'call_records': call_records,
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
    
    def _get_json_mode_system_prompt(self, tools_list: Optional[List[dict]] = None) -> str:
        """获取 JSON 模式的系统提示（执行器模式）"""
        # 构建工具列表说明
        tool_descriptions = []
        for tool in (tools_list or HOUDINI_TOOLS):
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

节点路径输出规范:
-回复中提及节点时必须写完整绝对路径(如/obj/geo1/box1),不能只写节点名(如box1)
-路径会自动变为可点击链接,用户可直接跳转到对应节点

工具调用参数规范（最高优先级）:
-调用前必须确认所有(必填)参数都已填写,缺少必填参数会导致调用失败
-node_path必须用完整绝对路径(如"/obj/geo1/box1"),不能只写节点名
-参数值类型必须正确:string/number/boolean/array,不要混用
-工具返回"缺少参数"错误时,直接修正参数重试,不要调用check_errors
-每次调用都要完整填写所有必填参数,不要假设系统记住上次参数

安全操作规则（必须遵守）:
-首次了解网络时调用get_network_structure,已查询过的网络不要重复调用(系统缓存同轮查询结果)
-设置参数前必须先用get_node_parameters查询正确的参数名和类型,不要猜测参数名
-execute_python中必须检查None:node=hou.node(path);if node:...
-创建节点后用返回的路径操作,不要猜测路径
-连接节点前确认两个节点都已存在

完成前必须检查（任务结束前强制执行）:
-调用verify_and_summarize自动检测(已内置网络检查,不需先调get_network_structure)
-如有问题修复后重新调用verify_and_summarize直到通过

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
                              max_iterations: int = 999,
                              temperature: float = 0.17,
                              max_tokens: Optional[int] = None,
                              enable_thinking: bool = True,
                              supports_vision: bool = True,
                              tools_override: Optional[List[dict]] = None,
                              on_content: Optional[Callable[[str], None]] = None,
                              on_thinking: Optional[Callable[[str], None]] = None,
                              on_tool_call: Optional[Callable[[str, dict], None]] = None,
                              on_tool_result: Optional[Callable[[str, dict, dict], None]] = None,
                              on_tool_args_delta: Optional[Callable[[str, str, str], None]] = None,
                              on_iteration_start: Optional[Callable[[int], None]] = None) -> Dict[str, Any]:
        """JSON 模式 Agent Loop（用于不支持 Function Calling 的模型）"""
        
        if not self._tool_executor:
            return {'ok': False, 'error': '未设置工具执行器', 'content': '', 'tool_calls_history': [], 'iterations': 0}
        
        # ★ 工具列表：支持外部覆盖（用于 Ask 模式等场景）
        effective_tools = tools_override if tools_override is not None else HOUDINI_TOOLS
        
        # 添加 JSON 模式系统提示
        json_system_prompt = self._get_json_mode_system_prompt(effective_tools)
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
        
        # ── 预处理：非视觉模型剥离所有 image_url 内容 ──
        if not supports_vision:
            n_stripped = self._strip_image_content(working_messages, keep_recent_user=0)
            if n_stripped > 0:
                print(f"[AI Client] 非视觉模型 ({model})：已剥离 {n_stripped} 张图片")
        
        tool_calls_history = []
        call_records = []  # 每次 API 调用的详细记录（对齐 Cursor）
        full_content = ""
        iteration = 0
        self._json_thinking_buffer = ""  # 初始化思考缓冲区
        
        # 累积 usage 统计（用于 cache 命中率统计）
        total_usage = {
            'prompt_tokens': 0,
            'completion_tokens': 0,
            'reasoning_tokens': 0,
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
                    'call_records': call_records,
                    'iterations': iteration, 'stopped': True, 'usage': total_usage
                }
            
            iteration += 1
            _call_start = time.time()  # 记录本次 API 调用起始时间（对齐 Cursor 延迟统计）
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
            
            # ★ 通知 UI 新一轮 API 请求即将开始（用于显示 "Generating..." 状态）
            if on_iteration_start:
                on_iteration_start(iteration)
            
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
                        'call_records': call_records,
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
                                'call_records': call_records,
                                'iterations': iteration, 'usage': total_usage
                            }
                        
                        if is_context_exceeded:
                            # 上下文超限：立即裁剪
                            if on_content:
                                on_content(f"\n[上下文超限，智能裁剪后重试 ({server_error_retries}/{max_server_retries})...]\n")
                            working_messages = self._progressive_trim(
                                working_messages, tool_calls_history,
                                trim_level=server_error_retries,
                                supports_vision=supports_vision
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
                                    trim_level=server_error_retries - 1,
                                    supports_vision=supports_vision
                                )
                        break  # 退出 for，回到 while 重试
                    return {
                        'ok': False, 'error': err_msg,
                        'content': full_content, 'tool_calls_history': tool_calls_history,
                        'call_records': call_records,
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
                        total_usage['reasoning_tokens'] += usage.get('reasoning_tokens', 0)
                        total_usage['total_tokens'] += usage.get('total_tokens', 0)
                        total_usage['cache_hit_tokens'] += usage.get('cache_hit_tokens', 0)
                        total_usage['cache_miss_tokens'] += usage.get('cache_miss_tokens', 0)
                    
                    # ---- 记录本次 API 调用详情（对齐 Cursor） ----
                    import datetime as _dt
                    _call_latency = time.time() - _call_start
                    _rec_inp = usage.get('prompt_tokens', 0)
                    _rec_out = usage.get('completion_tokens', 0)
                    _rec_reason = usage.get('reasoning_tokens', 0)
                    _rec_chit = usage.get('cache_hit_tokens', 0)
                    _rec_cmiss = usage.get('cache_miss_tokens', 0)
                    try:
                        from houdini_agent.utils.token_optimizer import calculate_cost as _calc_cost
                        _rec_cost = _calc_cost(model, _rec_inp, _rec_out, _rec_chit, _rec_cmiss, _rec_reason)
                    except Exception:
                        _rec_cost = 0.0
                    call_records.append({
                        'timestamp': _dt.datetime.now().isoformat(),
                        'model': model,
                        'iteration': iteration,
                        'input_tokens': _rec_inp,
                        'output_tokens': _rec_out,
                        'reasoning_tokens': _rec_reason,
                        'cache_hit': _rec_chit,
                        'cache_miss': _rec_cmiss,
                        'total_tokens': usage.get('total_tokens', 0),
                        'latency': round(_call_latency, 2),
                        'has_tool_calls': False,
                        'estimated_cost': _rec_cost,
                    })
                    break
            
            # 清理内容中的XML标签和格式问题（使用预编译正则）
            cleaned_content = round_content
            for _pat in self._RE_CLEAN_PATTERNS:
                cleaned_content = _pat.sub('', cleaned_content)
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
                    'call_records': call_records,
                    'iterations': iteration,
                    'usage': total_usage
                }
            
            # 添加助手消息（使用清理后的内容，但不要重复添加到full_content）
            json_assistant_msg = {'role': 'assistant', 'content': cleaned_content}
            # reasoning_content 仅在回传时对 DeepSeek / 原生 GLM 有效（Duojie 无需回传）
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
            
            # 保存当前轮次的内容（使用预编译正则清理XML标签）
            cleaned_round = round_content
            for _pat in self._RE_CLEAN_PATTERNS:
                cleaned_round = _pat.sub('', cleaned_round)
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
            'call_records': call_records,
            'iterations': iteration,
            'usage': total_usage
        }
    
    def agent_loop_auto(self,
                        messages: List[Dict[str, Any]],
                        model: str = 'gpt-5.2',
                        provider: str = 'openai',
                        **kwargs) -> Dict[str, Any]:
        """自动选择合适的 Agent Loop 模式"""
        if self._supports_function_calling(provider, model):
            return self.agent_loop_stream(messages=messages, model=model, provider=provider, **kwargs)
        else:
            return self.agent_loop_json_mode(messages=messages, model=model, provider=provider, **kwargs)


# 兼容旧代码
OpenAIClient = AIClient
