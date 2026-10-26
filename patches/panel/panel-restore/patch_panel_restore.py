#!/usr/bin/env python3
"""Kimi Code VS Code — 面板跨 Reload 原位恢复(2026-10-26)。

问题：`kimi.openInTab` 开出的面板既没注册 `WebviewPanelSerializer`，也没有任何
持久化，于是 Reload 窗口后标签页全没，必须重新开 tab 再从侧边栏点历史会话。

做法（extension.js 10 处 + webview.js 1 处）：
  H1  KimiWebviewProvider 加 panelKeys / pendingPanelOpens / workspaceState
  H2  createPanel 改走 registerPanel()：登记面板 key + 落盘记录 {key,sessionId,title}
  H3  setPanelTitle 顺手把标题写进记录
  H4  新增面板记录读写 / 待恢复队列 / restorePanel()
  H5  setupWebview 接受 panelKey，并在收到该 webview 第一条 RPC 时补发 openSession
  H6  getHtml 注入 window.__kimiPanelKey（供 webview 侧 setState 持久化）
  H7  reloadWebview / reloadAllWebviews 带上 key
  H8  createContext 暴露 ctx.updatePanelRecord(patch)
  H9  loadKimiSessionHistory / resetSession / streamChat 维护记录里的 sessionId
  H10 activate() 注册 "kimiPanel" 的 WebviewPanelSerializer
  W1  webview.js 末尾（IIFE 内）注入：启动时 vscode.setState({kimiPanelKey})；
      收到宿主 "openSession" 事件时走与侧边栏点击相同的
      `loadSessionHistory → store.loadSession` 回放路径

幂等：已打过则 skip；锚点命中数不符立即中止、不写文件。
升级 VSIX 会覆盖本补丁，重跑本脚本即可。
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

SUFFIX = "panel-restore"
RECORD_KEY = "kimi.panelSessions"

H1_ANCHOR = """var KimiWebviewProvider = class {
\textensionUri;
\twebviews = /* @__PURE__ */ new Map();
\tpanels = /* @__PURE__ */ new Map();
\tbridgeHandler;
\tconstructor(extensionUri, context, showLogs, writeLog) {
\t\tthis.extensionUri = extensionUri;
\t\tthis.bridgeHandler = new BridgeHandler(this.broadcastInternal.bind(this), context.workspaceState, context.globalStorageUri.fsPath, this.reloadWebview.bind(this), showLogs, writeLog);
\t\tthis.bridgeHandler.panelTitleSink = (webviewId, title) => this.setPanelTitle(webviewId, title);
\t}"""

H1_REPL = """var KimiWebviewProvider = class {
\textensionUri;
\twebviews = /* @__PURE__ */ new Map();
\tpanels = /* @__PURE__ */ new Map();
\tpanelKeys = /* @__PURE__ */ new Map();
\tpendingPanelOpens = /* @__PURE__ */ new Map();
\tworkspaceState;
\tbridgeHandler;
\tconstructor(extensionUri, context, showLogs, writeLog) {
\t\tthis.extensionUri = extensionUri;
\t\tthis.workspaceState = context.workspaceState;
\t\tthis.bridgeHandler = new BridgeHandler(this.broadcastInternal.bind(this), context.workspaceState, context.globalStorageUri.fsPath, this.reloadWebview.bind(this), showLogs, writeLog);
\t\tthis.bridgeHandler.panelTitleSink = (webviewId, title) => this.setPanelTitle(webviewId, title);
\t\tthis.bridgeHandler.panelRecordSink = (webviewId, patch) => this.updatePanelRecord(webviewId, patch);
\t}"""

H2_ANCHOR = """\tcreatePanel() {
\t\tconst webviewId = `panel_${crypto.randomUUID()}`;
\t\tconst panel = vscode.window.createWebviewPanel("kimiPanel", "Kimi Code", vscode.ViewColumn.One, {
\t\t\tenableScripts: true,
\t\t\tretainContextWhenHidden: true,
\t\t\tlocalResourceRoots: [this.extensionUri]
\t\t});
\t\tthis.setupWebview(webviewId, panel.webview);
\t\tthis.panels.set(webviewId, panel);
\t\tpanel.onDidDispose(() => {
\t\t\tthis.bridgeHandler.disposeView(webviewId);
\t\t\tthis.webviews.delete(webviewId);
\t\t\tthis.panels.delete(webviewId);
\t\t});
\t\treturn panel;
\t}"""

H2_REPL = """\tcreatePanel() {
\t\tconst webviewId = `panel_${crypto.randomUUID()}`;
\t\tconst panel = vscode.window.createWebviewPanel("kimiPanel", "Kimi Code", vscode.ViewColumn.One, {
\t\t\tenableScripts: true,
\t\t\tretainContextWhenHidden: true,
\t\t\tlocalResourceRoots: [this.extensionUri]
\t\t});
\t\tthis.registerPanel(webviewId, panel, `panelkey_${crypto.randomUUID()}`);
\t\tthis.writePanelRecord({
\t\t\tkey: this.panelKeys.get(webviewId),
\t\t\tsessionId: null,
\t\t\ttitle: "Kimi Code"
\t\t});
\t\treturn panel;
\t}
\tregisterPanel(webviewId, panel, panelKey) {
\t\tthis.setupWebview(webviewId, panel.webview, panelKey);
\t\tthis.panels.set(webviewId, panel);
\t\tthis.panelKeys.set(webviewId, panelKey);
\t\tpanel.onDidDispose(() => {
\t\t\tthis.bridgeHandler.disposeView(webviewId);
\t\t\tthis.webviews.delete(webviewId);
\t\t\tthis.panels.delete(webviewId);
\t\t\tthis.panelKeys.delete(webviewId);
\t\t\tthis.schedulePanelRecordRemoval(panelKey);
\t\t});
\t}
\t/** 面板关闭后延迟删记录：Reload 时宿主也会触发 onDidDispose，立即删会把待恢复记录一起抹掉。 */
\tschedulePanelRecordRemoval(key) {
\t\tsetTimeout(() => this.removePanelRecord(key), 3e4);
\t}
\t/** Reload 后由 WebviewPanelSerializer 调用：原地重建面板并按记录恢复会话（本机补丁 panel-restore）。 */
\tasync restorePanel(panel, state) {
\t\tconst hintedKey = typeof state?.kimiPanelKey === "string" && state.kimiPanelKey.length > 0 ? state.kimiPanelKey : void 0;
\t\tconst records = this.readPanelRecords();
\t\tconst currentTitle = typeof panel.title === "string" ? panel.title.trim() : "";
\t\tlet record = hintedKey === void 0 ? void 0 : records.find((item) => item.key === hintedKey);
\t\tif (record === void 0 && currentTitle.length > 0 && currentTitle !== "Kimi Code") record = records.find((item) => typeof item.title === "string" && item.title === currentTitle && typeof item.sessionId === "string");
\t\tlet recoveredSessionId;
\t\tif (record === void 0 && currentTitle.length > 0 && currentTitle !== "Kimi Code") {
\t\t\tconst matches = await this.harness.listSessions().then((list) => list.filter((item) => typeof item.title === "string" && item.title.trim() === currentTitle)).catch(() => []);
\t\t\tif (matches.length > 0) {
\t\t\t\tmatches.sort((a, b) => (b.updatedAt ?? 0) - (a.updatedAt ?? 0));
\t\t\t\trecoveredSessionId = matches[0].id;
\t\t\t}
\t\t}
\t\tconst webviewId = `panel_${crypto.randomUUID()}`;
\t\tthis.registerPanel(webviewId, panel, record?.key ?? `panelkey_${crypto.randomUUID()}`);
\t\tif (record === void 0) {
\t\t\tthis.writePanelRecord({
\t\t\t\tkey: this.panelKeys.get(webviewId),
\t\t\t\tsessionId: recoveredSessionId ?? null,
\t\t\t\ttitle: currentTitle.length > 0 ? currentTitle : "Kimi Code"
\t\t\t});
\t\t\tif (recoveredSessionId !== void 0) this.pendingPanelOpens.set(webviewId, recoveredSessionId);
\t\t\treturn;
\t\t}
\t\tif (typeof record.title === "string" && record.title.length > 0) this.setPanelTitle(webviewId, record.title);
\t\tconst sessionId = typeof record.sessionId === "string" && record.sessionId.length > 0 ? record.sessionId : void 0;
\t\tif (sessionId === void 0) return;
\t\tlet stillExists = true;
\t\ttry {
\t\t\tstillExists = (await this.harness.listSessions({ sessionId })).length > 0;
\t\t} catch {
\t\t\tstillExists = true;
\t\t}
\t\tif (!stillExists) {
\t\t\tthis.updatePanelRecord(webviewId, { sessionId: null });
\t\t\treturn;
\t\t}
\t\tthis.pendingPanelOpens.set(webviewId, sessionId);
\t}
\treadPanelRecords() {
\t\tconst raw = this.workspaceState.get("kimi.panelSessions");
\t\treturn Array.isArray(raw) ? raw.filter((item) => item !== null && typeof item === "object" && typeof item.key === "string") : [];
\t}
\tmutatePanelRecords(mutator) {
\t\tthis.panelRecordChain = (this.panelRecordChain ?? Promise.resolve()).then(async () => {
\t\t\tconst records = this.readPanelRecords();
\t\t\tconst next = mutator(records);
\t\t\tif (next === void 0) return;
\t\t\tawait this.workspaceState.update("kimi.panelSessions", next);
\t\t}).catch(() => void 0);
\t\treturn this.panelRecordChain;
\t}
\twritePanelRecord(entry) {
\t\tthis.mutatePanelRecords((records) => {
\t\t\tconst index = records.findIndex((item) => item.key === entry.key);
\t\t\tif (index < 0) records.push(entry);
\t\t\telse records[index] = {
\t\t\t\t...records[index],
\t\t\t\t...entry
\t\t\t};
\t\t\tif (records.length > 12) records.splice(0, records.length - 12);
\t\t\treturn records;
\t\t});
\t}
\tupdatePanelRecord(webviewId, patch) {
\t\tconst key = this.panelKeys.get(webviewId);
\t\tif (key === void 0) return;
\t\tthis.mutatePanelRecords((records) => {
\t\t\tconst record = records.find((item) => item.key === key);
\t\t\tif (record === void 0) return void 0;
\t\t\tconst next = {
\t\t\t\t...record,
\t\t\t\t...patch
\t\t\t};
\t\t\tif (next.sessionId === record.sessionId && next.title === record.title) return void 0;
\t\t\trecords[records.indexOf(record)] = next;
\t\t\treturn records;
\t\t});
\t}
\tremovePanelRecord(key) {
\t\tthis.mutatePanelRecords((records) => {
\t\t\tconst remaining = records.filter((item) => item.key !== key);
\t\t\treturn remaining.length === records.length ? void 0 : remaining;
\t\t});
\t}
\tflushPendingPanelOpen(webviewId, webview) {
\t\tconst sessionId = this.pendingPanelOpens.get(webviewId);
\t\tif (sessionId === void 0) return;
\t\tthis.pendingPanelOpens.delete(webviewId);
\t\tsetTimeout(() => {
\t\t\ttry {
\t\t\t\twebview.postMessage({
\t\t\t\t\tevent: "openSession",
\t\t\t\t\tdata: { sessionId }
\t\t\t\t});
\t\t\t} catch {}
\t\t}, 600);
\t}"""

H3_ANCHOR = """\tsetPanelTitle(webviewId, title) {
\t\tconst panel = this.panels.get(webviewId);
\t\tif (panel === void 0) return;
\t\tconst trimmed = typeof title === "string" ? title.trim() : "";
\t\tconst next = trimmed.length > 0 ? trimmed.slice(0, 120) : "Kimi Code";
\t\tif (panel.title !== next) panel.title = next;
\t}"""

H3_REPL = """\tsetPanelTitle(webviewId, title) {
\t\tconst panel = this.panels.get(webviewId);
\t\tif (panel === void 0) return;
\t\tconst trimmed = typeof title === "string" ? title.trim() : "";
\t\tconst next = trimmed.length > 0 ? trimmed.slice(0, 120) : "Kimi Code";
\t\tif (panel.title !== next) panel.title = next;
\t\tthis.updatePanelRecord(webviewId, { title: next });
\t}"""

H5_ANCHOR = """\tsetupWebview(webviewId, webview) {
\t\twebview.options = {
\t\t\tenableScripts: true,
\t\t\tlocalResourceRoots: [this.extensionUri]
\t\t};
\t\twebview.html = this.getHtml(webviewId, webview);
\t\tthis.webviews.set(webviewId, webview);
\t\twebview.onDidReceiveMessage(async (msg) => {
\t\t\tconst result = await this.bridgeHandler.handle(msg, webviewId);
\t\t\twebview.postMessage(result);
\t\t});
\t}"""

H5_REPL = """\tsetupWebview(webviewId, webview, panelKey) {
\t\twebview.options = {
\t\t\tenableScripts: true,
\t\t\tlocalResourceRoots: [this.extensionUri]
\t\t};
\t\twebview.html = this.getHtml(webviewId, webview, panelKey);
\t\tthis.webviews.set(webviewId, webview);
\t\twebview.onDidReceiveMessage(async (msg) => {
\t\t\tthis.flushPendingPanelOpen(webviewId, webview);
\t\t\tconst result = await this.bridgeHandler.handle(msg, webviewId);
\t\t\twebview.postMessage(result);
\t\t});
\t}"""

H7_ANCHOR = """\treloadWebview(webviewId) {
\t\tconst webview = this.webviews.get(webviewId);
\t\tif (webview) webview.html = this.getHtml(webviewId, webview);
\t}
\treloadAllWebviews() {
\t\tthis.webviews.forEach((webview, webviewId) => {
\t\t\twebview.html = this.getHtml(webviewId, webview);
\t\t});
\t}"""

H7_REPL = """\treloadWebview(webviewId) {
\t\tconst webview = this.webviews.get(webviewId);
\t\tif (webview) webview.html = this.getHtml(webviewId, webview, this.panelKeys.get(webviewId));
\t}
\treloadAllWebviews() {
\t\tthis.webviews.forEach((webview, webviewId) => {
\t\t\twebview.html = this.getHtml(webviewId, webview, this.panelKeys.get(webviewId));
\t\t});
\t}"""

H6_ANCHOR = """\tgetHtml(webviewId, webview) {
\t\tconst scriptUri = webview.asWebviewUri(vscode.Uri.joinPath(this.extensionUri, "dist", "webview.js"));"""

H6_REPL = """\tgetHtml(webviewId, webview, panelKey) {
\t\tconst scriptUri = webview.asWebviewUri(vscode.Uri.joinPath(this.extensionUri, "dist", "webview.js"));"""

H6B_ANCHOR = """  <title>Kimi Code</title>
</head>"""

H6B_REPL = """  <title>Kimi Code</title>
  <script nonce="${nonce}">window.__kimiPanelKey = ${JSON.stringify(typeof panelKey === "string" && panelKey.length > 0 ? panelKey : null)};</script>
</head>"""

H8_ANCHOR = """\t\t\tsetPanelTitle: (title, targetWebviewId) => {
\t\t\t\tif (typeof this.panelTitleSink !== "function") return;
\t\t\t\tthis.panelTitleSink(targetWebviewId ?? webviewId, title);
\t\t\t},"""

H8_REPL = H8_ANCHOR + """
\t\t\tupdatePanelRecord: (patch) => {
\t\t\t\tif (typeof this.panelRecordSink !== "function") return;
\t\t\t\tthis.panelRecordSink(webviewId, patch);
\t\t\t},"""

H9A_ANCHOR = """\t\tawait refreshPanelTitle(ctx, runtime.id);"""

H9A_REPL = """\t\tawait refreshPanelTitle(ctx, runtime.id);
\t\tctx.updatePanelRecord({ sessionId: runtime.id });"""

H9B_ANCHOR = """\tctx.setPanelTitle("");
\tctx.fileManager.clearTracked(ctx.webviewId);"""

H9B_REPL = """\tctx.setPanelTitle("");
\tctx.updatePanelRecord({ sessionId: null });
\tctx.fileManager.clearTracked(ctx.webviewId);"""

H9C_ANCHOR = """\t\tawait refreshPanelTitle(ctx, runtime.id, { force: true });"""

H9C_REPL = """\t\tawait refreshPanelTitle(ctx, runtime.id, { force: true });
\t\tctx.updatePanelRecord({ sessionId: runtime.id });"""

H10_ANCHOR = """\t}), vscode.window.registerWebviewViewProvider("kimi.webview", provider, { webviewOptions: { retainContextWhenHidden: true } }));"""

H10_REPL = """\t}), vscode.window.registerWebviewPanelSerializer("kimiPanel", {
\t\tdeserializeWebviewPanel: (panel, state) => provider === void 0 ? Promise.resolve() : provider.restorePanel(panel, state)
\t}), vscode.window.registerWebviewViewProvider("kimi.webview", provider, { webviewOptions: { retainContextWhenHidden: true } }));"""

EDITS = [
    ("H1", H1_ANCHOR, H1_REPL, 1),
    ("H2", H2_ANCHOR, H2_REPL, 1),
    ("H3", H3_ANCHOR, H3_REPL, 1),
    ("H5", H5_ANCHOR, H5_REPL, 1),
    ("H7", H7_ANCHOR, H7_REPL, 1),
    ("H6", H6_ANCHOR, H6_REPL, 1),
    ("H6B", H6B_ANCHOR, H6B_REPL, 1),
    ("H8", H8_ANCHOR, H8_REPL, 1),
    ("H9A", H9A_ANCHOR, H9A_REPL, 1),
    ("H9B", H9B_ANCHOR, H9B_REPL, 1),
    ("H9C", H9C_ANCHOR, H9C_REPL, 1),
    ("H10", H10_ANCHOR, H10_REPL, 1),
]

# ------------------------------------------------------------------ webview.js

W1_ANCHOR = """CY&&UH.createRoot(CY).render(d.jsx(S.StrictMode,{children:d.jsx(dP,{client:yhe,children:d.jsx(whe,{})})}))})();"""

W1_SNIPPET = r''';try{(function(){var __kk=typeof window<"u"?window.__kimiPanelKey:null;if(typeof __kk=="string"&&__kk.length>0){try{if(Qe&&Qe.vscode&&typeof Qe.vscode.setState=="function")Qe.vscode.setState({kimiPanelKey:__kk})}catch(__e){}}window.addEventListener("message",function(__ev){var __m=__ev&&__ev.data;if(!__m||__m.event!=="openSession"||!__m.data||typeof __m.data.sessionId!="string"||__m.data.sessionId.length===0)return;var __id=__m.data.sessionId;(async function(){try{var __h=await Qe.loadSessionHistory(__id);await J0.getState().loadSession(__id,__h)}catch(__err){console.error("[panel-restore] open session failed",__err)}})()})})()}catch(__err){console.error("[panel-restore] init failed",__err)}'''

W1_REPL = """CY&&UH.createRoot(CY).render(d.jsx(S.StrictMode,{children:d.jsx(dP,{client:yhe,children:d.jsx(whe,{})})}))""" + W1_SNIPPET + """})();"""


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


def apply_edits(path, edits, label):
    text = path.read_text(encoding="utf-8")
    changed = []
    for name, anchor, repl, expect in edits:
        if repl in text:
            print("[%s] skip（已打过）" % name)
            continue
        count = text.count(anchor)
        if count != expect:
            sys.exit("FATAL: [%s/%s] 锚点命中 %d 次（应为 %d）→ 中止，未写文件"
                     % (label, name, count, expect))
        text = text.replace(anchor, repl, expect)
        changed.append(name)
    return text, changed


PKG_ANCHOR = '"activationEvents": [],'
PKG_REPL = '"activationEvents": ["onWebviewPanel:kimiPanel"],'


def patch_package_json(pkg_path):
    """补激活事件：让 Reload 后 VS Code 为恢复面板而激活扩展。"""
    text = pkg_path.read_text(encoding="utf-8")
    if PKG_REPL in text:
        print("[P1] skip（已打过）")
        return False
    if text.count(PKG_ANCHOR) != 1:
        sys.exit("FATAL: [P1/package.json] 锚点命中 %d 次（应为 1）→ 中止"
                 % text.count(PKG_ANCHOR))
    new_text = text.replace(PKG_ANCHOR, PKG_REPL, 1)
    print("备份: %s" % patch_common.make_backup(str(pkg_path), SUFFIX))
    if not patch_common.write_validated(str(pkg_path), new_text, patch_common.json_check,
                                        label="package.json"):
        sys.exit("FATAL: package.json 校验失败，原文件未改动")
    return True


def main():
    bundle = resolve_bundle(sys.argv)
    version = version_of(bundle)
    print("目标 bundle: %s（版本 %s）" % (bundle, version))
    webview = bundle.parent / "webview.js"

    ext_text, ext_changed = apply_edits(bundle, EDITS, "extension.js")
    wv_text, wv_changed = apply_edits(webview, [("W1", W1_ANCHOR, W1_REPL, 1)], "webview.js")
    pkg_changed = patch_package_json(bundle.parent.parent / "package.json")

    if not ext_changed and not wv_changed:
        print("全部编辑已存在，无需改动。")
        if not pkg_changed:
            return

    if ext_changed:
        print("备份: %s" % patch_common.make_backup(str(bundle), SUFFIX))
        if not patch_common.write_validated(str(bundle), ext_text, patch_common.node_check,
                                            label="extension.js"):
            sys.exit("FATAL: extension.js 校验失败，原文件未改动")
    if wv_changed:
        print("备份: %s" % patch_common.make_backup(str(webview), SUFFIX))
        if not patch_common.write_validated(str(webview), wv_text, patch_common.node_check,
                                            label="webview.js"):
            sys.exit("FATAL: webview.js 校验失败，原文件未改动")

    ext_new = bundle.read_text(encoding="utf-8")
    checks = {
        "record 存储键": '"%s"' % RECORD_KEY,
        "restorePanel": "\tasync restorePanel(panel, state) {",
        "serializer 注册": 'vscode.window.registerWebviewPanelSerializer("kimiPanel"',
        "HTML 注入面板 key": "window.__kimiPanelKey = ${JSON.stringify(",
        "pending 队列刷新": "\tflushPendingPanelOpen(webviewId, webview) {",
    }
    missing = [k for k, v in checks.items() if v not in ext_new]
    if missing:
        sys.exit("FATAL: 落地后自检失败，缺：%s" % ", ".join(missing))
    wv_new = webview.read_text(encoding="utf-8")
    if "openSession" not in wv_new:
        sys.exit("FATAL: webview.js 自检失败（没有 openSession 处理）")

    print("extension.js 已应用: %s" % ", ".join(ext_changed))
    print("webview.js 已应用: %s" % (", ".join(wv_changed) or "（无变化）"))
    print("提示：改动 bundle 后必须 Reload VS Code 窗口才生效。")


if __name__ == "__main__":
    main()
