# reload-overlay

按下 Restart Extension Host 后，旧面板在约 2.5–3 秒内变成 `KIMI / Reloading…` 覆盖层，而不是定格在最后一帧旧内容。

## 解决什么

宿主重启时 `deactivate()` **不会被调用**（实测全天日志 0 条 `Kimi Code deactivating`），宿主侧来不及切画面；旧标签页会一直显示静态旧内容，直到被新面板替换，观感像"卡死"。

## 做了什么

在 `webview.js` 的 IIFE 末尾注入约 1.6 KB 心跳逻辑（注入点在 `panel-restore` 的片段之后，互不冲突）：

- 每 **800 ms** 发一次 `checkWorkspace` RPC（宿主侧只返回 `hasWorkspace/path/workspaceRoot`，零成本）；
- 单次 **900 ms** 无响应计一次 miss，**连续 3 次**才判死；
- 两道豁免防误报：页面加载后 **15 秒**内不判死（覆盖启动与历史回放高峰）、`isStreaming` 为真时跳过；
- 判定宿主已死后铺一层 `position:fixed` 覆盖层：`KIMI` 字标 + 蓝色进度条动画 + `Reloading…`；任意一次心跳成功立即撤掉。

## 用法

```bash
python3 patch_panel_reload_overlay.py     # 幂等；已打过会 skip
```

只改 `webview.js`，**Reload Window** 后生效。

## 验证

- `.mjs` 复制后 `node --check` 通过；
- 实测：Restart Extension Host → 旧标签约 2.5–3 s 出现 Reloading → 新面板接管；新面板加载时不再反复闪（v1 的 2 次 × 700 ms 阈值会在回放卡顿时误报，用户形容"抽疯"）。

## 已知限制

- 打开标签页 **15 秒内**就重启宿主时不会出现 Reloading（被宽限期豁免），属可接受的取舍。
- 心跳只依赖 `checkWorkspace` 的可用性；若将来该 RPC 改名需同步改这里。
- 补丁改的是扩展安装目录里的打包产物，上游升级即失效，重跑脚本。
