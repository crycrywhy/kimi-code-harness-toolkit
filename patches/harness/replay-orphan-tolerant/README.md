# replay-orphan-tolerant

回放折叠器遇到孤儿事件时跳过而非抛出异常，防止历史会话打开空白。

## 解决什么

上游在 turn 中断（如 user_cancelled）路径上偶尔漏写 `step.begin`，但后续的 `content.part` 或 `tool.call` 仍正常落盘。回放折叠器原本对此执行 hard throw（`throw new Error(...)`），导致异常被外层 swallow 为空回放（`EMPTY_FOLD`），出现历史会话打开空白、`/view` 彻底失灵的症状。目标版本：kimi code for VS Code 0.8.3（2026-10-25 基线）。

## 做了什么

在 `dist/extension.js` 的 `WireReplayFoldState.appendLoopEvent` 做了 2 处容错（E1/E2）：
- **E1**：`case "content.part"` 遇到未知 `stepUuid` 时由 `throw new Error(...)` 改为 `return;` 静默跳过；
- **E2**：`case "tool.call"` 遇到未知 `stepUuid` 时同样由 `throw new Error(...)` 改为 `return;` 静默跳过。

改动后对齐折叠器已有的 `case "tool.result"` 与 `case "step.end"` 宽容标准，残缺记录不再导致整个会话不可读，只跳过坏记录本身。

## 用法

```bash
# 检查当前锚点状态与是否已打补丁
python3 apply.py --check

# 打补丁（自动查找最新扩展并执行备份与 node --check 校验）
python3 apply.py

# 撤销补丁还原修改
python3 apply.py --revert
```
脚本支持显式传 bundle 路径。补丁修改的是扩展安装目录里的打包产物，上游升级后需重新执行 `apply.py`。改动后需 Reload VS Code 窗口。

## 验证

- 真实脏数据回放：使用含孤儿事件的 wire 样本，打补丁前折叠抛异常返回 0 条，打补丁后正常回放出数百上千条记录；
- 全量会话无回归：对几百个历史会话 wire 运行，0 异常抛出；
- 语法与状态检查：`apply.py --check` 确认 2 处 throw 均被替换为 return，`node --check` 校验通过。

## 已知限制

- 本补丁为读取折叠端的防御性容错，上游在中断路径漏写 `step.begin` 的写入瑕疵仍存在，但不影响正常使用；
- 仅影响单次有损的中断记录，正常事件流依然按原语义完整折叠解析；
- 扩展升级后打包产物被覆盖，需重新执行脚本。
