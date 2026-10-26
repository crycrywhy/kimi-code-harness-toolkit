# compaction-timestamps —— 压缩后保留消息带时间戳、摘要写压缩时刻

**目标版本：kimi code for VS Code 0.8.3（2026-10-25 基线）**

> 一句话：compaction 把用户消息原样保留下来却不带时间，agent 会把三天前的消息当成"上一条"。本补丁给保留消息加 `[YYYY-MM-DD HH:MM UTC+8]` 前缀，并在摘要开头写入压缩时刻。

## 解决什么

上下文压缩（compaction）保留的历史用户消息只有正文、没有时间；摘要本身也不写"什么时候压缩的"。后果是真实发生过的：用户 10-15 说的"我准备更新到 0.8.2"被摘要写成裸事实，10-21 仍被当成当前状态。

## 做了什么

**CT-1（P1 + P2）—— 11 处锚点，全部唯一**

| 编号 | 位置 | 改动 |
|---|---|---|
| A | `WireReplayFoldState.pushHistory` | 把 wire 记录时间挂到消息对象（不可枚举属性 + WeakMap） |
| B | `replaceMessageText` | 截断/重建消息时复制时间 |
| C | `buildContextCompactionShape` 之前 | 新增 `formatWireTimeUtc8` / `stampCompactionMessage` / `stampCompactionHistory` |
| D | `keptMessages` | 保留的用户消息统一加 `[时间]\n` 前缀（幂等） |
| E | `compactionRound` 的 `historyForModel` | 送摘要的请求也带时间戳 |
| F | `buildCompactionSummaryText` | 摘要开头写 `Compaction performed at … UTC+8` |
| G/H | `contextMemoryKey` 的两个事件订阅 | 把 `e.time` 传给 fold（live 上下文走 fold，不是 `pushHistory`） |
| I/J | `foldAppendMessage` / `foldLoopEvent` | 接收并下传 `time`（原来硬编码 `void 0`） |
| K | `createImmutableFoldSink.pushMessage` | `freeze` 前给消息挂时间 |

**P2（WeakMap 兜底）**：P1 在 0.8.2 实测发现 D 段连续不生效 —— 消息对象被冻结/复制后 `Object.defineProperty` 静默失败。P2 加模块级 WeakMap（按对象身份，不受冻结影响），A/K/B/T 四处改走 `__markWireTime` / `__readWireTime`，并在压缩时把 kept/real/miss 计数写 `~/.kimi-code/cache/compaction_stamp.log`。

**CT-2（摘要正文加时间锚）**：只改给摘要模型的指令模板 `compaction_instruction_default`，加 "Time anchoring" 段，要求摘要里的事实性断言带日期。不动正文格式、不动 CT-1 的任何锚点。

## 用法

```bash
cd patches/harness/compaction-timestamps
python3 apply.py      --check   # 11 个锚点各命中 1 次（已打则 SKIP）
python3 apply.py                # 打 CT-1 P1（自动备份 + .mjs 语法闸门）
python3 apply_p2.py   --check   # 6 个 P2 锚点
python3 apply_p2.py             # 打 P2（WeakMap）
python3 apply_ct2.py  --check   # 1 处指令模板锚点
python3 apply_ct2.py            # 打 CT-2
```

脚本默认自动挑已安装的**最新** kimi-code 版本目录，可用 `KIMI_EXT=<bundle 路径>` 覆盖；均幂等、失败中止不写盘、先备份为 `*.bak-<时间>-before-*`。

**重打顺序固定**：`apply.py` → `apply_p2.py` → `apply_ct2.py`。
**注意**：打完 P2 后，`apply.py --check` 的 **B 锚点会报 `count = 0`**（P2 已经改写了 `replaceMessageText`），属正常；这也意味着**不能跳过 P2 单独重跑 P1**。

## 验证

```bash
grep -c 'Compaction performed at' dist/extension.js     # 1
grep -c '__markWireTime'          dist/extension.js     # ≥ 2（P2）
cp dist/extension.js /tmp/c.mjs && node --check /tmp/c.mjs   # 必须复制成 .mjs，直接 check .js 会假通过
```

辅助函数单测（把三个函数抽出来跑即可）：`formatWireTimeUtc8(1792045852682)` → `"2026-10-15 14:30 UTC+8"`；`stampCompactionMessage` 幂等、只给真实用户输入加前缀、system 消息与无时间消息原样返回。

## 已知限制

- 只影响**新发生**的压缩与**会话恢复时的折叠重建**；wire 里已有的旧摘要文本不追溯改写。
- 消息没有 `time` 时（极老记录或非 wire 路径）不加前缀，行为与补丁前一致。
- 每条保留消息多约 20 token，相对 `COMPACT_USER_MESSAGE_MAX_TOKENS = 20k` 可忽略。
- 与 `agents-md-router` / `stream-newline-fix` / `panel-*` 补丁无锚点重叠。
- 补丁改的是扩展安装目录里的打包产物，**上游升级即失效**，升级后按上面顺序重跑。
