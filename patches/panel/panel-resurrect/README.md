# panel-resurrect

让 `kimi.openInTab` 开出的面板在 **Restart Extension Host** 之后原位重建并自动打开原会话。

## 解决什么

Reload Window 靠 `WebviewPanelSerializer` 能原地复活；但 **Restart Extension Host 不会重放编辑器、也不会调用序列化器**（实测日志 `sawDeserialize=false`），而且 `deactivate()` 根本不会被调用。结果是旧标签页留在原处但**已断链**，点不动、发不出去，每重启一次还多留一个。

## 做了什么

15 处编辑（`extension.js`）：

- **restart / reload 判定**：`workspaceState.lastActivationSessionId` 与 `vscode.env.sessionId` 比较（相同=宿主重启，不同=窗口重载）。这是 Claude Code 同款手法（其 `R04` 函数）。
- **序列化器前移**：`registerWebviewPanelSerializer("kimiPanel")` 提到 `activate()` 第一行，闭包等待 `provider` 就绪；窗口重载时因此能**原地**复活（日志 `serializer invoked`）。
- **兜底重建**：确认是宿主重启、且本宿主还没恢复出面板时，5 秒后按 `workspaceState.kimi.panelSessions` 逐条重建，**保留标题与列位**（记录里有 `viewColumn`），sessionId 进 `pendingPanelOpens` 自动打开原会话。
- **清死标签**：用 `vscode.window.tabGroups` 找 `TabInputWebview` 且 **`viewType.includes("kimiPanel")`** 的残留标签（VS Code 报的是 `mainThreadWebview-kimiPanel`，全等比较会 0 命中），建完新面板后 `tabGroups.close()` 掉它们。
- **打点**：`[panel-restore] activate / serializer invoked / resurrect check / stale kimi tabs / close stale tabs` 全写进 Kimi Code 输出通道，便于排障。

## 用法

```bash
python3 patch_panel_resurrect.py            # 自动挑最新版本目录
python3 patch_panel_resurrect.py --check    # 只看锚点（脚本内置幂等：已打过自动 skip）
```

**必须在本仓库的 `panel-restore` 之后执行**（它依赖 `workspaceState.kimi.panelSessions` 记录与 `panels` 表）。改动后需 **Reload Window** 生效。

## 验证

- 日志：窗口重载 → `serializer invoked (title=…)`、`sawDeserialize=true panels=1`；宿主重启 → `restart=true sawDeserialize=false`、`stale kimi tabs=N`、`close stale tabs -> true`。
- 实测：重启后面板落回原列、标题不变、会话自动打开，不再累积重复标签。

## 已知限制

- VS Code 没有"把断链 webview 重新挂回新宿主"的 API ⇒ 这里做的是**同列同标题替换**（先建后关），不是复用同一个 webview 实例。
- 若某次 Reload 只还原了部分面板，未被认领的记录会在 5 秒后被补建（概率低，正常还原路径会置 `sawPanelDeserialize` 直接跳过）。
- 补丁改的是扩展安装目录里的打包产物，上游升级即失效，重跑脚本。

## v4 / v5 修正（2026-10-26 当日迭代）

- **v4**：原实现用 `workspaceState.lastActivationSessionId` vs `vscode.env.sessionId` 判断"宿主重启"，但**从另一台客户端（PC2）连同一远端时会新开窗口 + 新 sessionId** ⇒ 判定必然 false，补丁在那边完全不生效。改为只要「本次没走序列化器 + 本窗口无面板 + 有记录」就补建；防误开靠 `panels.size` 与"关面板 30 秒后才删记录"。
- **v5**：面板记录**按客户端分桶** —— 键从 `kimi.panelSessions` 改为 `kimi.panelSessions.${clientScopeKey}`，scope 优先取 `SSH_CONNECTION`/`SSH_CLIENT`（远程时每台客户端不同），退化 `vscode.env.machineId`。这样 PC1 只记 PC1 上次关闭的面板、PC2 只记 PC2 的，互不覆盖（借鉴 Claude Code 把面板↔会话映射存在 `workspaceState`、各窗口各一份的做法）。
