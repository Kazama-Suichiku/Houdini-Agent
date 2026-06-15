import re
import html
from .theme import _linkify_node_paths, _NODE_PATH_RE, _NODE_LINK_STYLE, CursorTheme


class SimpleMarkdown:
    """将 Markdown 转换为 Qt Rich Text HTML（增强版）

    支持特性：
    - 标题 (# ~ ####)
    - 粗体 / 斜体 / 删除线 / 行内代码
    - 无序列表 / 有序列表 / 任务列表 / 嵌套列表
    - 引用块（多行合并，支持渐变背景）
    - 表格（居中 / 左对齐 / 右对齐）
    - 水平分割线
    - 链接 [text](url) / 自动 URL 检测
    - 图片 ![alt](url)
    - 脚注 [^id] / [^id]: ...
    - 转义字符 \\* \\` 等
    - 围栏代码块（交给 CodeBlockWidget）
    """

    _CODE_BLOCK_RE = re.compile(r'```(\w*)\n(.*?)```', re.DOTALL)
    _TABLE_SEP_RE = re.compile(r'^\|?\s*[-:]+[-| :]*$')  # 表头分割行
    # 自动检测裸 URL
    _AUTO_URL_RE = re.compile(
        r'(?<!["\w/=])(?<!\]\()(?<!\[)'       # 不在引号、字母、=、](、[ 之后
        r'(https?://[^\s<>\)\]\"\'`]+)'        # URL 本体
    )
    # 脚注引用
    _FOOTNOTE_REF_RE = re.compile(r'\[\^(\w+)\](?!:)')
    # 脚注定义
    _FOOTNOTE_DEF_RE = re.compile(r'^\[\^(\w+)\]:\s*(.*)')
    # 图片语法
    _IMAGE_RE = re.compile(r'!\[([^\]]*)\]\(([^)]+)\)')
    # 列表缩进检测
    _LIST_ITEM_RE = re.compile(r'^(\s*)([-*]|\d+\.)\s+(.*)')
    # 任务列表
    _TASK_ITEM_RE = re.compile(r'^(\s*)[-*]\s+\[([ xX])\]\s+(.*)')

    # -------- 公共接口 --------

    @classmethod
    def parse_segments(cls, text: str) -> list:
        """将文本拆分为 ('text', html), ('code', lang, raw_code), ('image', url, alt) 段落"""
        segments: list = []
        last = 0
        for m in cls._CODE_BLOCK_RE.finditer(text):
            before = text[last:m.start()]
            if before.strip():
                cls._parse_text_with_images(before, segments)
            segments.append(('code', m.group(1) or '', m.group(2).rstrip()))
            last = m.end()
        after = text[last:]
        if after.strip():
            cls._parse_text_with_images(after, segments)
        if not segments and text.strip():
            cls._parse_text_with_images(text, segments)
        return segments

    @classmethod
    def _parse_text_with_images(cls, text: str, segments: list):
        """将文本段落进一步拆分出独立的 image segment

        只有独占一行的 ![alt](url) 才作为独立 image segment，
        行内的图片语法仍按行内格式处理。
        """
        lines = text.split('\n')
        buf_lines: list = []

        def _flush_buf():
            if buf_lines:
                joined = '\n'.join(buf_lines)
                if joined.strip():
                    segments.append(('text', cls._text_to_html(joined)))
                buf_lines.clear()

        for line in lines:
            stripped = line.strip()
            img_match = cls._IMAGE_RE.fullmatch(stripped)
            if img_match:
                _flush_buf()
                segments.append(('image', img_match.group(2), img_match.group(1)))
            else:
                buf_lines.append(line)
        _flush_buf()

    @classmethod
    def has_rich_content(cls, text: str) -> bool:
        """判断文本是否包含 Markdown 格式"""
        if '```' in text:
            return True
        if re.search(r'^#{1,4}\s', text, re.MULTILINE):
            return True
        if '**' in text or '`' in text:
            return True
        if re.search(r'^[-*]\s', text, re.MULTILINE):
            return True
        if re.search(r'^\d+\.\s', text, re.MULTILINE):
            return True
        if '|' in text and re.search(r'^\|.+\|', text, re.MULTILINE):
            return True
        if cls._IMAGE_RE.search(text):
            return True
        if cls._FOOTNOTE_REF_RE.search(text):
            return True
        return False

    # -------- 块级解析 --------

    @classmethod
    def _get_indent(cls, line: str) -> int:
        """返回行的缩进空格数"""
        return len(line) - len(line.lstrip())

    @classmethod
    def _text_to_html(cls, text: str) -> str:
        # 合并表格行间的空行（兼容 LLM 生成的"松散表格"格式：行间有 \n\n）
        text = re.sub(r'(\|[^\n]*)\n{2,}(\|)', r'\1\n\2', text)
        lines = text.split('\n')
        out: list = []
        i = 0
        n = len(lines)

        # 嵌套列表状态栈: [(tag, indent_level), ...]
        list_stack: list = []
        # 引用块缓冲
        quote_buf: list = []
        # 脚注定义收集
        footnotes: dict = {}

        # 第一遍：收集脚注定义
        remaining_lines: list = []
        for line in lines:
            fn_match = cls._FOOTNOTE_DEF_RE.match(line.strip())
            if fn_match:
                footnotes[fn_match.group(1)] = fn_match.group(2)
            else:
                remaining_lines.append(line)
        lines = remaining_lines
        n = len(lines)

        def _flush_all_lists():
            while list_stack:
                _, ltag = list_stack.pop()
                out.append(f'</{ltag}>')

        def _flush_lists_to_indent(target_indent: int):
            """关闭所有缩进大于 target_indent 的列表层级"""
            while list_stack and list_stack[-1][0] > target_indent:
                _, ltag = list_stack.pop()
                out.append(f'</{ltag}>')

        def _flush_quote():
            nonlocal quote_buf
            if quote_buf:
                q_html = '<br>'.join(cls._inline(q, footnotes) for q in quote_buf)
                out.append(
                    f'<div style="border-left:2px solid rgba(148,163,184,50);padding:8px 14px;'
                    f'margin:8px 0;'
                    f'background:transparent;'
                    f'color:#cbd5e1;border-radius:0 6px 6px 0;'
                    f'line-height:1.6;">{q_html}</div>'
                )
                quote_buf = []

        while i < n:
            raw_line = lines[i]
            s = raw_line.strip()

            # ---- empty line ----
            if not s:
                _flush_quote()
                _flush_all_lists()
                out.append('<div style="height:4px;"></div>')
                i += 1
                continue

            # ---- horizontal rule ----
            if re.match(r'^[-*_]{3,}\s*$', s):
                _flush_quote()
                _flush_all_lists()
                out.append(
                    '<hr style="border:none;border-top:1px solid rgba(255,255,255,8);margin:16px 0;width:100%;">'
                )
                i += 1
                continue

            # ---- table ----
            if '|' in s and i + 1 < n and cls._TABLE_SEP_RE.match(lines[i + 1].strip()):
                _flush_quote()
                _flush_all_lists()
                table_html = cls._parse_table(lines, i)
                if table_html:
                    out.append(table_html[0])
                    i = table_html[1]
                    continue

            # ---- headers ----
            header_match = re.match(r'^(#{1,4})\s+(.+)', s)
            if header_match:
                _flush_quote()
                _flush_all_lists()
                lvl = len(header_match.group(1))
                content = header_match.group(2)
                styles = {
                    1: ('1.5em', '#f1f5f9', '700', '18px 0 8px 0', 'border-bottom:1px solid rgba(255,255,255,12);padding-bottom:8px;letter-spacing:0.3px;'),
                    2: ('1.3em', '#e2e8f0', '600', '16px 0 6px 0', 'letter-spacing:0.2px;'),
                    3: ('1.1em', '#cbd5e1', '600', '12px 0 4px 0', ''),
                    4: ('1.0em', '#94a3b8', '600', '10px 0 3px 0', ''),
                }
                sz, clr, wt, mg, extra = styles[lvl]
                out.append(
                    f'<p style="font-size:{sz};font-weight:{wt};'
                    f'color:{clr};margin:{mg};{extra}">'
                    f'{cls._inline(content, footnotes)}</p>'
                )
                i += 1
                continue

            # ---- blockquote (合并连续行) ----
            if s.startswith('> '):
                _flush_all_lists()
                quote_buf.append(s[2:])
                i += 1
                continue
            elif s.startswith('>'):
                _flush_all_lists()
                quote_buf.append(s[1:].lstrip())
                i += 1
                continue
            else:
                _flush_quote()

            # ---- task list (with nesting support) ----
            task_match = cls._TASK_ITEM_RE.match(raw_line)
            if task_match:
                indent = len(task_match.group(1))
                _flush_lists_to_indent(indent)
                if not list_stack or list_stack[-1][0] < indent:
                    out.append(
                        '<ul style="margin:2px 0;padding-left:4px;list-style:none;">'
                    )
                    list_stack.append((indent, 'ul'))
                checked = task_match.group(2) in ('x', 'X')
                box = (
                    '<span style="color:#10b981;font-weight:bold;margin-right:6px;">✓</span>'
                    if checked else
                    '<span style="color:#64748b;margin-right:6px;">○</span>'
                )
                text_style = 'color:#64748b;text-decoration:line-through;' if checked else ''
                out.append(
                    f'<li style="margin:4px 0;line-height:1.6;{text_style}">'
                    f'{box}{cls._inline(task_match.group(3), footnotes)}</li>'
                )
                i += 1
                continue

            # ---- unordered / ordered list (with nesting) ----
            list_match = cls._LIST_ITEM_RE.match(raw_line)
            if list_match:
                indent = len(list_match.group(1))
                marker = list_match.group(2)
                item_text = list_match.group(3)
                is_ordered = marker[-1] == '.'
                new_tag = 'ol' if is_ordered else 'ul'

                _flush_lists_to_indent(indent)

                if not list_stack or list_stack[-1][0] < indent:
                    # 开启新的嵌套层级
                    if is_ordered:
                        out.append(
                            '<ol style="margin:4px 0;padding-left:22px;color:#94a3b8;">'
                        )
                    else:
                        out.append(
                            '<ul style="margin:4px 0;padding-left:22px;'
                            'list-style-type:disc;color:#94a3b8;">'
                        )
                    list_stack.append((indent, new_tag))
                elif list_stack[-1][1] != new_tag:
                    # 同层级但类型切换
                    old_indent, old_tag = list_stack.pop()
                    out.append(f'</{old_tag}>')
                    if is_ordered:
                        out.append(
                            '<ol style="margin:4px 0;padding-left:22px;color:#94a3b8;">'
                        )
                    else:
                        out.append(
                            '<ul style="margin:4px 0;padding-left:22px;'
                            'list-style-type:disc;color:#94a3b8;">'
                        )
                    list_stack.append((indent, new_tag))

                out.append(
                    f'<li style="margin:4px 0;line-height:1.6;color:{CursorTheme.TEXT_PRIMARY};">'
                    f'{cls._inline(item_text, footnotes)}</li>'
                )
                i += 1
                continue

            # ---- normal paragraph ----
            _flush_all_lists()
            out.append(
                f'<p style="margin:4px 0;line-height:1.6;color:#e2e8f0;">'
                f'{cls._inline(s, footnotes)}</p>'
            )
            i += 1

        _flush_quote()
        _flush_all_lists()

        # 渲染脚注定义区域（如果有）
        if footnotes:
            out.append(
                '<hr style="border:none;border-top:1px solid rgba(255,255,255,8);'
                'margin:12px 0 6px 0;width:40%;">'
            )
            for fn_id, fn_text in footnotes.items():
                out.append(
                    f'<p style="margin:2px 0;font-size:0.85em;color:{CursorTheme.TEXT_SECONDARY};'
                    f'line-height:1.4;">'
                    f'<sup style="color:#60a5fa;">[{html.escape(fn_id)}]</sup> '
                    f'{cls._inline(fn_text, footnotes)}</p>'
                )

        return '\n'.join(out)

    # -------- 表格解析 --------

    @classmethod
    def _parse_table(cls, lines: list, start: int) -> tuple:
        """解析 Markdown 表格，返回 (html, next_line_index)"""
        header_line = lines[start].strip()
        if start + 1 >= len(lines):
            return None
        sep_line = lines[start + 1].strip()

        # 解析对齐方式
        sep_cells = [c.strip() for c in sep_line.strip('|').split('|')]
        aligns = []
        for c in sep_cells:
            c = c.strip()
            if c.startswith(':') and c.endswith(':'):
                aligns.append('center')
            elif c.endswith(':'):
                aligns.append('right')
            else:
                aligns.append('left')

        def _parse_row(line: str) -> list:
            line = line.strip()
            if line.startswith('|'):
                line = line[1:]
            if line.endswith('|'):
                line = line[:-1]
            return [c.strip() for c in line.split('|')]

        # 表头
        headers = _parse_row(header_line)

        # 表体
        rows = []
        j = start + 2
        while j < len(lines):
            row_s = lines[j].strip()
            if not row_s or '|' not in row_s:
                break
            rows.append(_parse_row(row_s))
            j += 1

        # 生成 HTML（现代极简：无外边框、无斑马纹、仅底线分隔）
        tbl = [
            '<table style="border-collapse:collapse;'
            'margin:10px 0;width:100%;font-size:0.92em;">'
        ]

        # thead
        tbl.append('<tr>')
        for ci, h in enumerate(headers):
            align = aligns[ci] if ci < len(aligns) else 'left'
            tbl.append(
                f'<th style="border-bottom:2px solid rgba(255,255,255,12);'
                f'padding:7px 14px;'
                f'background:transparent;color:#e2e8f0;font-weight:600;'
                f'text-align:{align};font-size:0.95em;">{cls._inline(h)}</th>'
            )
        tbl.append('</tr>')

        # tbody — 统一背景，仅底线分隔
        for ri, row in enumerate(rows):
            tbl.append('<tr>')
            for ci, cell in enumerate(row):
                align = aligns[ci] if ci < len(aligns) else 'left'
                border_bottom = (
                    'border-bottom:1px solid rgba(255,255,255,5);'
                    if ri < len(rows) - 1 else ''
                )
                tbl.append(
                    f'<td style="{border_bottom}padding:7px 14px;'
                    f'background:transparent;color:{CursorTheme.TEXT_PRIMARY};'
                    f'text-align:{align};line-height:1.5;">{cls._inline(cell)}</td>'
                )
            tbl.append('</tr>')

        tbl.append('</table>')
        return ('\n'.join(tbl), j)

    # -------- 行内解析 --------

    @classmethod
    def _inline(cls, text: str, footnotes: dict = None) -> str:
        """行内格式: **粗体**, *斜体*, ~~删除线~~, `代码`, [链接](url),
        ![图片](url), [^脚注], 自动URL, 转义字符, 节点路径"""
        # 1. 处理转义字符：先将 \X 替换为占位符，最后再还原
        _ESC_MAP = {}
        _esc_counter = [0]

        def _replace_escape(m):
            key = f'\x00ESC{_esc_counter[0]}\x00'
            _ESC_MAP[key] = m.group(1)  # 被转义的字符
            _esc_counter[0] += 1
            return key

        text = re.sub(r'\\([\\`*_~\[\]()#>!|])', _replace_escape, text)

        # 2. HTML 转义
        text = html.escape(text)

        # 3. 行内图片 ![alt](url)（行内级别，不独占行）
        text = re.sub(
            r'!\[([^\]]*)\]\(([^)]+)\)',
            r'<img src="\2" alt="\1" style="max-width:100%;max-height:200px;'
            r'border-radius:4px;margin:2px 0;vertical-align:middle;">',
            text,
        )

        # 4. 链接 [text](url)
        text = re.sub(
            r'\[([^\]]+?)\]\(([^)]+?)\)',
            r'<a href="\2" style="color:#818cf8;text-decoration:none;'
            r'border-bottom:1px solid rgba(129,140,248,0.3);">\1</a>',
            text,
        )

        # 5. 脚注引用 [^id]
        if footnotes:
            def _fn_ref(m):
                fid = m.group(1)
                if fid in footnotes:
                    return (
                        f'<sup style="color:#818cf8;cursor:pointer;">'
                        f'<a href="#fn-{html.escape(fid)}" style="color:#818cf8;'
                        f'text-decoration:none;">[{html.escape(fid)}]</a></sup>'
                    )
                return m.group(0)
            text = cls._FOOTNOTE_REF_RE.sub(_fn_ref, text)

        # 6. 粗体
        text = re.sub(r'\*\*(.+?)\*\*', r'<b style="color:#f1f5f9;font-weight:600;">\1</b>', text)
        # 7. 删除线
        text = re.sub(r'~~(.+?)~~', r'<s style="color:#64748b;">\1</s>', text)
        # 8. 斜体
        text = re.sub(r'(?<!\*)\*([^*]+?)\*(?!\*)', r'<i style="color:#cbd5e1;">\1</i>', text)
        # 9. 行内代码
        text = re.sub(
            r'`([^`]+?)`',
            r'<code style="background:rgba(255,255,255,8);padding:2px 7px;border-radius:5px;'
            r'font-family:Consolas,Monaco,monospace;color:#c9d1d9;'
            r'font-size:0.88em;border:1px solid rgba(255,255,255,5);">\1</code>',
            text,
        )
        # 10. 自动 URL 检测（裸链接）
        text = cls._AUTO_URL_RE.sub(
            r'<a href="\1" style="color:#818cf8;text-decoration:none;">\1</a>',
            text,
        )
        # 11. Houdini 节点路径 → 可点击链接
        text = _linkify_node_paths(text)

        # 12. 还原转义字符
        for key, char in _ESC_MAP.items():
            text = text.replace(key, html.escape(char))

        return text
