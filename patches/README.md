# Kimi Code 补丁集索引

本目录按功能域分为三大模块：**CLI 功能补齐（cli）**、**Harness 运行时增强（harness）** 与 **面板体验优化（panel）**。

| 补丁名 | 一句话作用 | 目标版本 | 目录 |
|---|---|---|---|
| **slash-wave1** | 接入 8 个斜杠命令（`/fork` `/undo` `/title` `/status` `/usage` `/tasks` `/skills` `/plugins`）并在 webview 登记补全菜单 | 0.8.3（2026-10-25 基线） | `cli/` |
| **slash-view** | `/view` 只渲染最近 N 轮（默认 20），治长会话打开与滚动卡顿 | 0.8.3（2026-10-25 基线） | `cli/` |
| **fast-session-restore** | 打开历史会话时默认不加载历史 subagent，超长会话秒开恢复 | 0.8.3（2026-10-25 基线） | `cli/fast-session-restore/` |
| **agents-md-router** | 依据上一条工具调用自动匹配规则，每步前向模型注入 AGENTS.md 对应小节提示与额度告警 | 0.8.3（2026-10-25 基线） | `harness/agents-md-router/` |
| **stream-newline-fix** | 双工序净化上游流式碎片换行与异常空格，规范 Markdown 语法结构 | 0.8.3（2026-10-25 基线） | `harness/stream-newline-fix/` |
| **replay-orphan-tolerant** | 回放折叠器对中断造成的孤儿事件静默跳过，治历史会话打开空白与 `/view` 失灵 | 0.8.3（2026-10-25 基线） | `harness/replay-orphan-tolerant/` |
| **compaction-timestamps** | 压缩后保留的用户消息带 `[YYYY-MM-DD HH:MM UTC+8]` 前缀、摘要写压缩时刻，治"把几天前的消息当成上一条" | 0.8.3（2026-10-25 基线） | `harness/compaction-timestamps/` |
| **panel-title** | 独立面板标签页实时同步当前会话标题，对齐 Claude Code 体验 | 0.8.3（2026-10-25 基线） | `panel/panel-title/` |
| **panel-restore** | Reload 窗口后面板原位保持，自动反查并重新打开对应会话 | 0.8.3（2026-10-25 基线） | `panel/panel-restore/` |
| **panel-resurrect** | Restart Extension Host 后按记录在原列重建面板并自动打开原会话（宿主重启既不重放编辑器也不调序列化器） | 0.8.3（2026-10-25 基线） | `panel/panel-resurrect/` |
| **reload-overlay** | 宿主重启时旧面板显示 `KIMI / Reloading…` 覆盖层（webview 心跳自检，不依赖 deactivate） | 0.8.3（2026-10-25 基线） | `panel/reload-overlay/` |

> 补丁修改的是扩展安装目录里的打包产物，上游升级后需按照对应补丁目录中的 README 说明重新执行打补丁脚本。
