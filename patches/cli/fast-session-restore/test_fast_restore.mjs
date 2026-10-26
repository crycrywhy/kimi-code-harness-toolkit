// Functional self-test for the code fast-session-restore inserts into the bundle.
//
// It extracts the inserted helper block *verbatim* from a PATCHED extension.js,
// stubs the bundle-scope collaborators it references, and exercises the logic.
//
// Usage:
//   node test_fast_restore.mjs <patched-extension.js> [<real-wire.jsonl>]
//
// <real-wire.jsonl> is optional: when given (and existing), the head+tail reader
// is run against a real journal, which is where the interesting numbers come from.
// Reference (2026-10-14, 78 MB session — measured on
// session_991b87e5-c106-4719-9865-590fd2f714d9/agents/main/wire.jsonl):
//   81,769,023 B / 41,398 records; the head+tail reader returns 3,696 of them.

import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const START = "// === LOCAL PATCH: fast session restore (2026-10-14) ===";
const END = "// === END LOCAL PATCH: fast session restore ===";

const bundlePath = process.argv[2];
const wirePath = process.argv[3];
if (!bundlePath || !fs.existsSync(bundlePath)) {
  console.error(`usage: node test_fast_restore.mjs <patched-extension.js> [<real-wire.jsonl>]`);
  process.exit(2);
}
const src = fs.readFileSync(bundlePath, "utf-8");
if (!src.includes(START) || !src.includes(END)) {
  console.error(`FATAL: ${bundlePath} does not contain the fast-session-restore marker block (apply the patch first).`);
  process.exit(2);
}
const block = src.slice(src.indexOf(START), src.indexOf(END));
const tmpBlock = path.join(os.tmpdir(), `fast_restore_block_${process.pid}.js`);
fs.writeFileSync(tmpBlock, block, "utf-8");

// --- stubs for the bundle-scope collaborators the block references ----------
const fsSync = {
  statSync: (p) => fs.statSync(p),
  openSync: (p, m) => fs.openSync(p, m),
  readSync: (fd, buf, off, len, pos) => fs.readSync(fd, buf, off, len, pos),
  closeSync: (fd) => fs.closeSync(fd),
};
const join = (...p) => path.join(...p);
function parseWireRecords(content) {
  const lines = content.split("\n");
  const records = [];
  for (const [index, rawLine] of lines.entries()) {
    const line = rawLine.endsWith("\r") ? rawLine.slice(0, -1) : rawLine;
    if (line.length === 0) continue;
    try { records.push(JSON.parse(line)); }
    catch (error) { if (index === lines.length - 1) break; throw error; }
  }
  return records;
}
function isAgentReplayUserTurnRecord(record) {
  if (record.type !== "message") return false;
  const { message } = record;
  if (message.role !== "user") return false;
  switch (message.origin?.kind) {
    case void 0: case "user": return true;
    case "skill_activation": return message.origin.trigger === "user-slash";
    case "plugin_command": return message.origin.trigger === "user-slash";
    case "shell_command": return message.origin.phase === "input";
    default: return false;
  }
}
function limitAgentReplayByTurns(records, maxTurns) {
  if (maxTurns === void 0) return records;
  if (maxTurns <= 0) return [];
  const turnStarts = records.flatMap((r, i) => (isAgentReplayUserTurnRecord(r) ? [i] : []));
  if (turnStarts.length <= maxTurns) return records;
  return records.slice(turnStarts[turnStarts.length - maxTurns]);
}
async function foldAgentWireReplay() { return { replay: [], toolStore: {} }; }
function getConfig() { return { get: (_k, d) => d }; }

const exportSrc = block + "\nreturn { FAST_RESTORE_FALLBACK_SUBAGENTS, FAST_RESTORE_TAIL_BYTES, FAST_RESTORE_MORE_TAIL_BYTES, readFastRestoreTurnLimit, fastRestoreSliceLimit, collectReferencedAgentIds, readWireRecordsHeadTail, findFastRestoreTurnBoundary, pickNewestSubagentIds, selectRecentSubagentIds };";
const api = new Function("fsSync", "join", "parseWireRecords", "limitAgentReplayByTurns", "foldAgentWireReplay", "getConfig", exportSrc)(
  fsSync, join, parseWireRecords, limitAgentReplayByTurns, foldAgentWireReplay, getConfig);

// ---------------------------------------------------------------- assertions
const results = [];
function ok(name, cond, extra = "") { results.push([name, !!cond, extra]); }

// turn-boundary finder
const tb = '{"type":"metadata","a":1}\n{"type":"content.part"}\n{"type":"turn.prompt"}\n{"type":"context.append_message"}\n';
const off = api.findFastRestoreTurnBoundary(tb);
ok("findFastRestoreTurnBoundary finds the turn.prompt offset", tb.slice(off).startsWith('{"type":"turn.prompt"}'), `offset=${off}`);
ok("findFastRestoreTurnBoundary returns -1 when absent", api.findFastRestoreTurnBoundary('{"type":"metadata"}\n{"type":"x"}\n') === -1);

// reference extraction
const synthetic = [
  { type: "message", message: { role: "user", content: [{ type: "text", text: "hi" }], origin: { kind: "user" } } },
  { type: "message", message: { role: "assistant", content: [], toolCalls: [
    { type: "function", id: "tool_A", name: "Agent", arguments: "{}" },
    { type: "function", id: "tool_B", name: "Bash", arguments: "{}" },
  ] } },
  { type: "message", message: { role: "tool", toolCallId: "tool_A", toolCalls: [], content: [{ type: "text", text: "agent_id: agent-42\nstatus: completed\n" }] } },
  { type: "message", message: { role: "tool", toolCallId: "tool_B", toolCalls: [], content: [{ type: "text", text: "agent_id: agent-999 (Bash output, must NOT count)" }] } },
];
const refs = api.collectReferencedAgentIds(synthetic);
ok("collectReferencedAgentIds picks agent-42 from the Agent result", refs.has("agent-42"), [...refs].join(","));
ok("collectReferencedAgentIds ignores a non-Agent tool result mentioning agent_id", !refs.has("agent-999"), [...refs].join(","));

// N resolution
process.env.KIMI_CODE_REPLAY_TURNS = "7";
ok("readFastRestoreTurnLimit honours env KIMI_CODE_REPLAY_TURNS", api.readFastRestoreTurnLimit() === 7);
process.env.KIMI_CODE_REPLAY_TURNS = "0";
ok("env 0 => slice limit undefined (unlimited)", api.fastRestoreSliceLimit(undefined) === undefined);
delete process.env.KIMI_CODE_REPLAY_TURNS;
ok("no env, no setting => default 20", api.readFastRestoreTurnLimit() === 20);
process.env.KIMI_CODE_REPLAY_TURNS = "abc";
ok("garbage env ignored => default 20", api.readFastRestoreTurnLimit() === 20);
delete process.env.KIMI_CODE_REPLAY_TURNS;
ok("explicit engine limit wins", api.fastRestoreSliceLimit(5) === 5);

// small file -> caller keeps the original full read
const smallPath = path.join(os.tmpdir(), `fast_restore_small_${process.pid}.jsonl`);
fs.writeFileSync(smallPath, '{"type":"metadata"}\n{"type":"turn.prompt"}\n');
ok("small file => readWireRecordsHeadTail returns undefined", api.readWireRecordsHeadTail(smallPath) === undefined);

// head+tail reader on a real journal
if (wirePath && fs.existsSync(wirePath)) {
  const recs = api.readWireRecordsHeadTail(wirePath);
  const size = fs.statSync(wirePath).size;
  // mirror of isAgentReplayUserTurnRecord, applied to wire records: a user-turn
  // boundary is a `context.append_message` whose message is a real user turn.
  const isUserTurnRecord = (r) => {
    if (!r || r.type !== "context.append_message") return false;
    const m = r.message;
    if (!m || m.role !== "user") return false;
    const k = m.origin?.kind;
    if (k === undefined || k === "user") return true;
    if (k === "skill_activation" || k === "plugin_command") return m.origin.trigger === "user-slash";
    if (k === "shell_command") return m.origin.phase === "input";
    if (k === "system_trigger") return m.origin.name === "goal_continuation";
    return false;
  };
  let fullCount = 0;
  let fullTurns = 0;
  {
    const fd = fs.openSync(wirePath, "r");
    const CH = 32 * 1024 * 1024; let pos = 0, carry = "";
    while (pos < size) {
      const len = Math.min(CH, size - pos);
      const buf = Buffer.allocUnsafe(len);
      fs.readSync(fd, buf, 0, len, pos);
      const parts = (carry + buf.toString("utf-8")).split("\n");
      carry = parts.pop();
      for (const l of parts) {
        if (!l.trim().length) continue;
        fullCount++;
        try { if (isUserTurnRecord(JSON.parse(l))) fullTurns++; } catch {}
      }
      pos += len;
    }
    if (carry.trim()) fullCount++;
    fs.closeSync(fd);
  }
  ok("readWireRecordsHeadTail returns records for the big wire", Array.isArray(recs) && recs.length > 0, `n=${recs && recs.length}`);
  ok("first record is metadata", recs && recs[0]?.type === "metadata");
  ok("slice is a strict subset of the full journal", recs && recs.length < fullCount, `partial=${recs && recs.length} full=${fullCount}`);
  // E6 regression: the tail window must cover the requested number of recent turns.
  const sliceTurns = (recs ?? []).filter(isUserTurnRecord).length;
  const wantTurns = Math.min(20, fullTurns);
  ok("tail window covers the recent turns asked for (min(20, full))",
    sliceTurns >= wantTurns, `sliceTurns=${sliceTurns} want=${wantTurns} fullTurns=${fullTurns} TAIL_BYTES=${api.FAST_RESTORE_TAIL_BYTES}`);
  const headRecords = (() => {
    const fd = fs.openSync(wirePath, "r");
    const buf = Buffer.allocUnsafe(4096);
    fs.readSync(fd, buf, 0, 4096, 0);
    fs.closeSync(fd);
    const t = buf.toString("utf-8");
    return parseWireRecords(t.slice(0, t.lastIndexOf("\n") + 1));
  })();
  ok("first tail record is a turn boundary", ["turn.prompt", "turn_begin"].includes(recs[headRecords.length]?.type), recs[headRecords.length]?.type);
  const open = new Map(); let orphanParts = 0, orphanResults = 0; const pending = new Set();
  for (const r of recs) {
    if (r.type !== "context.append_loop_event") continue;
    const e = r.event;
    if (e.type === "step.begin") open.set(e.uuid, true);
    else if (e.type === "content.part" && !open.has(e.stepUuid)) orphanParts++;
    else if (e.type === "tool.call") pending.add(e.toolCallId);
    else if (e.type === "tool.result") { if (pending.has(e.toolCallId)) pending.delete(e.toolCallId); else orphanResults++; }
  }
  ok("no orphan content.part in the returned slice", orphanParts === 0, `orphans=${orphanParts}`);
  ok("no orphan tool.result in the returned slice", orphanResults === 0, `orphans=${orphanResults}`);
  console.log(`NOTE  head 4KB keeps record types: ${headRecords.map((r) => r.type).join(", ")}`);
} else {
  console.log("NOTE  no real wire given — skipping the head+tail-on-real-journal tests");
}

// selection
const agentsDir = wirePath && fs.existsSync(wirePath) ? path.dirname(path.dirname(wirePath)) : null;
if (agentsDir) {
  const subDirs = fs.readdirSync(agentsDir, { withFileTypes: true }).filter((e) => e.isDirectory() && e.name !== "main").map((e) => e.name);
  const mainReplay = [
    { type: "message", message: { role: "assistant", toolCalls: [{ type: "function", id: "t1", name: "Agent" }] } },
    { type: "message", message: { role: "tool", toolCallId: "t1", content: [{ type: "text", text: "agent_id: agent-149\n" }] } },
  ];
  if (subDirs.includes("agent-149")) {
    const picked = await api.selectRecentSubagentIds(agentsDir, subDirs, mainReplay, 20);
    ok("selectRecentSubagentIds loads far fewer than all", picked.length > 0 && picked.length < subDirs.length, `picked=${picked.length} all=${subDirs.length}`);
    ok("selectRecentSubagentIds keeps the referenced agent", picked.includes("agent-149"));
    ok("selectRecentSubagentIds drops unreferenced agents", !picked.includes("agent-3"));
  }
  // v2: FAST_RESTORE_FALLBACK_SUBAGENTS defaults to 0 => no fallback load at all.
  const emptyPick = await api.selectRecentSubagentIds(agentsDir, subDirs, [], 20);
  ok(`no references => fallback loads FAST_RESTORE_FALLBACK_SUBAGENTS (${api.FAST_RESTORE_FALLBACK_SUBAGENTS})`,
    emptyPick.length === api.FAST_RESTORE_FALLBACK_SUBAGENTS, `n=${emptyPick.length}`);
  const badPick = await api.selectRecentSubagentIds(agentsDir, subDirs, "not-an-array", 20);
  ok(`garbage replay => conservative, no throw (<= fallback ${api.FAST_RESTORE_FALLBACK_SUBAGENTS})`,
    Array.isArray(badPick) && badPick.length === api.FAST_RESTORE_FALLBACK_SUBAGENTS, `n=${badPick.length}`);
}

try { fs.unlinkSync(tmpBlock); fs.unlinkSync(smallPath); } catch {}

let failed = 0;
for (const [name, pass, extra] of results) {
  if (!pass) failed++;
  console.log(`${pass ? "PASS" : "FAIL"}  ${name}${extra ? "  [" + extra + "]" : ""}`);
}
console.log(`\n${results.length - failed}/${results.length} passed`);
process.exit(failed === 0 ? 0 : 1);
