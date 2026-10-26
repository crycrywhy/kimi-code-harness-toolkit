#!/usr/bin/env python3
"""Kimi Code VS Code 0.7.5 — Wave 1 斜杠命令本地补丁 (2026-09-20).

把 CLI 已有、VS Code 未暴露的 8 个命令接进扩展宿主侧（不走模型）：
    /fork  /undo  /title  /status  /usage  /tasks  /skills  /plugins

改动三处，全部在安装目录的打包产物里（本机没有源码工程）：
  A. src/handlers/slash-command.ts  region  — HOST_COMMANDS 白名单 (必须加，否则命令会被当 prompt 发给模型)
  B. src/handlers/slash-command.ts  region  — runHostSlashCommand 的 switch 分支
  C. src/handlers/slash-command.ts  region  — 新增 8 个 handler 实现 + 1 个格式化助手
  D. src/handlers/config.handler.ts region  — SLASH_COMMANDS（webview 的 `/` 补全菜单：左白色命令名 / 右灰色说明）

幂等：已打过则原样报 skip；锚点不唯一则中止。
升级 VSIX 会覆盖本补丁，重跑本脚本即可。
"""
import difflib
import glob
import re
import sys
from pathlib import Path

_patches_dir = Path(__file__).resolve().parents[1 if Path(__file__).resolve().parent.name in ("cli", "harness", "panel") else 2]
if str(_patches_dir) not in sys.path:
    sys.path.insert(0, str(_patches_dir))

import patch_common

DEFAULT_GLOB = str(
    Path.home() / ".vscode-server/extensions/moonshot-ai.kimi-code-*/dist/extension.js"
)


def resolve_bundle(argv):
    """定位要打补丁的 bundle：显式给路径 > 自动挑 mtime 最新的那个安装目录。

    这样装了新版 VSIX 之后**不用改脚本**，直接重跑即可（版本号只进备份文件名）。
    """
    if len(argv) > 1:
        return Path(argv[1]).expanduser()
    candidates = [Path(p) for p in glob.glob(DEFAULT_GLOB)]
    if not candidates:
        sys.exit("FATAL: 没找到任何 kimi-code 扩展安装，glob=%s" % DEFAULT_GLOB)
    candidates.sort(key=lambda p: p.stat().st_mtime)
    return candidates[-1]


def version_of(bundle):
    match = re.search(r"moonshot-ai\.kimi-code-([0-9][^-]*)-", bundle.parent.parent.name)
    return match.group(1) if match else "unknown"


BUNDLE = resolve_bundle(sys.argv)
BACKUP = Path(str(BUNDLE) + ".bak-%s-before-slash-wave1" % version_of(BUNDLE))
LEGACY_BACKUP = Path(
    Path.home() / ".vscode-server/extensions/moonshot-ai.kimi-code-0.7.5-linux-x64/"
    "dist/extension.js.bak-20260920-before-slash-wave1"
)

# ---------------------------------------------------------------- A: 白名单
ANCHOR_A = """const HOST_COMMANDS = new Set([
\t"init",
\t"compact",
\t"clear",
\t"reset",
\t"yolo",
\t"auto",
\t"afk",
\t"plan",
\t"add-dir",
\t"export",
\t"import"
]);"""

REPLACE_A = """const HOST_COMMANDS = new Set([
\t"init",
\t"compact",
\t"clear",
\t"reset",
\t"yolo",
\t"auto",
\t"afk",
\t"plan",
\t"add-dir",
\t"export",
\t"import",
\t"fork",
\t"undo",
\t"title",
\t"status",
\t"usage",
\t"tasks",
\t"skills",
\t"plugins"
]);"""

# ------------------------------------------------------------ B: 分发分支
ANCHOR_B = """\t\t\tcase "export":
\t\t\t\tawait exportContext(runtime, command.args, emit);
\t\t\t\tbreak;
\t\t}"""

REPLACE_B = """\t\t\tcase "export":
\t\t\t\tawait exportContext(runtime, command.args, emit);
\t\t\t\tbreak;
\t\t\t// === LOCAL PATCH: Wave 1 slash commands (2026-09-20) ===
\t\t\tcase "fork":
\t\t\t\tawait runForkCommand(runtime, ctx, emit);
\t\t\t\tbreak;
\t\t\tcase "undo":
\t\t\t\tawait runUndoCommand(runtime, command.args, emit);
\t\t\t\tbreak;
\t\t\tcase "title":
\t\t\t\tawait runTitleCommand(runtime, command.args, ctx, emit);
\t\t\t\tbreak;
\t\t\tcase "status":
\t\t\t\tawait runStatusCommand(runtime, emit);
\t\t\t\tbreak;
\t\t\tcase "usage":
\t\t\t\tawait runUsageCommand(runtime, emit);
\t\t\t\tbreak;
\t\t\tcase "tasks":
\t\t\t\tawait runTasksCommand(runtime, emit);
\t\t\t\tbreak;
\t\t\tcase "skills":
\t\t\t\tawait runSkillsCommand(runtime, ctx, emit);
\t\t\t\tbreak;
\t\t\tcase "plugins":
\t\t\t\tawait runPluginsCommand(command.args, ctx, emit);
\t\t\t\tbreak;
\t\t\t// === END LOCAL PATCH ===
\t\t}"""

# ------------------------------------------------- C: 新 handler 实现代码块
ANCHOR_C = """function stripMatchingQuotes(value) {
\tif (value.length < 2) return value;
\tconst first = value[0];
\treturn first === value.at(-1) && (first === "\\"" || first === "'") ? value.slice(1, -1) : value;
}
//#endregion"""

NEW_FUNCS = r"""
// === LOCAL PATCH: Wave 1 slash commands (2026-09-20) ===
// 这 8 个命令都直接调用 @moonshot-ai/kimi-code-sdk 的 Session / KimiHarness
// 公开 API（与 CLI 完全同一套对象），没有新增任何底层能力。
function formatTokenCount(value) {
\tif (typeof value !== "number" || !Number.isFinite(value)) return "0";
\tif (Math.abs(value) < 1e3) return String(value);
\tif (Math.abs(value) < 1e6) return (value / 1e3).toFixed(1) + "k";
\treturn (value / 1e6).toFixed(2) + "M";
}

async function runForkCommand(runtime, ctx, emit) {
\tconst source = runtime.summary ?? {
\t\tid: runtime.id,
\t\tworkDir: runtime.session.workDir
\t};
\tconst sourceTitle = typeof source.title === "string" && source.title.trim().length > 0 ? source.title.trim() : runtime.id;
\tconst forked = await ctx.harness.forkSession({
\t\tid: runtime.id,
\t\ttitle: ("Fork: " + sourceTitle).slice(0, 200)
\t});
\tconst target = forked.summary;
\tif (target === void 0) {
\t\tawait forked.close().catch(() => void 0);
\t\tthrow new Error("Forked session metadata is unavailable.");
\t}
\tlet materializeError;
\ttry {
\t\tawait ctx.baselineManager.materializeToFork({
\t\t\tid: source.id,
\t\t\tworkDir: source.workDir,
\t\t\tmetadata: source.metadata
\t\t}, {
\t\t\tid: target.id,
\t\t\tworkDir: target.workDir,
\t\t\tmetadata: target.metadata
\t\t});
\t} catch (error) {
\t\tmaterializeError = error;
\t}
\ttry {
\t\tawait forked.close();
\t} catch (error) {
\t\tctx.logError("Unable to close a forked session", error);
\t}
\tif (materializeError !== void 0) {
\t\tawait ctx.harness.deleteSession(target.id).catch(() => void 0);
\t\tawait ctx.baselineManager.deleteSession(target.id).catch(() => void 0);
\t\tthrow materializeError;
\t}
\temit("Session forked: " + target.id + "\nStill in the original session. Open the fork from the session list when you want to continue there.");
}

async function runUndoCommand(runtime, args, emit) {
\tconst raw = stripMatchingQuotes(args.trim());
\tlet count = 1;
\tif (raw.length > 0) {
\t\tif (!/^[0-9]+$/.test(raw)) throw new Error("Usage: /undo [turn_count] — turn_count must be a positive integer.");
\t\tcount = Number.parseInt(raw, 10);
\t\tif (!Number.isSafeInteger(count) || count < 1) throw new Error("Usage: /undo [turn_count] — turn_count must be a positive integer.");
\t}
\tawait runtime.session.undoHistory(count);
\temit("Undid " + String(count) + " conversation turn" + (count === 1 ? "" : "s") + ".\nThe engine dropped them from the context; if this transcript still shows them, reopen the conversation to refresh the view.");
}

async function runTitleCommand(runtime, args, ctx, emit) {
\tconst title = args.trim();
\tif (title.length === 0) {
\t\tconst fresh = await ctx.harness.listSessions({ sessionId: runtime.id }).then((list) => list[0]).catch(() => void 0);
\t\tconst current = typeof fresh?.title === "string" && fresh.title.trim().length > 0 ? fresh.title.trim() : typeof runtime.summary?.title === "string" ? runtime.summary.title.trim() : "";
\t\temit(current.length > 0 ? "Session title: " + current : "Session title: (not set) — id: " + runtime.id);
\t\treturn;
\t}
\tconst newTitle = title.slice(0, 200);
\tawait ctx.harness.renameSession({
\t\tid: runtime.id,
\t\ttitle: newTitle
\t});
\temit("Session title set to: " + newTitle);
}

async function runStatusCommand(runtime, emit) {
\tconst status = await runtime.session.getStatus();
\tconst used = typeof status.contextTokens === "number" ? status.contextTokens : 0;
\tconst max = typeof status.maxContextTokens === "number" ? status.maxContextTokens : 0;
\tconst usage = typeof status.contextUsage === "number" && Number.isFinite(status.contextUsage) ? status.contextUsage : max > 0 ? used / max : 0;
\temit([
\t\t"Session: " + runtime.id,
\t\t"Model: " + (status.model ?? "(unknown)"),
\t\t"Thinking effort: " + (status.thinkingEffort ?? "off"),
\t\t"Permission: " + (status.permission ?? "(unknown)"),
\t\t"Plan mode: " + (status.planMode === true ? "on" : "off"),
\t\t"Swarm mode: " + (status.swarmMode === true ? "on" : "off"),
\t\t"Context: " + formatTokenCount(used) + " tokens" + (max > 0 ? " / " + formatTokenCount(max) + " (" + String(Math.round(usage * 100)) + "%)" : "")
\t].join("\n"));
}

async function runUsageCommand(runtime, emit) {
\tconst usage = await runtime.session.getUsage();
\tconst lines = [];
\tconst render = (label, entry) => {
\t\tif (entry === void 0 || entry === null) return;
\t\tlines.push(label + ": input " + formatTokenCount(entry.inputOther) + " · cache read " + formatTokenCount(entry.inputCacheRead) + " · cache write " + formatTokenCount(entry.inputCacheCreation) + " · output " + formatTokenCount(entry.output));
\t};
\trender("Current turn", usage?.currentTurn);
\trender("Session total", usage?.total);
\tconst byModel = usage?.byModel;
\tif (byModel !== void 0 && byModel !== null && typeof byModel === "object") for (const [model, entry] of Object.entries(byModel)) render("Model " + model, entry);
\tif (lines.length === 0) {
\t\temit("No token usage recorded for this session yet.");
\t\treturn;
\t}
\temit(lines.join("\n"));
}

async function runTasksCommand(runtime, emit) {
\tconst result = await runtime.session.listBackgroundTasks({});
\tconst list = Array.isArray(result) ? result : Array.isArray(result?.tasks) ? result.tasks : [];
\tif (list.length === 0) {
\t\temit("No background tasks in this session.");
\t\treturn;
\t}
\temit(["Background tasks (" + String(list.length) + "):", ...list.map((task) => {
\t\tconst id = task?.taskId ?? task?.id ?? task?.task_id ?? "(unknown)";
\t\tconst status = task?.status ?? task?.state ?? "?";
\t\tconst description = task?.description ?? task?.title ?? task?.prompt ?? task?.kind ?? "";
\t\treturn "- " + String(id) + " [" + String(status) + "]" + (String(description).length > 0 ? " " + String(description).slice(0, 120) : "");
\t})].join("\n"));
}

async function runSkillsCommand(runtime, ctx, emit) {
\tconst workDir = ctx.workDir ?? runtime.session.workDir;
\tconst skills = await ctx.harness.listWorkspaceSkills(workDir);
\tconst list = [...skills].filter((skill) => isUserActivatableSkill(skill.type)).toSorted((left, right) => left.name.localeCompare(right.name));
\tif (list.length === 0) {
\t\temit("No skills available for this workspace.");
\t\treturn;
\t}
\temit(["Skills (" + String(list.length) + ") — start one with /skill:<name>:", ...list.map((skill) => "- " + skill.name + (skill.description ? " — " + skill.description : ""))].join("\n"));
}

async function runPluginsCommand(args, ctx, emit) {
\tconst parts = args.trim().split(/\s+/).filter((part) => part.length > 0);
\tconst subcommand = (parts[0] ?? "list").toLowerCase();
\tconst target = parts[1];
\tif (subcommand === "list") {
\t\tconst plugins = await ctx.harness.listPlugins();
\t\tif (plugins.length === 0) {
\t\t\temit("No plugins installed. Install one with the CLI (`/plugins install <source>` in `kimi`).");
\t\t\treturn;
\t\t}
\t\temit(["Plugins (" + String(plugins.length) + "):", ...plugins.map((plugin) => "- " + plugin.id + (plugin.version ? " v" + plugin.version : "") + (plugin.enabled === false ? " (disabled)" : "") + (plugin.hasErrors === true ? " [has errors]" : ""))].join("\n"));
\t\treturn;
\t}
\tif (subcommand === "reload") {
\t\tconst summary = await ctx.harness.reloadPlugins();
\t\tconst added = summary?.added ?? [];
\t\tconst removed = summary?.removed ?? [];
\t\tconst errors = summary?.errors ?? [];
\t\temit(["Plugins reloaded: " + String(added.length) + " added, " + String(removed.length) + " removed, " + String(errors.length) + " error(s).", ...added.map((id) => "+ " + id), ...removed.map((id) => "- " + id), ...errors.map((error) => "! " + error.id + ": " + error.message)].join("\n"));
\t\treturn;
\t}
\tif (subcommand === "info") {
\t\tif (target === void 0) throw new Error("Usage: /plugins info <plugin_id>");
\t\tconst info = await ctx.harness.getPluginInfo(target);
\t\temit(JSON.stringify(info, null, 2));
\t\treturn;
\t}
\tif (subcommand === "enable" || subcommand === "disable" || subcommand === "remove") {
\t\tif (target === void 0) throw new Error("Usage: /plugins " + subcommand + " <plugin_id>");
\t\tconst installed = await ctx.harness.listPlugins();
\t\tif (!installed.some((plugin) => plugin.id === target)) throw new Error("Unknown plugin id '" + target + "'. Run /plugins to list installed plugins.");
\t\tif (subcommand === "remove") {
\t\t\tawait ctx.harness.removePlugin(target);
\t\t\temit("Plugin removed: " + target);
\t\t\treturn;
\t\t}
\t\tconst enabled = subcommand === "enable";
\t\tawait ctx.harness.setPluginEnabled(target, enabled);
\t\temit("Plugin " + target + " " + (enabled ? "enabled" : "disabled") + ".");
\t\treturn;
\t}
\tthrow new Error("Usage: /plugins [list | reload | info <id> | enable <id> | disable <id> | remove <id>]");
}
// === END LOCAL PATCH ===
//#endregion"""

# NEW_FUNCS 是 raw string（为了原样保留 JS 里的 "\n" 与 /\s+/ 等转义），
# 所以缩进写的 "\t" 标记必须在这里还原成真实制表符，否则 JS 语法非法。
NEW_FUNCS = NEW_FUNCS.replace("\\t", "\t")

REPLACE_C = ANCHOR_C.replace(
    "\n//#endregion", NEW_FUNCS
)

# ------------------------------------------- D: webview 补全菜单（名称+说明）
ANCHOR_D = """\t{
\t\tname: "import",
\t\taliases: [],
\t\tdescription: "Import context from a file or session ID"
\t}
];"""

REPLACE_D = """\t{
\t\tname: "import",
\t\taliases: [],
\t\tdescription: "Import context from a file or session ID"
\t},
\t// === LOCAL PATCH: Wave 1 slash commands (2026-09-20) ===
\t{
\t\tname: "fork",
\t\taliases: [],
\t\tdescription: "Fork the current session into an independent copy"
\t},
\t{
\t\tname: "undo",
\t\taliases: [],
\t\tdescription: "Undo the last conversation turn. Usage: /undo [turn_count]"
\t},
\t{
\t\tname: "title",
\t\taliases: [],
\t\tdescription: "Show or set the session title. Usage: /title [new title]"
\t},
\t{
\t\tname: "status",
\t\taliases: [],
\t\tdescription: "Show session status: model, thinking effort, permission, context usage"
\t},
\t{
\t\tname: "usage",
\t\taliases: [],
\t\tdescription: "Show token usage for this session"
\t},
\t{
\t\tname: "tasks",
\t\taliases: [],
\t\tdescription: "List the background tasks running in this session"
\t},
\t{
\t\tname: "skills",
\t\taliases: [],
\t\tdescription: "List the skills available in this workspace"
\t},
\t{
\t\tname: "plugins",
\t\taliases: [],
\t\tdescription: "List or manage plugins. Usage: /plugins [list|reload|info|enable|disable|remove] <id>"
\t}
\t// === END LOCAL PATCH ===
];"""

PATCHES = [
    ("A HOST_COMMANDS 白名单", ANCHOR_A, REPLACE_A),
    ("B runHostSlashCommand 分支", ANCHOR_B, REPLACE_B),
    ("C 8 个 handler 实现", ANCHOR_C, REPLACE_C),
    ("D SLASH_COMMANDS 补全菜单", ANCHOR_D, REPLACE_D),
]


def main() -> int:
    if not BUNDLE.exists():
        print("FATAL: bundle not found: %s" % BUNDLE)
        return 2
    print("Target bundle : %s (%d bytes)" % (BUNDLE, BUNDLE.stat().st_size))
    print("Backup path   : %s" % BACKUP)
    if LEGACY_BACKUP.exists():
        print("Known-good pre-patch copy: %s" % LEGACY_BACKUP)
    text = BUNDLE.read_text(encoding="utf-8")
    original = text
    applied, skipped = [], []

    for label, anchor, replacement in PATCHES:
        if replacement in text:
            skipped.append(label)
            continue
        count = text.count(anchor)
        if count != 1:
            print("FATAL: anchor for [%s] matched %d times (expected 1) — aborting, nothing written." % (label, count))
            return 3
        text = text.replace(anchor, replacement, 1)
        applied.append(label)

    if not applied:
        print("Nothing to do (all 4 patches already applied).")
        print("Skipped: %s" % ", ".join(skipped))
        return 0

    if not BACKUP.exists():
        BACKUP.write_bytes(original.encode("utf-8"))
        print("Backup written: %s" % BACKUP)
    else:
        print("Backup already exists (kept): %s" % BACKUP)

    # 2026-10-24 审查修复（#1）：写同扩展名临时文件 → node --check 通过才 os.replace。
    # 原先直接 write_text 覆盖 3MB 生产 bundle，写一半崩溃或替换引入语法错误都会
    # 留下损坏的扩展，且没有任何校验。校验失败时原文件一个字节未动。
    if not patch_common.write_validated(str(BUNDLE), text, patch_common.node_check,
                                        label="node --check"):
        print("FATAL: 语法校验失败，原 bundle 未改动（备份：%s）" % BACKUP)
        return 4
    print("Patched: %s" % BUNDLE)
    print("Applied: %s" % ", ".join(applied))
    if skipped:
        print("Skipped (already applied): %s" % ", ".join(skipped))

    diff = difflib.unified_diff(
        original.splitlines(), text.splitlines(), lineterm="", n=1,
    )
    added = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))
    print("Diff: +%d / -%d lines" % (added, removed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
