"""麦麦不要再看那个了！ — 核心逻辑（纯 Python，不依赖 MaiBot SDK，便于单元测试）。

职责：
- 北京时间（UTC+8）每日时间段 / 每周时间解析与匹配（参考 cateye_model_switcher 峰谷时段判断）；
- 群 / 用户黑名单（仅黑名单，用于过滤信息）匹配；
- 入站消息分类（戳一戳 / 表情包 / 合并转发 / 图片 / 文字）；
- 根据各消息类型开关（开启=不拦截，关闭=拦截）决定消息是否被拦截；
- 关键词屏蔽：对**纯文本消息**（不含图片/表情包/合并转发）命中配置关键词即拦截，
  受 ``allow_keyword`` 总开关控制，**不受其它类型开关（如 allow_text）影响**；
- 过滤配置归档 JSON 的构造与归档文件名校验。

约定：
- 时段格式 "HH:MM-HH:MM"，支持跨天（如 "22:00-02:00"）：开始时间 < 结束时间视为同一
  自然日区间，否则视为跨天区间，**以时段开始时间归属当日**（如「周一 22:00-02:00」
  覆盖周一 22:00 至周二 02:00，凌晨部分按前一天即周一的星期判定）；
- 星期：1=周一 ... 7=周日；filter_weekdays 为空 = 每天；
- filter_periods 为空 = 全天；
- 黑名单为交集过滤：配置了哪些维度就必须同时命中哪些维度（如同时配置群+具体用户，
  则只有该群内该用户的消息才被过滤）；某维度留空 = 不限制该维度；
  用户黑名单混填 ``all`` 与具体用户 ID 时取**并集**：``all``（需群维度命中）或
  具体用户 ID 命中，任一即视为用户维度命中（具体用户不会被 ``all`` 静默忽略）；
- 消息分类为并集：消息只要包含任一被关闭（拦截）的类型，即被整体拦截；
  只有包含的所有类型均为开启（放行）时才入库入站；未识别的消息类型（语音/视频/文件/其它通知等）一律放行；
- 关键词屏蔽只匹配纯文本消息（含文本段、且不含图片/表情包/合并转发段），
  命中任一关键词即拦截：独立于类型开关（allow_text 关掉只是整体拦截全部文字消息，
  与关键词列表无关），仅受 allow_keyword 总开关控制。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

# 北京时间
TZ = timezone(timedelta(hours=8))

# 消息类型常量（分类键）
CAT_POKE = "poke"        # 戳一戳
CAT_EMOJI = "emoji"      # 表情包
CAT_FORWARD = "forward"  # 合并转发的信息
CAT_IMAGE = "image"      # 包含图片的信息（区别于表情包）
CAT_TEXT = "text"        # 文字信息

# 全部消息类型（顺序即 README/文档展示顺序）
ALL_CATEGORIES: Tuple[str, ...] = (CAT_POKE, CAT_EMOJI, CAT_FORWARD, CAT_IMAGE, CAT_TEXT)

# 匹配 "HH:MM"
_TIME_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")
# 归档文件名（仅支持英文）：字母/数字开头结尾，中间允许字母、数字、点、下划线、短横线
_ARCHIVE_NAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")

# NapCat 通知附加配置键
CFG_NOTICE_TYPE = "napcat_notice_type"
CFG_NOTICE_SUB_TYPE = "napcat_notice_sub_type"


@dataclass(frozen=True)
class Period:
    """一个时间段（不跨年），start < end 为自然日区间，否则视为跨天区间。"""

    start: dtime
    end: dtime

    def contains(self, t: dtime) -> bool:
        if self.start < self.end:
            return self.start <= t < self.end
        # 跨天（如 22:00-02:00）
        return t >= self.start or t < self.end


def parse_period(spec: Any) -> Optional[Period]:
    """解析 "HH:MM-HH:MM" → Period；格式非法返回 None。"""
    if not isinstance(spec, str):
        return None
    s, sep, e = spec.partition("-")
    if not sep:
        return None
    ms = _TIME_RE.match(s)
    me = _TIME_RE.match(e)
    if not ms or not me:
        return None
    sh, sm = int(ms.group(1)), int(ms.group(2))
    eh, em = int(me.group(1)), int(me.group(2))
    if sh > 23 or sm > 59 or eh > 23 or em > 59:
        return None
    return Period(dtime(sh, sm), dtime(eh, em))


def build_schedule(
    periods: Sequence[Any],
    weekdays: Sequence[Any],
) -> Tuple[List[Period], Set[int], List[str]]:
    """从配置原始值构建过滤时间窗口。

    返回 (periods, weekdays, 非法项描述列表)。
    """
    parsed_periods: List[Period] = []
    bad: List[str] = []
    for spec in periods or ():
        p = parse_period(spec)
        if p is not None:
            parsed_periods.append(p)
        else:
            bad.append(f"每日时间段 {spec!r} 格式非法（应为 HH:MM-HH:MM），已忽略")
    parsed_weekdays: Set[int] = set()
    for w in weekdays or ():
        try:
            wd = int(w)
        except (TypeError, ValueError):
            bad.append(f"filter_weekdays 中存在非法值 {w!r}，已忽略")
            continue
        if 1 <= wd <= 7:
            parsed_weekdays.add(wd)
        else:
            bad.append(f"filter_weekdays 中存在越界值 {wd}（应为 1-7），已忽略")
    return parsed_periods, parsed_weekdays, bad


def in_filter_window(
    periods: Sequence[Period],
    weekdays: Set[int],
    dt: Optional[datetime] = None,
) -> bool:
    """是否处于「要过滤的时间」。

    - weekdays 为空 = 每天；非空 = 仅在这些星期（1=周一 ... 7=周日）；
    - periods 为空 = 全天；非空 = 仅在这些每日时间段内（含跨天）；
    - **跨天时段以时段开始时间归属当日**：如「周一 22:00-02:00」的窗口覆盖
      周一 22:00 至周二 02:00——当天 22:00 起的前半夜按当天星期判定，
      凌晨 00:00 至结束时刻的部分按**前一天**的星期判定（归属前一天开始的窗口）。
    """
    now = dt or datetime.now(TZ)
    if not periods:
        return not weekdays or now.isoweekday() in weekdays
    t = now.time()
    for p in periods:
        if p.start < p.end:
            # 自然日区间：归属当日
            if p.start <= t < p.end and (not weekdays or now.isoweekday() in weekdays):
                return True
        else:
            # 跨天区间（如 22:00-02:00）：以时段开始时间归属当日。
            # 当天 start 起的前半夜按当天星期判定；凌晨 00:00 至 end 的部分
            # 属于「前一天开始的窗口」，按前一天的星期判定。
            if t >= p.start and (not weekdays or now.isoweekday() in weekdays):
                return True
            if t < p.end and (
                not weekdays or (now - timedelta(days=1)).isoweekday() in weekdays
            ):
                return True
    return False


# -------------------- 消息身份提取 --------------------


def extract_ids(message: Mapping[str, Any]) -> Tuple[str, str]:
    """从消息 dict 提取 (group_id, user_id)；取不到返回空字符串。"""
    group_id = ""
    user_id = ""
    if not isinstance(message, Mapping):
        return group_id, user_id
    message_info = message.get("message_info")
    if isinstance(message_info, Mapping):
        group_info = message_info.get("group_info")
        if isinstance(group_info, Mapping):
            group_id = str(group_info.get("group_id") or "").strip()
        user_info = message_info.get("user_info")
        if isinstance(user_info, Mapping):
            user_id = str(user_info.get("user_id") or "").strip()
    if not user_id:
        # 兜底：顶层字段
        user_id = str(message.get("user_id") or "").strip()
    return group_id, user_id


def id_matches(value: str, entry: Any) -> bool:
    """ID 匹配：支持纯 ID 与「平台:ID」（如 qq:123456789），比较时兼容 ID 部分。"""
    value = str(value or "").strip()
    entry_str = str(entry or "").strip()
    if not value or not entry_str:
        return False
    if value == entry_str:
        return True
    v_id = value.split(":", 1)[-1]
    e_id = entry_str.split(":", 1)[-1]
    return bool(v_id and e_id and v_id == e_id)


def is_all_marker(entry: Any) -> bool:
    """判断黑名单条目是否为「all」标记（群内所有成员）。

    - 支持 "all" / "ALL" / "All"（大小写不敏感）；
    - 兼容平台前缀形式："qq:all"（取冒号后的 ID 部分判断）。
    """
    return str(entry or "").strip().split(":", 1)[-1].strip().lower() == "all"


def is_targeted(
    group_id: str,
    user_id: str,
    group_blacklist: Sequence[Any],
    user_blacklist: Sequence[Any],
) -> bool:
    """是否命中黑名单（仅黑名单）。

    交集过滤：配置了哪些维度，就必须同时命中哪些维度，全部满足才视为过滤对象：

    - 仅配置群黑名单：消息群号命中即过滤（整群过滤）；
    - 仅配置用户黑名单：消息用户 ID 命中即过滤（含私聊；``all`` 标记在无群
      黑名单时不生效）；
    - 同时配置群 + 用户黑名单：消息必须**既**来自黑名单群、**又**来自黑名单
      用户（交集）才过滤；
    - 用户黑名单混填 ``all``（大小写不敏感，也支持 ``平台:all``）与具体用户 ID
      时取**并集语义**：``all`` 命中（群黑名单中所配置的群 → 群内所有成员，
      需群维度命中）**或** 具体用户 ID 命中（该用户的所有消息，含私聊与非
      黑名单群），任一即视为用户维度命中——具体用户 ID 不再被 ``all`` 静默
      忽略（修复混填漏拦）；
    - 两者均为空：不过滤任何消息。
    """
    group_hit: Optional[bool] = None
    if group_blacklist:
        group_hit = bool(group_id) and any(id_matches(group_id, g) for g in group_blacklist)
    if not user_blacklist:
        # 未配置用户维度：仅按群维度判定（群黑名单为空 = 不过滤任何消息）
        return group_hit is True
    entries = list(user_blacklist)
    # 具体用户 ID 命中（跳过 all 标记）
    id_hit = bool(user_id) and any(
        id_matches(user_id, u) for u in entries if not is_all_marker(u)
    )
    if any(is_all_marker(u) for u in entries):
        # 含 all 标记：并集语义 —— all（需群维度命中）或具体用户 ID 命中，任一即拦截
        return (group_hit is True) or id_hit
    # 纯具体用户：交集语义（配置了群维度则须同时命中）
    if group_hit is None:
        return id_hit
    return group_hit and id_hit


# -------------------- 消息分类 --------------------


def _segment_text(data: Any) -> str:
    """文本段内容：data 可能为字符串或 dict（取 text/content 字段）。"""
    if isinstance(data, str):
        return data.strip()
    if isinstance(data, Mapping):
        for key in ("text", "content"):
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return ""


def classify_message(message: Mapping[str, Any]) -> Set[str]:
    """对入站消息分类，返回包含的消息类型集合。

    规则：
    - 通知（is_notify）：
      - NapCat 戳一戳通知（napcat_notice_type=notify 且 sub_type=poke）→ {poke}（忽略其渲染文本段）；
      - 其它通知（撤回/禁言/表情回应等）→ 空集合（不受本插件拦截）。
    - 普通消息：扫描 raw_message 段：
      - type=="emoji" → 表情包；
      - type=="image" → 图片；
      - type=="forward" → 合并转发；
      - type=="text" 且内容非空 → 文字。
      兼容顶层 is_emoji / is_picture 标志。
    """
    if not isinstance(message, Mapping):
        return set()
    cats: Set[str] = set()

    # 通知：戳一戳
    if bool(message.get("is_notify", False)):
        message_info = message.get("message_info")
        additional = (
            message_info.get("additional_config")
            if isinstance(message_info, Mapping)
            else None
        )
        if isinstance(additional, Mapping):
            notice_type = str(additional.get(CFG_NOTICE_TYPE) or "").strip()
            sub_type = str(additional.get(CFG_NOTICE_SUB_TYPE) or "").strip()
            if notice_type == "notify" and sub_type == "poke":
                cats.add(CAT_POKE)
        return cats

    # 顶层标志（部分适配器会设置）
    if bool(message.get("is_emoji", False)):
        cats.add(CAT_EMOJI)
    if bool(message.get("is_picture", False)):
        cats.add(CAT_IMAGE)

    # 扫描消息段
    raw_message = message.get("raw_message")
    if isinstance(raw_message, list):
        for seg in raw_message:
            if not isinstance(seg, Mapping):
                continue
            seg_type = str(seg.get("type") or "").strip()
            if seg_type == "emoji":
                cats.add(CAT_EMOJI)
            elif seg_type == "image":
                cats.add(CAT_IMAGE)
            elif seg_type == "forward":
                cats.add(CAT_FORWARD)
            elif seg_type == "text" and _segment_text(seg.get("data")):
                cats.add(CAT_TEXT)
    return cats


def is_category_allowed(category: str, type_config: Mapping[str, Any]) -> bool:
    """某类型是否放行（开启=不拦截）。"""
    key = f"allow_{category}"
    val = type_config.get(key)
    return bool(val)


# -------------------- 关键词屏蔽（仅纯文本消息） --------------------

# 出现这些段即视为「非纯文本消息」，关键词规则不生效
_NON_TEXT_SEGMENTS = frozenset({CAT_IMAGE, CAT_EMOJI, CAT_FORWARD})


def extract_plain_text(message: Mapping[str, Any]) -> str:
    """提取**纯文本消息**的文本内容；非纯文本消息返回空字符串。

    - 只匹配纯文本消息：消息含 ``image`` / ``emoji`` / ``forward`` 段（或顶层
      ``is_picture`` / ``is_emoji`` 标志）时返回空字符串 —— 合并转发与表情包不匹配；
    - 通知（戳一戳 / 撤回 / 禁言等，``is_notify=true``）不参与关键词匹配；
    - 多个文本段按出现顺序拼接（段间以换行分隔），便于按整条消息文本匹配关键词。
    """
    if not isinstance(message, Mapping):
        return ""
    if bool(message.get("is_notify", False)):
        return ""
    if bool(message.get("is_emoji", False)) or bool(message.get("is_picture", False)):
        return ""
    raw_message = message.get("raw_message")
    if not isinstance(raw_message, list):
        return ""
    parts: List[str] = []
    for seg in raw_message:
        if not isinstance(seg, Mapping):
            continue
        seg_type = str(seg.get("type") or "").strip()
        if seg_type in _NON_TEXT_SEGMENTS:
            return ""
        if seg_type == "text":
            text = _segment_text(seg.get("data"))
            if text:
                parts.append(text)
    return "\n".join(parts)


def normalize_keywords(entries: Any) -> List[str]:
    """规范化关键词列表：跳过空值与纯空白项，去重并保持配置顺序。"""
    if isinstance(entries, str):
        entries = [entries]
    if not isinstance(entries, (list, tuple, set, frozenset)):
        return []
    result: List[str] = []
    for entry in entries:
        keyword = str(entry or "").strip()
        if keyword and keyword not in result:
            result.append(keyword)
    return result


def match_keywords(text: str, keywords: Sequence[str]) -> Optional[str]:
    """返回**首个命中**的配置关键词；无命中返回 None（子串匹配、大小写敏感）。

    关键词与消息文本均 ``strip()``，避免配置里误带首尾空格导致永远不命中。
    """
    if not text or not keywords:
        return None
    for keyword in keywords:
        if keyword and keyword in text:
            return keyword
    return None


def should_intercept(
    message: Mapping[str, Any],
    filter_config: Mapping[str, Any],
    *,
    schedule: Optional[Tuple[Sequence[Period], Set[int]]] = None,
) -> bool:
    """核心决策：该消息是否应被拦截（不入库、不入站）。

    filter_config 结构（与插件配置模型对应）::

        {
            "group_blacklist": [...],
            "user_blacklist": [...],
            "filter_periods": [...],     # "HH:MM-HH:MM"
            "filter_weekdays": [...],    # 1=周一 ... 7=周日
            "types": {"allow_poke": bool, "allow_emoji": bool,
                      "allow_forward": bool, "allow_image": bool, "allow_text": bool},
            "allow_keyword": bool,       # 关键词屏蔽总开关（默认 True）
            "keyword_blacklist": [...],  # 关键词屏蔽（仅纯文本消息）
        }

    schedule：可选的预解析时间窗口 ``(periods, weekdays)``（调用方缓存
    ``build_schedule`` 结果时传入，避免逐条消息重复解析时段配置）；
    缺省时由 filter_config 现场解析。

    判定流程：
    1. 群/用户黑名单命中（交集语义；用户黑名单混填 all 与具体 ID 时取并集，
       见 is_targeted）；
    2. 当前时间在要过滤的时间窗口内；
    3. 关键词屏蔽（allow_keyword 为真且列表非空）：纯文本消息命中任一关键词
       → 整体拦截（独立于类型开关，allow_text 为「关」时也只看本步骤的开关）；
    4. 消息包含至少一个类型；
    5. 消息包含的任一类型是「关闭」（拦截）→ 整体拦截；
       全部包含类型均「开启」（放行）→ 放行。
    """
    if not isinstance(message, Mapping) or not isinstance(filter_config, Mapping):
        return False
    group_id, user_id = extract_ids(message)
    if not is_targeted(
        group_id,
        user_id,
        filter_config.get("group_blacklist") or (),
        filter_config.get("user_blacklist") or (),
    ):
        return False
    if schedule is None:
        periods, weekdays, _ = build_schedule(
            filter_config.get("filter_periods") or (),
            filter_config.get("filter_weekdays") or (),
        )
    else:
        periods, weekdays = schedule
    if not in_filter_window(periods, weekdays):
        return False
    # 关键词屏蔽：只匹配纯文本消息（图片/表情包/合并转发不匹配），独立于类型开关
    # （仅受同节的 allow_keyword 总开关控制，不受 allow_text 等类型开关影响）
    keywords = (
        normalize_keywords(filter_config.get("keyword_blacklist"))
        if bool(filter_config.get("allow_keyword", True))
        else []
    )
    if keywords and match_keywords(extract_plain_text(message), keywords):
        return True
    cats = classify_message(message)
    if not cats:
        return False
    type_config = filter_config.get("types") or {}
    # 任一包含的类型为关闭（拦截）→ 整体拦截
    for cat in cats:
        if not is_category_allowed(cat, type_config):
            return True
    return False


# -------------------- 归档 --------------------


def sanitize_archive_filename(name: Any) -> Optional[str]:
    """校验并规范化归档文件名（仅支持英文）。

    返回规范后的文件名（自动补 .json）；无效返回 None（调用方用时间戳兜底）。
    """
    raw = str(name or "").strip()
    if not raw:
        return None
    if "." in raw and not raw.endswith(".json"):
        return None  # 含点但非 .json 结尾 → 视为无效（避免歧义扩展名）
    base = raw[:-5] if raw.endswith(".json") else raw
    if not _ARCHIVE_NAME_RE.match(base):
        return None
    if base in (".", ".."):
        return None
    # 防路径穿越：不允许路径分隔符（正则已排除），再保险校验 basename
    if os.path.basename(base) != base:
        return None
    return f"{base}.json"


def build_archive_payload(
    filter_config: Mapping[str, Any],
    *,
    config_version: str,
    exported_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """把当前过滤配置构造为可归档的 JSON 结构。

    仅包含「过滤规则」（黑名单/时间/类型开关/关键词），不含归档控制字段
    （archive_file_name / archive_enabled）。
    """
    exported_at = exported_at or datetime.now(TZ)
    return {
        "meta": {
            "plugin_id": "github.cateye.custom-filter",
            "config_version": str(config_version or ""),
            "exported_at": exported_at.strftime("%Y-%m-%d %H:%M:%S %z"),
        },
        "filter": {
            "group_blacklist": [str(x) for x in (filter_config.get("group_blacklist") or []) if str(x).strip()],
            "user_blacklist": [str(x) for x in (filter_config.get("user_blacklist") or []) if str(x).strip()],
            "filter_periods": [str(x) for x in (filter_config.get("filter_periods") or []) if str(x).strip()],
            "filter_weekdays": [int(x) for x in (filter_config.get("filter_weekdays") or []) if str(x).strip().isdigit()],
        },
        "types": {
            f"allow_{cat}": bool((filter_config.get("types") or {}).get(f"allow_{cat}"))
            for cat in ALL_CATEGORIES
        },
        # 关键词屏蔽总开关（不在 types.allow_* 里：它不是消息类型开关）
        "allow_keyword": bool(filter_config.get("allow_keyword", True)),
        "keywords": normalize_keywords(filter_config.get("keyword_blacklist")),
    }
