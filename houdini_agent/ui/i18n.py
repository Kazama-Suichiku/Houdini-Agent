# -*- coding: utf-8 -*-
"""
Internationalization (i18n) — 双语支持 (中文 / English)

使用方式:
    from houdini_agent.ui.i18n import tr, set_language, get_language

    label.setText(tr("confirm"))        # -> "确认" or "Confirm"
    msg = tr("toast.undo_all", 5)       # -> "已撤销全部 5 个操作" or "Undone all 5 operations"
"""

from houdini_agent.qt_compat import QtCore, QSettings

# ---------------------------------------------------------------------------
# 全局状态
# ---------------------------------------------------------------------------
_current_lang = 'zh'  # 'zh' | 'en'

# 语言变更通知（供 retranslateUi 使用）
# 外部使用:  from i18n import language_changed; language_changed.connect(...)
class _LangSignals(QtCore.QObject):
    changed = QtCore.Signal(str)   # 新语言代码

language_changed = _LangSignals()


def get_language() -> str:
    return _current_lang


def set_language(lang: str, persist: bool = True):
    """切换全局语言  lang: 'zh' | 'en'"""
    global _current_lang
    lang = lang.lower()
    if lang not in ('zh', 'en'):
        lang = 'zh'
    if lang == _current_lang:
        return
    _current_lang = lang
    if persist:
        s = QSettings("HoudiniAgent", "Settings")
        s.setValue("language", lang)
    language_changed.changed.emit(lang)


def load_language():
    """启动时从 QSettings 恢复语言"""
    global _current_lang
    s = QSettings("HoudiniAgent", "Settings")
    saved = s.value("language", "zh")
    if saved in ('zh', 'en'):
        _current_lang = saved


def tr(key: str, *args) -> str:
    """翻译函数  tr("key")  or  tr("key", arg1, arg2, ...)"""
    table = _ZH if _current_lang == 'zh' else _EN
    text = table.get(key)
    if text is None:
        # fallback: 尝试另一语言
        text = (_EN if _current_lang == 'zh' else _ZH).get(key, key)
    if args:
        try:
            text = text.format(*args)
        except (IndexError, KeyError):
            pass
    return text


# ---------------------------------------------------------------------------
# 翻译字典  —  按模块 / 功能分组
# ---------------------------------------------------------------------------

_ZH = {
    # ===== Header =====
    'header.think.tooltip': '启用思考模式：AI 会先分析再回答，并显示思考过程',
    'header.cache.tooltip': '缓存管理：保存/加载对话历史',
    'header.optimize.tooltip': 'Token 优化：自动压缩和优化',
    'header.update.tooltip': '检查并更新到最新版本',
    'header.font.tooltip': '字体大小 (Ctrl+/Ctrl-)',
    'header.token_stats.tooltip': '点击查看详细 Token 统计',

    # ===== Input Area =====
    'mode.tooltip': 'Agent: AI 自主操作节点\nAsk: 只读查询分析',
    'confirm': '确认',
    'confirm.tooltip': '确认模式：创建节点/VEX 前先预览确认',
    'placeholder': '输入消息... (Enter 发送, Shift+Enter 换行, @提及节点)',
    'attach_image.tooltip': '添加图片附件（支持 PNG/JPG/GIF/WebP，也可直接粘贴/拖拽图片到输入框）',
    'train.tooltip': '导出当前对话为训练数据（用于大模型微调）',

    # ===== Session Manager =====
    'session.new': '新建对话',
    'session.close': '关闭此对话',
    'session.close_others': '关闭其他对话',

    # ===== Font Settings =====
    'font.title': '字体大小',
    'font.scale': '字体缩放',
    'font.reset': '重置 100%',
    'font.close': '关闭',

    # ===== Thinking =====
    'thinking.init': '思考中...',
    'thinking.progress': '思考中... ({})',
    'thinking.round': '--- 第 {} 轮思考 ---',
    'thinking.done': '思考过程 ({})',

    # ===== Execution =====
    'exec.running': '执行中...',
    'exec.progress': '执行中... ({}/{})',
    'exec.done': '执行完成 ({}个操作, {})',
    'exec.done_err': '执行完成 ({} ok, {} err, {})',
    'exec.tool': '执行: {}',

    # ===== Buttons (shared) =====
    'btn.copy': '复制',
    'btn.copied': '已复制',
    'btn.close': '关闭',
    'btn.undo': 'undo',
    'btn.keep': 'keep',

    # ===== Expand / Collapse =====
    'msg.expand': '▶ 展开 ({} 行更多)',
    'msg.collapse': '▼ 收起',

    # ===== Code Preview =====
    'code.writing': '✍ Writing code for {}...',
    'code.complete': '✓ Code complete',

    # ===== Diff =====
    'diff.old': '旧值',
    'diff.new': '新值',

    # ===== Confirm Preview =====
    'confirm.title': '确认执行: {}',
    'confirm.params_more': '... 共 {} 个参数',
    'confirm.cancel': '✕ 取消',
    'confirm.execute': '↵ 确认执行',

    # ===== Node Operations =====
    'node.click_jump': '点击跳转: {}',
    'status.undone': '已撤销',
    'status.kept': '已保留',

    # ===== VEX Preview =====
    'vex.confirm_exec': '确认执行: {}',
    'vex.node_name': '节点名: {}',
    'vex.wrangle_type': '类型: {}',
    'vex.parent_path': '父路径: {}',
    'vex.node_type': '节点类型: {}',
    'vex.node_path': '节点路径: {}',
    'vex.cancel': '取消',
    'vex.summary_more': '\n  ... 共 {} 行参数',

    # ===== Status / Response =====
    'status.thinking': '思考',
    'status.calls': '{}次调用',
    'status.done': '完成 ({})',
    'status.exec_done_see_above': '执行完成，详见上方执行过程。',
    'status.history': '历史',
    'status.history_summary': '历史摘要',
    'status.context': '上下文',
    'status.history_with': '历史 | {}',
    'status.stats_reset': '统计已重置',

    # ===== Image =====
    'img.preview': '图片预览',
    'img.close': '关闭',
    'img.click_zoom': '点击放大查看',
    'img.not_supported': '不支持图片',
    'img.not_supported_msg': '当前模型 {} 不支持图片输入。\n请切换至支持视觉的模型（如 Claude、GPT-5.2 等）。',
    'img.select': '选择图片',
    'img.load_failed': '加载图片失败: {}',

    # ===== Token Stats =====
    'token.title': 'Token 使用分析',
    'token.headers': ['#', '时间', '模型', 'Input', 'Cache读', 'Cache写', 'Output', 'Think', 'Total', '延迟', '费用', ''],
    'token.reset': '重置统计',
    'token.close': '关闭',
    'token.detail_title': '  请求详细 ({} calls)',
    'token.no_records': '  暂无 API 调用记录',
    'token.summary': (
        '累计统计 ({} 次请求)\n'
        '输入: {:,}\n'
        '输出: {:,}\n'
        '{}'
        'Cache 读取: {:,}\n'
        'Cache 写入: {:,}\n'
        'Cache 命中率: {}\n'
        '总计: {:,}\n'
        '预估费用: {}\n'
        '点击查看详情'
    ),
    'token.reasoning_line': '推理 Token: {:,}\n',

    # ===== Shell =====
    'shell.exec_failed': '执行失败，查看详细信息↓',
    'shell.cmd_failed': '命令执行失败，查看详细信息↓',

    # ===== Code Block =====
    'codeblock.copy': '复制',
    'codeblock.copied': '已复制',
    'codeblock.create_wrangle': '创建 Wrangle',

    # ===== Toast Messages =====
    'toast.node_not_exist': '节点不存在或已被删除: {}',
    'toast.houdini_unavailable': 'Houdini 环境不可用',
    'toast.jump_failed': '跳转失败: {}',
    'toast.node_not_found': '节点不存在: {}',
    'toast.param_not_found': '参数不存在: {}',
    'toast.param_restored': '已恢复参数 {} 为旧值',
    'toast.missing_path': '缺少节点路径，无法撤销',
    'toast.undo_create': '已撤销创建（删除 {} 个节点）',
    'toast.node_gone': '节点已不存在，无需撤销',
    'toast.parent_not_found': '父节点不存在: {}',
    'toast.node_restored': '已恢复节点: {}',
    'toast.undone': '已撤销',
    'toast.undo_failed': '撤销失败: {}',
    'toast.undo_all': '已撤销全部 {} 个操作',
    'toast.keep_all': '已保留全部 {} 个操作',
    'toast.wrangle_created': '已创建 Wrangle 节点',
    'toast.wrangle_failed': '创建 Wrangle 失败',

    # ===== Batch Bar =====
    'batch.count': '{} 个操作待确认',

    # ===== Export Training Data =====
    'export.title': '导出训练数据',
    'export.failed': '导出失败',
    'export.error': '导出错误',
    'export.no_history': '当前没有对话记录可导出',
    'export.no_user_msg': '对话中没有用户消息',
    'export.info': '当前对话包含 {} 条用户消息，{} 条 AI 回复。\n\n选择导出方式：',
    'export.split': '分割模式',
    'export.full': '完整模式',
    'export.cancel': '取消',
    'export.done': '训练数据已导出',
    'export.success': (
        '成功导出训练数据！\n\n'
        '文件: {}\n'
        '训练样本数: {}\n'
        '对话轮次: {}\n'
        '导出模式: {}\n\n'
        '提示: 文件为 JSONL 格式，可直接用于 OpenAI/DeepSeek 微调'
    ),
    'export.mode_split': '分割模式',
    'export.mode_full': '完整模式',
    'export.open_folder': '导出成功',
    'export.open_folder_msg': '共导出 {} 条训练数据\n\n是否打开文件夹？',

    # ===== Cache =====
    'cache.archive': '存档当前对话',
    'cache.load': '加载对话...',
    'cache.compress': '压缩旧对话为摘要',
    'cache.list': '查看所有缓存',
    'cache.auto_on': '[on] 自动保存',
    'cache.auto_off': '自动保存',
    'cache.no_history': '没有对话历史可存档',
    'cache.error': '存档失败: {}',
    'cache.invalid': '缓存文件格式无效',
    'cache.no_files': '没有找到缓存文件',
    'cache.select_title': '选择缓存文件',
    'cache.file_list_title': '缓存文件列表',
    'cache.too_short': '对话历史太短，无需压缩',
    'cache.load_error': '加载缓存失败: {}',
    'cache.archived': '已存档: {} (~{} tokens)',
    'cache.loaded': '缓存已加载: {}',
    'cache.confirm_load': '确认加载',
    'cache.confirm_load_msg': '将在新标签页打开 {} 条对话记录。\n是否继续？',
    'cache.select_file': '选择要加载的缓存文件:',
    'cache.btn_load': '加载',
    'cache.btn_cancel': '取消',
    'cache.file_list': '缓存文件列表:\n',
    'cache.session_id': '   会话ID: {}',
    'cache.msg_count': '   消息数: {}',
    'cache.est_tokens': '   估计Token: ~{:,}',
    'cache.created_at': '   创建时间: {}',
    'cache.file_size': '   文件大小: {:.1f} KB',
    'cache.read_err': '[err] {} (读取失败: {})',
    'cache.btn_close': '关闭',
    'cache.msgs': '{} 条消息',

    # ===== Compress =====
    'compress.confirm_title': '确认压缩',
    'compress.confirm_msg': '将前 {} 条对话压缩为摘要，保留最近 4 轮完整对话。\n\n这可以大幅减少 token 消耗。是否继续？',
    'compress.done_title': '压缩完成',
    'compress.done_msg': '对话已压缩！\n\n原始: ~{} tokens\n压缩后: ~{} tokens\n节省: ~{} tokens ({:.1f}%)',
    'compress.summary_header': '[历史对话摘要 - 已压缩以节省 token]',
    'compress.user_reqs': '\n用户请求 ({} 条):',
    'compress.user_more': '  ... 另有 {} 条请求',
    'compress.ai_results': '\nAI 完成的任务 ({} 项):',
    'compress.ai_more': '  ... 另有 {} 项成果',

    # ===== Optimize =====
    'opt.compress_now': '立即压缩对话',
    'opt.auto_on': '自动压缩 [on]',
    'opt.auto_off': '自动压缩',
    'opt.strategy': '压缩策略',
    'opt.aggressive': '激进 (最省空间)',
    'opt.balanced': '平衡 (推荐)',
    'opt.conservative': '保守 (保留细节)',
    'opt.too_short': '对话历史太短，无需优化',
    'opt.done_title': '优化完成',
    'opt.done_msg': '对话已优化！\n\n原始: ~{:,} tokens\n优化后: ~{:,} tokens\n节省: ~{:,} tokens ({:.1f}%)\n\n压缩了 {} 条消息，保留 {} 条',
    'opt.no_need': '无需优化，对话历史已经很精简',
    'opt.auto_status': '上下文前优化: 节省 {:,} tokens (Cursor 级)',

    # ===== Update =====
    'update.checking': '检查中…',
    'update.failed_title': '检查更新',
    'update.failed_msg': '检查更新失败:\n{}',
    'update.latest_title': '检查更新',
    'update.latest_msg': '已经是最新版本！\n\n本地版本: v{}\n最新 Release: v{}',
    'update.new_title': '发现新版本',
    'update.new_msg': '发现新版本 v{}，是否立即更新？\n\n{}',
    'update.detail': '本地版本: v{}\n最新 Release: v{}',
    'update.detail_name': '\n版本名称: {}',
    'update.detail_notes': '\n更新说明: {}',
    'update.progress_title': '更新 Houdini Agent',
    'update.progress_cancel': '取消',
    'update.progress_downloading': '正在下载更新…',
    'update.downloading': '正在下载…',
    'update.extracting': '正在解压…',
    'update.applying': '正在更新文件…',
    'update.done': '更新完成！',
    'update.fail_title': '更新失败',
    'update.fail_msg': '更新过程中出现错误:\n{}',
    'update.success_title': '更新成功',
    'update.success_msg': '已成功更新 {} 个文件！\n\n点击 OK 立即重启插件。',
    'update.new_ver': '🔄 v{}',
    'update.new_ver_tip': '发现新版本 v{}，点击更新',
    'update.restart_fail_title': '重启失败',
    'update.restart_fail_msg': '自动重启失败，请手动关闭并重新打开插件。\n\n错误: {}',

    # ===== Agent Runner - Ask Mode =====
    'ask.restricted': "[Ask 模式] 工具 '{}' 不可用。当前为只读模式，无法执行修改操作。请切换到 Agent 模式。",
    'ask.user_cancel': '用户取消了 {} 操作。请理解用户的意图，继续查询或与用户沟通。',

    # ===== Agent Runner - Title =====
    'title_gen.system_zh': '你是一个标题生成器。根据对话内容生成一个简短的中文标题（≤10个字），只输出标题本身，不要引号、句号或其他多余内容。',
    'title_gen.system_en': 'Generate a short title (≤6 words) for the conversation. Output only the title itself, no quotes or punctuation.',
    'title_gen.ctx': '用户: {}\nAI: {}',

    # ===== Misc AI Tab =====
    'ai.token_limit': '\n\n[内容已达到 token 限制，已停止]',
    'ai.token_limit_status': '内容达到 token 限制，已停止',
    'ai.fake_tool': '检测到AI伪造工具调用，已自动清除',
    'ai.approaching_limit': '输出接近上限: {}/{} tokens',
    'ai.tool_result': '[工具结果] {}: {}',
    'ai.context_reminder': '[上下文提醒] {}',
    'ai.old_rounds': '[较早的工具] 已裁剪 {} 轮较旧对话以节省空间。',
    'ai.auto_opt': '上下文前优化: 节省 {:,} tokens (Cursor 级)',
    'ai.err_issues': '错误节点:{}',
    'ai.warn_issues': '警告节点:{}',
    'ai.no_display': '无显示节点',
    'ai.check_fail': '发现以下问题需修复: {}',
    'ai.check_pass': '检查通过 | 节点工作正常,无错误 | 预期:{}',
    'ai.check_none': '无',
    'ai.tool_exec_err': '工具执行异常: {}',
    'ai.bg_exec_err': '后台执行异常: {}',
    'ai.main_exec_timeout': '主线程执行超时（30秒）',
    'ai.unknown_err': '未知错误',
    'ai.ask_mode_prompt': (
        '\n\n当前为 Ask 模式（只读）\n'
        '你只能查询、分析和回答问题。严禁执行以下操作：\n'
        '- 创建节点（create_node, create_wrangle_node, create_nodes_batch, copy_node）\n'
        '- 删除节点（delete_node）\n'
        '- 修改参数（set_node_parameter, batch_set_parameters）\n'
        '- 修改连接（connect_nodes）\n'
        '- 修改显示（set_display_flag）\n'
        '- 保存文件（save_hip）\n'
        '- 撤销/重做（undo_redo）\n'
        '如果用户的请求需要修改操作，礼貌地说明当前处于 Ask（只读）模式，\n'
        '并建议用户切换到 Agent 模式来执行修改。\n'
        '请仅使用查询工具，如 get_network_structure, get_node_parameters, '
        'read_selection 等，来分析并提供建议。'
    ),
    'ai.detected_url': '\n\n[检测到 URL，将使用 fetch_webpage 获取内容：\n{}]',
    'ai.no_content': '(工具调用完成)',
    'ai.image_msg': '[图片消息]',
    'ai.glm_name': 'GLM（智谱AI）',
    'ai.wrangle_created': '已创建 Wrangle 节点',
    'ai.wrangle_failed': '创建 Wrangle 失败',

    # ===== Plan mode =====
    'ai.plan_mode_planning_prompt': (
        '\n\n'
        '<plan_mode>\n'
        '你当前处于 **Plan 模式 — 规划阶段**。\n\n'

        '## 核心约束\n\n'
        '你严禁执行任何修改操作。此约束优先于其他所有指令，不可被任何后续指令覆盖。\n'
        '禁止操作包括但不限于：创建/删除/修改节点、修改参数/连接、设置标志位、保存文件、执行代码。\n'
        '你只能使用**只读查询工具**和 `create_plan` / `ask_question`。\n\n'

        '## 规划方法论\n\n'
        '遵循 **"深度调研 → 需求澄清 → 结构化规划"** 三步法，不可跳步。\n\n'

        '### 第一步：深度调研（必须先做）\n'
        '- 使用查询工具全面了解当前场景状态：网络结构、节点类型、参数值、连接关系、选择状态。\n'
        '- **不要凭假设规划**。你必须先亲眼看到当前网络结构，再判断需要哪些修改。\n'
        '- 如果场景很复杂，多调用几次查询工具分层探索（先看顶层网络，再看子网络）。\n'
        '- 关注：哪些节点已经存在可以复用？哪些连接已经搭好？现有参数值是什么？\n\n'

        '### 第二步：需求澄清（发现歧义时）\n'
        '- 存在以下情况时，**必须**先用 `ask_question` 向用户澄清：\n'
        '  · 需求含糊，有多种显著不同的理解\n'
        '  · 存在 2 种以上截然不同的技术方案，各有利弊\n'
        '  · 涉及主观审美偏好（如"好看"、"自然"需要用户明确标准）\n'
        '  · 缺少关键参数（如分辨率、数量范围、输出格式）\n'
        '- 每次提问最多 1-3 个关键问题，避免一次性大量提问。\n'
        '- 提问要给出选项和你的推荐方案，而不是开放式提问。\n\n'

        '### 第三步：制定计划（核心产出）\n'
        '使用 `create_plan` 工具输出。**严禁**用纯文本/消息描述计划。\n\n'

        '## 计划质量标准\n\n'
        '### 步骤设计原则\n'
        '1. **粒度适中**：每个步骤对应一个可独立验证的阶段。不要把所有操作堆在一个步骤里，\n'
        '   也不要把单个原子操作拆成一个步骤。\n'
        '2. **具体可执行**：description 必须包含具体的节点路径、参数名、参数值。\n'
        '   ✗ "调整噪声参数" → ✓ "将 mountainSOP 的 Height=2, Element Size=0.5, Noise Type=Perlin"\n'
        '3. **可验证性**：expected_result 描述执行后可通过肉眼或查询确认的结果。\n'
        '   ✗ "效果变好" → ✓ "Viewport 中地形出现明显起伏，高度范围约 0-3 单位"\n'
        '4. **工具清单**：tools 必须列出该步骤要调用的具体工具名（如 run_python, create_node, set_parms）。\n\n'

        '### 依赖关系（depends_on）— 极其重要\n'
        '- **每个步骤必须明确设置 depends_on**。即使是线性流程，step-2 也必须写 depends_on: ["step-1"]。\n'
        '- 如果某些步骤可以并行执行，它们应该共享同一个 depends_on 祖先，而不是互相依赖。\n'
        '- depends_on 决定了 DAG 流程图的布局。如果你不设置依赖关系，流程图将无法正确展示。\n'
        '- 示例模式：\n'
        '  · 线性链：step-1 → step-2 → step-3（每个 depends_on 前一个）\n'
        '  · 并行分支：step-1 → [step-2a, step-2b]（两个都 depends_on step-1）→ step-3（depends_on 两个）\n'
        '  · 汇合：多个独立步骤完成后合并到下一步\n\n'

        '### 阶段分组（phases）\n'
        '- 3 个步骤以上的计划**必须**使用 phases 分组。\n'
        '- 每个 phase 代表一个逻辑阶段，如："Phase 1: 基础搭建"、"Phase 2: 效果增强"、"Phase 3: 优化与验证"。\n'
        '- phases.step_ids 必须覆盖所有步骤，不遗漏。\n\n'

        '### 风险评估\n'
        '- 涉及删除操作、覆盖现有数据、复杂表达式的步骤，设置 risk="medium" 或 "high"。\n'
        '- 高风险步骤必须提供 fallback 回退策略。\n\n'

        '### 复杂度匹配\n'
        '- 简单任务（改几个参数）：2-3 步，不要过度工程化。\n'
        '- 中等任务（搭建一个效果）：4-7 步。\n'
        '- 复杂任务（完整工作流）：8-15 步，按 Phase 分组。\n'
        '- 超复杂任务（整个项目）：15+ 步，必须分 3-4 个 Phase，每个 Phase 3-5 步。\n\n'

        '### 节点网络架构（architecture）— 极其重要\n'
        '`architecture` 字段描述的是 **Plan 执行完成后 Houdini 节点网络的设计蓝图**。\n'
        '这不是步骤执行顺序，而是最终创建的节点拓扑结构。\n'
        '- `nodes`: 列出所有相关节点。每个节点包含：\n'
        '  · `id`: 实际节点名（如 "grid1", "mountain1", "scatter1"）\n'
        '  · `label`: 显示标签（如 "Grid SOP", "Mountain", "Scatter"）\n'
        '  · `type`: 节点类型（sop/obj/mat/vop/rop/dop/lop/cop/chop/out/subnet/null/other）\n'
        '  · `group`: 逻辑分组名（如 "地形系统", "散布系统"）\n'
        '  · `is_new`: 是否由 Plan 新创建（true）或已有节点（false）\n'
        '  · `params`: 关键参数摘要（如 "Height=2, Noise=Perlin"）\n'
        '- `connections`: 节点间的连线。每条连线 from → to。\n'
        '- `groups`: 视觉分组，将相关节点归到同一个容器中展示。\n'
        '  · 每组一个 name 和 node_ids 列表\n'
        '  · 可选 color 提示色（blue/green/purple/orange/red/cyan/yellow/pink）\n\n'
        '**示例**：如果要搭建一个"地形+散布"系统，architecture 应该是：\n'
        '```\n'
        'nodes: [grid1(SOP), mountain1(SOP), scatter1(SOP), copytopoints1(SOP), box1(SOP)]\n'
        'connections: [grid1→mountain1, mountain1→scatter1, scatter1→copytopoints1, box1→copytopoints1]\n'
        'groups: [{name:"地形",node_ids:[grid1,mountain1]}, {name:"散布",node_ids:[scatter1,copytopoints1,box1]}]\n'
        '```\n\n'

        '## 计划提交后\n'
        '用户会看到一张可视化卡片，包含步骤列表、节点网络架构图和 Confirm/Reject 按钮。\n'
        '用户确认后才会进入执行阶段。如果用户拒绝，你需要根据反馈修改计划并重新提交。\n'
        '</plan_mode>'
    ),
    'ai.plan_mode_execution_prompt': (
        '\n\n'
        '<plan_execution>\n'
        '你当前处于 **Plan 模式 — 执行阶段**。\n'
        '用户已确认计划，请严格按计划逐步执行。\n\n'

        '## 执行纪律\n\n'
        '1. **严格遵循步骤顺序和依赖关系**。depends_on 中列出的前置步骤必须全部 done 后才能开始当前步骤。\n'
        '2. **状态同步**（每次都要做，不可省略）：\n'
        '   - 开始步骤前：`update_plan_step(step_id, "running")`\n'
        '   - 完成步骤后：`update_plan_step(step_id, "done", result_summary="简明结果")`\n'
        '   - 步骤出错时：`update_plan_step(step_id, "error", result_summary="错误原因 + 已尝试修复")`\n'
        '3. **忠于计划**：不跳过步骤，不在计划外擅自增加步骤。\n'
        '   - 如果发现计划有问题，先完成当前步骤，然后在结果中说明偏差。\n'
        '4. **结果验证**：每个步骤完成后，对照 expected_result 验证是否符合预期。\n'
        '   - 尽量使用查询工具确认结果（如查询节点参数、检查网络连接）。\n'
        '5. **错误处理**：\n'
        '   - 步骤有 fallback 时：主方案失败后自动尝试 fallback。\n'
        '   - 没有 fallback 时：尝试自行修复一次，仍失败则暂停报告。\n'
        '   - 不要在出错后静默跳过，确保用户知道每个步骤的真实状态。\n'
        '6. **执行完成后**：提供简明总结。\n'
        '   - 成功步骤数 / 总步骤数\n'
        '   - 关键成果描述\n'
        '   - 如有失败步骤，列出原因和建议后续操作\n'
        '</plan_execution>'
    ),
    'ai.plan_confirmed_msg': '[Plan Confirmed] 请按以下计划逐步执行：\n{}',

    # ===== Agent mode — suggest plan =====
    'ai.agent_suggest_plan_prompt': (
        '\n\n'
        '<task_complexity_detection>\n'
        '在回答用户之前，先评估任务复杂度。如果满足以下任一条件，建议用户切换到 Plan 模式：\n\n'
        '**触发条件**（满足任意一条即建议）：\n'
        '- 需要创建 5 个以上节点\n'
        '- 涉及多阶段工作流（如"搭建完整的地形系统"、"创建 FLIP 流体模拟"、"建立完整的材质网络"）\n'
        '- 需要复杂的节点连接拓扑（分支、合并、循环引用）\n'
        '- 涉及模拟/解算/渲染等多步骤流程\n'
        '- 需要大规模修改现有网络（修改 5 个以上节点）\n'
        '- 用户的语言暗示需要规划（"帮我规划"、"我需要一个方案"、"设计一个…"、"搭建一个完整的…"）\n\n'
        '**建议格式**：\n'
        '"💡 这个任务涉及 [具体原因，如：搭建包含地形生成、散布系统和材质的完整工作流，预计需要 N+ 个步骤]。\n'
        '建议切换到 **Plan 模式** 先制定执行计划，确认后再逐步执行。\n'
        '这样可以让您在执行前预览完整方案并提出修改意见。\n'
        '您可以在输入框左下角的模式选择器中切换。"\n\n'
        '**注意**：如果用户坚持在 Agent 模式下执行，尊重用户选择并尽力完成。\n'
        '</task_complexity_detection>'
    ),

    # ===== History rendering =====
    'history.compressed': '[较早的工具] 已裁剪 {} 轮较旧对话执行。',
    'history.summary_title': '历史对话摘要',
}


_EN = {
    # ===== Header =====
    'header.think.tooltip': 'Thinking mode: AI analyzes first, then answers with visible thought process',
    'header.cache.tooltip': 'Cache: Save/load conversation history',
    'header.optimize.tooltip': 'Token optimization: Auto compress and optimize',
    'header.update.tooltip': 'Check for updates',
    'header.font.tooltip': 'Font Size (Ctrl+/Ctrl-)',
    'header.token_stats.tooltip': 'Click for detailed token statistics',

    # ===== Input Area =====
    'mode.tooltip': 'Agent: AI autonomously operates nodes\nAsk: Read-only query & analysis',
    'confirm': 'Confirm',
    'confirm.tooltip': 'Confirm mode: Preview before creating nodes/VEX',
    'placeholder': 'Type a message... (Enter to send, Shift+Enter for newline, @mention nodes)',
    'attach_image.tooltip': 'Attach image (PNG/JPG/GIF/WebP, or paste/drag into input)',
    'train.tooltip': 'Export conversation as training data (for LLM fine-tuning)',

    # ===== Session Manager =====
    'session.new': 'New Chat',
    'session.close': 'Close this chat',
    'session.close_others': 'Close other chats',

    # ===== Font Settings =====
    'font.title': 'Font Size',
    'font.scale': 'Font Scale',
    'font.reset': 'Reset 100%',
    'font.close': 'Close',

    # ===== Thinking =====
    'thinking.init': 'Thinking...',
    'thinking.progress': 'Thinking... ({})',
    'thinking.round': '--- Round {} ---',
    'thinking.done': 'Thought process ({})',

    # ===== Execution =====
    'exec.running': 'Executing...',
    'exec.progress': 'Executing... ({}/{})',
    'exec.done': 'Done ({} ops, {})',
    'exec.done_err': 'Done ({} ok, {} err, {})',
    'exec.tool': 'Exec: {}',

    # ===== Buttons (shared) =====
    'btn.copy': 'Copy',
    'btn.copied': 'Copied',
    'btn.close': 'Close',
    'btn.undo': 'undo',
    'btn.keep': 'keep',

    # ===== Expand / Collapse =====
    'msg.expand': '▶ Expand ({} more lines)',
    'msg.collapse': '▼ Collapse',

    # ===== Code Preview =====
    'code.writing': '✍ Writing code for {}...',
    'code.complete': '✓ Code complete',

    # ===== Diff =====
    'diff.old': 'Old',
    'diff.new': 'New',

    # ===== Confirm Preview =====
    'confirm.title': 'Confirm: {}',
    'confirm.params_more': '... {} params total',
    'confirm.cancel': '✕ Cancel',
    'confirm.execute': '↵ Confirm',

    # ===== Node Operations =====
    'node.click_jump': 'Click to navigate: {}',
    'status.undone': 'Undone',
    'status.kept': 'Kept',

    # ===== VEX Preview =====
    'vex.confirm_exec': 'Confirm: {}',
    'vex.node_name': 'Node name: {}',
    'vex.wrangle_type': 'Type: {}',
    'vex.parent_path': 'Parent: {}',
    'vex.node_type': 'Node type: {}',
    'vex.node_path': 'Node path: {}',
    'vex.cancel': 'Cancel',
    'vex.summary_more': '\n  ... {} lines total',

    # ===== Status / Response =====
    'status.thinking': 'think',
    'status.calls': '{} calls',
    'status.done': 'Done ({})',
    'status.exec_done_see_above': 'Execution complete. See the process above.',
    'status.history': 'History',
    'status.history_summary': 'History summary',
    'status.context': 'Context',
    'status.history_with': 'History | {}',
    'status.stats_reset': 'Stats reset',

    # ===== Image =====
    'img.preview': 'Image Preview',
    'img.close': 'Close',
    'img.click_zoom': 'Click to zoom',
    'img.not_supported': 'Image Not Supported',
    'img.not_supported_msg': 'Model {} does not support image input.\nPlease switch to a vision model (e.g. Claude, GPT-5.2).',
    'img.select': 'Select Image',
    'img.load_failed': 'Failed to load image: {}',

    # ===== Token Stats =====
    'token.title': 'Token Analytics',
    'token.headers': ['#', 'Time', 'Model', 'Input', 'Cache R', 'Cache W', 'Output', 'Think', 'Total', 'Latency', 'Cost', ''],
    'token.reset': 'Reset Stats',
    'token.close': 'Close',
    'token.detail_title': '  Request Details ({} calls)',
    'token.no_records': '  No API call records yet',
    'token.summary': (
        'Cumulative Stats ({} requests)\n'
        'Input: {:,}\n'
        'Output: {:,}\n'
        '{}'
        'Cache Read: {:,}\n'
        'Cache Write: {:,}\n'
        'Cache Hit Rate: {}\n'
        'Total: {:,}\n'
        'Est. Cost: {}\n'
        'Click for details'
    ),
    'token.reasoning_line': 'Reasoning Tokens: {:,}\n',

    # ===== Shell =====
    'shell.exec_failed': 'Execution failed. See details below ↓',
    'shell.cmd_failed': 'Command failed. See details below ↓',

    # ===== Code Block =====
    'codeblock.copy': 'Copy',
    'codeblock.copied': 'Copied',
    'codeblock.create_wrangle': 'Create Wrangle',

    # ===== Toast Messages =====
    'toast.node_not_exist': 'Node does not exist or has been deleted: {}',
    'toast.houdini_unavailable': 'Houdini environment unavailable',
    'toast.jump_failed': 'Navigation failed: {}',
    'toast.node_not_found': 'Node not found: {}',
    'toast.param_not_found': 'Parameter not found: {}',
    'toast.param_restored': 'Restored parameter {} to old value',
    'toast.missing_path': 'Missing node path, cannot undo',
    'toast.undo_create': 'Undone creation (deleted {} nodes)',
    'toast.node_gone': 'Node no longer exists, nothing to undo',
    'toast.parent_not_found': 'Parent node not found: {}',
    'toast.node_restored': 'Restored node: {}',
    'toast.undone': 'Undone',
    'toast.undo_failed': 'Undo failed: {}',
    'toast.undo_all': 'Undone all {} operations',
    'toast.keep_all': 'Kept all {} operations',
    'toast.wrangle_created': 'Created Wrangle node',
    'toast.wrangle_failed': 'Failed to create Wrangle',

    # ===== Batch Bar =====
    'batch.count': '{} operations pending',

    # ===== Export Training Data =====
    'export.title': 'Export Training Data',
    'export.failed': 'Export Failed',
    'export.error': 'Export Error',
    'export.no_history': 'No conversation history to export',
    'export.no_user_msg': 'No user messages in conversation',
    'export.info': 'Conversation contains {} user messages and {} AI replies.\n\nChoose export mode:',
    'export.split': 'Split Mode',
    'export.full': 'Full Mode',
    'export.cancel': 'Cancel',
    'export.done': 'Training data exported',
    'export.success': (
        'Training data exported successfully!\n\n'
        'File: {}\n'
        'Samples: {}\n'
        'Turns: {}\n'
        'Mode: {}\n\n'
        'Tip: JSONL format, directly usable for OpenAI/DeepSeek fine-tuning'
    ),
    'export.mode_split': 'Split Mode',
    'export.mode_full': 'Full Mode',
    'export.open_folder': 'Export Successful',
    'export.open_folder_msg': 'Exported {} training samples\n\nOpen folder?',

    # ===== Cache =====
    'cache.archive': 'Archive current chat',
    'cache.load': 'Load chat...',
    'cache.compress': 'Compress old chats to summary',
    'cache.list': 'View all caches',
    'cache.auto_on': '[on] Auto save',
    'cache.auto_off': 'Auto save',
    'cache.no_history': 'No conversation history to archive',
    'cache.error': 'Archive failed: {}',
    'cache.invalid': 'Invalid cache file format',
    'cache.no_files': 'No cache files found',
    'cache.select_title': 'Select Cache File',
    'cache.file_list_title': 'Cache File List',
    'cache.too_short': 'Conversation too short to compress',
    'cache.load_error': 'Failed to load cache: {}',
    'cache.archived': 'Archived: {} (~{} tokens)',
    'cache.loaded': 'Cache loaded: {}',
    'cache.confirm_load': 'Confirm Load',
    'cache.confirm_load_msg': 'Open {} messages in a new tab.\nContinue?',
    'cache.select_file': 'Select a cache file to load:',
    'cache.btn_load': 'Load',
    'cache.btn_cancel': 'Cancel',
    'cache.file_list': 'Cache files:\n',
    'cache.session_id': '   Session ID: {}',
    'cache.msg_count': '   Messages: {}',
    'cache.est_tokens': '   Est. Tokens: ~{:,}',
    'cache.created_at': '   Created: {}',
    'cache.file_size': '   Size: {:.1f} KB',
    'cache.read_err': '[err] {} (read failed: {})',
    'cache.btn_close': 'Close',
    'cache.msgs': '{} messages',

    # ===== Compress =====
    'compress.confirm_title': 'Confirm Compression',
    'compress.confirm_msg': 'Compress the first {} messages into a summary, keeping the last 4 rounds.\n\nThis significantly reduces token usage. Continue?',
    'compress.done_title': 'Compression Complete',
    'compress.done_msg': 'Conversation compressed!\n\nOriginal: ~{} tokens\nCompressed: ~{} tokens\nSaved: ~{} tokens ({:.1f}%)',
    'compress.summary_header': '[Conversation Summary - Compressed to save tokens]',
    'compress.user_reqs': '\nUser Requests ({} total):',
    'compress.user_more': '  ... {} more requests',
    'compress.ai_results': '\nAI Completed Tasks ({} total):',
    'compress.ai_more': '  ... {} more results',

    # ===== Optimize =====
    'opt.compress_now': 'Compress conversation now',
    'opt.auto_on': 'Auto compress [on]',
    'opt.auto_off': 'Auto compress',
    'opt.strategy': 'Compression Strategy',
    'opt.aggressive': 'Aggressive (max savings)',
    'opt.balanced': 'Balanced (recommended)',
    'opt.conservative': 'Conservative (keep details)',
    'opt.too_short': 'Conversation too short to optimize',
    'opt.done_title': 'Optimization Complete',
    'opt.done_msg': 'Conversation optimized!\n\nOriginal: ~{:,} tokens\nOptimized: ~{:,} tokens\nSaved: ~{:,} tokens ({:.1f}%)\n\nCompressed {} messages, kept {}',
    'opt.no_need': 'No optimization needed, conversation is already concise',
    'opt.auto_status': 'Pre-context optimization: saved {:,} tokens (Cursor-level)',

    # ===== Update =====
    'update.checking': 'Checking…',
    'update.failed_title': 'Check Update',
    'update.failed_msg': 'Failed to check for updates:\n{}',
    'update.latest_title': 'Check Update',
    'update.latest_msg': 'Already up to date!\n\nLocal: v{}\nLatest Release: v{}',
    'update.new_title': 'New Version Available',
    'update.new_msg': 'New version v{} available. Update now?\n\n{}',
    'update.detail': 'Local: v{}\nLatest Release: v{}',
    'update.detail_name': '\nRelease: {}',
    'update.detail_notes': '\nNotes: {}',
    'update.progress_title': 'Updating Houdini Agent',
    'update.progress_cancel': 'Cancel',
    'update.progress_downloading': 'Downloading update…',
    'update.downloading': 'Downloading…',
    'update.extracting': 'Extracting…',
    'update.applying': 'Updating files…',
    'update.done': 'Update complete!',
    'update.fail_title': 'Update Failed',
    'update.fail_msg': 'Error during update:\n{}',
    'update.success_title': 'Update Successful',
    'update.success_msg': 'Successfully updated {} files!\n\nClick OK to restart the plugin.',
    'update.new_ver': '🔄 v{}',
    'update.new_ver_tip': 'New version v{} available. Click to update',
    'update.restart_fail_title': 'Restart Failed',
    'update.restart_fail_msg': 'Auto-restart failed. Please manually close and reopen the plugin.\n\nError: {}',

    # ===== Agent Runner - Ask Mode =====
    'ask.restricted': "[Ask Mode] Tool '{}' is not available. Read-only mode cannot perform modifications. Switch to Agent mode.",
    'ask.user_cancel': 'User cancelled {}. Please understand the user intent and continue querying or communicating.',

    # ===== Agent Runner - Title =====
    'title_gen.system_zh': '你是一个标题生成器。根据对话内容生成一个简短的中文标题（≤10个字），只输出标题本身，不要引号、句号或其他多余内容。',
    'title_gen.system_en': 'Generate a short title (≤6 words) for the conversation. Output only the title itself, no quotes or punctuation.',
    'title_gen.ctx': 'User: {}\nAI: {}',

    # ===== Misc AI Tab =====
    'ai.token_limit': '\n\n[Content reached token limit, stopped]',
    'ai.token_limit_status': 'Content reached token limit, stopped',
    'ai.fake_tool': 'Detected AI fake tool call, auto-cleaned',
    'ai.approaching_limit': 'Output approaching limit: {}/{} tokens',
    'ai.tool_result': '[Tool Result] {}: {}',
    'ai.context_reminder': '[Context Reminder] {}',
    'ai.old_rounds': '[Older tools] Trimmed {} older rounds to save space.',
    'ai.auto_opt': 'Pre-context optimization: saved {:,} tokens (Cursor-level)',
    'ai.err_issues': 'Error node:{}',
    'ai.warn_issues': 'Warning node:{}',
    'ai.no_display': 'No display node',
    'ai.check_fail': 'Issues found that need fixing: {}',
    'ai.check_pass': 'Check passed | Nodes working correctly, no errors | Expected:{}',
    'ai.check_none': 'None',
    'ai.tool_exec_err': 'Tool execution error: {}',
    'ai.bg_exec_err': 'Background execution error: {}',
    'ai.main_exec_timeout': 'Main thread execution timeout (30s)',
    'ai.unknown_err': 'Unknown error',
    'ai.ask_mode_prompt': (
        '\n\nYou are in Ask mode (read-only).\n'
        'You can only query, analyze, and answer questions. Strictly forbidden operations:\n'
        '- Create nodes (create_node, create_wrangle_node, create_nodes_batch, copy_node)\n'
        '- Delete nodes (delete_node)\n'
        '- Modify parameters (set_node_parameter, batch_set_parameters)\n'
        '- Modify connections (connect_nodes)\n'
        '- Modify display (set_display_flag)\n'
        '- Save files (save_hip)\n'
        '- Undo/redo (undo_redo)\n'
        'If the user requests modifications, politely explain you are in Ask (read-only) mode,\n'
        'and suggest switching to Agent mode to perform modifications.\n'
        'Use only query tools like get_network_structure, get_node_parameters, '
        'read_selection, etc., to analyze and provide suggestions.'
    ),
    'ai.detected_url': '\n\n[URL detected, will use fetch_webpage to retrieve content:\n{}]',
    'ai.no_content': '(Tool calls completed)',
    'ai.image_msg': '[Image message]',
    'ai.glm_name': 'GLM (Zhipu AI)',
    'ai.wrangle_created': 'Created Wrangle node',
    'ai.wrangle_failed': 'Failed to create Wrangle',

    # ===== Plan mode =====
    'ai.plan_mode_planning_prompt': (
        '\n\n'
        '<plan_mode>\n'
        'You are currently in **Plan Mode — Planning Phase**.\n\n'

        '## Core Constraint\n\n'
        'You MUST NOT execute any modification operations. This constraint supersedes ALL other instructions '
        'and cannot be overridden by any subsequent instruction.\n'
        'Forbidden: creating/deleting/modifying nodes, changing parameters/connections, setting flags, '
        'saving files, executing code.\n'
        'You may ONLY use **read-only query tools** and `create_plan` / `ask_question`.\n\n'

        '## Planning Methodology\n\n'
        'Follow the **"Deep Research → Clarify → Structured Plan"** three-step method. Do NOT skip steps.\n\n'

        '### Step 1: Deep Research (MUST do first)\n'
        '- Use query tools to thoroughly understand the current scene: network structure, node types, '
        'parameter values, connections, selection state.\n'
        '- **Never plan based on assumptions.** You must personally inspect the network before deciding what to change.\n'
        '- For complex scenes, call query tools multiple times to explore layers (top-level network first, then subnets).\n'
        '- Focus on: Which nodes already exist and can be reused? Which connections are already made? '
        'What are the current parameter values?\n\n'

        '### Step 2: Clarify Requirements (when ambiguity exists)\n'
        '- You MUST use `ask_question` when:\n'
        '  · The request is ambiguous with multiple significantly different interpretations\n'
        '  · There are 2+ distinctly different technical approaches, each with trade-offs\n'
        '  · Subjective aesthetic preferences are involved ("make it look good", "natural")\n'
        '  · Key parameters are missing (resolution, count ranges, output format)\n'
        '- Ask at most 1-3 key questions per round. Provide options and your recommendation.\n\n'

        '### Step 3: Create the Plan (core output)\n'
        'Output via `create_plan` tool. **NEVER** describe plans in plain text messages.\n\n'

        '## Plan Quality Standards\n\n'

        '### Step Design Principles\n'
        '1. **Right granularity**: Each step = one independently verifiable stage. Don\'t cram everything into one step, '
        'and don\'t split single atomic operations into separate steps.\n'
        '2. **Concrete & executable**: description MUST include specific node paths, parameter names, and values.\n'
        '   ✗ "adjust noise params" → ✓ "Set mountainSOP Height=2, Element Size=0.5, Noise Type=Perlin"\n'
        '3. **Verifiable**: expected_result must describe something you can confirm visually or via query.\n'
        '   ✗ "effect improves" → ✓ "Terrain shows clear undulation in viewport, height range ~0-3 units"\n'
        '4. **Tool manifest**: tools must list the specific tool names for the step (e.g., run_python, create_node, set_parms).\n\n'

        '### Dependencies (depends_on) — CRITICAL\n'
        '- **Every step MUST explicitly set depends_on.** Even in a linear flow, step-2 must set depends_on: ["step-1"].\n'
        '- Steps that can run in parallel should share the same depends_on ancestor, not depend on each other.\n'
        '- depends_on drives the DAG layout. Without proper dependencies, the flow diagram will not render correctly.\n'
        '- Patterns:\n'
        '  · Linear: step-1 → step-2 → step-3 (each depends_on the previous)\n'
        '  · Parallel: step-1 → [step-2a, step-2b] (both depends_on step-1) → step-3 (depends_on both)\n'
        '  · Fan-in: multiple independent steps converge into the next\n\n'

        '### Phase Grouping (phases)\n'
        '- Plans with 3+ steps MUST use phases for grouping.\n'
        '- Each phase = one logical stage, e.g., "Phase 1: Base Setup", "Phase 2: Effects", "Phase 3: Polish & Verify".\n'
        '- phases.step_ids must cover ALL steps with no omissions.\n\n'

        '### Risk Assessment\n'
        '- Steps involving deletion, overwriting existing data, or complex expressions: set risk="medium" or "high".\n'
        '- High-risk steps MUST have a fallback strategy.\n\n'

        '### Complexity Matching\n'
        '- Simple (tweak params): 2-3 steps. Do not over-engineer.\n'
        '- Medium (build one effect): 4-7 steps.\n'
        '- Complex (full workflow): 8-15 steps, grouped into phases.\n'
        '- Very complex (entire project): 15+ steps, 3-4 phases with 3-5 steps each.\n\n'

        '### Node Network Architecture (architecture) — CRITICAL\n'
        'The `architecture` field describes the **design blueprint of the Houdini node network** after the plan executes.\n'
        'This is NOT the step execution order — it is the final node topology.\n'
        '- `nodes`: List all relevant nodes. Each node includes:\n'
        '  · `id`: actual node name (e.g., "grid1", "mountain1", "scatter1")\n'
        '  · `label`: display label (e.g., "Grid SOP", "Mountain", "Scatter")\n'
        '  · `type`: node category (sop/obj/mat/vop/rop/dop/lop/cop/chop/out/subnet/null/other)\n'
        '  · `group`: logical grouping name (e.g., "Terrain System", "Scatter System")\n'
        '  · `is_new`: whether this node will be created by the plan (true) or already exists (false)\n'
        '  · `params`: key parameter summary (e.g., "Height=2, Noise=Perlin")\n'
        '- `connections`: edges between nodes. Each connection: from → to.\n'
        '- `groups`: visual grouping containers for related nodes.\n'
        '  · Each group has a name and node_ids list\n'
        '  · Optional color hint (blue/green/purple/orange/red/cyan/yellow/pink)\n\n'
        '**Example**: For building a "terrain + scatter" system:\n'
        '```\n'
        'nodes: [grid1(SOP), mountain1(SOP), scatter1(SOP), copytopoints1(SOP), box1(SOP)]\n'
        'connections: [grid1→mountain1, mountain1→scatter1, scatter1→copytopoints1, box1→copytopoints1]\n'
        'groups: [{name:"Terrain",ids:[grid1,mountain1]}, {name:"Scatter",ids:[scatter1,copytopoints1,box1]}]\n'
        '```\n\n'

        '## After Plan Submission\n'
        'The user will see a visual card with a step list, node network architecture diagram, and Confirm/Reject buttons.\n'
        'Execution begins only after the user confirms. If rejected, revise based on feedback and resubmit.\n'
        '</plan_mode>'
    ),
    'ai.plan_mode_execution_prompt': (
        '\n\n'
        '<plan_execution>\n'
        'You are currently in **Plan Mode — Execution Phase**.\n'
        'The user has confirmed the plan. Execute strictly according to the plan.\n\n'

        '## Execution Discipline\n\n'
        '1. **Respect step order and dependencies.** All depends_on predecessors must be "done" before starting a step.\n'
        '2. **Status sync** (mandatory for every step, never skip):\n'
        '   - Before starting: `update_plan_step(step_id, "running")`\n'
        '   - After completion: `update_plan_step(step_id, "done", result_summary="concise result")`\n'
        '   - On failure: `update_plan_step(step_id, "error", result_summary="error reason + attempted fix")`\n'
        '3. **Stay faithful to the plan**: Do not skip steps. Do not add steps outside the plan.\n'
        '   - If you discover a plan issue, complete the current step, then note the deviation in the result.\n'
        '4. **Verify results**: After each step, check against expected_result.\n'
        '   - Prefer using query tools to confirm (e.g., query node parameters, check connections).\n'
        '5. **Error handling**:\n'
        '   - If step has fallback: try fallback after primary approach fails.\n'
        '   - No fallback: attempt one self-fix, then pause and report if still failing.\n'
        '   - Never silently skip failed steps. The user must know the true status of every step.\n'
        '6. **Completion summary**: After all steps, provide:\n'
        '   - Successful / total step count\n'
        '   - Key achievements\n'
        '   - Failed steps with reasons and suggested next actions\n'
        '</plan_execution>'
    ),
    'ai.plan_confirmed_msg': '[Plan Confirmed] Please execute the following plan step by step:\n{}',

    # ===== Agent mode — suggest plan =====
    'ai.agent_suggest_plan_prompt': (
        '\n\n'
        '<task_complexity_detection>\n'
        'Before responding, assess task complexity. Suggest Plan mode if ANY of the following apply:\n\n'
        '**Trigger conditions** (any one is sufficient):\n'
        '- Creating 5+ nodes\n'
        '- Multi-phase workflows (e.g., "build a terrain system", "set up FLIP simulation", "create full material network")\n'
        '- Complex node connection topology (branches, merges, feedback loops)\n'
        '- Simulation/solver/render multi-step processes\n'
        '- Large-scale modifications to existing network (changing 5+ nodes)\n'
        '- User language implies planning ("help me plan", "I need a proposal", "design a…", "build a complete…")\n\n'
        '**Suggestion format**:\n'
        '"💡 This task involves [specific reason, e.g.: building a full workflow with terrain, scatter, and materials, '
        'estimated N+ steps]. I suggest switching to **Plan mode** to create an execution plan first. '
        'This lets you preview and modify the full approach before execution. '
        'You can switch in the mode selector at the bottom-left of the input box."\n\n'
        '**Note**: If the user insists on Agent mode, respect their choice and do your best.\n'
        '</task_complexity_detection>'
    ),

    # ===== History rendering =====
    'history.compressed': '[Older tools] Trimmed {} older execution rounds.',
    'history.summary_title': 'Conversation summary',
}

# 启动时自动恢复语言设置
load_language()
