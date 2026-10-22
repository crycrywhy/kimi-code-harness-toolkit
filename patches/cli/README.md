# CLI 功能补齐补丁

CLI 里已有、但 VS Code 扩展没暴露的能力，全部在宿主侧实现（不经模型）。

## 组成

| 脚本 | 作用 | 目标产物 |
|---|---|---|
| `patch_slash_wave1.py` | 接入 8 个斜杠命令：`/fork` `/undo` `/title` `/status` `/usage` `/tasks` `/skills` `/plugins`（白名单 + 分发 + handler + `/` 补全菜单四处） | `extension.js` |
| `patch_slash_view.py` | `/view`：只渲染最近 N 轮，治长会话卡顿 | `extension.js` + `package.json` |
| `apply_pj.py` | **手动给历史对话命名**：会话列表每一行加 Rename（等价 `/title`，历史会话也能改） | `webview.js` |
| `apply_pn.py` → `apply_po.py` → `apply_pp.py` → `apply_pq.py` → `apply_ps.py` → `apply_pt.py` | 权限模式按钮：加按钮 → 改下拉 → 打开会话时加载 → 点击等价发 `/auto`、`/yolo` → 链路修正 | `webview.js` |
| `fast-session-restore/` | 打开长会话时不恢复历史 subagent（`KIMI_CODE_LOAD_SUBAGENTS=true` 恢复） | `extension.js` |

## 用法

```bash
python3 patch_slash_wave1.py            # 自动定位扩展目录
python3 patch_slash_wave1.py --revert   # 回退
KIMI_DIST=/path/to/dist python3 patch_slash_wave1.py   # 手动指定
```

顺序：`slash_wave1 → slash_view → pj → pn → po → pp → pq → ps → pt → fast-session-restore`。

改动前自动备份为 `*.bak-<时间>-before-xxx`。**回退方式**：用备份覆盖目标文件
（`fast-session-restore/apply.py` 例外，自带 `--revert`）。脚本对已打过的状态会跳过或中止，不会写坏文件。
