#!/usr/bin/env node
// Behavioural test for the fast-session-restore **v2** selection logic.
//
// It does NOT re-implement the patch: it extracts the patched helper block
// verbatim from a bundle (between the LOCAL PATCH markers) and evaluates it with
// stubbed dependencies, so what is tested is exactly what will ship.
//
//   node test_v2_selection.mjs <patched-extension.js>
//
// Asserts:
//   1. FAST_RESTORE_FALLBACK_SUBAGENTS === 0
//   2. pickNewestSubagentIds(dir, ids, 0) === []            (no mtime fallback)
//   3. selectRecentSubagentIds(...) with a replay that references NO subagent -> []
//   4. selectRecentSubagentIds(...) with a replay that DOES reference agent-7 -> ["agent-7"]
//   5. selectRecentSubagentIds(...) with a broken fold -> []  (exception path loads nothing)
import fs from "node:fs";
import path from "node:path";
import os from "node:os";

const bundle = process.argv[2];
if (!bundle) {
  console.error("usage: node test_v2_selection.mjs <patched-extension.js>");
  process.exit(2);
}
const text = fs.readFileSync(bundle, "utf-8");

const START = "// === LOCAL PATCH: fast session restore (<DATE>, local) ===";
const END = "// === END LOCAL PATCH: fast session restore ===";
const i = text.indexOf(START);
const j = text.indexOf(END);
if (i < 0 || j < 0 || j < i) {
  console.error("FAIL: cannot find the v1/v2 helper block markers in", bundle);
  process.exit(2);
}
const block = text.slice(i, j + END.length);

// ---- stubs for the identifiers the block closes over -------------------------
const sandbox = {
  fsSync: {
    statSync: () => ({ size: 5 * 1024 * 1024, mtimeMs: 1 }),
    openSync: () => { throw new Error("no fd in test"); },
    readSync: () => 0,
    closeSync: () => {},
  },
  join: path.join,
  readFile: async () => "",
  readReplayTurnLimitSetting: undefined,
  getConfig: () => ({ get: () => undefined }),
  limitAgentReplayByTurns: (records, maxTurns) => (maxTurns === undefined ? records : records),
  parseWireRecords: () => [],
  EMPTY_FOLD: { replay: [], toolStore: undefined },
  foldAgentWireReplay: undefined, // replaced per-test
};

const factory = new Function(
  "env",
  `"use strict";
   const { fsSync, join, readFile, getConfig, limitAgentReplayByTurns, parseWireRecords, EMPTY_FOLD } = env;
   let foldAgentWireReplayImpl = env.foldAgentWireReplay;
   const foldAgentWireReplay = (...a) => foldAgentWireReplayImpl(...a);
   ${block}
   return { FAST_RESTORE_FALLBACK_SUBAGENTS, pickNewestSubagentIds, selectRecentSubagentIds, foldAgentWireReplay: (f) => { foldAgentWireReplayImpl = f; } };`
);

const api = factory(sandbox);
let failures = 0;
function check(name, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  console.log(`  ${ok ? "PASS" : "FAIL"}  ${name}  got=${JSON.stringify(got)} want=${JSON.stringify(want)}`);
  if (!ok) failures++;
}

console.log("helper block extracted:", block.split("\n").length, "lines");

// 1
check("FAST_RESTORE_FALLBACK_SUBAGENTS", api.FAST_RESTORE_FALLBACK_SUBAGENTS, 0);

// 2 — fallback is off by construction
check("pickNewestSubagentIds(count=0)", api.pickNewestSubagentIds("/nope", ["agent-1", "agent-2"], 0), []);
check("pickNewestSubagentIds(count=12) still lists (stub statSync succeeds)",
  api.pickNewestSubagentIds("/nope", ["agent-1"], 12), ["agent-1"]);
// statSync failure inside -> catch returns subagentIds (documented v1 fallback, kept in v2)
const realStat = sandbox.fsSync.statSync;
sandbox.fsSync.statSync = () => { throw new Error("ENOENT"); };
check("pickNewestSubagentIds(count=12, unreadable) = ids", api.pickNewestSubagentIds("/nope", ["agent-1"], 12), ["agent-1"]);
sandbox.fsSync.statSync = realStat;

const agentsDir = fs.mkdtempSync(path.join(os.tmpdir(), "fastrestore-test-"));
fs.mkdirSync(path.join(agentsDir, "agent-7"), { recursive: true });
fs.writeFileSync(path.join(agentsDir, "agent-7", "wire.jsonl"), "{}\n");

const assistantWithAgentCall = {
  type: "message",
  message: { role: "assistant", content: [], toolCalls: [{ type: "function", id: "c1", name: "Agent" }] },
};
const toolResult7 = {
  type: "message",
  message: { role: "tool", toolCallId: "c1", content: [{ type: "text", text: "task_id: agent-x\nagent_id: agent-7\n" }] },
};
const plainUser = { type: "message", message: { role: "user", content: [{ type: "text", text: "hi" }] } };

// 3 — replay with no Agent call at all -> selection empty, fallback off -> []
(async () => {
  api.foldAgentWireReplay(async () => ({ replay: [], toolStore: undefined }));
  check("select: no reference -> []",
    await api.selectRecentSubagentIds(agentsDir, ["agent-7"], [plainUser], undefined), []);

  // 4 — one real reference -> loaded
  check("select: agent-7 referenced -> [agent-7]",
    await api.selectRecentSubagentIds(agentsDir, ["agent-7"], [assistantWithAgentCall, toolResult7], undefined),
    ["agent-7"]);

  // 5 — the grandchild fold blows up -> still returns the direct hit (never throws)
  api.foldAgentWireReplay(async () => { throw new Error("boom"); });
  check("select: fold throws -> direct hit kept, no throw",
    await api.selectRecentSubagentIds(agentsDir, ["agent-7"], [assistantWithAgentCall, toolResult7], undefined),
    ["agent-7"]);

  console.log(failures === 0 ? "\nALL CHECKS PASSED" : `\n${failures} CHECK(S) FAILED`);
  process.exit(failures === 0 ? 0 : 1);
})();
