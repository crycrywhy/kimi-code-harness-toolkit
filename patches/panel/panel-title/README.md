# panel-title

面板标签页跟随会话标题显示，对齐 Claude Code 标签页行为。

## 解决什么

`createPanel` 写死标题为 "Kimi Code"，且全库没有第二处 `panel.title` 赋值。用户切换会话、重命名会话或新建会话时，标签页标题始终不变，多个标签页难以分辨。目标版本：kimi code for VS Code 0.8.3（2026-10-25 基线）。

## 做了什么

为面板构建 `panels` 表并实现 `setPanelTitle()`，在 bundle（`dist/extension.js`）共 11 处编辑：
- **E1–E3**：`KimiWebviewProvider` 新增 `panels` 映射表，`createPanel` 登记，`BridgeHandler` 暴露 `ctx.setPanelTitle`；
- **E4**：`loadKimiSessionHistory` 切换会话时按 id 刷新标题；
- **E5**：`setSessionTitle` 重命名时同步（0.8.3 该 handler 重复两次，两处均匹配替换）；
- **E6, E10**：`resetSession` 与 `NewConversation` 广播时回退默认标题；
- **E7**：`streamChat` 收尾强制现查（首轮由模型自动生成的标题可及时感知）；
- **E8–E9**：新增 `refreshPanelTitle` 助手，本地 `/title` 命令同步；
- **E11**：带 `sessionId` 的续聊请求同步；空标题统一回退 "Kimi Code"（超长截断 120 字符）。

## 用法

```bash
# 进入补丁目录执行（自动查找最新扩展目录并打补丁）
python3 patch_panel_title.py

# 指定扩展 bundle 路径
python3 patch_panel_title.py ~/.vscode-server/extensions/moonshot-ai.kimi-code-0.8.3-linux-x64/dist/extension.js
```
脚本具备严格幂等性，已打过会自动跳过；锚点计数异常时立即中止不落盘。补丁改动的是扩展安装目录里的打包产物，上游升级后需重新执行脚本打补丁。改动后需 Reload VS Code 窗口生效。

## 验证

- 语法校验：脚本内置 `node --check` 语法门控，未通过不写入原文件；
- 模块载入：支持使用 vscode 命名空间桩完整加载 bundle 并验证 `activate/deactivate`；
- 运行时行为：0.8.3 实测开出多个 tab 分别切入不同会话，各标签页实时显示对应会话标题。

## 已知限制

- 仅作用于 `kimi.openInTab` 开出的独立面板，侧边栏视图（webview view）无独立 `panel.title` 仍为固定名；
- 同一会话被多个面板打开时，重命名目前仅刷新发起操作的那个面板；
- 扩展升级后打包产物被覆盖，需重新执行脚本。
