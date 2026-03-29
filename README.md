## JM PDF (AstrBot 版)

这是一个 AstrBot 的 JM 漫画下载插件。（迁移自 [JM_PDF_plugin](https://github.com/AmethystTim/JM_PDF_plugin)）

### 已实现功能

- `/jm get [ID] [CHAPTER]` 下载并转换 PDF
- `/jm search [KEYWORD]` 站内搜索（纯文本输出）
- `/jm rank [week|month]` 排行榜（纯文本输出）
- `/jm clear` 清理下载内容
- `/jm help` 查看帮助

### 命令示例

```text
/jm help
/jm get 123456
/jm get 123456 2
/jm search 偶像大师
/jm rank week
/jm clear
```

### AstrBot 配置

常用配置示例：

- `runtime_config.max_cache_count`: 缓存上限（downloads 内缓存项超过该值时清理最旧项）
- `runtime_config.enable_auto_clean_cache`: 是否启用每日 04:00 自动清理缓存
- `runtime_config.whitelist`: 会话白名单（`unified_msg_origin`，留空表示不限制）
- `runtime_config.fallback_send_path`: 文件发送失败时返回本地路径
- `runtime_config.download_timeout_seconds`: `/jm get` 下载阶段超时阈值
- `runtime_config.request_debounce_seconds`: 用户级请求防抖秒数（覆盖 get/search/rank）
- `runtime_config.max_concurrent_downloads`: `/jm get` 最大并发任务数（低内存建议 1）
- `runtime_config.max_download_batch_count`: 下载线程硬上限（对 `jm_config.batch_count` 再限流）
- `runtime_config.low_memory_protection`: 按可用内存动态下调下载线程
- `runtime_config.low_memory_floor_mb`: 低内存阈值（MB），低于阈值时下载线程降为 1
- `runtime_config.query_timeout_seconds`: `/jm search` 与 `/jm rank` 请求超时阈值
- `runtime_config.recall_seconds`: 延时撤回秒数（大于 0 时生效）
- `runtime_config.recall_get_notice/get_file/search_messages/rank_messages`: 各场景消息撤回开关
- `jm_config.client_api_domains`: JM API 域名列表（按优先级）
- `jm_config.batch_count`: 下载线程数
- `jm_config.login_username/login_password`: 登录配置（留空不启用）
- `command_config.enable_download/search/rank/clear`: 命令开关
- `command_config.enable_text_match`: 自动识别 6-7 位 jm 号

说明：`/jm get` 文件消息在撤回失败时会默认尝试群文件删除兜底，该行为已内置，无需额外配置项。

### 依赖

```text
jmcomic
Pillow
PyYAML
```

### 迁移差异

- 已移除 LangBot 专属事件模型和 API 调用层。
- 原先的转发消息展示改为纯文本列表（兼容性更好）。
- 已支持消息撤回；/jm get 文件消息在撤回失败时会默认尝试执行群文件删除兜底。

### 目录结构（当前）

```text
astrbot_plugin_JM_PDF/
  main.py
  metadata.yaml
  _conf_schema.json
  requirements.txt
  cells/
  handlers/
  utils/
```

###### Coded By GPT-5.3-Codex
