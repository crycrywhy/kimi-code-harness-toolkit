# CLI 补丁集（slash-wave1 / slash-view）

把 CLI 已有、VS Code 扩展未暴露的能力接入宿主侧，包含 8 个斜杠命令与 `/view` 轮数控制。

## 解决什么

VS Code 扩展缺失 CLI 具备的关键命令：`/fork /undo /title /status /usage /tasks /skills /plugins` 无法使用，不在白名单的命令会被当普通文本发给模型；且打开长会话时 DOM 全量渲染无轮数限制导致严重卡顿。目标版本：kimi code for VS Code 0.8.3（2026-10-25 基线）。

## 做了什么

- **slash-wave1（`patch_slash_wave1.py`）**：
  在 `dist/extension.js` 接入 8 个斜杠命令的白名单、分发、宿主 handler 实现（不走模型），并向 webview 的 `/` 补全菜单登记说明。0.8.3 重打 +262 行、4 个锚点全中，自带测试 15/15。
- **slash-view（`patch_slash_view.py`）**：
  接入 `/view` 命令与配置项，将回放轮数限制（默认 20）传递给引擎 `resumeSession({replayTurnLimit})`。0.8.3 重打 +81 行并在 `package.json` 注册 `kimi.replayTurnLimit` 配置项。
- **历史命名与权限模式**：
  收录既有 `apply_pj.py`（会话列表每一行加 Rename 按钮）与 `apply_pn.py`–`pt.py`（权限模式切换按钮与下拉）。
- **fast-session-restore**：
  历史会话打开加速子目录（见 `fast-session-restore/README.md`）。

## 用法

```bash
# 依次执行打补丁（推荐先 slash-wave1 再 slash-view）
python3 patch_slash_wave1.py
python3 patch_slash_view.py

# 亦可显式指定 bundle 路径
python3 patch_slash_wave1.py ~/.vscode-server/extensions/moonshot-ai.kimi-code-0.8.3-linux-x64/dist/extension.js
```
脚本支持显式指定 bundle 路径，具备幂等检查（已打过自动 skip，锚点不唯一立即中止），写入前自动生成时间戳备份。补丁改动的是扩展安装目录里的打包产物，上游升级后需重新打补丁。改动后需 Reload VS Code 窗口。

## 验证

- `patch_slash_wave1.py`：4 个锚点唯一命中，执行后 `node --check` 语法通过，命令可在 webview 补全列表显示；
- `patch_slash_view.py`：6 处锚点全中，`package.json` 配置项生效，输入 `/view 10` 可截断渲染最近 10 轮；
- 幂等复跑：再次运行各脚本均报 skip，原文件逐字节不变。

## 已知限制

- `/view` 限制的是 GUI 回放渲染层，底层 session 数据文件依然完整；
- 会话重命名作用于列表里的任意会话（含历史），不必先进入该会话；
- 扩展升级后打包产物被覆盖，需重新执行脚本打补丁。
