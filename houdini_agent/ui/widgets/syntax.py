import re
import html


class SyntaxHighlighter:
    """代码语法高亮 — 基于 token 的着色

    支持语言: VEX, Python, JSON, YAML, Bash/Shell, JavaScript/TypeScript,
    HScript, GLSL, Markdown
    """

    COL = {
        'keyword':  '#569CD6',
        'type':     '#4EC9B0',
        'builtin':  '#DCDCAA',
        'string':   '#CE9178',
        'comment':  '#6A9955',
        'number':   '#B5CEA8',
        'attr':     '#9CDCFE',
        'key':      '#9CDCFE',    # JSON / YAML key
        'constant': '#569CD6',    # true / false / null
        'operator': '#D4D4D4',    # operators
        'directive': '#C586C0',   # preprocessor / shebang
    }

    # ---- VEX ----
    VEX_KW = frozenset(
        'if else for while return break continue foreach do switch case default'.split()
    )
    VEX_TY = frozenset(
        'float vector vector2 vector4 int string void matrix matrix3 dict'.split()
    )
    VEX_BI = frozenset(
        'set getattrib setattrib point prim detail chf chi chs chv chramp '
        'length normalize fit fit01 rand noise sin cos pow sqrt abs min max '
        'clamp lerp smooth cross dot addpoint addprim addvertex removeprim '
        'removepoint npoints nprims printf sprintf push pop append resize len '
        'find sort sample_direction_uniform pcopen pcfilter nearpoint '
        'nearpoints xyzdist primuv'.split()
    )

    # ---- Python ----
    PY_KW = frozenset(
        'import from def class return if else elif for while try except finally '
        'with as in not and or is None True False pass break continue raise '
        'yield lambda global nonlocal del assert'.split()
    )
    PY_BI = frozenset(
        'print len range str int float list dict tuple set type isinstance '
        'enumerate zip map filter sorted reversed open super property '
        'staticmethod classmethod hasattr getattr setattr'.split()
    )

    # ---- JavaScript / TypeScript ----
    JS_KW = frozenset(
        'var let const function return if else for while do switch case default '
        'break continue new this typeof instanceof void delete throw try catch '
        'finally class extends import export from as async await yield of in '
        'static get set super'.split()
    )
    JS_TY = frozenset(
        'string number boolean any void never unknown object symbol bigint '
        'undefined null Array Promise Map Set Record Partial Required Readonly '
        'interface type enum namespace'.split()
    )
    JS_BI = frozenset(
        'console log warn error parseInt parseFloat isNaN isFinite '
        'JSON Math Date RegExp Object Array String Number Boolean '
        'setTimeout setInterval clearTimeout clearInterval '
        'fetch require module exports process'.split()
    )

    # ---- Bash / Shell ----
    BASH_KW = frozenset(
        'if then else elif fi for do done while until case esac in '
        'function return exit break continue select'.split()
    )
    BASH_BI = frozenset(
        'echo printf cd ls cp mv rm mkdir rmdir cat grep sed awk find '
        'chmod chown tar gzip gunzip curl wget git pip python node npm '
        'export source alias unalias set unset read eval exec test '
        'true false shift'.split()
    )

    # ---- HScript ----
    HSCRIPT_KW = frozenset(
        'if else endif for foreach end set setenv echo opcf opcd '
        'opparm oprm opadd opsave opload chadd chkey chls optype '
        'opflag opname opset oppane opproperty'.split()
    )

    # ---- GLSL ----
    GLSL_KW = frozenset(
        'if else for while do return break continue discard switch case default '
        'struct void const in out inout uniform varying attribute '
        'layout precision highp mediump lowp flat smooth noperspective '
        'centroid sample'.split()
    )
    GLSL_TY = frozenset(
        'float vec2 vec3 vec4 int ivec2 ivec3 ivec4 uint uvec2 uvec3 uvec4 '
        'bool bvec2 bvec3 bvec4 mat2 mat3 mat4 mat2x2 mat2x3 mat2x4 '
        'mat3x2 mat3x3 mat3x4 mat4x2 mat4x3 mat4x4 '
        'sampler1D sampler2D sampler3D samplerCube sampler2DShadow'.split()
    )
    GLSL_BI = frozenset(
        'texture texture2D textureCube normalize length distance dot cross '
        'reflect refract mix clamp smoothstep step min max abs sign floor '
        'ceil fract mod pow exp log sqrt inversesqrt sin cos tan asin acos atan '
        'radians degrees dFdx dFdy fwidth'.split()
    )

    @classmethod
    def highlight_vex(cls, code: str) -> str:
        return cls._tokenize(code, cls.VEX_KW, cls.VEX_TY, cls.VEX_BI,
                              '//', ('/*', '*/'), '@')

    @classmethod
    def highlight_python(cls, code: str) -> str:
        return cls._tokenize(code, cls.PY_KW, frozenset(), cls.PY_BI,
                              '#', None, None)

    @classmethod
    def highlight_javascript(cls, code: str) -> str:
        return cls._tokenize(code, cls.JS_KW, cls.JS_TY, cls.JS_BI,
                              '//', ('/*', '*/'), None)

    @classmethod
    def highlight_bash(cls, code: str) -> str:
        return cls._tokenize(code, cls.BASH_KW, frozenset(), cls.BASH_BI,
                              '#', None, '$')

    @classmethod
    def highlight_hscript(cls, code: str) -> str:
        return cls._tokenize(code, cls.HSCRIPT_KW, frozenset(), frozenset(),
                              '#', None, '$')

    @classmethod
    def highlight_glsl(cls, code: str) -> str:
        return cls._tokenize(code, cls.GLSL_KW, cls.GLSL_TY, cls.GLSL_BI,
                              '//', ('/*', '*/'), None)

    @classmethod
    def highlight_json(cls, code: str) -> str:
        """JSON 高亮：key 和 value 区分着色"""
        parts: list = []
        i, n = 0, len(code)
        # 简单状态：上一个非空白字符是 { 或 , 或行首 → 下一个字符串是 key
        expect_key = True

        while i < n:
            c = code[i]

            # 空白
            if c in (' ', '\t', '\n', '\r'):
                parts.append(c)
                if c == '\n':
                    expect_key = True
                i += 1
                continue

            # 字符串
            if c == '"':
                j = i + 1
                while j < n and code[j] != '"':
                    if code[j] == '\\':
                        j += 1
                    j += 1
                if j < n:
                    j += 1
                s = code[i:j]
                # 判断是 key 还是 value
                # key 后面（跳过空白）应该是 :
                rest = code[j:].lstrip()
                if expect_key and rest.startswith(':'):
                    parts.append(cls._span('key', s))
                    expect_key = False
                else:
                    parts.append(cls._span('string', s))
                i = j
                continue

            # 冒号
            if c == ':':
                parts.append(html.escape(c))
                expect_key = False
                i += 1
                continue

            # 逗号
            if c == ',':
                parts.append(html.escape(c))
                expect_key = True
                i += 1
                continue

            # 大括号 / 方括号
            if c in ('{', '['):
                parts.append(html.escape(c))
                expect_key = True
                i += 1
                continue
            if c in ('}', ']'):
                parts.append(html.escape(c))
                i += 1
                continue

            # 数字
            if c.isdigit() or (c == '-' and i + 1 < n and code[i + 1].isdigit()):
                j = i + 1 if c == '-' else i
                while j < n and (code[j].isdigit() or code[j] in ('.', 'e', 'E', '+', '-')):
                    j += 1
                parts.append(cls._span('number', code[i:j]))
                i = j
                continue

            # true / false / null
            for kw in ('true', 'false', 'null'):
                if code[i:i + len(kw)] == kw:
                    parts.append(cls._span('constant', kw))
                    i += len(kw)
                    break
            else:
                parts.append(html.escape(c))
                i += 1

        return ''.join(parts)

    @classmethod
    def highlight_yaml(cls, code: str) -> str:
        """YAML 高亮：key-value 区分、注释、列表标记"""
        parts: list = []
        lines = code.split('\n')
        for li, line in enumerate(lines):
            if li > 0:
                parts.append('\n')

            stripped = line.lstrip()

            # 注释
            if stripped.startswith('#'):
                parts.append(cls._span('comment', line))
                continue

            # 文档分隔符 ---
            if stripped in ('---', '...'):
                parts.append(cls._span('directive', line))
                continue

            # 列表项 - xxx: value
            indent = line[:len(line) - len(stripped)]
            if indent:
                parts.append(html.escape(indent))

            # 检查 key: value 格式
            colon_pos = stripped.find(':')
            if colon_pos > 0 and (colon_pos + 1 >= len(stripped) or stripped[colon_pos + 1] == ' '):
                # 处理列表标记
                key_part = stripped[:colon_pos]
                if key_part.startswith('- '):
                    parts.append(html.escape('- '))
                    key_part = key_part[2:]

                parts.append(cls._span('key', key_part))
                parts.append(html.escape(':'))

                value_part = stripped[colon_pos + 1:]
                if value_part:
                    # 检查 value 中的注释
                    comment_pos = value_part.find(' #')
                    if comment_pos >= 0:
                        val = value_part[:comment_pos]
                        comment = value_part[comment_pos:]
                        parts.append(cls._highlight_yaml_value(val))
                        parts.append(cls._span('comment', comment))
                    else:
                        parts.append(cls._highlight_yaml_value(value_part))
            else:
                # 列表项或纯值
                if stripped.startswith('- '):
                    parts.append(html.escape('- '))
                    parts.append(cls._highlight_yaml_value(stripped[2:]))
                else:
                    parts.append(html.escape(stripped))

        return ''.join(parts)

    @classmethod
    def _highlight_yaml_value(cls, value: str) -> str:
        """高亮 YAML 值"""
        v = value.strip()
        if not v:
            return html.escape(value)

        # 保留前导空格
        leading = value[:len(value) - len(value.lstrip())]
        result = html.escape(leading) if leading else ''

        # 字符串（带引号）
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            return result + cls._span('string', v)
        # 布尔 / null
        if v.lower() in ('true', 'false', 'yes', 'no', 'on', 'off', 'null', '~'):
            return result + cls._span('constant', v)
        # 数字
        try:
            float(v)
            return result + cls._span('number', v)
        except ValueError:
            pass
        return result + html.escape(v)

    @classmethod
    def _tokenize(cls, code, keywords, types, builtins,
                   comment_single, comment_multi, attr_prefix):
        parts: list = []
        i, n = 0, len(code)
        while i < n:
            c = code[i]
            # --- single-line comment ---
            if comment_single and code[i:i + len(comment_single)] == comment_single:
                end = code.find('\n', i)
                if end == -1:
                    end = n
                parts.append(cls._span('comment', code[i:end]))
                i = end
                continue
            # --- multi-line comment ---
            if comment_multi and code[i:i + len(comment_multi[0])] == comment_multi[0]:
                end = code.find(comment_multi[1], i + len(comment_multi[0]))
                end = n if end == -1 else end + len(comment_multi[1])
                parts.append(cls._span('comment', code[i:end]))
                i = end
                continue
            # --- strings ---
            if c in ('"', "'", '`'):
                # Template literals (JS backtick strings)
                if c == '`':
                    j = i + 1
                    while j < n and code[j] != '`':
                        if code[j] == '\\':
                            j += 1
                        j += 1
                    if j < n:
                        j += 1
                    parts.append(cls._span('string', code[i:j]))
                    i = j
                    continue
                triple = code[i:i + 3]
                if triple in ('"""', "'''"):
                    end = code.find(triple, i + 3)
                    end = n if end == -1 else end + 3
                    parts.append(cls._span('string', code[i:end]))
                    i = end
                    continue
                j = i + 1
                while j < n and code[j] != c and code[j] != '\n':
                    if code[j] == '\\':
                        j += 1
                    j += 1
                if j < n and code[j] == c:
                    j += 1
                parts.append(cls._span('string', code[i:j]))
                i = j
                continue
            # --- attribute prefix (@P, $VAR etc.) ---
            if (attr_prefix and c == attr_prefix
                    and i + 1 < n and (code[i + 1].isalpha() or code[i + 1] == '_')):
                j = i + 1
                while j < n and (code[j].isalnum() or code[j] in ('_', '.')):
                    j += 1
                parts.append(cls._span('attr', code[i:j]))
                i = j
                continue
            # --- preprocessor directive (#include, #define) ---
            if c == '#' and (not comment_single or comment_single != '#'):
                if i == 0 or code[i - 1] == '\n':
                    end = code.find('\n', i)
                    if end == -1:
                        end = n
                    parts.append(cls._span('directive', code[i:end]))
                    i = end
                    continue
            # --- identifier / keyword ---
            if c.isalpha() or c == '_':
                j = i
                while j < n and (code[j].isalnum() or code[j] == '_'):
                    j += 1
                word = code[i:j]
                if word in keywords:
                    parts.append(cls._span('keyword', word))
                elif word in types:
                    parts.append(cls._span('type', word))
                elif word in builtins:
                    parts.append(cls._span('builtin', word))
                else:
                    parts.append(html.escape(word))
                i = j
                continue
            # --- number (including hex 0x...) ---
            if c.isdigit() or (c == '.' and i + 1 < n and code[i + 1].isdigit()):
                j = i
                if c == '0' and j + 1 < n and code[j + 1] in ('x', 'X'):
                    j += 2
                    while j < n and (code[j].isdigit() or code[j] in 'abcdefABCDEF'):
                        j += 1
                else:
                    while j < n and (code[j].isdigit() or code[j] in ('.', 'e', 'E', '+', '-', 'f')):
                        if code[j] in ('+', '-') and j > 0 and code[j - 1] not in ('e', 'E'):
                            break
                        j += 1
                parts.append(cls._span('number', code[i:j]))
                i = j
                continue
            parts.append(html.escape(c))
            i += 1
        return ''.join(parts)

    @classmethod
    def _span(cls, tok_type: str, text: str) -> str:
        color = cls.COL.get(tok_type, '#D4D4D4')
        return f'<span style="color:{color};">{html.escape(text)}</span>'
