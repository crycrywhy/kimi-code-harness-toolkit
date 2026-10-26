#!/usr/bin/env python3
"""Kimi Code VS Code 0.7.5 — AGENTS.md 规则路由与 subagent 额度提醒注入本地补丁 (2026-10-13).

机制：
  1. IAgentReminderService (line ~224312) 在 loop.hooks.onWillBeginStep (line ~224277)
     每次模型调用 (step) 前调用注册的 provider(context)。
  2. providerContext (line ~224186) 补充 history 与 runtime 字段，以便 reminder 生产者
     获取消息历史及工具调用。
  3. 注册 reminder variant "agents_md_scope"（规则路由）：
     每步从上下文历史中寻找最近一条 assistant 工具调用并格式化为 "<toolName>: <args 摘要>"。
     采用 rules.json 定义的 19 条正则及 2 道闸门进行判定，命中后注入
     "[规则路由 #{n}] 参考 AGENTS.md 中的 {节}"。
  4. 注册 reminder variant "subagent_status"（阶段二 step 1：subagent 额度提醒）：
     (a) 派发时报额度：assistant 工具调用为 Agent(...) 时，读取 quota_state.json，
         注入 "[subagent 状态 #{n}] 派发前额度：..."，并跟踪记录派发模型与 t0；
     (b) 告警：扫描 quota_events.jsonl，当出现针对本 session 派发模型且晚于 t0 的事件时，
         注入 "[subagent 告警 #{n}] 模型 <model> 于 <HH:MM UTC+8> 报 <event>..."（本 session 去重）。
  5. 幂等：每处锚点独立幂等（已打过则报 skip）；锚点不唯一立即中止且不写文件；支持 --revert 还原。
"""
import difflib
import glob
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

DEFAULT_GLOB = str(
    Path.home() / ".vscode-server/extensions/moonshot-ai.kimi-code-*/dist/extension.js"
)
HERE = Path(__file__).resolve().parent
RULES_FILE = HERE / "rules.json"


def resolve_bundle(args):
    positional = [a for a in args if not a.startswith("--")]
    if positional:
        return Path(positional[0]).expanduser().resolve()
    candidates = [Path(p) for p in glob.glob(DEFAULT_GLOB)]
    if not candidates:
        sys.exit(f"FATAL: 没找到任何 kimi-code 扩展安装，glob={DEFAULT_GLOB}")
    candidates.sort(key=lambda p: p.stat().st_mtime)
    return candidates[-1].resolve()


def get_backup_path(bundle):
    date_str = datetime.now().strftime("%Y%m%d")
    return bundle.parent / f"{bundle.name}.bak-{date_str}-before-agents-md-router"


def build_patches(rules_data):
    ro = json.dumps(rules_data["gate_readonly"])
    wo = json.dumps(rules_data["gate_writeop"])
    sc = json.dumps(rules_data["state_changing"], ensure_ascii=False)

    rules_list = []
    for r in rules_data["rules"]:
        pat = json.dumps(r["pattern"])
        secs = json.dumps(r["sections"], ensure_ascii=False)
        rid = r["id"]
        no_gate_str = ", no_gate: true" if r.get("no_gate") else ""
        rules_list.append(
            f'\t{{ id: {rid}, sections: {secs}, pattern: new RegExp({pat}, "i"){no_gate_str} }}'
        )
    rules_str = ",\n".join(rules_list)

    # Patch A: providerContext 补充 history 和 runtime
    anchor_a = (
        "function providerContext(runtime, entry, isNewTurn) {\n"
        "\tconst history = runtime.get(IAgentContextMemoryService).get();\n"
        "\tconst injectedPositions = findInjections(history, entry.variant);\n"
        "\tconst lastInjectedAt = injectedPositions.at(-1) ?? null;\n"
        "\tconst lastInjection = lastInjectedAt === null ? void 0 : history[lastInjectedAt];\n"
        "\treturn {\n"
        "\t\tinjectedPositions,\n"
        "\t\tlastInjectedAt,\n"
        "\t\tlastInjection,\n"
        '\t\tlastDisclosure: lastInjection?.origin?.kind === "injection" ? lastInjection.origin.disclosure : void 0,\n'
        "\t\tisNewTurn\n"
        "\t};\n"
        "}"
    )
    replace_a = (
        "function providerContext(runtime, entry, isNewTurn) {\n"
        "\tconst history = runtime.get(IAgentContextMemoryService).get();\n"
        "\tconst injectedPositions = findInjections(history, entry.variant);\n"
        "\tconst lastInjectedAt = injectedPositions.at(-1) ?? null;\n"
        "\tconst lastInjection = lastInjectedAt === null ? void 0 : history[lastInjectedAt];\n"
        "\treturn {\n"
        "\t\tinjectedPositions,\n"
        "\t\tlastInjectedAt,\n"
        "\t\tlastInjection,\n"
        '\t\tlastDisclosure: lastInjection?.origin?.kind === "injection" ? lastInjection.origin.disclosure : void 0,\n'
        "\t\tisNewTurn,\n"
        "\t\thistory,\n"
        "\t\truntime\n"
        "\t};\n"
        "}"
    )

    # Patch B: 常量表、正则与辅助函数定义
    anchor_b = (
        'const DISCOVERY_REMINDER_VARIANT = "agents_md";\n'
        'const agentsMdReminderKnownKey = defineState("agentsMdReminder.known", () => /* @__PURE__ */ new Set());\n'
        'const agentsMdReminderCwdKey = defineState("agentsMdReminder.cwd", () => void 0);\n'
        'const agentsMdReminderSeededKey = defineState("agentsMdReminder.seeded", () => false);\n'
        "let AgentAgentsMdReminderService = class AgentAgentsMdReminderService extends Disposable {"
    )

    patch_b_insert = (
        "// === LOCAL PATCH: AGENTS.md rule router reminder (2026-10-13) ===\n"
        "// 源自 script/kimi-code/patches/agents-md-router/rules.json，改那里要同步重打\n"
        'const AGENTS_MD_SCOPE_VARIANT = "agents_md_scope";\n'
        "let agentsMdRouterCounter = 0;\n"
        f"const GATE_READONLY = new RegExp({ro}, \"i\");\n"
        f"const GATE_WRITEOP = new RegExp({wo});\n"
        f"const STATE_CHANGING_SECTIONS = new Set({sc});\n"
        "const AGENTS_MD_RULES = [\n"
        f"{rules_str}\n"
        "];\n\n"
        "function formatActionString(call) {\n"
        '\tif (!call) return "";\n'
        '\tconst name = call.name || call.function?.name || "";\n'
        "\tlet rawArgs = call.arguments ?? call.args ?? {};\n"
        "\tlet args = rawArgs;\n"
        '\tif (typeof rawArgs === "string") {\n'
        "\t\ttry {\n"
        "\t\t\targs = JSON.parse(rawArgs);\n"
        "\t\t} catch {\n"
        "\t\t\targs = rawArgs;\n"
        "\t\t}\n"
        "\t}\n"
        '\tif (typeof args !== "object" || args === null) {\n'
        "\t\tconst str = String(args).slice(0, 200);\n"
        '\t\treturn str ? `${name}: ${str}` : name;\n'
        "\t}\n"
        '\tfor (const key of ["command", "file_path", "path", "pattern", "query", "url"]) {\n'
        "\t\tif (key in args && args[key] !== void 0 && args[key] !== null) {\n"
        '\t\t\treturn `${name}: ${String(args[key]).slice(0, 200)}`;\n'
        "\t\t}\n"
        "\t}\n"
        "\tconst keys = Object.keys(args).slice(0, 4);\n"
        '\treturn `${name}(${keys.join(", ")})`;\n'
        "}\n\n"
        "function matchAgentsMdRule(action) {\n"
        "\tif (!action) return null;\n"
        "\tconst isReadonly = GATE_READONLY.test(action) && !GATE_WRITEOP.test(action);\n"
        "\tfor (const rule of AGENTS_MD_RULES) {\n"
        "\t\tif (rule.pattern.test(action)) {\n"
        "\t\t\tconst hasStateChanging = rule.sections.some((sec) => STATE_CHANGING_SECTIONS.has(sec));\n"
        "\t\t\tif (isReadonly && !rule.no_gate && !hasStateChanging) {\n"
        "\t\t\t\tcontinue;\n"
        "\t\t\t}\n"
        "\t\t\treturn rule.sections[0];\n"
        "\t\t}\n"
        "\t}\n"
        "\treturn null;\n"
        "}\n\n"
        "function findLastAssistantToolCall(history, lastInjectedAt) {\n"
        "\tif (!Array.isArray(history)) return null;\n"
        "\tfor (let i = history.length - 1; i >= 0; i--) {\n"
        "\t\tconst msg = history[i];\n"
        "\t\tif (!msg) continue;\n"
        '\t\tif (msg.role === "user" && msg.origin?.kind !== "injection") {\n'
        "\t\t\tbreak;\n"
        "\t\t}\n"
        '\t\tif (msg.role === "assistant" && Array.isArray(msg.toolCalls) && msg.toolCalls.length > 0) {\n'
        "\t\t\tif (lastInjectedAt !== null && lastInjectedAt !== void 0 && i <= lastInjectedAt) {\n"
        "\t\t\t\treturn null;\n"
        "\t\t\t}\n"
        "\t\t\treturn msg.toolCalls[msg.toolCalls.length - 1];\n"
        "\t\t}\n"
        "\t}\n"
        "\treturn null;\n"
        "}\n"
        "// === END LOCAL PATCH ===\n"
    )
    replace_b = patch_b_insert + anchor_b

    # Patch C: 在构造函数中注册 reminder
    anchor_c = (
        "\t\tthis.states.contributeState(agentsMdReminderKnownKey);\n"
        "\t\tthis.states.contributeState(agentsMdReminderCwdKey);\n"
        "\t\tthis.states.contributeState(agentsMdReminderSeededKey);\n"
        "\t\tthis._register(this.reminder.register(DISCOVERY_REMINDER_VARIANT, (context) => this.injectReminder(context)));"
    )
    replace_c = (
        "\t\tthis.states.contributeState(agentsMdReminderKnownKey);\n"
        "\t\tthis.states.contributeState(agentsMdReminderCwdKey);\n"
        "\t\tthis.states.contributeState(agentsMdReminderSeededKey);\n"
        "\t\tthis._register(this.reminder.register(DISCOVERY_REMINDER_VARIANT, (context) => this.injectReminder(context)));\n"
        "\t\tthis._register(this.reminder.register(AGENTS_MD_SCOPE_VARIANT, (context) => this.injectScopeReminder(context)));"
    )

    # Patch D: 实现 injectScopeReminder 方法
    anchor_d = (
        "\t\treturn {\n"
        "\t\t\tcontent: reminderText(fresh),\n"
        "\t\t\tdisclosure: [...covered, ...fresh]\n"
        "\t\t};\n"
        "\t}\n"
        "\tasync ensureSeeded() {"
    )
    replace_d = (
        "\t\treturn {\n"
        "\t\t\tcontent: reminderText(fresh),\n"
        "\t\t\tdisclosure: [...covered, ...fresh]\n"
        "\t\t};\n"
        "\t}\n"
        "\tinjectScopeReminder(context) {\n"
        "\t\ttry {\n"
        "\t\t\tconst history = context.history ?? context.runtime?.get(IAgentContextMemoryService)?.get();\n"
        "\t\t\tif (!Array.isArray(history) || history.length === 0) return void 0;\n"
        "\t\t\tconst toolCall = findLastAssistantToolCall(history, context.lastInjectedAt);\n"
        "\t\t\tif (!toolCall) return void 0;\n"
        "\t\t\tconst action = formatActionString(toolCall);\n"
        "\t\t\tconst section = matchAgentsMdRule(action);\n"
        "\t\t\tif (!section) return void 0;\n"
        "\t\t\tagentsMdRouterCounter += 1;\n"
        "\t\t\treturn `[规则路由 #${agentsMdRouterCounter}] 参考 AGENTS.md 中的 ${section}`;\n"
        "\t\t} catch {\n"
        "\t\t\treturn void 0;\n"
        "\t\t}\n"
        "\t}\n"
        "\tasync ensureSeeded() {"
    )

    # Patch E: subagent_status 常量表、缓存与辅助函数定义
    anchor_e = (
        "// === END LOCAL PATCH ===\n"
        'const DISCOVERY_REMINDER_VARIANT = "agents_md";'
    )
    patch_e_insert = (
        "// === END LOCAL PATCH ===\n"
        "// === LOCAL PATCH: subagent status reminder (2026-10-13) ===\n"
        'const SUBAGENT_STATUS_VARIANT = "subagent_status";\n'
        "let subagentStatusCounter = 0;\n"
        "let subagentAlertCounter = 0;\n\n"
        'const SUBAGENT_QUOTA_STATE_PATH = (process.env.HOME || "") + "/.kimi-code/cache/quota_state.json";\n'
        'const SUBAGENT_QUOTA_EVENTS_PATH = (process.env.HOME || "") + "/.kimi-code/cache/quota_events.jsonl";\n\n'
        "// 模型显示名与顺序：默认留空，由 config.toml 的 model 别名自动兜底。\n"
        "// 想自定义就把 {\"<provider>/<model>\": \"<短名>\"} 填进来，顺序按 SUBAGENT_DISPLAY_ORDER 排。\n"
        "const SUBAGENT_MODEL_DISPLAY_NAMES = {};\n\n"
        "const SUBAGENT_DISPLAY_ORDER = [];\n\n"
        "let subagentQuotaStateCache = { mtimeMs: 0, data: null };\n"
        "function getSubagentQuotaState() {\n"
        "\ttry {\n"
        "\t\tconst st = fsSync.statSync(SUBAGENT_QUOTA_STATE_PATH);\n"
        "\t\tif (subagentQuotaStateCache.data && subagentQuotaStateCache.mtimeMs === st.mtimeMs) {\n"
        "\t\t\treturn subagentQuotaStateCache.data;\n"
        "\t\t}\n"
        '\t\tconst text = fsSync.readFileSync(SUBAGENT_QUOTA_STATE_PATH, "utf-8");\n'
        "\t\tconst data = JSON.parse(text);\n"
        "\t\tsubagentQuotaStateCache = { mtimeMs: st.mtimeMs, data };\n"
        "\t\treturn data;\n"
        "\t} catch {\n"
        "\t\treturn null;\n"
        "\t}\n"
        "}\n\n"
        "let subagentQuotaEventsCache = { mtimeMs: 0, events: [] };\n"
        "function getSubagentQuotaEvents() {\n"
        "\ttry {\n"
        "\t\tconst st = fsSync.statSync(SUBAGENT_QUOTA_EVENTS_PATH);\n"
        "\t\tif (subagentQuotaEventsCache.mtimeMs === st.mtimeMs) {\n"
        "\t\t\treturn subagentQuotaEventsCache.events;\n"
        "\t\t}\n"
        '\t\tconst text = fsSync.readFileSync(SUBAGENT_QUOTA_EVENTS_PATH, "utf-8");\n'
        '\t\tconst lines = text.trim().split("\\n");\n'
        "\t\tconst events = [];\n"
        "\t\tfor (const line of lines) {\n"
        "\t\t\tif (!line.trim()) continue;\n"
        "\t\t\ttry {\n"
        "\t\t\t\tevents.push(JSON.parse(line));\n"
        "\t\t\t} catch {}\n"
        "\t\t}\n"
        "\t\tsubagentQuotaEventsCache = { mtimeMs: st.mtimeMs, events };\n"
        "\t\treturn events;\n"
        "\t} catch {\n"
        "\t\treturn [];\n"
        "\t}\n"
        "}\n\n"
        "function formatUtc8Time(ms) {\n"
        "\ttry {\n"
        '\t\tif (!ms || isNaN(ms)) return "未知时间";\n'
        "\t\tconst d = new Date(Number(ms) + 8 * 3600 * 1000);\n"
        '\t\tconst hh = String(d.getUTCHours()).padStart(2, "0");\n'
        '\t\tconst mm = String(d.getUTCMinutes()).padStart(2, "0");\n'
        "\t\treturn `${hh}:${mm}`;\n"
        "\t} catch {\n"
        '\t\treturn "未知时间";\n'
        "\t}\n"
        "}\n\n"
        "function formatSubagentQuotaSnapshot(state) {\n"
        '\tif (!state || typeof state !== "object") return "未知";\n'
        "\tconst keys = [...new Set([...SUBAGENT_DISPLAY_ORDER, ...Object.keys(state)])];\n"
        "\tconst parts = [];\n"
        "\tfor (const key of keys) {\n"
        "\t\tconst info = state[key];\n"
        '\t\tif (!info || typeof info !== "object") continue;\n'
        '\t\tconst label = SUBAGENT_MODEL_DISPLAY_NAMES[key] || key.split("/").pop() || key;\n'
        "\t\tif (info.available === true) {\n"
        "\t\t\tparts.push(`${label} OK`);\n"
        "\t\t} else {\n"
        '\t\t\tlet resetStr = "";\n'
        "\t\t\tif (info.reset_at_utc8) {\n"
        "\t\t\t\tconst m = String(info.reset_at_utc8).match(/(\\d{2}:\\d{2})(?::\\d{2})?/);\n"
        "\t\t\t\tif (m) resetStr = `(reset ${m[1]})`;\n"
        "\t\t\t} else if (info.reset_at) {\n"
        "\t\t\t\tresetStr = `(reset ${formatUtc8Time(info.reset_at * 1000)})`;\n"
        "\t\t\t}\n"
        "\t\t\tparts.push(`${label} DOWN${resetStr}`);\n"
        "\t\t}\n"
        "\t}\n"
        '\treturn parts.join(" / ");\n'
        "}\n\n"
        "function normalizeSubagentModel(m) {\n"
        '\tif (!m) return "";\n'
        "\tlet s = String(m).trim().toLowerCase();\n"
        "\t// 去掉 provider 前缀（形如 \"provider/model\"），只留模型名比对\n"
        '\tconst slash = s.lastIndexOf("/");\n'
        "\tif (slash >= 0) s = s.slice(slash + 1);\n"
        "\treturn s;\n"
        "}\n\n"
        "function isSubagentModelMatch(trackedModel, eventModel) {\n"
        "\tif (!trackedModel || !eventModel) return false;\n"
        '\tif (trackedModel === "(未指定)") {\n'
        "\t\tconst normEvent = normalizeSubagentModel(eventModel);\n"
        '\t\treturn normEvent.length > 0;\n'
        "\t}\n"
        "\tconst normTracked = normalizeSubagentModel(trackedModel);\n"
        "\tconst normEvent = normalizeSubagentModel(eventModel);\n"
        "\treturn normTracked === normEvent || normTracked.includes(normEvent) || normEvent.includes(normTracked);\n"
        "}\n\n"
        "function findLastAssistantAgentCalls(history, lastInjectedAt) {\n"
        "\tif (!Array.isArray(history)) return [];\n"
        "\tfor (let i = history.length - 1; i >= 0; i--) {\n"
        "\t\tconst msg = history[i];\n"
        "\t\tif (!msg) continue;\n"
        '\t\tif (msg.role === "user" && msg.origin?.kind !== "injection") {\n'
        "\t\t\tbreak;\n"
        "\t\t}\n"
        '\t\tif (msg.role === "assistant" && Array.isArray(msg.toolCalls) && msg.toolCalls.length > 0) {\n'
        "\t\t\tif (lastInjectedAt !== null && lastInjectedAt !== void 0 && i <= lastInjectedAt) {\n"
        "\t\t\t\treturn [];\n"
        "\t\t\t}\n"
        '\t\t\treturn msg.toolCalls.filter((call) => (call.name || call.function?.name) === "Agent");\n'
        "\t\t}\n"
        "\t}\n"
        "\treturn [];\n"
        "}\n"
        "// === END LOCAL PATCH: subagent status reminder ===\n"
        'const DISCOVERY_REMINDER_VARIANT = "agents_md";'
    )
    replace_e = patch_e_insert

    # Patch F: 在构造函数中注册 subagent_status reminder
    anchor_f = (
        "\t\tthis._register(this.reminder.register(AGENTS_MD_SCOPE_VARIANT, (context) => this.injectScopeReminder(context)));"
    )
    replace_f = (
        "\t\tthis._register(this.reminder.register(AGENTS_MD_SCOPE_VARIANT, (context) => this.injectScopeReminder(context)));\n"
        "\t\tthis._register(this.reminder.register(SUBAGENT_STATUS_VARIANT, (context) => this.injectSubagentReminder(context)));"
    )

    # Patch G: 实现 injectSubagentReminder 方法
    anchor_g = (
        "\t\t\treturn `[规则路由 #${agentsMdRouterCounter}] 参考 AGENTS.md 中的 ${section}`;\n"
        "\t\t} catch {\n"
        "\t\t\treturn void 0;\n"
        "\t\t}\n"
        "\t}\n"
        "\tasync ensureSeeded() {"
    )
    replace_g = (
        "\t\t\treturn `[规则路由 #${agentsMdRouterCounter}] 参考 AGENTS.md 中的 ${section}`;\n"
        "\t\t} catch {\n"
        "\t\t\treturn void 0;\n"
        "\t\t}\n"
        "\t}\n"
        "\tinjectSubagentReminder(context) {\n"
        "\t\ttry {\n"
        "\t\t\tif (!this.trackedSubagents) this.trackedSubagents = [];\n"
        "\t\t\tif (!this.seenAgentCallIds) this.seenAgentCallIds = new Set();\n"
        "\t\t\tif (!this.deliveredSubagentEventKeys) this.deliveredSubagentEventKeys = new Set();\n"
        "\t\t\tconst history = context.history ?? context.runtime?.get(IAgentContextMemoryService)?.get();\n"
        "\t\t\tlet statusMsg;\n"
        "\t\t\tlet shouldReportSnapshot = false;\n"
        "\t\t\tif (Array.isArray(history) && history.length > 0) {\n"
        "\t\t\t\tconst agentCalls = findLastAssistantAgentCalls(history, context.lastInjectedAt);\n"
        "\t\t\t\tfor (const toolCall of agentCalls) {\n"
        "\t\t\t\t\tconst callId = toolCall.id || toolCall.callId || `agent_${Date.now()}`;\n"
        "\t\t\t\t\tif (!this.seenAgentCallIds.has(callId)) {\n"
        "\t\t\t\t\t\tthis.seenAgentCallIds.add(callId);\n"
        "\t\t\t\t\t\tlet rawArgs = toolCall.arguments ?? toolCall.args ?? {};\n"
        "\t\t\t\t\t\tlet args = rawArgs;\n"
        '\t\t\t\t\t\tif (typeof rawArgs === "string") {\n'
        "\t\t\t\t\t\t\ttry {\n"
        "\t\t\t\t\t\t\t\targs = JSON.parse(rawArgs);\n"
        "\t\t\t\t\t\t\t} catch {}\n"
        "\t\t\t\t\t\t}\n"
        '\t\t\t\t\t\tconst model = (args && typeof args === "object" && args.model) ? String(args.model) : "(未指定)";\n'
        "\t\t\t\t\t\tthis.trackedSubagents.push({\n"
        "\t\t\t\t\t\t\tmodel,\n"
        "\t\t\t\t\t\t\tt0: Date.now(),\n"
        "\t\t\t\t\t\t\tid: callId\n"
        "\t\t\t\t\t\t});\n"
        "\t\t\t\t\t\tshouldReportSnapshot = true;\n"
        "\t\t\t\t\t}\n"
        "\t\t\t\t}\n"
        "\t\t\t\tif (shouldReportSnapshot) {\n"
        "\t\t\t\t\tconst quotaState = getSubagentQuotaState();\n"
        "\t\t\t\t\tif (quotaState) {\n"
        "\t\t\t\t\t\tsubagentStatusCounter += 1;\n"
        "\t\t\t\t\t\tstatusMsg = `[subagent 状态 #${subagentStatusCounter}] 派发前额度：${formatSubagentQuotaSnapshot(quotaState)}`;\n"
        "\t\t\t\t\t}\n"
        "\t\t\t\t}\n"
        "\t\t\t}\n"
        "\t\t\tconst alertLines = [];\n"
        "\t\t\tif (this.trackedSubagents.length > 0) {\n"
        "\t\t\t\tconst events = getSubagentQuotaEvents();\n"
        "\t\t\t\tfor (const ev of events) {\n"
        '\t\t\t\t\tif (!ev || typeof ev !== "object" || !ev.model || !ev.event_time) continue;\n'
        "\t\t\t\t\tconst dedupeKey = `${ev.model}|${ev.event_time}`;\n"
        "\t\t\t\t\tif (this.deliveredSubagentEventKeys.has(dedupeKey)) continue;\n"
        '\t\t\t\t\tconst evTimeMs = typeof ev.event_time === "number" ? (ev.event_time < 1e11 ? ev.event_time * 1000 : ev.event_time) : new Date(ev.event_time).getTime();\n'
        "\t\t\t\t\tif (isNaN(evTimeMs)) continue;\n"
        "\t\t\t\t\tlet matchedTracked = null;\n"
        "\t\t\t\t\tfor (let i = this.trackedSubagents.length - 1; i >= 0; i--) {\n"
        "\t\t\t\t\t\tconst tracked = this.trackedSubagents[i];\n"
        "\t\t\t\t\t\tif (isSubagentModelMatch(tracked.model, ev.model) && evTimeMs > tracked.t0) {\n"
        "\t\t\t\t\t\t\tmatchedTracked = tracked;\n"
        "\t\t\t\t\t\t\tbreak;\n"
        "\t\t\t\t\t\t}\n"
        "\t\t\t\t\t}\n"
        "\t\t\t\t\tif (!matchedTracked) continue;\n"
        "\t\t\t\t\tthis.deliveredSubagentEventKeys.add(dedupeKey);\n"
        '\t\t\t\t\tlet resetStr = "无";\n'
        "\t\t\t\t\tif (ev.quotaResetTimeStamp) {\n"
        "\t\t\t\t\t\tconst resetMs = new Date(ev.quotaResetTimeStamp).getTime();\n"
        "\t\t\t\t\t\tif (!isNaN(resetMs)) resetStr = formatUtc8Time(resetMs);\n"
        "\t\t\t\t\t} else if (ev.reset_at_utc8) {\n"
        "\t\t\t\t\t\tconst m = String(ev.reset_at_utc8).match(/(\\d{2}:\\d{2})(?::\\d{2})?/);\n"
        "\t\t\t\t\t\tif (m) resetStr = m[1];\n"
        "\t\t\t\t\t} else if (ev.reset_at) {\n"
        "\t\t\t\t\t\tresetStr = formatUtc8Time(ev.reset_at * 1000);\n"
        "\t\t\t\t\t}\n"
        '\t\t\t\t\tconst eventKind = ev.kind || ev.event || "quota_exhausted";\n'
        "\t\t\t\t\tsubagentAlertCounter += 1;\n"
        "\t\t\t\t\talertLines.push(`[subagent 告警 #${subagentAlertCounter}] 模型 ${ev.model} 于 ${formatUtc8Time(evTimeMs)} UTC+8 报 ${eventKind}（reset ${resetStr}）；受影响：你 ${formatUtc8Time(matchedTracked.t0)} 派的 subagent。处置：按回退链换模型重派 / 起 verify_recovered 恢复监视`);\n"
        "\t\t\t\t}\n"
        "\t\t\t}\n"
        "\t\t\tconst results = [];\n"
        "\t\t\tif (statusMsg) results.push(statusMsg);\n"
        "\t\t\tif (alertLines.length > 0) results.push(...alertLines);\n"
        "\t\t\tif (results.length === 0) return void 0;\n"
        '\t\t\treturn results.join("\\n");\n'
        "\t\t} catch {\n"
        "\t\t\treturn void 0;\n"
        "\t\t}\n"
        "\t}\n"
        "\tasync ensureSeeded() {"
    )

    return [
        ("A providerContext 暴露 history 与 runtime", anchor_a, replace_a, "history,\n\t\truntime\n\t};"),
        ("B 常量表/正则/辅助函数定义", anchor_b, replace_b, 'const AGENTS_MD_SCOPE_VARIANT = "agents_md_scope";'),
        ("C 注册 agents_md_scope reminder", anchor_c, replace_c, "register(AGENTS_MD_SCOPE_VARIANT"),
        ("D injectScopeReminder 方法实现", anchor_d, replace_d, "injectScopeReminder(context) {"),
        ("E subagent_status 常量表/缓存/辅助函数定义", anchor_e, replace_e, 'const SUBAGENT_STATUS_VARIANT = "subagent_status";'),
        ("F 注册 subagent_status reminder", anchor_f, replace_f, "register(SUBAGENT_STATUS_VARIANT"),
        ("G injectSubagentReminder 方法实现", anchor_g, replace_g, "injectSubagentReminder(context) {"),
    ]


def run_node_check(bundle_path):
    """真语法闸门：先 `cp` 成 `.mjs` 再 `node --check`。

    ⚠️ Node 22 对**首行含 `import` 的 `.js`**，`node --check` 会走 module-detection
    而**假通过**（直接返回 0、不解析函数体）——2026-10-14 实测：往 10.7 MB bundle 里插
    `function broken( {{{`，`node --check extension.js` 仍 exit 0；同一份改名 `.mjs`
    才报 SyntaxError。故必须先复制成 `.mjs` 再 check（口径同 fast-session-restore / stream-newline-fix）。
    """
    print("Checking bundle with `node --check` (.mjs probe)...")
    with tempfile.TemporaryDirectory(prefix="amr-check-") as tmp:
        probe = Path(tmp) / "extension.mjs"
        shutil.copyfile(bundle_path, probe)
        res = subprocess.run(["node", "--check", str(probe)], capture_output=True, text=True)
    if res.returncode != 0:
        print("ERROR: node --check failed!")
        if res.stdout:
            print("stdout:", res.stdout)
        if res.stderr:
            print("stderr:", res.stderr)
        return False
    print("node --check passed successfully (.mjs probe).")
    return True


def revert(bundle):
    backup = get_backup_path(bundle)
    if not backup.exists():
        backups = sorted(bundle.parent.glob(f"{bundle.name}.bak-*-before-agents-md-router"))
        if backups:
            backup = backups[-1]
        else:
            print(f"FATAL: 未找到任何 before-agents-md-router 备份文件 (预期: {backup})")
            return 1
    print(f"Reverting {bundle} from {backup}...")
    bundle.write_bytes(backup.read_bytes())
    print("Reverted successfully.")
    run_node_check(bundle)
    return 0


def main():
    if "--revert" in sys.argv:
        bundle = resolve_bundle(sys.argv[1:])
        return revert(bundle)

    bundle = resolve_bundle(sys.argv[1:])
    backup = get_backup_path(bundle)

    if not RULES_FILE.exists():
        print(f"FATAL: rules.json 未找到: {RULES_FILE}")
        return 2

    rules_data = json.loads(RULES_FILE.read_text(encoding="utf-8"))
    patches = build_patches(rules_data)

    print(f"Target bundle : {bundle} ({bundle.stat().st_size:,} bytes)")
    print(f"Backup target : {backup}")

    original = bundle.read_text(encoding="utf-8")
    text = original
    applied = []
    skipped = []

    for name, anchor, replacement, signature in patches:
        if signature in text:
            skipped.append(name)
            continue
        count = text.count(anchor)
        if count != 1:
            print(
                f"FATAL: 锚点 [{name}] 在 bundle 中匹配了 {count} 次 (预期恰好 1 次) — 中止操作，未修改任何文件。"
            )
            return 3
        text = text.replace(anchor, replacement, 1)
        applied.append(name)

    if not applied:
        print("Nothing to do (already patched, skip).")
        if skipped:
            print(f"Skipped (already present): {', '.join(skipped)}")
        return 0

    if not backup.exists():
        backup.write_bytes(original.encode("utf-8"))
        print(f"Backup written: {backup} ({backup.stat().st_size:,} bytes)")
    else:
        print(f"Backup already exists (kept): {backup}")

    bundle.write_text(text, encoding="utf-8")
    print(f"Patched: {bundle}")
    print(f"Applied: {', '.join(applied)}")
    if skipped:
        print(f"Skipped: {', '.join(skipped)}")

    diff = difflib.unified_diff(
        original.splitlines(), text.splitlines(), lineterm="", n=1
    )
    added = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))
    print(f"Diff: +{added} / -{removed} lines")

    if not run_node_check(bundle):
        print("FATAL: node --check failed after patching! Reverting...")
        bundle.write_bytes(backup.read_bytes())
        return 4

    return 0


if __name__ == "__main__":
    sys.exit(main())
