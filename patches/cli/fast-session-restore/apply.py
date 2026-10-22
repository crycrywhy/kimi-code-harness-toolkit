#!/usr/bin/env python3
"""Kimi Code VS Code 0.7.5 — fast session restore, **v3** (<DATE>, local).

治「打开历史 session 极卡」。**v3 = v1 + E1..E6 六条编辑**（数据见 README）：

  v1 做了什么
    A. 子 agent 按需加载（`resumedSessionSummary`）：只 fold「最近 N 轮被引用到的」subagent。
       取不到引用集合时退「按 wire mtime 最新的 12 个」；整段异常时退「加载全部」。
    B. main fold 只读头尾（`foldAgentWireReplay`）：>4 MB 的 wire 只读 头 4 KB + 尾 4 MB。

  v2（E1..E3）改了什么（只动常量与 fail-open 方向，逻辑不变）
    1. `FAST_RESTORE_FALLBACK_SUBAGENTS: 12 -> 0` ⇒ 取不到真引用集合时一个都不加载。
    2. `pickNewestSubagentIds(..., count<=0) => []`：兜底「开关+上限」＝一个常量，默认关。
    3. fail-open 方向反转：`resumedSessionSummary` 里 `catch` / `!Array.isArray` 两处由
       回退 `subagentIds`（加载全部）改为回退 `[]`（宁可少加载，不崩）。
       安全性依据：v1 引擎自己的 `resumeSessionResult(..., includeSubagents = false)` 默认就不
       加载 subagent（extension.js:166099），webview 侧 `buildSubagentReplayIndex` 对缺失的
       子 agent 直接 `continue`（extension.js:277282）。

  v3（E4..E6）追加了什么 —— 一律「顶部常量 + 一处门控」，都有回改办法
    E4. `FAST_RESTORE_LOAD_SUBAGENTS = false`（总开关）：resume 时**一个 subagent 都不加载**。
        门控点在锚点 A 分支内（不改上游那两个 `includeSubagents: true` 调用点）：
        `if (FAST_RESTORE_LOAD_SUBAGENTS) try { …selectRecentSubagentIds… }`。
        false ⇒ 既不 select、也不递归 fold、也不 `create()`（循环体为空数组）；不报错。
        收益（本会话实测）：省掉 8 个 subagent 各自**一整份**阶段 1 restore（12.3 MB / 7,693 条）
        + 8 次阶段 2 fold + 8 次递归 fold。回改：置 `true` 即恢复「按需加载」。
    E5. `FAST_RESTORE_MAX_AGENT_DEPTH: 2 -> 1`：不再递归找孙级。实测本会话孙级 0 个、
        递归白花 ≈116 ms parse + 8 次 fold。回改：置 2 即恢复递归。
    E6. `FAST_RESTORE_TAIL_BYTES: 4 MiB -> 8 MiB`：4 MiB 尾部在本会话只折出 **18** 个用户轮
        边界（UI 少渲染约 2 轮）；8 MiB 折出 **52** 个 ⇒ 最近 20 轮的切片与全量读**完全一致**
        （386 条记录 / 11 次 Agent 调用 / 9 个 id）。代价见 README（≈2× 该阶段读+parse）。

**未做**（数据判定不可行 / 不在文本补丁能力内，见 README）：
  * 「跳过最后一次 compaction 之前的记录」——**不等价**，故不实现（证据：上下文重建依赖
    被跳过的前缀，见 README「compaction 边界」节）。
  * 子 agent 完全懒加载——需要 webview + 新 RPC，文本补丁做不到（只给设计评估）。

⚠️ 上线纪律：本包必须**最后**打；打完之后**不得再 revert 其它包**（它依赖 v1 的文本锚点，
   且别的包的 `--revert` 会按备份整文件回滚，把本包一起抹掉）。

用法：
    python3 apply.py --check [bundle]              # 只读：v1/v2/v3 状态 + 每条编辑命中计数
    python3 apply.py          [bundle]             # 打（或升级到）v3；幂等；先备份；node --check 失败自动回滚
    python3 apply.py --revert [bundle]             # 撤销本包**全部**编辑 ⇒ 逐字节回到 v1
    python3 apply.py --revert --group v3 [bundle]  # 只撤销 E4..E6 ⇒ 逐字节回到 v2
    python3 apply.py --revert-backup [bundle]      # 整文件回滚到本包最近一次备份

bundle 缺省 = 最新安装的 `~/.vscode-server/extensions/moonshot-ai.kimi-code-*/dist/extension.js`。
"""
import difflib
import glob
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

DEFAULT_GLOB = str(
    Path.home() / ".vscode-server/extensions/moonshot-ai.kimi-code-*/dist/extension.js"
)
PATCH_ID = "fast-session-restore"
VERSION = 3
CST = timezone(timedelta(hours=8))

# =====================================================================================
# PART 1 — v1 的原始替换（只在 bundle 里还没有 v1 时用；从干净基线一步到 v2 走这条路）
# =====================================================================================

# ---------------------------------------------------------------- 锚点 A（子 agent 按需加载）
A_ANCHOR = "\n".join([
    '\t\tif (replay?.includeSubagents === true) {',
    '\t\t\tconst agentsDir = join(handle.accessor.get(ISessionContext).sessionDir, "agents");',
    '\t\t\tlet subagentIds = [];',
    '\t\t\ttry {',
    '\t\t\t\tsubagentIds = (await readdir$1(agentsDir, { withFileTypes: true })).filter((entry) => entry.isDirectory() && entry.name !== "main").map((entry) => entry.name);',
    '\t\t\t} catch {}',
    '\t\t\tfor (const agentId of subagentIds) try {',
    '\t\t\t\tawait handle.accessor.get(IAgentLifecycleService).create({ agentId });',
    '\t\t\t\tagents[agentId] = await this.resumedAgentState(handle, handle.accessor.get(IAgentLifecycleService).handleOf(agentId), "sub", replay.replayTurnLimit);',
    '\t\t\t} catch {}',
    '\t\t}',
])

A_SIGNATURE = "selectRecentSubagentIds(agentsDir, subagentIds"

A_REPLACE = "\n".join([
    '\t\tif (replay?.includeSubagents === true) {',
    '\t\t\tconst agentsDir = join(handle.accessor.get(ISessionContext).sessionDir, "agents");',
    '\t\t\tlet subagentIds = [];',
    '\t\t\ttry {',
    '\t\t\t\tsubagentIds = (await readdir$1(agentsDir, { withFileTypes: true })).filter((entry) => entry.isDirectory() && entry.name !== "main").map((entry) => entry.name);',
    '\t\t\t} catch {}',
    '\t\t\t// === LOCAL PATCH: fast session restore — subagent on-demand load (<DATE>, local) ===',
    '\t\t\t// 只加载最近 N 轮真正引用到的 subagent；任何异常都回退「全部加载」（fail-open）。',
    '\t\t\tlet selectedSubagentIds = subagentIds;',
    '\t\t\ttry {',
    '\t\t\t\tselectedSubagentIds = await selectRecentSubagentIds(agentsDir, subagentIds, agents[MAIN_AGENT_ID$1]?.replay, replay?.replayTurnLimit);',
    '\t\t\t} catch {',
    '\t\t\t\tselectedSubagentIds = subagentIds;',
    '\t\t\t}',
    '\t\t\tif (!Array.isArray(selectedSubagentIds)) selectedSubagentIds = subagentIds;',
    '\t\t\t// === END LOCAL PATCH: fast session restore — subagent on-demand load ===',
    '\t\t\tfor (const agentId of selectedSubagentIds) try {',
    '\t\t\t\tawait handle.accessor.get(IAgentLifecycleService).create({ agentId });',
    '\t\t\t\tagents[agentId] = await this.resumedAgentState(handle, handle.accessor.get(IAgentLifecycleService).handleOf(agentId), "sub", replay.replayTurnLimit);',
    '\t\t\t} catch {}',
    '\t\t}',
])

# ---------------------------------------------------------------- 锚点 B（main fold 只读头尾）
B_ANCHOR = "\n".join([
    "async function foldAgentWireReplay(wirePath) {",
    "\ttry {",
    '\t\tconst records = parseWireRecords(await readFile(wirePath, "utf-8"));',
    "\t\tif (records.length === 0) return EMPTY_FOLD;",
    "\t\tconst agent = new Agent$4({",
    "\t\t\tkaos: await LocalKaos.create(),",
    "\t\t\tpersistence: new ReadOnlyAgentRecordPersistence(records),",
    '\t\t\ttype: "sub"',
    "\t\t});",
    "\t\tawait agent.resume({ rewriteMigratedRecords: false });",
    "\t\treturn {",
    "\t\t\treplay: agent.replayBuilder.buildResult(),",
    "\t\t\ttoolStore: agent.tools.storeData()",
    "\t\t};",
    "\t} catch {",
    "\t\treturn EMPTY_FOLD;",
    "\t}",
    "}",
])

B_SIGNATURE = "function readWireRecordsHeadTail(wirePath)"

B_HELPERS = "\n".join([
    "// === LOCAL PATCH: fast session restore (<DATE>, local) ===",
    "// A. 子 agent 按需加载：只 materialize 最近 N 轮引用到的 subagent。",
    "// B. 大 wire 只读头尾折叠：不为渲染最近切片而整读几十 MB 的 wire.jsonl。",
    "const FAST_RESTORE_DEFAULT_TURNS = 20;",
    "const FAST_RESTORE_HEAD_BYTES = 4096;",
    "const FAST_RESTORE_TAIL_BYTES = 4 * 1024 * 1024;",
    "const FAST_RESTORE_MORE_TAIL_BYTES = 16 * 1024 * 1024;",
    "const FAST_RESTORE_WIRE_THRESHOLD = 4 * 1024 * 1024;",
    "const FAST_RESTORE_FALLBACK_SUBAGENTS = 12;",
    "const FAST_RESTORE_MAX_AGENT_DEPTH = 2;",
    "const FAST_RESTORE_TURN_BOUNDARY_TYPES = /* @__PURE__ */ new Set([\"turn.prompt\", \"turn_begin\"]);",
    "/** N：KIMI_CODE_REPLAY_TURNS > 设置 kimi.replayTurnLimit（复用 #3 slash-view 的读取器）> 20。 */",
    "function readFastRestoreTurnLimit() {",
    "\ttry {",
    '\t\tif (typeof process !== "undefined" && process.env !== void 0) {',
    "\t\t\tconst raw = process.env.KIMI_CODE_REPLAY_TURNS;",
    '\t\t\tif (raw !== void 0 && String(raw).trim().length > 0) {',
    "\t\t\t\tconst parsed = Number.parseInt(String(raw).trim(), 10);",
    "\t\t\t\tif (Number.isSafeInteger(parsed) && parsed >= 0) return parsed;",
    "\t\t\t}",
    "\t\t}",
    "\t} catch {}",
    "\ttry {",
    '\t\tif (typeof readReplayTurnLimitSetting === "function") return readReplayTurnLimitSetting();',
    "\t} catch {}",
    "\ttry {",
    '\t\tconst value = getConfig().get("replayTurnLimit", FAST_RESTORE_DEFAULT_TURNS);',
    '\t\tif (typeof value === "number" && Number.isInteger(value) && value >= 0) return value;',
    "\t} catch {}",
    "\treturn FAST_RESTORE_DEFAULT_TURNS;",
    "}",
    "/** 给 limitAgentReplayByTurns 的限值：0 表示「不限」⇒ undefined（与 replayTurnLimitForResume 同口径）。 */",
    "function fastRestoreSliceLimit(engineReplayTurnLimit) {",
    '\tif (typeof engineReplayTurnLimit === "number" && Number.isInteger(engineReplayTurnLimit) && engineReplayTurnLimit > 0) return engineReplayTurnLimit;',
    "\tconst turns = readFastRestoreTurnLimit();",
    "\treturn turns > 0 ? turns : void 0;",
    "}",
    "/** 一条回放记录的可见文本（tool 结果可能是字符串或 part 数组）。 */",
    "function fastRestoreMessageText(message) {",
    "\tconst content = message?.content;",
    '\tif (typeof content === "string") return content;',
    '\tif (!Array.isArray(content)) return "";',
    '\tlet text = "";',
    '\tfor (const part of content) if (part && typeof part.text === "string") text += part.text + "\\n";',
    "\treturn text;",
    "}",
    "/** 从一段回放记录里收集「被引用到的 subagent id」：Agent 工具调用的结果文本含 `agent_id: X`。 */",
    "function collectReferencedAgentIds(records) {",
    "\tconst found = /* @__PURE__ */ new Set();",
    "\tif (!Array.isArray(records) || records.length === 0) return found;",
    "\tconst agentCallIds = /* @__PURE__ */ new Set();",
    "\tfor (const record of records) {",
    '\t\tconst message = record?.type === "message" ? record.message : void 0;',
    '\t\tif (!message || message.role !== "assistant" || !Array.isArray(message.toolCalls)) continue;',
    "\t\tfor (const call of message.toolCalls) {",
    "\t\t\tconst name = call?.name ?? call?.function?.name;",
    '\t\t\tif (name !== "Agent") continue;',
    "\t\t\tconst id = call?.id ?? call?.toolCallId;",
    "\t\t\tif (id !== void 0 && id !== null) agentCallIds.add(id);",
    "\t\t}",
    "\t}",
    "\tif (agentCallIds.size === 0) return found;",
    "\tfor (const record of records) {",
    '\t\tconst message = record?.type === "message" ? record.message : void 0;',
    '\t\tif (!message || message.role !== "tool") continue;',
    "\t\tif (!agentCallIds.has(message.toolCallId)) continue;",
    "\t\tconst match = /agent_id:\\s*([A-Za-z0-9_.:-]+)/.exec(fastRestoreMessageText(message));",
    "\t\tif (match) found.add(match[1]);",
    "\t}",
    "\treturn found;",
    "}",
    "/** 取不到引用集合时的保守回退：按 wire.jsonl mtime 最新的 K 个。 */",
    "function pickNewestSubagentIds(agentsDir, subagentIds, count) {",
    "\ttry {",
    "\t\treturn subagentIds.map((name) => {",
    "\t\t\tlet mtimeMs = 0;",
    "\t\t\ttry {",
    '\t\t\t\tmtimeMs = fsSync.statSync(join(agentsDir, name, "wire.jsonl")).mtimeMs;',
    "\t\t\t} catch {}",
    "\t\t\treturn { name, mtimeMs };",
    "\t\t}).sort((a, b) => b.mtimeMs - a.mtimeMs).slice(0, count).map((entry) => entry.name);",
    "\t} catch {",
    "\t\treturn subagentIds;",
    "\t}",
    "}",
    "/** 选定要加载的 subagent：最近 N 轮引用到的 + 浅递归一层（子 agent 再派生的孙级）。 */",
    "async function selectRecentSubagentIds(agentsDir, subagentIds, mainReplay, engineReplayTurnLimit) {",
    "\tif (!Array.isArray(subagentIds) || subagentIds.length === 0) return subagentIds;",
    "\tconst sliceLimit = fastRestoreSliceLimit(engineReplayTurnLimit);",
    "\tlet directIds;",
    "\ttry {",
    "\t\tdirectIds = collectReferencedAgentIds(limitAgentReplayByTurns(Array.isArray(mainReplay) ? mainReplay : [], sliceLimit));",
    "\t} catch {",
    "\t\treturn subagentIds;",
    "\t}",
    "\tif (directIds.size === 0) return pickNewestSubagentIds(agentsDir, subagentIds, FAST_RESTORE_FALLBACK_SUBAGENTS);",
    "\tconst onDisk = /* @__PURE__ */ new Set(subagentIds);",
    "\tconst selected = /* @__PURE__ */ new Set();",
    "\tconst queue = [];",
    "\tfor (const agentId of directIds) {",
    '\t\tif (agentId === "main" || !onDisk.has(agentId) || selected.has(agentId)) continue;',
    "\t\tselected.add(agentId);",
    "\t\tqueue.push({ agentId, depth: 1 });",
    "\t}",
    "\tif (selected.size === 0) return pickNewestSubagentIds(agentsDir, subagentIds, FAST_RESTORE_FALLBACK_SUBAGENTS);",
    "\twhile (queue.length > 0) {",
    "\t\tconst { agentId, depth } = queue.shift();",
    "\t\tif (depth >= FAST_RESTORE_MAX_AGENT_DEPTH) continue;",
    "\t\tlet childReplay = [];",
    "\t\ttry {",
    '\t\t\tconst folded = await foldAgentWireReplay(join(agentsDir, agentId, "wire.jsonl"));',
    '\t\t\tchildReplay = limitAgentReplayByTurns(folded?.replay ?? [], sliceLimit);',
    "\t\t} catch {",
    "\t\t\tcontinue;",
    "\t\t}",
    "\t\tfor (const subId of collectReferencedAgentIds(childReplay)) {",
    '\t\t\tif (subId === "main" || !onDisk.has(subId) || selected.has(subId)) continue;',
    "\t\t\tselected.add(subId);",
    "\t\t\tqueue.push({ agentId: subId, depth: depth + 1 });",
    "\t\t}",
    "\t}",
    "\treturn [...selected];",
    "}",
    "/** 尾部从第一个完整 turn 边界起；找不到边界返回 -1（由调用方决定加大尾巴 / 回退全量读）。 */",
    "function findFastRestoreTurnBoundary(text) {",
    '\tlet offset = 0;',
    '\tfor (const line of text.split("\\n")) {',
    "\t\tconst trimmed = line.trim();",
    "\t\tif (trimmed.length > 0) {",
    "\t\t\ttry {",
    '\t\t\t\tif (FAST_RESTORE_TURN_BOUNDARY_TYPES.has(JSON.parse(trimmed)?.type)) return offset;',
    "\t\t\t} catch {}",
    "\t\t}",
    "\t\toffset += line.length + 1;",
    "\t}",
    "\treturn -1;",
    "}",
    "/** 头 4 KB + 尾 tailBytes，拼成同一记录序列；不可对齐 / 头部丢 metadata ⇒ 返回 void 0（回退全量读）。 */",
    "function readWireHeadTailOnce(wirePath, size, tailBytes) {",
    "\tlet fd;",
    "\ttry {",
    '\t\tfd = fsSync.openSync(wirePath, "r");',
    "\t\tconst headLen = Math.min(FAST_RESTORE_HEAD_BYTES, size);",
    "\t\tconst headBuffer = Buffer.allocUnsafe(headLen);",
    "\t\tfsSync.readSync(fd, headBuffer, 0, headLen, 0);",
    "\t\tconst tailLen = Math.min(tailBytes, size);",
    "\t\tconst tailBuffer = Buffer.allocUnsafe(tailLen);",
    "\t\tfsSync.readSync(fd, tailBuffer, 0, tailLen, size - tailLen);",
    "\t\tconst headText = headBuffer.toString(\"utf-8\");",
    '\t\tconst headCut = headText.lastIndexOf("\\n");',
    "\t\tif (headCut <= 0) return void 0;",
    '\t\tconst headRecords = parseWireRecords(headText.slice(0, headCut + 1));',
    '\t\tif (headRecords.length === 0 || headRecords[0]?.type !== "metadata") return void 0;',
    "\t\tconst tailText = tailBuffer.toString(\"utf-8\");",
    "\t\tconst tailOffset = findFastRestoreTurnBoundary(tailText);",
    "\t\tif (tailOffset < 0) return void 0;",
    "\t\tconst tailRecords = parseWireRecords(tailText.slice(tailOffset));",
    "\t\tif (tailRecords.length === 0) return void 0;",
    "\t\treturn headRecords.concat(tailRecords);",
    "\t} catch {",
    "\t\treturn void 0;",
    "\t} finally {",
    '\t\tif (fd !== void 0) try { fsSync.closeSync(fd); } catch {}',
    "\t}",
    "}",
    "/** 大 wire 的头尾读取；小文件或无法对齐返回 void 0 ⇒ 调用方走原样全量读。 */",
    "function readWireRecordsHeadTail(wirePath) {",
    "\tlet size = 0;",
    "\ttry {",
    "\t\tsize = fsSync.statSync(wirePath).size;",
    "\t} catch {",
    "\t\treturn void 0;",
    "\t}",
    "\tif (!(size > FAST_RESTORE_WIRE_THRESHOLD)) return void 0;",
    "\tfor (const tailBytes of [FAST_RESTORE_TAIL_BYTES, FAST_RESTORE_MORE_TAIL_BYTES]) {",
    "\t\tconst records = readWireHeadTailOnce(wirePath, size, Math.min(tailBytes, size));",
    "\t\tif (records !== void 0) return records;",
    "\t}",
    "\treturn void 0;",
    "}",
    "// === END LOCAL PATCH: fast session restore ===",
    "async function foldAgentWireReplay(wirePath) {",
    "\tconst foldRecords = async (records) => {",
    "\t\tif (records.length === 0) return EMPTY_FOLD;",
    "\t\tconst agent = new Agent$4({",
    "\t\t\tkaos: await LocalKaos.create(),",
    "\t\t\tpersistence: new ReadOnlyAgentRecordPersistence(records),",
    '\t\t\ttype: "sub"',
    "\t\t});",
    "\t\tawait agent.resume({ rewriteMigratedRecords: false });",
    "\t\treturn {",
    "\t\t\treplay: agent.replayBuilder.buildResult(),",
    "\t\t\ttoolStore: agent.tools.storeData()",
    "\t\t};",
    "\t};",
    "\t// === LOCAL PATCH: fast session restore — head+tail fold (<DATE>, local) ===",
    "\t// 大 wire 先试头尾折叠；抛错则落到下面的全量读（fail-open）。小 wire 永不进这条路径。",
    "\ttry {",
    "\t\tconst partial = readWireRecordsHeadTail(wirePath);",
    "\t\tif (partial !== void 0) return await foldRecords(partial);",
    "\t} catch {}",
    "\t// === END LOCAL PATCH: fast session restore — head+tail fold ===",
    "\ttry {",
    '\t\treturn await foldRecords(parseWireRecords(await readFile(wirePath, "utf-8")));',
    "\t} catch {",
    "\t\treturn EMPTY_FOLD;",
    "\t}",
    "}",
])

V1_PATCHES = [
    ("A 子 agent 按需加载", A_ANCHOR, A_REPLACE, A_SIGNATURE),
    ("B main fold 只读头尾（含常量/辅助函数块）", B_ANCHOR, B_HELPERS, B_SIGNATURE),
]

# =====================================================================================
# PART 2 — 升级编辑 (前像 -> 后像)，逐条独立可逆；`group` 用于 --revert --group
# =====================================================================================

V2_EDITS = [
    (
        "v2", "E1 兜底常量 12 -> 0",
        "const FAST_RESTORE_FALLBACK_SUBAGENTS = 12;",
        "// v2: 0 = 「取不到真引用集合」时一个 subagent 都不加载（主屏 subagent 卡片仍由父级 replay 提供）。\n"
        "const FAST_RESTORE_FALLBACK_SUBAGENTS = 0;",
    ),
    (
        "v2", "E2 pickNewestSubagentIds 尊重 count<=0",
        "/** 取不到引用集合时的保守回退：按 wire.jsonl mtime 最新的 K 个。 */\n"
        "function pickNewestSubagentIds(agentsDir, subagentIds, count) {\n"
        "\ttry {",
        "/** 取不到引用集合时的回退：按 wire.jsonl mtime 最新的 K 个；K <= 0（v2 默认）时返回空 = 不加载。 */\n"
        "function pickNewestSubagentIds(agentsDir, subagentIds, count) {\n"
        "\tif (!(count > 0)) return [];\n"
        "\ttry {",
    ),
    (
        "v2", "E3 fail-open 方向反转（异常/取不到 -> 不加载，而非加载全部）",
        "\t\t\t// 只加载最近 N 轮真正引用到的 subagent；任何异常都回退「全部加载」（fail-open）。\n"
        "\t\t\tlet selectedSubagentIds = subagentIds;\n"
        "\t\t\ttry {\n"
        "\t\t\t\tselectedSubagentIds = await selectRecentSubagentIds(agentsDir, subagentIds, agents[MAIN_AGENT_ID$1]?.replay, replay?.replayTurnLimit);\n"
        "\t\t\t} catch {\n"
        "\t\t\t\tselectedSubagentIds = subagentIds;\n"
        "\t\t\t}\n"
        "\t\t\tif (!Array.isArray(selectedSubagentIds)) selectedSubagentIds = subagentIds;",
        "\t\t\t// v2：只加载最近 N 轮真正引用到的 subagent；取不到引用集合或任何异常时**不加载**任何 subagent\n"
        "\t\t\t// （宁可少加载，UI 不崩：主屏 subagent 卡片来自父级 replay，缺项被 buildSubagentReplayIndex 跳过）。\n"
        "\t\t\tlet selectedSubagentIds = [];\n"
        "\t\t\ttry {\n"
        "\t\t\t\tselectedSubagentIds = await selectRecentSubagentIds(agentsDir, subagentIds, agents[MAIN_AGENT_ID$1]?.replay, replay?.replayTurnLimit);\n"
        "\t\t\t} catch {\n"
        "\t\t\t\tselectedSubagentIds = [];\n"
        "\t\t\t}\n"
        "\t\t\tif (!Array.isArray(selectedSubagentIds)) selectedSubagentIds = [];",
    ),
    # ---------------------------------------------------------------- v3（E4..E6）
    (
        "v3", "E4-1 新增总开关常量 FAST_RESTORE_LOAD_SUBAGENTS = false",
        "// v2: 0 = 「取不到真引用集合」时一个 subagent 都不加载（主屏 subagent 卡片仍由父级 replay 提供）。\n"
        "const FAST_RESTORE_FALLBACK_SUBAGENTS = 0;",
        "// v2: 0 = 「取不到真引用集合」时一个 subagent 都不加载（主屏 subagent 卡片仍由父级 replay 提供）。\n"
        "const FAST_RESTORE_FALLBACK_SUBAGENTS = 0;\n"
        "// v3/E4 总开关：false = resume 时**一个 subagent 都不加载**（最省）。依据：引擎 v1 resume 默认\n"
        "// includeSubagents=false（extension.js:166099），webview buildSubagentReplayIndex 对缺失子 agent\n"
        "// 直接 continue（extension.js:277282）⇒ 不崩、不白屏，只是主屏不再内联展开子 agent 步骤流。\n"
        "const FAST_RESTORE_LOAD_SUBAGENTS = false;",
    ),
    (
        "v3", "E4-2 门控 selectRecentSubagentIds（false 时连选择/递归 fold 都不做）",
        "\t\t\t// v2：只加载最近 N 轮真正引用到的 subagent；取不到引用集合或任何异常时**不加载**任何 subagent\n"
        "\t\t\t// （宁可少加载，UI 不崩：主屏 subagent 卡片来自父级 replay，缺项被 buildSubagentReplayIndex 跳过）。\n"
        "\t\t\tlet selectedSubagentIds = [];\n"
        "\t\t\ttry {\n"
        "\t\t\t\tselectedSubagentIds = await selectRecentSubagentIds(agentsDir, subagentIds, agents[MAIN_AGENT_ID$1]?.replay, replay?.replayTurnLimit);\n"
        "\t\t\t} catch {\n"
        "\t\t\t\tselectedSubagentIds = [];\n"
        "\t\t\t}",
        "\t\t\t// v2：只加载最近 N 轮真正引用到的 subagent；取不到引用集合或任何异常时**不加载**任何 subagent\n"
        "\t\t\t// （宁可少加载，UI 不崩：主屏 subagent 卡片来自父级 replay，缺项被 buildSubagentReplayIndex 跳过）。\n"
        "\t\t\t// v3/E4：FAST_RESTORE_LOAD_SUBAGENTS=false（默认）时**短路**——不 select、不递归 fold、不 create()。\n"
        "\t\t\tlet selectedSubagentIds = [];\n"
        "\t\t\tif (FAST_RESTORE_LOAD_SUBAGENTS) try {\n"
        "\t\t\t\tselectedSubagentIds = await selectRecentSubagentIds(agentsDir, subagentIds, agents[MAIN_AGENT_ID$1]?.replay, replay?.replayTurnLimit);\n"
        "\t\t\t} catch {\n"
        "\t\t\t\tselectedSubagentIds = [];\n"
        "\t\t\t}",
    ),
    (
        "v3", "E5 递归深度 2 -> 1（不找孙级）",
        "const FAST_RESTORE_MAX_AGENT_DEPTH = 2;",
        "// v3/E5：1 = 不递归找孙级（实测本会话孙级 0 个、白花 ≈116 ms parse + 8 次 fold）；要恢复递归改回 2。\n"
        "const FAST_RESTORE_MAX_AGENT_DEPTH = 1;",
    ),
    (
        "v3", "E6 尾部读取窗口 4 MiB -> 8 MiB（覆盖最近 20 轮）",
        "const FAST_RESTORE_TAIL_BYTES = 4 * 1024 * 1024;",
        "// v3/E6：8 MiB = 实测能覆盖最近 20 轮（4 MiB 只折出 18 个用户轮边界 ⇒ UI 少渲染约 2 轮；\n"
        "// 8 MiB 折出 52 个，最近 20 轮切片与全量读一致）。代价 ≈2× 该阶段读+parse，见 README。\n"
        "const FAST_RESTORE_TAIL_BYTES = 8 * 1024 * 1024;",
    ),
]

SIG_V1 = "FAST_RESTORE_FALLBACK_SUBAGENTS = 12"
SIG_V2 = "FAST_RESTORE_FALLBACK_SUBAGENTS = 0"
SIG_V3 = "FAST_RESTORE_LOAD_SUBAGENTS = false"
V2_MARKER = "// v2: 0 = 「取不到真引用集合」时一个 subagent 都不加载"
V3_MARKER = "// v3/E4 总开关：false = resume 时**一个 subagent 都不加载**（最省）"


# ---------------------------------------------------------------- helpers
def resolve_bundle(args):
    skip_next = False
    positional = []
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg == "--group":
            skip_next = True
            continue
        if not arg.startswith("--"):
            positional.append(arg)
    if positional:
        return Path(positional[0]).expanduser().resolve()
    candidates = [Path(p) for p in glob.glob(DEFAULT_GLOB)]
    if not candidates:
        sys.exit("FATAL: 没找到任何 kimi-code 扩展安装，glob=%s" % DEFAULT_GLOB)
    candidates.sort(key=lambda p: p.stat().st_mtime)
    return candidates[-1].resolve()


def backup_path_for(bundle, tag):
    stamp = datetime.now(CST).strftime("%Y%m%d-%H%M%S")
    return bundle.parent / ("%s.bak-%s-before-%s" % (bundle.name, stamp, tag))


def run_node_check(bundle):
    """Real syntax gate. `node --check` on an `import`-leading `.js` returns 0
    **without parsing the body**; a `.mjs` probe forces the real parser."""
    print("node --check %s ..." % bundle)
    try:
        with tempfile.TemporaryDirectory(prefix="fast-restore-check-") as tmp:
            probe = Path(tmp) / "extension.mjs"
            shutil.copyfile(bundle, probe)
            res = subprocess.run(["node", "--check", str(probe)], capture_output=True, text=True)
    except FileNotFoundError:
        print("node --check: SKIPPED (node not on PATH)")
        return True
    if res.returncode != 0:
        print("ERROR: node --check failed!")
        if res.stdout:
            print("stdout:", res.stdout)
        if res.stderr:
            print("stderr:", res.stderr)
        return False
    print("node --check: OK (via .mjs probe)")
    return True


def state_of(text):
    if SIG_V3 in text and V3_MARKER in text:
        return "v3"
    if SIG_V2 in text and V2_MARKER in text:
        return "v2"
    if SIG_V1 in text:
        return "v1"
    if B_SIGNATURE in text or A_SIGNATURE in text:
        return "partial"
    return "none"


def do_check(bundle):
    if not bundle.exists():
        print("FATAL: bundle 不存在: %s" % bundle)
        return 2
    text = bundle.read_text(encoding="utf-8")
    print("bundle  : %s (%d bytes)" % (bundle, len(text.encode("utf-8"))))
    print("patch id: %s   v%d" % (PATCH_ID, VERSION))
    print("当前状态: %s" % state_of(text))
    for name, anchor, _repl, signature in V1_PATCHES:
        print("  [v1 %-40s] anchor×%d  v1-signature=%-5s" % (name, text.count(anchor), signature in text))
    for group, label, old, new in V2_EDITS:
        print("  [%s %-46s] pre×%d  post×%d" % (group, label, text.count(old), text.count(new)))
    st = state_of(text)
    if st == "v3":
        print("结论：v3 已打进 bundle（幂等，重跑 apply 只报 skip）。")
    elif st == "v2":
        print("结论：v2 已打、v3 未打 —— 可直接 apply 升级到 v3。")
    elif st == "v1":
        print("结论：v1 已打、v2/v3 未打 —— 可直接 apply 升级到 v3。")
    elif st == "none":
        print("结论：未打任何版本 —— apply 会先打 v1 再升到 v3。")
    else:
        print("结论：状态异常（部分特征存在），请人工核对。")
    return 0


def apply_v1(text):
    """把 v1 的替换打进去（只在 signature 缺失时）。返回 (text, applied_names)。"""
    applied = []
    for name, anchor, replacement, signature in V1_PATCHES:
        if signature in text:
            continue
        count = text.count(anchor)
        if count != 1:
            raise RuntimeError("锚点 [%s] 在 bundle 中匹配 %d 次（预期恰好 1 次）" % (name, count))
        text = text.replace(anchor, replacement, 1)
        applied.append(name)
    return text, applied


def apply_edits(text, group=None):
    """按列表顺序应用编辑（后像已在文本里就跳过 ⇒ 幂等）。"""
    applied = []
    for grp, label, old, new in V2_EDITS:
        if group is not None and grp != group:
            continue
        if new in text:
            continue
        count = text.count(old)
        if count != 1:
            raise RuntimeError("编辑 [%s] 在 bundle 中匹配 %d 次（预期恰好 1 次）" % (label, count))
        text = text.replace(old, new, 1)
        applied.append(label)
    return text, applied


def revert_edits(text, group=None):
    """逆序反向替换（逐字节回到上一版本文本）。group 限定只退某一批（v2 / v3）。"""
    undone = []
    for grp, label, old, new in reversed(V2_EDITS):
        if group is not None and grp != group:
            continue
        if new not in text:
            continue
        count = text.count(new)
        if count != 1:
            raise RuntimeError("回滚 [%s] 匹配 %d 次（预期恰好 1 次）" % (label, count))
        text = text.replace(new, old, 1)
        undone.append(label)
    return text, undone


def _group_of(argv):
    if "--group" not in argv:
        return None
    index = argv.index("--group")
    if index + 1 >= len(argv):
        sys.exit("FATAL: --group 后面要跟 v2 或 v3")
    value = argv[index + 1]
    if value not in ("v2", "v3"):
        sys.exit("FATAL: --group 只接受 v2 / v3（收到 %r）" % value)
    return value


def do_revert(bundle, group=None):
    if not bundle.exists():
        print("FATAL: bundle 不存在: %s" % bundle)
        return 2
    text = bundle.read_text(encoding="utf-8")
    if state_of(text) not in ("v2", "v3"):
        print("Nothing to do (本包编辑不在 bundle 里，无需回滚).  当前状态: %s" % state_of(text))
        return 0
    backup = backup_path_for(bundle, "%s-v%d" % (PATCH_ID, VERSION))
    backup.write_bytes(text.encode("utf-8"))
    print("Backup written (回滚前状态 %s): %s" % (state_of(text), backup))
    text, undone = revert_edits(text, group)
    bundle.write_text(text, encoding="utf-8")
    print("Reverted (group=%s) -> %s : %s" % (group or "all", state_of(text), ", ".join(undone)))
    if not run_node_check(bundle):
        print("FATAL: node --check failed after revert! Restoring from backup...")
        bundle.write_bytes(backup.read_bytes())
        return 4
    return 0


def do_revert_backup(bundle):
    found = sorted(bundle.parent.glob("%s.bak-*-before-%s*" % (bundle.name, PATCH_ID)))
    if not found:
        print("FATAL: 未找到本包备份（%s.bak-*-before-%s*）" % (bundle.name, PATCH_ID))
        return 2
    backup = found[-1]
    print("Revert %s\n  from %s" % (bundle, backup))
    bundle.write_bytes(backup.read_bytes())
    print("Reverted (byte-copy). 现在状态: %s" % state_of(bundle.read_text(encoding="utf-8")))
    return 0 if run_node_check(bundle) else 4


def main():
    argv = sys.argv[1:]
    bundle = resolve_bundle(argv)
    group = _group_of(argv)

    if "--revert-backup" in argv:
        return do_revert_backup(bundle)
    if "--revert" in argv:
        return do_revert(bundle, group)
    if "--check" in argv:
        return do_check(bundle)

    if not bundle.exists():
        print("FATAL: bundle 不存在: %s" % bundle)
        return 2

    original = bundle.read_text(encoding="utf-8")
    if state_of(original) == "v3":
        print("Nothing to do (already v3, skip).")
        return 0

    try:
        text, v1_applied = apply_v1(original)
        text, edits_applied = apply_edits(text, group)
    except RuntimeError as error:
        print("FATAL: %s — 中止，未写任何文件。" % error)
        return 3

    if not edits_applied:
        print("Nothing to do (already v%d, skip)." % VERSION)
        return 0

    diff = difflib.unified_diff(original.splitlines(), text.splitlines(), lineterm="", n=1)
    added = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))

    backup = backup_path_for(bundle, "%s-v%d" % (PATCH_ID, VERSION))
    backup.write_bytes(original.encode("utf-8"))
    print("Backup written: %s (%d bytes, 状态=%s)" % (backup, backup.stat().st_size, state_of(original)))

    bundle.write_text(text, encoding="utf-8")
    print("Patched: %s   (now: %s)" % (bundle, state_of(text)))
    if v1_applied:
        print("Applied v1: %s" % ", ".join(v1_applied))
    print("Applied edits (%s): %s" % (group or "all", ", ".join(edits_applied)))
    print("Diff: +%d / -%d lines" % (added, removed))

    if not run_node_check(bundle):
        print("FATAL: node --check failed after patching! Rolling back from backup...")
        bundle.write_bytes(backup.read_bytes())
        print("Rolled back to original.")
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
