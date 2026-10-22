# rich-report（MCP 插件）

把图直接画进对话：**流程图**用 `render_diagram`，**多图对比**用 `build_image_wall`。

## 文件

| 文件 | 作用 |
|---|---|
| `mcp_server.py` | MCP server：三个工具 + 输出契约（返回 `wall` 代码块） |
| `wall.py` | 图片墙合成（PIL）：切格、contain/cover、标签、PNG 落盘 |
| `kimi.plugin.json` | 插件清单（含 `systemPrompt` 使用约定） |

## 安装

```bash
mkdir -p ~/.kimi-code/plugins/managed/rich-report
cp mcp_server.py wall.py kimi.plugin.json ~/.kimi-code/plugins/managed/rich-report/
```

在 `~/.kimi-code/plugins/installed.json` 里登记：

```json
{"version": 1, "plugins": [{
  "id": "rich-report",
  "root": "~/.kimi-code/plugins/managed/rich-report",
  "source": "local-path", "enabled": true
}]}
```

然后 **Reload Window**（插件的 MCP 是独立进程）。

## 工具

| 工具 | 参数 | 说明 |
|---|---|---|
| `render_diagram` | `svg, caption, max_width` | 手写 SVG → 图块；默认宽 520 |
| `build_image_wall` | `files, layout/cols, labels, fit, split` | 多图 → 图块（`split=true` 每格单独 PNG，可单击放大） |
| `check_diagram` | `svg` | 校验 SVG 是否符合渲染约定 |

产物默认写 `~/tmp/chat_figures/walls/`，可用 `out_path` 覆盖。

## 依赖

- Python 3 + Pillow（图片墙）
- 想让 `wall` 块显示成图块，需要 `patches/rich-report/` 里的补丁；没有补丁时输出为代码块
