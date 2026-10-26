#!/usr/bin/env python3
"""Kimi Code VS Code 0.7.5 — /view 命令（渲染最近 N 轮）本地补丁 (2026-09-22).

背景：GUI 转录 DOM 随轮数无限增长 → 长会话卡。引擎本来就有「只回放最近 N 轮」的能力
（`limitAgentReplayByTurns`，bundle :162869；参数 `resumeSession({replayTurnLimit})`），
但 VS Code 两处 resume 调用点都只传 includeSubagents，从没传 replayTurnLimit ⇒ 全量渲染。

本补丁做的事（不改引擎）：
  A. package.json  — 声明设置 `kimi.replayTurnLimit`（默认 20）
  B. extension.js  — HOST_COMMANDS 加 "view"
  C. extension.js  — runHostSlashCommand 加 case "view"
  D. extension.js  — 新增 runViewCommand / 读取与映射助手
  E. extension.js  — SLASH_COMMANDS 加 /view 的菜单文案
  F. extension.js  — 两处 harness.resumeSession 传入 replayTurnLimit（真正生效点）

语义映射（重要）：引擎侧 `undefined` = 保留全部回放，而 `0` 或负数 = **空回放**。
所以设置里 `0` 表示"不限"，落到引擎参数时必须映射成 `undefined`，否则视窗会全空。

依赖：先跑 patch_slash_wave1.py（本脚本的锚点引用了它的 LOCAL PATCH 标记）。
幂等：已打过则报 skip；锚点不唯一立即中止且不写文件。
"""
import difflib
import glob
import json
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
    if len(argv) > 1:
        return Path(argv[1]).expanduser()
    candidates = [Path(p) for p in glob.glob(DEFAULT_GLOB)]
    if not candidates:
        sys.exit("FATAL: 没找到任何 kimi-code 扩展安装，glob=%s" % DEFAULT_GLOB)
    candidates.sort(key=lambda p: p.stat().st_mtime)
    return candidates[-1]


BUNDLE = resolve_bundle(sys.argv)
PKG = BUNDLE.parent.parent / "package.json"
VERSION = re.search(r"moonshot-ai\.kimi-code-([0-9][^-]*)-", BUNDLE.parent.parent.name)
VERSION = VERSION.group(1) if VERSION else "unknown"
BACKUP = Path(str(BUNDLE) + ".bak-%s-before-slash-view" % VERSION)
PKG_BACKUP = Path(str(PKG) + ".bak-%s-before-slash-view" % VERSION)

# ---------------------------------------------------- A: package.json 设置声明
PKG_ANCHOR = '\t\t\t\t"kimi.editorContext": {'
PKG_REPLACE = (
    '\t\t\t\t"kimi.replayTurnLimit": {\n'
    '\t\t\t\t\t"type": "number",\n'
    '\t\t\t\t\t"default": 20,\n'
    '\t\t\t\t\t"description": "How many of the most recent user turns of a conversation are '
    'rendered in the chat view (0 = render the full history). Lower values keep the view light on '
    'long conversations; change it with the /view command."\n'
    '\t\t\t\t},\n'
) + PKG_ANCHOR

# ------------------------------------------------ B: HOST_COMMANDS 加 "view"
B_ANCHOR = '\t"skills",\n\t"plugins"\n]);'
B_REPLACE = '\t"skills",\n\t"plugins",\n\t"view"\n]);'

# --------------------------------------------- C: runHostSlashCommand 加分支
C_ANCHOR = (
    '\t\t\tcase "plugins":\n'
    '\t\t\t\tawait runPluginsCommand(command.args, ctx, emit);\n'
    '\t\t\t\tbreak;\n'
    '\t\t\t// === END LOCAL PATCH ==='
)
C_REPLACE = (
    '\t\t\tcase "plugins":\n'
    '\t\t\t\tawait runPluginsCommand(command.args, ctx, emit);\n'
    '\t\t\t\tbreak;\n'
    '\t\t\tcase "view":\n'
    '\t\t\t\tawait runViewCommand(command.args, ctx, emit);\n'
    '\t\t\t\tbreak;\n'
    '\t\t\t// === END LOCAL PATCH ==='
)

# --------------------------------------------------------- D: 新 handler 代码块
D_ANCHOR = "// === END LOCAL PATCH ===\n//#endregion"
D_FUNCS = r"""
// === LOCAL PATCH: /view (2026-09-22) ===
// 「只渲染最近 N 轮」——引擎侧已有能力（resumeSession 的 replayTurnLimit，
// 由 limitAgentReplayByTurns 实现），这里只是把它接到设置项 + 一条命令上。
const DEFAULT_REPLAY_TURNS = 20;
let replayTurnLimitSetting;
function resolveReplayTurnLimit(raw) {
\tif (typeof raw === "number" && Number.isInteger(raw) && raw >= 0) return raw;
\treturn DEFAULT_REPLAY_TURNS;
}
function readReplayTurnLimitSetting() {
\tif (replayTurnLimitSetting !== void 0) return replayTurnLimitSetting;
\ttry {
\t\treturn resolveReplayTurnLimit(getConfig().get("replayTurnLimit", DEFAULT_REPLAY_TURNS));
\t} catch (error) {
\t\tvoid error;
\t\treturn DEFAULT_REPLAY_TURNS;
\t}
}
/**
* 给 harness.resumeSession 用的值：设置 0 表示「不限」，而引擎语义里
* undefined = 保留全部回放、0 = 空回放，所以这里必须把 0 映射成 undefined。
*/
function replayTurnLimitForResume() {
\tconst setting = readReplayTurnLimitSetting();
\treturn setting > 0 ? setting : void 0;
}
function describeReplayTurnLimit(setting) {
\treturn setting > 0 ? String(setting) + " turn(s)" : "all turns";
}
async function runViewCommand(args, ctx, emit) {
\tconst raw = stripMatchingQuotes(args.trim());
\tconst current = readReplayTurnLimitSetting();
\tif (raw.length === 0) {
\t\temit([
\t\t\t"Rendered history: " + describeReplayTurnLimit(current) + ".",
\t\t\t"Usage: /view <turns> — render only the most recent N user turns of a conversation (0 = render everything; default " + String(DEFAULT_REPLAY_TURNS) + ")."
\t\t].join("\n"));
\t\treturn;
\t}
\tif (!/^[0-9]+$/.test(raw)) throw new Error("Usage: /view <turns> — turns must be a non-negative integer (0 = render everything).");
\tconst turns = Number.parseInt(raw, 10);
\tif (!Number.isSafeInteger(turns) || turns > 100000) throw new Error("Usage: /view <turns> — turns must be a non-negative integer (0 = render everything).");
\t// 先落在内存里（万一设置写入被拒也仍然生效），再尝试持久化到用户设置。
\treplayTurnLimitSetting = turns;
\tlet persisted = true;
\ttry {
\t\tawait getConfig().update("replayTurnLimit", turns, vscode.ConfigurationTarget.Global);
\t} catch (error) {
\t\tpersisted = false;
\t\tctx.logError("Unable to persist kimi.replayTurnLimit; using it for this window only", error);
\t}
\tawait dropCurrentSessionRuntime(ctx);
\temit([
\t\t"Rendered history set to " + describeReplayTurnLimit(turns) + (persisted ? "." : " (this window only — could not write kimi.replayTurnLimit)."),
\t\t"Reopen this conversation from the session list (or switch to another conversation and back) to rebuild the view with the new limit.",
\t\tturns > 0 ? "Older turns stay on disk and stay in the model's context — only the rendered view is trimmed. Use /export to save the full transcript." : "The full history will be rendered again — long conversations may feel slow."
\t].join("\n"));
}
/** 丢弃当前视图绑定的会话 runtime，使下次打开会话时按新上限重新 resume（引擎会话与磁盘数据都不受影响）。 */
async function dropCurrentSessionRuntime(ctx) {
\ttry {
\t\tawait ctx.closeSession();
\t} catch (error) {
\t\tctx.logError("Unable to release the session runtime after /view", error);
\t}
}
// === END LOCAL PATCH ===
//#endregion"""
D_FUNCS = D_FUNCS.replace("\\t", "\t")
D_REPLACE = D_ANCHOR.replace("\n//#endregion", D_FUNCS)

# ------------------------------------------------------- E: SLASH_COMMANDS 条目
E_ANCHOR = (
    '\t\tname: "plugins",\n'
    '\t\taliases: [],\n'
    '\t\tdescription: "List or manage plugins. Usage: /plugins [list|reload|info|enable|disable|remove] <id>"\n'
    '\t}\n'
    '\t// === END LOCAL PATCH ==='
)
E_REPLACE = (
    '\t\tname: "plugins",\n'
    '\t\taliases: [],\n'
    '\t\tdescription: "List or manage plugins. Usage: /plugins [list|reload|info|enable|disable|remove] <id>"\n'
    '\t},\n'
    '\t{\n'
    '\t\tname: "view",\n'
    '\t\taliases: [],\n'
    '\t\tdescription: "Limit the rendered history to the most recent turns. Usage: /view [turns] (0 = show all, default 20)"\n'
    '\t}\n'
    '\t// === END LOCAL PATCH ==='
)

# --------------------------------------------------- F: 两处 resume 传参
F1_ANCHOR = (
    '\t\t\t}) : await this.harness.resumeSession({\n'
    '\t\t\t\tid: requestedId,\n'
    '\t\t\t\tincludeSubagents: true\n'
    '\t\t\t});'
)
F1_REPLACE = (
    '\t\t\t}) : await this.harness.resumeSession({\n'
    '\t\t\t\tid: requestedId,\n'
    '\t\t\t\tincludeSubagents: true,\n'
    '\t\t\t\treplayTurnLimit: replayTurnLimitForResume()\n'
    '\t\t\t});'
)
F2_ANCHOR = (
    '\t\t\t\tconst session = current?.session ?? await this.runtime.harness.resumeSession({\n'
    '\t\t\t\t\tid: sessionId,\n'
    '\t\t\t\t\tincludeSubagents: true\n'
    '\t\t\t\t});'
)
F2_REPLACE = (
    '\t\t\t\tconst session = current?.session ?? await this.runtime.harness.resumeSession({\n'
    '\t\t\t\t\tid: sessionId,\n'
    '\t\t\t\t\tincludeSubagents: true,\n'
    '\t\t\t\t\treplayTurnLimit: replayTurnLimitForResume()\n'
    '\t\t\t\t});'
)

JSON_PATCHES = [("A package.json 设置声明", PKG_ANCHOR, PKG_REPLACE)]
JS_PATCHES = [
    ("B HOST_COMMANDS", B_ANCHOR, B_REPLACE),
    ("C runHostSlashCommand 分支", C_ANCHOR, C_REPLACE),
    ("D runViewCommand 实现", D_ANCHOR, D_REPLACE),
    ("E SLASH_COMMANDS 菜单文案", E_ANCHOR, E_REPLACE),
    ("F1 resume 传参（KimiRuntime.openSession）", F1_ANCHOR, F1_REPLACE),
    ("F2 resume 传参（provider ctx.resumeSession）", F2_ANCHOR, F2_REPLACE),
]


def apply_patches(path, patches, label):
    if not path.exists():
        print("FATAL: %s not found: %s" % (label, path))
        return None, None
    original = path.read_text(encoding="utf-8")
    text = original
    applied, skipped = [], []
    for name, anchor, replacement in patches:
        if replacement in text:
            skipped.append(name)
            continue
        count = text.count(anchor)
        if count != 1:
            print("FATAL: anchor for [%s] matched %d times (expected 1) — aborting, nothing written." % (name, count))
            return None, None
        text = text.replace(anchor, replacement, 1)
        applied.append(name)
    return original, (text, applied, skipped)


def main() -> int:
    js = apply_patches(BUNDLE, JS_PATCHES, "bundle")
    if js[0] is None:
        return 3
    pkg = apply_patches(PKG, JSON_PATCHES, "package.json")
    if pkg[0] is None:
        return 3

    # 2026-10-24 审查修复（#1）：写同扩展名临时文件、校验通过才 os.replace。
    # bundle 用 node --check，package.json 用 json.loads。原版 package.json 是
    # 「先覆盖写、后校验」，校验失败时坏文件已经留在盘上；本版失败时原文件未动。
    checks = {"js": patch_common.node_check, "json": patch_common.json_check}
    for path, backup, (original, (text, applied, skipped)) in (
        (BUNDLE, BACKUP, js),
        (PKG, PKG_BACKUP, pkg),
    ):
        if not applied:
            print("%s: nothing to do (already applied). skipped: %s" % (path.name, ", ".join(skipped)))
            continue
        if not backup.exists():
            backup.write_bytes(original.encode("utf-8"))
            print("Backup written: %s" % backup)
        check = checks[path.suffix.lstrip(".")]
        if not patch_common.write_validated(str(path), text, check,
                                            label="%s check" % path.name):
            print("FATAL: %s 校验失败，原文件未改动（备份：%s）" % (path, backup))
            return 4
        diff = difflib.unified_diff(original.splitlines(), text.splitlines(), lineterm="", n=1)
        added = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
        removed = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))
        print("Patched: %s  (+%d/-%d lines)" % (path, added, removed))
        print("  applied: %s" % ", ".join(applied))
        if skipped:
            print("  skipped (already applied): %s" % ", ".join(skipped))
    return 0


if __name__ == "__main__":
    sys.exit(main())
