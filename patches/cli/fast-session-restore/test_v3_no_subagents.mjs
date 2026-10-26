#!/usr/bin/env node
// Behavioural test for the fast-session-restore **v3 / E4** change:
// "no subagent is ever loaded when FAST_RESTORE_LOAD_SUBAGENTS === false".
//
//   node test_v3_no_subagents.mjs <patched-extension.js>
//
// Three independent things are checked against the PATCHED bundle text (no
// re-implementation):
//   A. the constant exists and is `false`;
//   B. the anchor-A block, executed verbatim with stubs, calls create()/fold ZERO
//      times with the constant false — and DOES call it when forced true (so the
//      gate, not an accident, is what blocks it), never throwing;
//   C. the webview-side consumer `buildSubagentReplayIndex` (upstream code, taken
//      verbatim) does not throw and yields no nested invocation when the subagent
//      entry is absent — the "缺 subagent 不崩" contract our safety claim rests on.
import fs from "node:fs";

const bundlePath = process.argv[2];
if (!bundlePath || !fs.existsSync(bundlePath)) {
  console.error("usage: node test_v3_no_subagents.mjs <patched-extension.js>");
  process.exit(2);
}
const src = fs.readFileSync(bundlePath, "utf-8");
const results = [];
const ok = (name, cond, extra = "") => results.push([name, !!cond, extra]);

// ---------------------------------------------------------------- A. constant
const hasConst = src.includes("const FAST_RESTORE_LOAD_SUBAGENTS = false;");
ok("FAST_RESTORE_LOAD_SUBAGENTS exists and is false", hasConst);
const hasDepth1 = src.includes("const FAST_RESTORE_MAX_AGENT_DEPTH = 1;");
ok("FAST_RESTORE_MAX_AGENT_DEPTH is 1 (E5)", hasDepth1);
const hasTail8 = src.includes("const FAST_RESTORE_TAIL_BYTES = 8 * 1024 * 1024;");
ok("FAST_RESTORE_TAIL_BYTES is 8 MiB (E6)", hasTail8);

// ---------------------------------------------------------------- B. anchor-A block
const END_MARK = "// === END LOCAL PATCH: fast session restore — subagent on-demand load ===";
const startMark = "if (replay?.includeSubagents === true) {";
const startAt = src.indexOf(startMark);
const endMarkAt = src.indexOf(END_MARK);
if (startAt < 0 || endMarkAt < 0 || endMarkAt < startAt) {
  console.error("FATAL: cannot locate the anchor-A block / its END marker");
  process.exit(2);
}
// the block ends at the closing brace of the `if`, i.e. after the loader loop's `catch {}`
const tailProbe = "} catch {}\n\t\t}";
const tailAt = src.indexOf(tailProbe, endMarkAt);
const block = src.slice(startAt, tailAt + tailProbe.length);
ok("anchor-A block extracted", block.includes("for (const agentId of selectedSubagentIds)"), `${block.split("\n").length} lines`);
ok("gate present in the block", block.includes("if (FAST_RESTORE_LOAD_SUBAGENTS) try {"));
ok("no un-gated create() outside the loop", block.split("create({ agentId })").length === 2, "one occurrence");

function makeBlockRunner(blockText) {
  const body = `return (async () => {\n${blockText}\n})();`;
  return new Function(
    "replay", "handle", "agents", "join", "readdir$1", "ISessionContext",
    "IAgentLifecycleService", "MAIN_AGENT_ID$1", "selectRecentSubagentIds",
    "FAST_RESTORE_LOAD_SUBAGENTS", body
  );
}

function makeEnv() {
  const created = [];
  const folded = [];
  const accessor = {
    get: (token) => {
      if (token === "sessionDir") return "/fake/session";
      if (token === "lifecycle") {
        return {
          create: async ({ agentId }) => { created.push(agentId); },
          handleOf: (agentId) => ({ id: agentId }),
        };
      }
      return undefined;
    },
  };
  const handle = { accessor };
  const sessionContext = { sessionDir: "/fake/session" };
  // make ISessionContext/IAgentLifecycleService distinguishable tokens
  const ISessionContext = "sessionDir";
  const IAgentLifecycleService = "lifecycle";
  const readdir = async () => Array.from({ length: 84 }, (_, i) => ({ name: `agent-${i + 1}`, isDirectory: () => true }));
  const thisRef = {
    resumedAgentState: async (_h, agentHandle) => { folded.push(agentHandle.id); return { replay: [] }; },
  };
  return { created, folded, handle, sessionContext, ISessionContext, IAgentLifecycleService, readdir, thisRef };
}

(async () => {
  // B1 — constant false (the shipped default)
  {
    const env = makeEnv();
    const agents = {};
    const run = makeBlockRunner(block).bind(env.thisRef);
    let threw = null;
    try {
      await run(
        { includeSubagents: true, replayTurnLimit: 20 }, env.handle, agents, (...p) => p.join("/"),
        env.readdir, env.ISessionContext, env.IAgentLifecycleService, "main",
        async () => ["agent-7"],        // would return a hit if it were ever called
        false                            // FAST_RESTORE_LOAD_SUBAGENTS
      );
    } catch (error) { threw = error; }
    ok("false: no throw", threw === null, threw ? String(threw) : "");
    ok("false: zero create() calls", env.created.length === 0, `created=${JSON.stringify(env.created)}`);
    ok("false: zero resumedAgentState/fold calls", env.folded.length === 0, `folded=${JSON.stringify(env.folded)}`);
    ok("false: agents map untouched (only main would exist upstream)", Object.keys(agents).length === 0, `keys=${JSON.stringify(Object.keys(agents))}`);
  }

  // B2 — same block, constant forced true: proves the gate is the only blocker
  {
    const env = makeEnv();
    const agents = {};
    const run = makeBlockRunner(block).bind(env.thisRef);
    await run(
      { includeSubagents: true, replayTurnLimit: 20 }, env.handle, agents, (...p) => p.join("/"),
      env.readdir, env.ISessionContext, env.IAgentLifecycleService, "main",
      async () => ["agent-7"], true
    );
    ok("true: create() called exactly once with the referenced id",
      env.created.length === 1 && env.created[0] === "agent-7", `created=${JSON.stringify(env.created)}`);
    ok("true: agent state recorded", Object.keys(agents).join(",") === "agent-7", `keys=${JSON.stringify(Object.keys(agents))}`);
  }

  // B3 — false + includeSubagents false (no-op branch)
  {
    const env = makeEnv();
    const agents = {};
    const run = makeBlockRunner(block).bind(env.thisRef);
    await run({ includeSubagents: false }, env.handle, agents, (...p) => p.join("/"),
      env.readdir, env.ISessionContext, env.IAgentLifecycleService, "main", async () => ["agent-7"], false);
    ok("includeSubagents=false: nothing happens, no throw", env.created.length === 0 && Object.keys(agents).length === 0);
  }

  // ---------------------------------------------------------------- C. webview consumer
  const b1Start = src.indexOf("function buildSubagentReplayIndex(state) {");
  const b1End = src.indexOf("function renderSubagentInvocations(", b1Start);
  const b2Start = src.indexOf("function parentCallKey(agentId, toolCallId) {");
  const b2End = src.indexOf("function resumedStatusPayload(agent) {", b2Start);
  if (b1Start < 0 || b1End < 0 || b2Start < 0 || b2End < 0) {
    console.error("FATAL: cannot locate buildSubagentReplayIndex / parentCallKey");
    process.exit(2);
  }
  const consumerSrc = src.slice(b1Start, b1End) + src.slice(b2Start, b2End);
  const buildIndex = new Function(`${consumerSrc}\nreturn buildSubagentReplayIndex;`)();

  const agentCall = { type: "message", message: { role: "assistant", content: [], toolCalls: [{ type: "function", id: "c1", name: "Agent" }] } };
  const toolResult = { type: "message", message: { role: "tool", toolCallId: "c1", content: [{ type: "text", text: "task_id: agent-xyz\nagent_id: agent-7\n" }] } };
  const meta = (parent) => ({ agents: { "agent-7": { parentAgentId: parent } } });

  // C1 — child NOT loaded (agents has only main) => must not throw, no nested invocation
  {
    const state = { agents: { main: { replay: [agentCall, toolResult] } }, sessionMetadata: meta("main") };
    let threw = null; let index;
    try { index = buildIndex(state); } catch (error) { threw = error; }
    ok("webview consumer: missing subagent does not throw", threw === null, threw ? String(threw) : "");
    ok("webview consumer: missing subagent yields no nested invocation",
      index && index.byParentCall.size === 0, `byParentCall=${index && index.byParentCall.size}`);
  }
  // C2 — child loaded => exactly one nested invocation (positive control)
  {
    const state = {
      agents: { main: { replay: [agentCall, toolResult] }, "agent-7": { replay: [{ type: "message", message: { role: "user", content: [{ type: "text", text: "hi" }] }, time: 2 }] } },
      sessionMetadata: meta("main"),
    };
    let threw = null; let index;
    try { index = buildIndex(state); } catch (error) { threw = error; }
    ok("webview consumer: loaded subagent yields one nested invocation",
      threw === null && index && index.byParentCall.size === 1, `byParentCall=${index && index.byParentCall.size}`);
  }

  let failed = 0;
  for (const [name, pass, extra] of results) {
    if (!pass) failed++;
    console.log(`${pass ? "PASS" : "FAIL"}  ${name}${extra ? "  [" + extra + "]" : ""}`);
  }
  console.log(`\n${results.length - failed}/${results.length} passed`);
  process.exit(failed === 0 ? 0 : 1);
})();
