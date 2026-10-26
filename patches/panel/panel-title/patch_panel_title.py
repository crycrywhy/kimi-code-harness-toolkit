#!/usr/bin/env python3
"""Kimi Code VS Code — 面板标签页跟随当前会话标题(2026-10-26)。

问题：`kimi.openInTab` 开出的每个面板，标签页固定写死 "Kimi Code"，
切换/重命名会话后不跟着变；Claude Code 的标签页显示的是会话标题。

做法（全部在安装目录的 dist/extension.js，本机没有源码工程）：
  E1 KimiWebviewProvider 加 panels 表（webviewId → WebviewPanel）+ 把 sink 交给 BridgeHandler
  E2 createPanel 登记/注销面板；新增 setPanelTitle()（空标题回退 "Kimi Code"）
  E3 createContext 暴露 ctx.setPanelTitle(title, targetWebviewId?)
  E4 loadKimiSessionHistory 成功后按会话 id 刷新标题（切会话）
  E5 setSessionTitle 重命名后同步（上游该 handler 在 0.8.3 里重复了一次，故期望命中 2 次）
  E6 resetSession（新会话）标题回退
  E7 streamChat 收尾强制现查一次（首轮结束后引擎自动生成标题）
  E8 新增 refreshPanelTitle() 助手
  E9 本机 slash-wave1 的 /title 命令改名后同步

幂等：已打过则整段 skip；锚点命中数不符立即中止、不写文件。
升级 VSIX 会覆盖本补丁，重跑本脚本即可（脚本自动挑 mtime 最新的安装目录）。
"""
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

SUFFIX = "panel-title"


def resolve_bundle(argv):
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


# ------------------------------------------------------------------ 锚点与替换

E1_ANCHOR = """var KimiWebviewProvider = class {
\textensionUri;
\twebviews = /* @__PURE__ */ new Map();
\tbridgeHandler;
\tconstructor(extensionUri, context, showLogs, writeLog) {
\t\tthis.extensionUri = extensionUri;
\t\tthis.bridgeHandler = new BridgeHandler(this.broadcastInternal.bind(this), context.workspaceState, context.globalStorageUri.fsPath, this.reloadWebview.bind(this), showLogs, writeLog);
\t}"""

E1_REPL = """var KimiWebviewProvider = class {
\textensionUri;
\twebviews = /* @__PURE__ */ new Map();
\tpanels = /* @__PURE__ */ new Map();
\tbridgeHandler;
\tconstructor(extensionUri, context, showLogs, writeLog) {
\t\tthis.extensionUri = extensionUri;
\t\tthis.bridgeHandler = new BridgeHandler(this.broadcastInternal.bind(this), context.workspaceState, context.globalStorageUri.fsPath, this.reloadWebview.bind(this), showLogs, writeLog);
\t\tthis.bridgeHandler.panelTitleSink = (webviewId, title) => this.setPanelTitle(webviewId, title);
\t}"""

E2_ANCHOR = """\t\tthis.setupWebview(webviewId, panel.webview);
\t\tpanel.onDidDispose(() => {
\t\t\tthis.bridgeHandler.disposeView(webviewId);
\t\t\tthis.webviews.delete(webviewId);
\t\t});
\t\treturn panel;
\t}"""

E2_REPL = """\t\tthis.setupWebview(webviewId, panel.webview);
\t\tthis.panels.set(webviewId, panel);
\t\tpanel.onDidDispose(() => {
\t\t\tthis.bridgeHandler.disposeView(webviewId);
\t\t\tthis.webviews.delete(webviewId);
\t\t\tthis.panels.delete(webviewId);
\t\t});
\t\treturn panel;
\t}
\t/** 标签页标题跟随会话标题；空标题回退 "Kimi Code"（本机补丁 panel-title）。 */
\tsetPanelTitle(webviewId, title) {
\t\tconst panel = this.panels.get(webviewId);
\t\tif (panel === void 0) return;
\t\tconst trimmed = typeof title === "string" ? title.trim() : "";
\t\tconst next = trimmed.length > 0 ? trimmed.slice(0, 120) : "Kimi Code";
\t\tif (panel.title !== next) panel.title = next;
\t}"""

E3_ANCHOR = "\t\t\treloadWebview: () => this.reloadWebview(webviewId),"

E3_REPL = """\t\t\treloadWebview: () => this.reloadWebview(webviewId),
\t\t\tsetPanelTitle: (title, targetWebviewId) => {
\t\t\t\tif (typeof this.panelTitleSink !== "function") return;
\t\t\t\tthis.panelTitleSink(targetWebviewId ?? webviewId, title);
\t\t\t},"""

E4_ANCHOR = """\t\tif (!areSameFsPath(runtime.session.workDir, ctx.workDir)) {
\t\t\tawait ctx.closeSession();
\t\t\tthrow new Error("The selected session belongs to a different working directory.");
\t\t}"""

E4_REPL = E4_ANCHOR + """
\t\tawait refreshPanelTitle(ctx, runtime.id);"""

E5_ANCHOR = """\t\tawait ctx.harness.renameSession({ id: params.sessionId, title });
\t\treturn { ok: true, title };"""

E5_REPL = """\t\tawait ctx.harness.renameSession({ id: params.sessionId, title });
\t\tctx.setPanelTitle(title);
\t\treturn { ok: true, title };"""

E6_ANCHOR = """const resetSession = async (_, ctx) => {
\tconst runtime = ctx.getSession();
\tif (runtime !== void 0) injectedEditorContextSessions.delete(runtime.id);
\tawait ctx.closeSession();
\tctx.fileManager.clearTracked(ctx.webviewId);
\treturn { ok: true };
};"""

E6_REPL = """const resetSession = async (_, ctx) => {
\tconst runtime = ctx.getSession();
\tif (runtime !== void 0) injectedEditorContextSessions.delete(runtime.id);
\tawait ctx.closeSession();
\tctx.setPanelTitle("");
\tctx.fileManager.clearTracked(ctx.webviewId);
\treturn { ok: true };
};"""

E7_ANCHOR = """\tconst systemContext = await buildSystemContext(runtime.id, ctx);
\ttry {
\t\treturn { done: (await runtime.prompt(prependSystemContext(params.content, systemContext))).status === "finished" };
\t} catch (error) {
\t\temitCaughtError(ctx, error, "runtime", runtime.id);
\t\treturn { done: false };
\t}
};"""

E7_REPL = """\tconst systemContext = await buildSystemContext(runtime.id, ctx);
\ttry {
\t\tconst promptResult = await runtime.prompt(prependSystemContext(params.content, systemContext));
\t\treturn { done: promptResult.status === "finished" };
\t} catch (error) {
\t\temitCaughtError(ctx, error, "runtime", runtime.id);
\t\treturn { done: false };
\t} finally {
\t\tawait refreshPanelTitle(ctx, runtime.id, { force: true });
\t}
};"""

E8_ANCHOR = "function toSessionInfo(summary) {"

E8_REPL = """/**
 * 面板标签页跟随会话标题（本机补丁 panel-title，对齐 Claude Code）。
 * 会话不在内存 / 视图已切到别的会话 ⇒ 不动；force 或内存里没标题 ⇒ 现查 listSessions。
 */
async function refreshPanelTitle(ctx, sessionId, options) {
\tif (typeof ctx.setPanelTitle !== "function") return;
\tconst current = ctx.getSession();
\tif (current === void 0 || current.id !== sessionId) return;
\tconst force = options?.force === true;
\tlet title = force ? "" : typeof current.summary?.title === "string" ? current.summary.title.trim() : "";
\tif (title.length === 0) {
\t\tlet summary;
\t\ttry {
\t\t\tsummary = (await ctx.harness.listSessions({ sessionId }))[0];
\t\t} catch {
\t\t\treturn;
\t\t}
\t\tif (typeof summary?.title === "string") title = summary.title.trim();
\t}
\tctx.setPanelTitle(title);
}
function toSessionInfo(summary) {"""

E9_ANCHOR = """\tawait ctx.harness.renameSession({
\t\tid: runtime.id,
\t\ttitle: newTitle
\t});"""

E9_REPL = E9_ANCHOR + """
\tctx.setPanelTitle(newTitle);"""

E10_ANCHOR = """\tbroadcastInternal(event, data, targetWebviewId) {
\t\tconst msg = {
\t\t\tevent,
\t\t\tdata
\t\t};"""

E10_REPL = E10_ANCHOR + """
\t\tif (event === Events$2.NewConversation) {
\t\t\tif (targetWebviewId) this.setPanelTitle(targetWebviewId, "");
\t\t\telse this.panels.forEach((_, panelWebviewId) => this.setPanelTitle(panelWebviewId, ""));
\t\t}"""

E11_ANCHOR = """\tlet runtime;
\ttry {
\t\truntime = await ctx.getOrCreateSession(params.model, params.effort ?? (params.thinking === true ? "on" : "off"), params.sessionId);
\t} catch (error) {
\t\temitCaughtError(ctx, error, "preflight");
\t\treturn { done: false };
\t}"""

E11_REPL = E11_ANCHOR + """
\tif (typeof params.sessionId === "string" && params.sessionId.length > 0) await refreshPanelTitle(ctx, runtime.id);"""

EDITS = [
    ("E1", E1_ANCHOR, E1_REPL, 1),
    ("E2", E2_ANCHOR, E2_REPL, 1),
    ("E3", E3_ANCHOR, E3_REPL, 1),
    ("E4", E4_ANCHOR, E4_REPL, 1),
    ("E5", E5_ANCHOR, E5_REPL, 2),
    ("E6", E6_ANCHOR, E6_REPL, 1),
    ("E7", E7_ANCHOR, E7_REPL, 1),
    ("E8", E8_ANCHOR, E8_REPL, 1),
    ("E9", E9_ANCHOR, E9_REPL, 1),
    ("E10", E10_ANCHOR, E10_REPL, 1),
    ("E11", E11_ANCHOR, E11_REPL, 1),
]


def main():
    bundle = resolve_bundle(sys.argv)
    version = version_of(bundle)
    print("目标 bundle: %s（版本 %s）" % (bundle, version))
    text = bundle.read_text(encoding="utf-8")

    changed = []
    for name, anchor, repl, expect in EDITS:
        if repl in text:
            print("[%s] skip（已打过）" % name)
            continue
        count = text.count(anchor)
        if count != expect:
            sys.exit("FATAL: [%s] 锚点命中 %d 次（应为 %d）→ 中止，未写文件"
                     % (name, count, expect))
        text = text.replace(anchor, repl, expect)
        changed.append(name)

    if not changed:
        print("全部编辑已存在，无需改动。")
        return

    backup = patch_common.make_backup(str(bundle), SUFFIX)
    print("备份: %s" % backup)

    ok = patch_common.write_validated(str(bundle), text, patch_common.node_check,
                                      label="extension.js")
    if not ok:
        sys.exit("FATAL: 校验失败，原文件未改动")

    new_text = bundle.read_text(encoding="utf-8")
    checks = {
        "panels 表": "panels = /* @__PURE__ */ new Map();",
        "sink 注入": "this.bridgeHandler.panelTitleSink =",
        "setPanelTitle 方法": "\tsetPanelTitle(webviewId, title) {",
        "ctx.setPanelTitle": "setPanelTitle: (title, targetWebviewId) => {",
        "refreshPanelTitle 助手": "async function refreshPanelTitle(ctx, sessionId, options) {",
    }
    missing = [k for k, v in checks.items() if v not in new_text]
    if missing:
        sys.exit("FATAL: 落地后自检失败，缺：%s" % ", ".join(missing))

    print("已应用: %s" % ", ".join(changed))
    print("自检 OK：%d 项特征全部命中；新文件 %d B" % (len(checks), len(new_text.encode())))
    print("提示：改动 bundle 后必须 Reload VS Code 窗口才生效。")


if __name__ == "__main__":
    main()
