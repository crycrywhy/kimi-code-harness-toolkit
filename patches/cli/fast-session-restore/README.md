# fast-session-restore

历史会话极速打开，默认不折叠加载历史 subagent 事件。

## 解决什么

打开包含大量子任务的历史长会话时，扩展默认把该会话下所有 subagent 的 wire 事件全量回放，导致几万个事件全量折叠，会话打开极度卡顿（数十兆会话耗时数分钟）。目标版本：kimi code for VS Code 0.8.3（2026-10-25 基线）。

## 做了什么

0.8.3 上游重写了会话回放折叠机制，旧版头尾截断逻辑作废。本补丁（v4）改造 `dist/extension.js`：
- **门控下沉**：在 resume 入口将加载条件改造为 `includeSubagents === true && KIMI_CODE_LOAD_SUBAGENTS === "true"`；
- **默认轻量**：默认打开历史会话时完全跳过历史 subagent 的读取与折叠，长会话恢复实现秒开；
- **按需恢复**：若用户确实需要查看历史 subagent 详情，启动 VS Code 时设置环境变量 `KIMI_CODE_LOAD_SUBAGENTS=true` 即可全量恢复。

## 用法

```bash
# 检查锚点现状
python3 apply.py --check

# 打补丁（自动备份并执行校验）
python3 apply.py

# 回退补丁
python3 apply.py --revert
```
补丁修改的是扩展安装目录里的打包产物，上游升级后需重新执行 `apply.py`。改动后需 Reload VS Code 窗口。

## 验证

- 自检脚本：运行 `python3 selfcheck.py` 校验 bundle 中门控签名与锚点；
- 回归测试：`test_fast_restore.mjs`、`test_v2_selection.mjs`、`test_v3_no_subagents.mjs` 测试套件全部通过；
- 实测效果：数十兆含大量 subagent 的历史会话由原来的卡顿等待缩短至秒级呈现。

## 已知限制

- 默认状态下历史会话中不展示旧 subagent 的详细折叠气泡；如需查看必须显式设置环境变量 `KIMI_CODE_LOAD_SUBAGENTS=true`；
- 该门控仅作用于历史会话恢复阶段，不影响当前会话内新派发的 subagent 正常执行与展示；
- 扩展升级后打包产物被覆盖，需重新执行脚本打补丁。

