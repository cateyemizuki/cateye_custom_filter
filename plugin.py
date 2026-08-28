"""麦麦不要再看那个了！ — MaiBot 插件入口。

按黑名单（群 + 用户）+ 每日时间段 + 每周时间，精准拦截指定群/用户的消息，
不让它们入库、不入站（通过 ``chat.receive.before_process`` 的 ``abort`` 终止消息处理链）。

判定流程（全部满足才拦截）：
1. 群 / 用户黑名单命中（仅黑名单；群、用户任一命中即指定对象；名单为空 = 不限制该维度）；
2. 当前北京时间（UTC+8）处于配置的「要过滤的时间」窗口（每日时间段 + 每周星期，参考峰谷模型切换插件）；
3. 消息包含至少一个可分类类型，且包含的任一类型开关为「关闭」（拦截）→ 整体拦截。

消息类型开关（开启=不拦截，关闭=拦截）：
- 戳一戳（开）     poke   —— 默认开启（不拦截）
- 表情包（开）     emoji  —— 默认开启（不拦截）
- 合并转发的信息   forward—— 默认关闭（拦截）
- 包含图片的信息   image  —— 默认关闭（拦截，区别于表情包）
- 文字信息（开）   text   —— 默认开启（不拦截）

归档功能（多版过滤规则）：
- 配置开头（紧跟在 config_version 后）新增 ``archive_file_name``（仅支持英文，默认空）
  与 ``archive_enabled``（默认关闭）；
- 开启 archive_enabled 并保存（设为 true 并保存修改）后，自动把当前过滤配置转成 JSON
  放进插件数据文件夹（data/plugins/github.cateye.custom-filter/archive/）：
  - 填写了有效的 archive_file_name → 用该名称归档；
  - 未填或填入内容无效 → 用时间戳兜底命名（filter_YYYYmmdd_HHMMSS.json）；
- 插件只负责归档，**不写回/不重置 config.toml**（config.toml 由 Runner 生成与维护，
  插件不自行落盘，避免与 WebUI 保存、热重载竞争）；需要恢复默认时请在 WebUI 手动清空；
- 即使已经配置好的文件没有使用开关进行归档（即还可以在 WebUI 中修改），也生效。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, ClassVar, Dict, Iterable, List, Mapping, Optional

from maibot_sdk import (
    CONFIG_RELOAD_SCOPE_SELF,
    Command,
    Field,
    HookHandler,
    MaiBotPlugin,
    PluginConfigBase,
)
from maibot_sdk.types import ErrorPolicy, HookMode, HookOrder

from .filter_core import (
    ALL_CATEGORIES,
    TZ,
    build_schedule,
    build_archive_payload,
    classify_message,
    extract_ids,
    in_filter_window,
    is_targeted,
    sanitize_archive_filename,
    should_intercept,
)

# ==================== 常量 ====================

# 配置版本：与 _manifest.json 的 version 保持同步。
# 1.0.0：初始版本。
SUPPORTED_CONFIG_VERSION = "1.0.0"

# 默认过滤时间：全天（periods 空） + 每天（weekdays 空）
DEFAULT_FILTER_PERIODS: List[str] = []
DEFAULT_FILTER_WEEKDAYS: List[int] = []

# 默认类型开关：戳一戳/表情包/文字信息 开（不拦截）；合并转发/图片 关（拦截）
DEFAULT_ALLOW_POKE = True
DEFAULT_ALLOW_EMOJI = True
DEFAULT_ALLOW_FORWARD = False
DEFAULT_ALLOW_IMAGE = False
DEFAULT_ALLOW_TEXT = True

# 归档 JSON 存放子目录（位于 ctx.paths.data_dir 下）
ARCHIVE_DIR_NAME = "archive"


# ==================== 配置模型 ====================


class BlacklistSectionConfig(PluginConfigBase):
    """群/用户黑名单（仅黑名单，用于过滤信息）。"""

    __ui_label__ = "群/用户黑名单"
    __ui_icon__ = "block"
    __ui_order__ = 1

    group_blacklist: list[str] = Field(
        default_factory=list,
        description="群黑名单（填群号；仅黑名单，命中即过滤。留空 = 不按群过滤）",
    )
    user_blacklist: list[str] = Field(
        default_factory=list,
        description="用户黑名单（填用户ID 或 平台:用户ID，如 \"123456789\" 或 \"qq:123456789\"；仅黑名单，命中即过滤。可填 all（大小写不敏感，也支持 \"qq:all\"）表示群黑名单中所配置的群的群内所有成员，需配合群黑名单使用。留空 = 不按用户过滤）",
    )


class ScheduleSectionConfig(PluginConfigBase):
    """过滤时间窗口。"""

    __ui_label__ = "过滤时间"
    __ui_icon__ = "schedule"
    __ui_order__ = 2

    filter_periods: list[str] = Field(
        default_factory=lambda: list(DEFAULT_FILTER_PERIODS),
        description="每日要过滤的时间段（北京时间 HH:MM-HH:MM，支持跨天如 \"22:00-02:00\"；例：\"09:00-12:00\"。留空 = 全天）",
    )
    filter_weekdays: list[int] = Field(
        default_factory=lambda: list(DEFAULT_FILTER_WEEKDAYS),
        description="每周要过滤的星期（1=周一 ... 7=周日；如 [1,2,3,4,5] 表示仅工作日。留空 = 每天）",
    )


class TypeSwitchSectionConfig(PluginConfigBase):
    """消息类型开关：开启 = 不拦截（放行入库入站）；关闭 = 拦截。"""

    __ui_label__ = "消息类型开关"
    __ui_icon__ = "toggle_on"
    __ui_order__ = 3

    allow_poke: bool = Field(
        default=DEFAULT_ALLOW_POKE,
        description="戳一戳：开启 = 不拦截；关闭 = 拦截（默认开）",
    )
    allow_emoji: bool = Field(
        default=DEFAULT_ALLOW_EMOJI,
        description="表情包：开启 = 不拦截；关闭 = 拦截（默认开）",
    )
    allow_forward: bool = Field(
        default=DEFAULT_ALLOW_FORWARD,
        description="合并转发的信息：开启 = 不拦截；关闭 = 拦截（默认关）",
    )
    allow_image: bool = Field(
        default=DEFAULT_ALLOW_IMAGE,
        description="包含图片的信息（区别于表情包）：开启 = 不拦截；关闭 = 拦截（默认关）",
    )
    allow_text: bool = Field(
        default=DEFAULT_ALLOW_TEXT,
        description="文字信息：开启 = 不拦截；关闭 = 拦截（默认开）",
    )


class PluginSectionConfig(PluginConfigBase):
    """插件自身配置（plugin 配置节）。"""

    __ui_label__ = "插件"
    __ui_icon__ = "package"
    __ui_order__ = 0

    enabled: bool = Field(default=True, description="是否启用插件（关闭后不拦截任何消息）")
    config_version: str = Field(
        default=SUPPORTED_CONFIG_VERSION,
        description="配置版本（与插件版本同步，用于检查配置文件是否需要更新）",
        json_schema_extra={
            "disabled": True,
            "hidden": True,
            "label": "配置版本",
        },
    )
    # 归档（紧跟 config_version 后）：保存的 json 文件名称（仅支持英文）+ 归档开关
    archive_file_name: str = Field(
        default="",
        description="保存的 json 文件名称（仅支持英文，如 my_rule；可省略 .json。留空 = 使用时间戳命名）",
    )
    archive_enabled: bool = Field(
        default=False,
        description="归档开关：开启（设为 true）并保存修改后，自动把当前过滤配置转成 json 放进插件数据文件夹（只归档，不自动重置；恢复默认请在 WebUI 手动清空）。未归档的配置同样生效",
    )


class CateyeCustomFilterConfig(PluginConfigBase):
    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    blacklist: BlacklistSectionConfig = Field(default_factory=BlacklistSectionConfig)
    schedule: ScheduleSectionConfig = Field(default_factory=ScheduleSectionConfig)
    types: TypeSwitchSectionConfig = Field(default_factory=TypeSwitchSectionConfig)


# ==================== 插件主体 ====================


class CateyeCustomFilterPlugin(MaiBotPlugin):
    """麦麦不要再看那个了！：黑名单 + 时间窗口 + 类型开关的精准消息过滤插件。"""

    config_model: ClassVar[type[PluginConfigBase] | None] = CateyeCustomFilterConfig
    config_reload_subscriptions: ClassVar[Iterable[str]] = ()

    # ==================== 配置读取 ====================

    def _build_filter_config(self) -> Dict[str, Any]:
        """把当前配置模型转成 filter_core 使用的扁平 dict。"""
        types: Dict[str, bool] = {}
        for cat in ALL_CATEGORIES:
            types[f"allow_{cat}"] = bool(getattr(self.config.types, f"allow_{cat}"))
        return {
            "group_blacklist": list(self.config.blacklist.group_blacklist or []),
            "user_blacklist": list(self.config.blacklist.user_blacklist or []),
            "filter_periods": list(self.config.schedule.filter_periods or []),
            "filter_weekdays": list(self.config.schedule.filter_weekdays or []),
            "types": types,
        }

    def _get_archive_dir(self) -> str:
        """归档数据目录：data/plugins/<plugin_id>/archive。"""
        return os.path.join(str(self.ctx.paths.data_dir), ARCHIVE_DIR_NAME)

    def _get_archive_path(self, filename: str) -> str:
        """在归档目录内解析目标路径，并校验不越出目录（防路径穿越）。"""
        archive_dir = self._get_archive_dir()
        base = os.path.basename(filename)
        return os.path.join(archive_dir, base)

    # ==================== 归档 ====================

    def _archive_current_config(self, requested_name: str = "") -> Optional[str]:
        """把当前过滤配置归档为 JSON 到插件数据文件夹。

        - requested_name 有效 → 使用该名称；否则用时间戳兜底命名；
        - 返回写入的文件路径；失败返回 None。
        """
        try:
            archive_dir = self._get_archive_dir()
            os.makedirs(archive_dir, exist_ok=True)
            filename = sanitize_archive_filename(requested_name)
            if filename is None:
                ts = datetime.now(TZ).strftime("%Y%m%d_%H%M%S")
                filename = f"filter_{ts}.json"
            payload = build_archive_payload(
                self._build_filter_config(),
                config_version=SUPPORTED_CONFIG_VERSION,
            )
            path = self._get_archive_path(filename)
            if not path.startswith(archive_dir + os.sep):
                self.ctx.logger.error("归档路径越出数据目录，已拒绝：%s", path)
                return None
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            self.ctx.logger.info("过滤配置已归档：%s", path)
            return path
        except Exception as e:
            self.ctx.logger.error("归档过滤配置失败：%s", e)
            return None

    # ==================== 命令 ====================

    @Command(
        "custom_filter_status",
        description="查看麦麦不要再看那个了！的过滤规则状态",
        pattern=r"^/?过滤状态\s*$",
    )
    async def cmd_filter_status(self, **kwargs: Any) -> tuple[bool, str, int]:
        """输出当前生效的过滤规则摘要（纯文本回复，不声明额外能力）。"""
        stream_id = str(kwargs.get("stream_id") or "")
        lines = self._describe_config()
        try:
            await self.ctx.send.text("\n".join(lines), stream_id)
        except Exception as e:
            self.ctx.logger.warning("发送过滤状态失败：%s", e)
        return True, "已发送过滤状态", 1

    def _describe_config(self) -> List[str]:
        """生成过滤规则描述文本（日志/命令共用）。"""
        from .filter_core import is_all_marker

        cfg = self._build_filter_config()
        lines: List[str] = []
        lines.append("【麦麦不要再看那个了！】当前生效规则")
        gb = cfg["group_blacklist"]
        ub = cfg["user_blacklist"]
        lines.append(f"群黑名单：{'、'.join(gb) if gb else '（空，不按群过滤）'}")
        if ub:
            ub_text = []
            for u in ub:
                ub_text.append("群内所有成员（all）" if is_all_marker(u) else str(u))
            lines.append(f"用户黑名单：{'、'.join(ub_text)}")
        else:
            lines.append("用户黑名单：（空，不按用户过滤）")
        periods = cfg["filter_periods"]
        weekdays = cfg["filter_weekdays"]
        wd_text = "、".join(str(w) for w in weekdays) if weekdays else "每天"
        lines.append(
            f"过滤时间：{('、'.join(periods) if periods else '全天')}｜星期：{wd_text}"
        )
        lines.append("类型开关（开=不拦截，关=拦截）：")
        for cat in ALL_CATEGORIES:
            label = {
                "poke": "戳一戳",
                "emoji": "表情包",
                "forward": "合并转发",
                "image": "图片",
                "text": "文字",
            }.get(cat, cat)
            state = "开（放行）" if cfg["types"].get(f"allow_{cat}") else "关（拦截）"
            lines.append(f"  {label}：{state}")
        return lines

    # ==================== Hook：消息过滤 ====================

    @HookHandler(
        "chat.receive.before_process",
        name="custom_filter_blocker",
        description="按黑名单+时间窗口+类型开关拦截指定群/用户的消息（不入库、不入站）",
        mode=HookMode.BLOCKING,
        order=HookOrder.EARLY,
        error_policy=ErrorPolicy.SKIP,
    )
    async def hook_custom_filter(self, **kwargs: Any) -> Dict[str, Any]:
        """拦截入站消息：命中黑名单 + 时间窗口 + 关闭类型 → abort（消息不入库、不入站）。"""
        try:
            if not self.config.plugin.enabled:
                return {"action": "continue"}
            message = kwargs.get("message")
            if not isinstance(message, Mapping):
                return {"action": "continue"}
            filter_config = self._build_filter_config()
            if should_intercept(message, filter_config):
                group_id, user_id = _extract_ids(message)
                cats = classify_message(message)
                self.ctx.logger.info(
                    "已拦截消息（群=%s 用户=%s 类型=%s）",
                    group_id or "-",
                    user_id or "-",
                    ",".join(sorted(cats)) or "-",
                )
                return {"action": "abort"}
        except Exception as e:
            self.ctx.logger.warning("消息过滤判定异常（放行本条）：%s", e)
        return {"action": "continue"}

    # ==================== 版本兼容 ====================

    def _check_config_version(self) -> None:
        """检测配置版本并自动兼容旧版配置文件（缺失字段由 Runner 按默认值补齐）。"""
        try:
            raw = self.get_plugin_config_data()
            current = str((raw.get("plugin") or {}).get("config_version") or "").strip()
        except Exception:
            return
        if current and current != SUPPORTED_CONFIG_VERSION:
            self.ctx.logger.info(
                "检测到旧版配置（config_version=%s，当前支持 %s），缺失字段已按默认值自动补齐",
                current,
                SUPPORTED_CONFIG_VERSION,
            )

    # ==================== 生命周期 ====================

    async def on_load(self) -> None:
        self._check_config_version()
        self._validate_schedule_config()
        if self.config.plugin.enabled:
            self.ctx.logger.info(
                "麦麦不要再看那个了！已加载：\n%s", "\n".join(self._describe_config())
            )
        else:
            self.ctx.logger.info("麦麦不要再看那个了！已加载（插件开关关闭，不拦截消息）")

    async def on_unload(self) -> None:
        self.ctx.logger.info("麦麦不要再看那个了！已卸载")

    async def on_config_update(self, scope: str, config_data: Dict[str, Any], version: str) -> None:
        del config_data, version
        if scope != CONFIG_RELOAD_SCOPE_SELF:
            return
        self._check_config_version()
        self._validate_schedule_config()
        # 归档开关：开启（true）并保存修改 → 归档当前配置（只归档，不自动重置）
        try:
            archive_enabled = bool(self.config.plugin.archive_enabled)
        except Exception:
            archive_enabled = False
        if archive_enabled:
            requested = str(self.config.plugin.archive_file_name or "").strip()
            path = self._archive_current_config(requested)
            if path:
                # 只归档、不自动重置：config.toml 由 Runner 生成与维护，插件不自行写回，
                # 避免与 WebUI 保存、热重载竞争（插件中心评审建议）。
                # 需要恢复默认时请在 WebUI 手动清空黑名单/恢复时间与类型开关。
                msg = (
                    f"过滤配置已归档：{path}\n"
                    "如需恢复默认过滤规则，请在 WebUI 手动清空黑名单、恢复默认时间窗口与类型开关。"
                )
            else:
                msg = "归档失败（见日志）。"
            self.ctx.logger.info("归档流程：%s", msg)
        else:
            self.ctx.logger.info("麦麦不要再看那个了！配置已更新（未触发归档）")

    def _validate_schedule_config(self) -> None:
        """校验过滤时间配置，非法项记录警告（不影响加载）。"""
        periods, weekdays, bad = build_schedule(
            list(self.config.schedule.filter_periods or []),
            list(self.config.schedule.filter_weekdays or []),
        )
        for msg in bad:
            self.ctx.logger.warning("过滤时间配置无效：%s", msg)
        # 供调试/日志：展示当前是否处于过滤时间窗口
        try:
            if in_filter_window(periods, weekdays):
                self.ctx.logger.debug("当前处于过滤时间窗口内")
        except Exception:
            pass


def _extract_ids(message: Mapping[str, Any]) -> tuple[str, str]:
    """日志用的 ID 提取（复用 filter_core 逻辑）。"""
    from .filter_core import extract_ids

    return extract_ids(message)


def create_plugin() -> CateyeCustomFilterPlugin:
    return CateyeCustomFilterPlugin()
