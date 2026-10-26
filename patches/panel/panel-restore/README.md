# panel-restore

Reload 窗口后 Kimi Code 面板原位恢复并找回原会话。

## 解决什么

上游未注册 `WebviewPanelSerializer` 且未持久化任何面板状态，导致 VS Code Reload Window 后所有已打开的面板全部消失，必须重新开 tab 并从侧边栏翻找历史会话。目标版本：kimi code for VS Code 0.8.3（2026-10-25 基线）。

## 做了什么

共涉及 14 处编辑（`extension.js` 12 处 + `webview.js` 1 处 + `package.json` 1 处）：
- **状态记录**：`createPanel` 时向 `workspaceState.kimi.panelSessions` 记录 `{key, sessionId, title}`（最多 12 条，串行写队列防并发丢失）；
- **HTML/Webview 注入**：面板 HTML 注入 `window.__kimiPanelKey`，webview 启动时通过 `vscode.setState` 持久化 key；
- **反序列化器注册**：注册 `WebviewPanelSerializer("kimiPanel")`，Reload 后按 key → 标题 → 会话标题反查三级匹配找回会话；
- **回放通路对齐**：webview 挂载后宿主补发 `openSession` 事件，走与侧边栏点击完全相同的 `loadSessionHistory → store.loadSession` 回放路径；
- **防误删保护**：Reload 触发的 dispose 实行延迟 30 秒删除机制，防止 Reload 瞬间抹掉待恢复记录；
- **激活声明**：`package.json` 增加 `activationEvents` 声明 `onWebviewPanel:kimiPanel`。

## 用法

```bash
# 进入补丁目录执行
python3 patch_panel_restore.py

# 指定扩展目录路径
python3 patch_panel_restore.py ~/.vscode-server/extensions/moonshot-ai.kimi-code-0.8.3-linux-x64
```
脚本执行前会对 `extension.js`、`webview.js` 与 `package.json` 分别备份。补丁改动的是扩展安装目录里的打包产物，上游升级后需重新打补丁。改动后需 Reload VS Code 窗口生效。

## 验证

- 语法与格式：`node --check` 两个 bundle 校验通过，`package.json` JSON 校验通过；
- 幂等性：已打过再次运行全部报 skip，原文件逐字节一致；
- 实测效果：Reload 后各 tab 面板保留在原工作区位置，并自动回放原会话内容。

## 已知限制

- 补丁安装前已经打开但未持久化 state 的面板，首次 Reload 会显示空白，在 tab 中点一次历史会话即可正常纳入持久化；
- 状态记录按工作区保存在 `workspaceState`，跨工作区不互通；
- 若会话已在磁盘被删除，恢复时保留面板但不加载不存在的会话内容；
- 扩展升级后打包产物被覆盖，需重新执行脚本。
