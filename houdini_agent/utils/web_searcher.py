# -*- coding: utf-8 -*-
"""
WebSearcher — extracted from ai_client.py

Multi-engine web search with automatic fallback (Brave → DuckDuckGo) and caching.
"""

import os
import sys
import re
import time
from typing import List, Dict, Any

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
