# -*- coding: utf-8 -*-
"""
Meshy 动画库目录的本地检索（纯本地、零网络、零 credits）。

meshy_search_animations 用它做"语义初筛"：把用户的自然语言意图（中/英）匹配到
动作库里的若干候选 {id, name, category}，交给 Agent + 用户挑选，再用 meshy_animate
按 action_id 生成。

匹配策略（无需向量模型）：关键词/子串打分 + 一张中英意图同义词表。命中不了也不报错，
退回返回热门基础动作，避免空结果卡住流程。
"""

import json
import os

_DATA_PATH = os.path.join(os.path.dirname(__file__), "animation_library.json")

_CACHE = None


def _load():
    global _CACHE
    if _CACHE is None:
        try:
            with open(_DATA_PATH, "r", encoding="utf-8") as f:
                _CACHE = json.load(f)
        except Exception:
            _CACHE = {"actions": [], "id_range": [0, 696], "verified_max_id": 0}
    return _CACHE


def all_actions():
    return list(_load().get("actions", []))


def id_range():
    r = _load().get("id_range", [0, 696])
    return int(r[0]), int(r[1])


def get_by_id(action_id):
    aid = int(action_id)
    for a in _load().get("actions", []):
        if a.get("id") == aid:
            return dict(a)
    return None


# 中/英意图 -> 用于匹配动作名的关键词。命中后给对应动作加权。
_SYNONYMS = {
    "走": ["walk"], "走路": ["walk"], "行走": ["walk"], "散步": ["walk", "casual"],
    "跑": ["run", "sprint"], "奔跑": ["run", "sprint"], "冲刺": ["sprint", "charge", "run"],
    "跳": ["jump", "leap", "hop"], "跳跃": ["jump", "leap"], "跳起": ["jump"],
    "跳舞": ["dance", "groove"], "舞蹈": ["dance", "groove"], "蹦迪": ["dance", "groove"],
    "挥手": ["wave"], "打招呼": ["wave", "hello", "greet"], "招手": ["wave"],
    "坐": ["sit"], "坐下": ["sit"], "起立": ["stand", "arise", "stand_up"], "站立": ["idle", "stand"],
    "站": ["idle", "stand"], "待机": ["idle"], "发呆": ["idle", "look_around"],
    "攻击": ["attack", "punch", "slash", "kick", "combo"], "打": ["punch", "attack", "fight"],
    "打架": ["fight", "punch", "combo"], "战斗": ["combat", "fight", "attack"],
    "拳": ["punch", "jab", "hook", "uppercut"], "踢": ["kick"], "剑": ["sword", "slash", "parry"],
    "格挡": ["block", "parry", "dodge"], "闪避": ["dodge", "roll"],
    "射箭": ["archery", "bow", "shoot"], "开枪": ["shoot", "gun", "reload"], "换弹": ["reload"],
    "施法": ["spell", "mage", "cast"], "魔法": ["spell", "mage", "cast"],
    "死": ["dead", "dying", "fall_dead", "knock_down"], "死亡": ["dead", "dying"], "倒下": ["fall", "knock_down"],
    "受击": ["hit_reaction", "behit"], "中弹": ["shot", "gunshot"], "受伤": ["injured", "hit"],
    "爬": ["climb", "ladder"], "攀爬": ["climb"], "翻越": ["vault", "parkour"], "跑酷": ["parkour", "vault"],
    "游泳": ["swim"], "游": ["swim"], "潜水": ["dive"],
    "睡": ["sleep", "doze", "lie"], "睡觉": ["sleep"], "躺": ["lie", "lying"],
    "鼓掌": ["clap"], "欢呼": ["cheer", "victory"], "胜利": ["victory", "cheer"],
    "鞠躬": ["bow"], "打电话": ["phone", "call"], "说话": ["talk", "discuss", "chat"], "交谈": ["talk", "chat"],
    "拿": ["pick_up", "pick", "collect"], "捡": ["pick_up", "pick"], "推": ["push"],
    "开门": ["open_door"], "健身": ["squat", "push_up", "crunch", "curl", "jumping_jacks", "burpee"],
    "俯卧撑": ["push_up"], "深蹲": ["squat"], "仰卧起坐": ["situps", "crunch"],
    "转身": ["turn"], "侧移": ["strafe"], "后退": ["backward", "back"], "蹲": ["crouch", "squat"],
    "潜行": ["sneaky", "cautious"], "瘸": ["limping", "injured"], "拄拐": ["walker"],
    "翻滚": ["roll"], "后空翻": ["backflip"], "倒立": ["handstand"],
}

_CATEGORY_HINTS = {
    "dance": "Dancing", "dancing": "Dancing", "舞": "Dancing",
    "fight": "Fighting", "combat": "Fighting", "战斗": "Fighting", "打斗": "Fighting",
    "walk": "WalkAndRun", "run": "WalkAndRun", "移动": "WalkAndRun",
    "daily": "DailyActions", "日常": "DailyActions",
    "body": "BodyMovements", "技巧": "BodyMovements", "动作": "BodyMovements",
}


def _expand_query(query):
    """把查询拆成小写关键词集合，并按同义词表展开。返回 (keywords, category_filter)。"""
    q = (query or "").lower().strip()
    keywords = set()
    # 英文/拼音词：按非字母数字切分
    cur = ""
    for ch in q:
        if ch.isalnum():
            cur += ch
        else:
            if len(cur) >= 2:
                keywords.add(cur)
            cur = ""
    if len(cur) >= 2:
        keywords.add(cur)
    # 中文同义词：子串命中即展开
    for zh, ens in _SYNONYMS.items():
        if zh in q:
            keywords.update(ens)
    # 英文同义词的 key 也可能直接出现
    for en_word in list(keywords):
        if en_word in _SYNONYMS:
            keywords.update(_SYNONYMS[en_word])
    cat_filter = None
    for hint, cat in _CATEGORY_HINTS.items():
        if hint in q:
            cat_filter = cat
            break
    return keywords, cat_filter


def _name_tokens(name):
    return set(t for t in name.lower().replace("-", "_").split("_") if t)


def search(query, limit=12):
    """按自然语言/关键词检索动作库，返回打分最高的若干 {id, name, category, score}。
    查询为空时返回一组常用基础动作。"""
    actions = _load().get("actions", [])
    if not actions:
        return []
    keywords, cat_filter = _expand_query(query)

    if not keywords and not cat_filter:
        # 空查询：给一组通用基础动作作为起点
        common = ["Idle", "Casual_Walk", "RunFast", "Regular_Jump", "Wave_One_Hand",
                  "Big_Wave_Hello", "Victory_Cheer", "FunnyDancing_01", "Combat_Stance",
                  "Sit_Cross_Legged", "Clapping_Run", "Formal_Bow"]
        out = []
        for nm in common:
            for a in actions:
                if a["name"] == nm:
                    out.append(dict(a, score=1))
                    break
        return out[:limit]

    scored = []
    for a in actions:
        name = a.get("name", "")
        toks = _name_tokens(name)
        nlow = name.lower()
        score = 0
        for kw in keywords:
            if kw in toks:
                score += 3                 # 整词命中权重最高
            elif kw in nlow:
                score += 2                 # 子串命中
        if cat_filter and a.get("category") == cat_filter:
            score += 1                     # 类别契合小幅加权
        if score > 0:
            scored.append(dict(a, score=score))

    scored.sort(key=lambda x: (-x["score"], x["id"]))
    if not scored and cat_filter:
        # 关键词没中但有类别意图：返回该类别前若干个
        scored = [dict(a, score=1) for a in actions if a.get("category") == cat_filter]
    return scored[:limit]


def resolve(items):
    """把一组 action 标识（int id 或 字符串名）解析成 [{id, name, category}]。
    名字大小写/连字符不敏感；不在目录里的纯数字 id 也透传（0–696 都是合法 action_id）。
    返回 (resolved, unknown)：unknown 是无法解析的原始项。"""
    actions = _load().get("actions", [])
    by_id = {a["id"]: a for a in actions}
    by_name = {a["name"].lower().replace("-", "_"): a for a in actions}
    lo, hi = id_range()

    resolved, unknown = [], []
    for it in (items or []):
        a = None
        # 数字 id
        try:
            aid = int(it)
            if aid in by_id:
                a = by_id[aid]
            elif lo <= aid <= hi:
                a = {"id": aid, "name": "action_%d" % aid, "category": ""}
        except (ValueError, TypeError):
            key = str(it).strip().lower().replace("-", "_").replace(" ", "_")
            a = by_name.get(key)
        if a is not None:
            resolved.append(dict(a))
        else:
            unknown.append(it)
    # 去重（按 id），保持顺序
    seen, uniq = set(), []
    for a in resolved:
        if a["id"] not in seen:
            seen.add(a["id"])
            uniq.append(a)
    return uniq, unknown
