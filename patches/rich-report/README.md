# 图块渲染补丁

配合 `rich-report` MCP 使用：把工具产出的 `wall` 规格渲染成**可折叠图块**，
并让聊天里的 markdown 大图按容器宽度显示。

## 组成（按顺序打）

| 脚本 | 作用 |
|---|---|
| `apply_rb1.py` | `svg` 代码块 → 内联 SVG（净化 + viewBox 固有宽上限） |
| `apply_rb2.py` | 工具结果里的裸 SVG 画在卡片上（折叠区外） |
| `apply_rb3.py` | 工具卡片里的 SVG 撑满容器宽度并居中（`fill` 模式） |
| `apply_img_full.py` | markdown 图片：大图按容器全宽、小图保持缩略图 |
| `apply_img_grid.py` / `apply_img_grid2.py` | 同段多图排网格（被图块取代，保留兼容） |
| `apply_wall_block.py` … `apply_wall_block6.py` | `wall` 代码块 → 图块（CSS Grid、标题栏、折叠） |
| `apply_wall_step.py` | 纯文本 step 里有 `wall` 块时也画时间线轨道 |
| `apply_wall_icon.py` / `apply_wall_icon2.py` | 图块图标（后者把图标简化为 xy 轴 + 三根实心柱） |
| `apply_wall_svg.py` | 图块支持内联手写 SVG（文字可选中、不抢点击） |
| `apply_wall_card.py` | 工具卡片遇到 `wall` 规格时整卡替换成图块 |
| `apply_qd6.py` | 图片绝对路径 → 工作区相对（扩展侧） |

## 说明

- 全部脚本支持 `--revert`，并在改动前备份为 `*.bak-<时间>-before-*`。
- 目标版本 0.8.2；锚点不匹配会直接退出。
