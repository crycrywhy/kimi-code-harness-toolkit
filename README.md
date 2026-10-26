# Kimi Code Harness Toolkit

**Kimi Code for VS Code 本地增强补丁集** —— 补齐 CLI 已有但扩展没暴露的能力。

`kimi-code` · `kimi-code-for-vscode` · `vscode-extension` · `slash-commands` · `/fork` · `permission-mode` ·
`session-rename` · `session-restore` · `patch-scripts`

| 能力 | 说明 |
|---|---|
| **8 个斜杠命令** | `/fork` `/undo` `/title` `/status` `/usage` `/tasks` `/skills` `/plugins`（CLI 有、扩展没暴露） |
| **`/view`** | 只渲染最近 N 轮，治长会话卡顿 |
| **手动给历史对话命名** | 会话列表里每一行都有 Rename（含历史会话），等价 `/title`，不用回 CLI |
| **权限模式切换** | 输入框右下角按钮 / 下拉，等价于发 `/auto`、`/yolo` |
| **会话打开加速** | 默认不恢复历史 subagent，长会话秒开 |
| **规则路由注入** | 每步自动提示该看 AGENTS.md 哪一节 |
| **正文流式净化** | 治上游注入的碎行/空格 |
| **面板体验** | 标签页跟随会话标题 + Reload 后原地恢复会话 + **Restart Extension Host 后原位重建**（旧标签先显示 Reloading 动画） |
| **压缩时间锚** | 压缩后保留的消息带 `[YYYY-MM-DD HH:MM UTC+8]` 前缀，摘要写压缩时刻 |

> 图的渲染与图墙工具已拆到独立仓库：**kimi-code-rich-report**。

## 内容

| 目录 | 是什么 |
|---|---|
| `patches/cli/` | **CLI 功能补齐**：8 个斜杠命令、`/view`、**会话重命名（含历史会话）**、权限模式切换、历史会话打开加速 |
| `patches/harness/` | **运行时增强**：AGENTS.md 规则路由提示注入、流式载荷碎片换行与 Markdown 结构净化、孤儿事件回放容错、压缩消息时间锚 |
| `patches/panel/` | **面板体验优化**：独立面板标签页跟随会话标题、窗口 Reload 原地恢复会话 |

## 安装（两步）

1. **打补丁**：`patches/` 下各目录按对应 README 说明执行（每个脚本先备份为 `*.bak-<时间>-before-xxx`）。
2. **Reload Window**。

## 兼容性说明

- 补丁修改的是**扩展安装目录里的打包产物**，不包含、也不分发任何上游代码；
  锚点是字符串常量，匹配失败会直接退出，不会写坏文件。
- 上游升级后需要按 `patches/*/README.md` 的顺序重打。
- 会话重命名作用于**列表里的任意会话**（含历史），不必先进入该会话。

## 已知限制

- 会话加速补丁默认不恢复历史 subagent，需要时设 `KIMI_CODE_LOAD_SUBAGENTS=true`。
- 渲染层（图片降采样/软重载/会话重命名）在 0.8.3 上是**手工适配**，脚本仍是 0.8.2 锚点，本仓库暂不收录。

## License

本项目代码 MIT（见 `LICENSE`）。补丁作用的对象是第三方扩展，使用者需自行确认其许可条款；本项目不提供任何担保。
