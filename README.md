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

> 图的渲染与图墙工具已拆到独立仓库：**kimi-code-rich-report**。

## 内容

| 目录 | 是什么 |
|---|---|
| `patches/cli/` | **CLI 功能补齐**：8 个斜杠命令、`/view`、**会话重命名（含历史会话）**、权限模式切换、历史会话打开加速 |

## 安装（三步）

1. **打补丁**：`patches/cli/` 里按 README 的顺序执行（每个脚本先备份为 `*.bak-<时间>-before-xxx`）。
2. **Reload Window**。

## 兼容性说明

- 补丁修改的是**扩展安装目录里的打包产物**，不包含、也不分发任何上游代码；
  锚点是字符串常量，匹配失败会直接退出，不会写坏文件。
- 上游升级后需要按 `patches/*/README.md` 的顺序重打。
- 会话重命名作用于**列表里的任意会话**（含历史），不必先进入该会话。

## 已知限制

- 图片墙单块建议 ≤4 格；`render_diagram` 默认显示宽度 520px（`max_width` 可调）。
- 会话加速补丁默认不恢复历史 subagent，需要时设 `KIMI_CODE_LOAD_SUBAGENTS=true`。

## License

本项目代码 MIT（见 `LICENSE`）。补丁作用的对象是第三方扩展，使用者需自行确认其许可条款；本项目不提供任何担保。
