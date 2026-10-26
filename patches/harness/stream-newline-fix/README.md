# stream-newline-fix

净化上游流式载荷中的碎片换行与异常空格，规范 Markdown 结构。

## 解决什么

部分模型（如 DeepSeek 系）在流式输出中会注入异常碎换行、撕裂表格行内文本或将标题与正文粘连。本补丁在流式出口挂载双工序清洗管道，消除流式乱码与格式撕裂。目标版本：kimi code for VS Code 0.8.3（2026-10-25 基线）。

## 做了什么

补丁采用双工序管道（`v7.3.2`）并在四大适配器（OpenAI Chat、Anthropic、GoogleGenAI、OpenAI Responses）出口接入，共 19 项编辑：
- **工序一（newline_fix.js）**：清洗流式碎片换行。保住 `##` 标题、表格、列表前的合法空行，吃掉表格单元格内部的注入换行；单流独立状态仓隔离（`__NLST`）；
- **工序二（md_normalize.js）**：Markdown 结构规范器。修复块首粘连、标题劈裂、表格缺分隔行、行内标记闭合审计与 CJK 间注入空格删除；
- **模型门控**：默认只对匹配 `/deepseek/i` 的模型武装，K3 及其他模型逐字节原样透传；
- **环境变量控制**：支持 `KIMI_NL_FIX=0`、`KIMI_MD_FIX=0` 分别单独停用各工序。

## 用法

```bash
# 检查 19 项锚点状态与补丁签名
python3 apply.py --check

# 打补丁（自动备份并执行 node --check 校验）
python3 apply.py

# 回滚补丁
python3 apply.py --revert
```
支持 `--file <path>` 手动指定 bundle。补丁修改的是扩展安装目录里的打包产物，上游升级后需重新执行 `apply.py`。改动后需 Reload VS Code 窗口。

## 验证

- 锚点自证：`python3 apply.py --check` 验证 19 项编辑的 sig 计数全部为 1，确认补丁完整打入；
- 语法门控：打补丁过程自动通过 `node --check` 语法校验；
- 噪声指标监测：配套 `check_noise_forms.py` 监测分片头换行占比，健康流指标稳定趋近 0%。

## 已知限制

- 仅对流式输出管道有效，非流式输出不走此链路；
- 状态仓按请求流隔离，跨流不保留上下文；
- 扩展升级后打包产物被覆盖，需重新执行脚本打补丁。
