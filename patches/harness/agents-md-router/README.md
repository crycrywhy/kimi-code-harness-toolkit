# agents-md-router

模型每步调用前按上一条工具调用自动匹配注入 AGENTS.md 对应规则节提示。

## 解决什么

在长上下文会话中，模型容易遗忘项目规范与纪律要求。本补丁在宿主发起模型请求（step）前，依据上下文意图自动精准提示应当参考的项目规范小节。目标版本：kimi code for VS Code 0.8.3（2026-10-25 基线）。

## 做了什么

在 `dist/extension.js` 中接入 7 个锚点（A–G），实现零开销规则路由：
- **规则选节**：读取 `rules.json` 中的 19 条正则规则与 2 道闸门，依据上一轮 assistant 的工具调用匹配最佳规范小节；
- **系统提示注入**：向模型请求上下文注入 `[规则路由 #n] 参考 AGENTS.md 中的 <节>`；
- **额度与告警注入**：派发 subagent 前动态检查额度状态，并在超出阈值时注入额度提醒与告警；
- **双向撤销**：完整支持 `--revert`，自动还原 A–G 锚点。

## 用法

```bash
# 检查锚点匹配状态
python3 apply.py --check

# 打补丁（自动检测最新 bundle、先备份后替换并通过 node --check 校验）
python3 apply.py

# 撤销补丁还原
python3 apply.py --revert

# 对拍校验逻辑一致性
python3 verify_parity.py
```
补丁修改的是扩展安装目录里的打包产物，上游升级后需重新执行 `apply.py`。改动后需 Reload VS Code 窗口。

## 验证

- 对拍一致性：`verify_parity.py` 针对 404 条真实用例进行逻辑对拍，达成 0 差异；
- 锚点匹配：0.8.3 bundle 上 A–G 7 个锚点唯一匹配全部命中；
- 语法门控：打补丁前后均通过 `node --check` 语法检查。

## 已知限制

- 依赖工作区内存在规范的 AGENTS.md 结构；若未命中任何规则则静默不注入；
- 扩展升级后打包产物被覆盖，需重新执行脚本打补丁。

## 关于 rules.json 的节名

`rules.json` 里的 `sections` 是**示例节名**（下载规范 / 汇报规范 / 记录规范 等），对应本机 `AGENTS.md` 的章节标题。请按你自己的 `AGENTS.md` 改写这些名字与正则；不改也能跑，只是提示会指向不存在的节。
