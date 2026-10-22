# Kimi Code Harness Toolkit

**Kimi Code for VS Code 本地增强补丁集** —— 补齐 CLI 已有但扩展没暴露的能力，并给对话加一套可折叠图块渲染。

`kimi-code` · `kimi-code-for-vscode` · `vscode-extension` · `slash-commands` · `/fork` · `permission-mode` ·
`session-restore` · `inline-svg` · `figure-block` · `mcp` · `markdown-rendering` · `patch-scripts`

| 能力 | 说明 |
|---|---|
| **8 个斜杠命令** | `/fork` `/undo` `/title` `/status` `/usage` `/tasks` `/skills` `/plugins`（CLI 有、扩展没暴露） |
| **`/view`** | 只渲染最近 N 轮，治长会话卡顿 |
| **会话重命名** | 会话列表里直接改名（等价 `/title`） |
| **权限模式切换** | 输入框右下角按钮 / 下拉，等价于发 `/auto`、`/yolo` |
| **会话打开加速** | 默认不恢复历史 subagent，长会话秒开 |
| **内联 SVG** | `svg` 代码块直接渲染成图（净化 + 尺寸约束） |
| **图块（Figure block）** | `wall` 代码块 → 可折叠图块：多图并排、每格可放大、内联手写 SVG、跟随主题 |
| **markdown 大图** | 按容器全宽显示，小图保持缩略图 |
| **rich-report MCP** | `render_diagram`（流程图）/ `build_image_wall`（多图对比）/ `check_diagram` |

> 仓库内只有**补丁脚本与工具**，不包含、不再分发任何上游代码。

> 针对 **Kimi Code for VS Code** 的本地增强：补齐 CLI 已有但扩展未暴露的能力，并给对话加一套「图块」渲染。

**目标版本**：`moonshot-ai.kimi-code` **0.8.2**（Linux x64）。其它版本锚点可能失效，脚本会自检并在失败时中止。

## 内容

| 目录 | 是什么 |
|---|---|
| `patches/cli/` | **CLI 功能补齐**：8 个斜杠命令、`/view`、会话重命名、权限模式切换、历史会话打开加速 |
| `patches/rich-report/` | **图块渲染**：把 `svg` 代码块、markdown 图片、`wall` 代码块渲染成内联图/可折叠图块（含 `apply_rb3.py`、`apply_wall_icon2.py`） |
| `rich-report/` | **rich-report MCP 插件**：`render_diagram` / `build_image_wall` / `check_diagram` |

## 安装（三步）

1. **打补丁**：先 `patches/cli/`，再 `patches/rich-report/`，每个脚本 `python3 apply_xxx.py`
   （脚本会先备份为 `*.bak-<时间>-before-xxx`；`patches/rich-report/` 多数支持 `--revert`，`patches/cli/` 用备份覆盖回退）。
2. **装插件**：把 `rich-report/` 放进 `~/.kimi-code/plugins/managed/rich-report/`，
   并在 `~/.kimi-code/plugins/installed.json` 里登记（见 `rich-report/README.md`）。
3. **Reload Window**。

## 兼容性说明

- 补丁修改的是**扩展安装目录里的打包产物**，不包含、也不分发任何上游代码；
  锚点是字符串常量，匹配失败会直接退出，不会写坏文件。
- 上游升级后需要按 `patches/*/README.md` 的顺序重打。
- 没有补丁时，`rich-report` 仍可作为普通 MCP 使用，只是输出会显示为代码块。

## 已知限制

- 图片墙单块建议 ≤4 格；`render_diagram` 默认显示宽度 520px（`max_width` 可调）。
- 会话加速补丁默认不恢复历史 subagent，需要时设 `KIMI_CODE_LOAD_SUBAGENTS=true`。

## License

本项目代码 MIT（见 `LICENSE`）。补丁作用的对象是第三方扩展，使用者需自行确认其许可条款；本项目不提供任何担保。
