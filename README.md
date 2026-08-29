# 麦麦不要再看那个了！（MaiBot 插件）

按**群黑名单 + 用户黑名单**精准过滤消息：仅当消息来自**被指定的群/用户**、且当前时间处于**配置的过滤时间窗口**（每日时间段 + 每周星期）时，再根据**消息类型开关**决定是否拦截。被拦截的消息**不入库、不入站**（通过 `chat.receive.before_process` 钩子 `abort` 终止处理链，麦麦既不会看到，也不会写入聊天记录）。

> 插件制作背景：深夜群友开始发送神秘的图片合并转发，或者一些llm不能正常识别的图片，该插件可以让这些消息完全不进入麦麦，不影响别人。

> ~~求你们不让maimai看涩图了啊~~

## 功能

- **群/用户黑名单（仅黑名单，交集过滤）**：`group_blacklist`（群号）、`user_blacklist`（用户ID 或 `平台:用户ID`）。**配置了哪些维度就必须同时命中哪些维度**——例如同时配置群+用户 = 只过滤「该群内该用户」的消息；只配群 = 整群过滤；只配用户 = 该用户所有消息（含私聊）。名单为空 = 不按该维度过滤。`user_blacklist` 支持特殊值 **`all`**（大小写不敏感，也支持 `qq:all`）：表示**群黑名单中所配置的群的群内所有成员**（需配合群黑名单使用，不匹配私聊）。
- **每日时间段**：`filter_periods`，格式 `HH:MM-HH:MM`，支持跨天（如 `22:00-02:00`），精确到分钟；留空 = 全天。
- **每周星期**：`filter_weekdays`，`1`=周一 … `7`=周日（如 `[1,2,3,4,5]` 仅工作日）；留空 = 每天。
- **消息类型开关**（开启=不拦截，关闭=拦截）：

  | 类型 | 配置项 | 默认 |
  |------|--------|------|
  | 戳一戳 | `types.allow_poke` | **开**（不拦截） |
  | 表情包 | `types.allow_emoji` | **开**（不拦截） |
  | 合并转发的信息 | `types.allow_forward` | **关**（拦截） |
  | 包含图片的信息（区别于表情包） | `types.allow_image` | **关**（拦截） |
  | 文字信息 | `types.allow_text` | **开**（不拦截） |

  > 未用括号备注「开」的项默认**关闭（即默认拦截）**。
- **判定为拦截**：命中黑名单 + 处于过滤时间窗口 + 消息包含至少一个类型，且包含的任一类型开关为「关闭」→ **整条消息拦截**；包含的所有类型均「开启」→ 放行。未识别类型（语音/视频/文件/其它通知等）一律放行。
- **归档（多版过滤规则）**：可把当前过滤配置导出为 JSON 存到插件数据文件夹，实现多套规则按需切换。

## 安装

1. 将插件目录（含 `_manifest.json`、`plugin.py` 等）放入 MaiBot 的 `plugins/` 目录。
2. 重启 MaiBot，或在 WebUI 插件中心安装。
3. 插件为标准 SDK 插件（`maibot-plugin-sdk`，由 MaiBot Runner 内置提供），**无第三方依赖**，无需手动安装任何包。

> 兼容：`host_application` `1.0.0 ~ 1.99.99`，`sdk` `2.0.0 ~ 2.99.99`（Manifest v2）。

## 配置

插件加载后由 Runner 在插件目录生成 `config.toml`，可在 WebUI 修改：

```toml
[plugin]
enabled = true                 # 是否启用插件（关闭后不拦截任何消息）
config_version = "1.0.1"       # 配置版本（与插件版本同步，UI 中隐藏）
archive_file_name = ""         # 保存的 json 文件名称（仅支持英文，如 my_rule；可省略 .json。留空 = 时间戳命名）
archive_enabled = false        # 归档开关：开启（true）并保存修改后，自动把当前过滤配置转成 json（只归档，不自动重置）

[blacklist]                    # 群/用户黑名单（仅黑名单，用于过滤信息）
group_blacklist = []           # 群黑名单（填群号；留空 = 不按群过滤）
user_blacklist = []            # 用户黑名单（用户ID 或 平台:用户ID，如 "123456789" 或 "qq:123456789"；可填 all 表示群黑名单中所配置的群的群内所有成员；留空 = 不按用户过滤）

[schedule]                     # 过滤时间窗口
filter_periods = []            # 每日要过滤的时间段（北京时间 HH:MM-HH:MM，支持跨天如 "22:00-02:00"；留空 = 全天）
filter_weekdays = []           # 每周要过滤的星期（1=周一 ... 7=周日；留空 = 每天）

[types]                        # 消息类型开关：开启=不拦截，关闭=拦截
allow_poke = true              # 戳一戳（默认开）
allow_emoji = true             # 表情包（默认开）
allow_forward = false          # 合并转发的信息（默认关）
allow_image = false            # 包含图片的信息（区别于表情包；默认关）
allow_text = true              # 文字信息（默认开）
```

### 配置项一览

| 配置项 | 说明 |
|--------|------|
| `plugin.enabled` | 是否启用插件（默认开）。关闭后不拦截任何消息 |
| `plugin.config_version` | 配置版本（与插件版本同步，UI 中隐藏） |
| `plugin.archive_file_name` | 归档 JSON 文件名称，**仅支持英文**（如 `my_rule`，可省略 `.json`；默认空） |
| `plugin.archive_enabled` | 归档开关（默认关）。**开启（设为 true）并保存修改后**：自动把当前过滤配置转成 JSON 放进插件数据文件夹（只归档，不自动重置；恢复默认请在 WebUI 手动清空） |
| `blacklist.group_blacklist` | 群黑名单（群号）。留空 = 不按群过滤 |
| `blacklist.user_blacklist` | 用户黑名单（用户ID 或 `平台:用户ID`）。可填 `all`（大小写不敏感，也支持 `qq:all`）表示群黑名单中所配置的群的群内所有成员（需配合群黑名单使用）。留空 = 不按用户过滤 |
| `schedule.filter_periods` | 每日要过滤的时间段列表，`HH:MM-HH:MM`（半开区间 `[start, end)`），支持跨天；留空 = 全天 |
| `schedule.filter_weekdays` | 每周要过滤的星期数组，`1`=周一 … `7`=周日；留空 = 每天 |
| `types.allow_poke` | 戳一戳：开=不拦截，关=拦截（默认开） |
| `types.allow_emoji` | 表情包：开=不拦截，关=拦截（默认开） |
| `types.allow_forward` | 合并转发的信息：开=不拦截，关=拦截（默认关） |
| `types.allow_image` | 包含图片的信息（区别于表情包）：开=不拦截，关=拦截（默认关） |
| `types.allow_text` | 文字信息：开=不拦截，关=拦截（默认开） |

## 使用

### 过滤流程

```
入站消息
 → 群黑名单 / 用户黑名单命中？（交集：配置了的维度全部命中 = 指定对象；名单为空 = 不限制；
    用户黑名单中的 all = 群黑名单所配置群的群内所有成员）
 → 当前时间在过滤时间窗口内？（每日时间段 + 每周星期）
 → 消息分类（戳一戳 / 表情包 / 合并转发 / 图片 / 文字；可多类并存）
 → 包含的任一类型为「关闭」（拦截）？→ 是 → abort（不入库、不入站）
                                     → 否 → 放行
```

- **拦截**：`chat.receive.before_process`（BLOCKING、EARLY）返回 `{"action": "abort"}`，终止整个消息处理链——消息**不写入数据库（WebUI 不可见）、不会进入 LLM**。
- **放行**：返回 `{"action": "continue"}`。
- 判定异常时**默认放行**（`error_policy=SKIP`），不会误杀正常消息。

### 命令

发送 `/过滤状态` 可查看当前生效的过滤规则摘要（黑名单、时间窗口、各类型开关状态）。

### 归档（多版过滤规则）

- **归档开关**：在 WebUI 把 `plugin.archive_enabled` 设为 `true` 并保存修改（即设为 true 并保存后），插件自动执行：
  1. 把**当前过滤配置**（群/用户黑名单 + 过滤时间 + 类型开关）转为 JSON 写入插件数据文件夹：`data/plugins/github.cateye.custom-filter/archive/`；
  2. 文件名优先使用 `plugin.archive_file_name`（仅支持英文，如 `my_rule` → `my_rule.json`）；**未填或填入的内容无效**（非英文、含非法字符、含路径等）→ **使用时间戳兜底命名**（如 `filter_20260827_233000.json`）；
  3. 插件**只负责归档、不自动重置配置**（config.toml 由 Runner 生成与维护，插件不自行写回，避免与 WebUI 保存、热重载竞争）。需要恢复默认过滤规则时，请在 **WebUI 手动清空黑名单、恢复默认时间窗口与类型开关**。
- **已配置但未归档的规则同样生效**：即使不点归档开关，WebUI 里配置好的过滤规则也会实时生效（不需要先归档）。
- **多版规则**：归档得到多个 JSON 后，需要哪版就把哪版内容手动填回 WebUI（或作为参考备份），即可切换规则版本。

> 归档 JSON 结构（`meta` + `filter`）：`meta.config_version` / `meta.exported_at` 为元信息；`filter.group_blacklist` / `filter.user_blacklist` / `filter.filter_periods` / `filter.filter_weekdays` / `filter.types.allow_*` 为规则本体，可直接照着填回 WebUI。

### 消息类型判定说明（NapCat）

- **戳一戳**：NapCat `poke` 通知（`is_notify=true` 且 `napcat_notice_type=notify`、`napcat_notice_sub_type=poke`）；
- **表情包**：`raw_message` 段 `type == "emoji"`（QQ 表情 `face` 会转换为文字段，不算表情包）；
- **合并转发**：`raw_message` 段 `type == "forward"`；
- **图片**：`raw_message` 段 `type == "image"`（区别于 `emoji` 段）；
- **文字**：`raw_message` 段 `type == "text"` 且内容非空；
- 其它（语音/视频/文件/撤回/禁言等通知）不分类，一律放行。

## 日志示例

```
[INFO] 麦麦不要再看那个了！已加载：
【麦麦不要再看那个了！】当前生效规则
群黑名单：123456789
用户黑名单：（空，不按用户过滤）
过滤时间：23:00-07:00｜星期：每天
类型开关（开=不拦截，关=拦截）：
  戳一戳：开（放行）
  表情包：开（放行）
  合并转发：关（拦截）
  图片：关（拦截）
  文字：开（放行）
[INFO] 已拦截消息（群=123456789 用户=987654321 类型=emoji,text）
[INFO] 过滤配置已归档：D:\MaiBot\data\plugins\github.cateye.custom-filter\archive\my_rule.json
[INFO] 归档流程：过滤配置已归档：...\n如需恢复默认过滤规则，请在 WebUI 手动清空黑名单、恢复默认时间窗口与类型开关。
```

## 常见问题

- **为什么消息没被拦截？** 检查：① `plugin.enabled` 开启；② 群/用户黑名单已填且 ID 正确（群号、用户ID 或 `平台:用户ID`）；③ 使用 `all`（群内所有成员）时须同时填写群黑名单；④ 当前时间在过滤时间窗口内（`filter_periods`/`filter_weekdays`）；⑤ 消息包含的类型开关为「关闭」（如图片默认关、文字默认开）；⑥ 消息类型可被识别。
- **不想拦截了怎么办？** WebUI 关闭 `plugin.enabled`，或清空黑名单/过滤时间。
- **归档文件名没生效？** `archive_file_name` 仅支持英文（字母/数字/点/下划线/短横线，不能含路径），无效时自动用时间戳命名。
- **归档后想恢复默认规则？** 插件只归档、不自动重置（config.toml 由 Runner 维护，插件不自行写回）。请在 **WebUI 手动清空黑名单、恢复默认时间窗口与类型开关**；已归档的 JSON 可作参考备份，需要时照 JSON 填回。

## 版本历史

| 版本 | 变更 |
|------|------|
| 1.0.1 | 归档不再自动重置配置（移除自行写回 config.toml，改由 WebUI 手动重置）；文档同步 |
| 1.0.0 | 初始版本：群/用户黑名单 + 每日/每周时间窗口 + 五种消息类型开关；归档（命名/时间戳兜底）与配置重置；`/过滤状态` 命令 |

## 文件结构

```
cateye_custom_filter/
├── _manifest.json      # 插件清单（Manifest v2）
├── plugin.py           # SDK 插件入口（配置模型、Hook 过滤、归档、/过滤状态 命令；依赖 maibot_sdk）
├── filter_core.py      # 核心逻辑（时段解析、黑白名单匹配、消息分类、归档构造；纯 Python，不依赖 SDK）
├── __init__.py
├── README.md
└── LICENSE             # MIT
```

## 开发与测试

本插件为标准 **MaiBot SDK 插件**（基于 `maibot-plugin-sdk`）。

- **`filter_core.py`**：核心逻辑（时间解析/匹配、黑名单匹配、消息分类、归档构造）**不依赖 SDK**，可离线单元测试；
- **`plugin.py`**：SDK 插件入口，依赖 `maibot_sdk`（由 MaiBot Runner 提供），本地开发需先安装 SDK 才能导入。

```bash
pip install maibot-plugin-sdk   # 本地开发依赖

python test/test_filter_core.py        # 核心逻辑单元测试（时间/黑名单/分类/归档，不依赖 SDK）
python test/test_filter_plugin.py      # 插件类集成测试（stub SDK，无需真实 SDK 环境）
```
