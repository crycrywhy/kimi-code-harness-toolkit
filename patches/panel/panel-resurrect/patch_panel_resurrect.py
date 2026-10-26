#!/usr/bin/env python3
"""Kimi Code VS Code — 面板在「Restart Extension Host」后也能复活(2026-10-26)。

问题：panel-restore 依赖 `WebviewPanelSerializer`，它只在 **Reload Window**（工作台重放编辑器）
时被调用；「Restart Extension Host」只重启扩展宿主、不重放编辑器 ⇒ 面板全丢，记录白存。

做法（extension.js，4 处）：
  R1 增加 `sawPanelDeserialize` 标记
  R2 构造时排一个 2.5 s 的「补建面板」任务
  R3 `restorePanel()` 一进来就置标记（说明这次是窗口重放路径，别补建，避免重复）
  R4 新增 `schedulePanelResurrection()` / `resurrectPanels()`：
     超时后若 `sawPanelDeserialize` 仍为 false，说明是宿主重启路径 ⇒ 按
     `workspaceState.kimi.panelSessions` 记录逐个重建面板（保留标题/列位），
     并把 sessionId 塞进 `pendingPanelOpens` 让 webview 挂载后自动打开原会话。
  R5 `createPanel()` 的记录补 `viewColumn`，重启后能回到原来的列。

幂等：已打过则 skip；锚点命中数不符立即中止、不写文件。
升级 VSIX 会覆盖，重跑本脚本即可（依赖 patch_panel_restore.py 先打过）。
"""
import glob
import re
import sys
from pathlib import Path

import patch_common

DEFAULT_GLOB = str(
    Path.home() / ".vscode-server/extensions/moonshot-ai.kimi-code-*/dist/extension.js"
)
SUFFIX = "panel-resurrect"

R1_ANCHOR = """	panelKeys = /* @__PURE__ */ new Map();
	pendingPanelOpens = /* @__PURE__ */ new Map();"""

R1_REPL = """	panelKeys = /* @__PURE__ */ new Map();
	pendingPanelOpens = /* @__PURE__ */ new Map();
	sawPanelDeserialize = false;"""

R2_ANCHOR = """		this.bridgeHandler.panelRecordSink = (webviewId, patch) => this.updatePanelRecord(webviewId, patch);
	}"""

R2_REPL = """		this.bridgeHandler.panelRecordSink = (webviewId, patch) => this.updatePanelRecord(webviewId, patch);
		this.schedulePanelResurrection();
	}"""

R3_ANCHOR = """	async restorePanel(panel, state) {
		const hintedKey ="""

R3_REPL = '''	async restorePanel(panel, state) {
		this.sawPanelDeserialize = true;
		log("[panel-restore] restorePanel begin");
		const hintedKey ='''

R4_ANCHOR = """	schedulePanelRecordRemoval(key) {
		setTimeout(() => this.removePanelRecord(key), 3e4);
	}"""

R4_REPL = '\tschedulePanelRecordRemoval(key) {\n\t\tsetTimeout(() => this.removePanelRecord(key), 3e4);\n\t}\n\t/** 宿主关闭/重启前，把面板切回启动画面（KIMI splash），让用户看到「正在重载」而不是一帧静止的旧内容。 */\n\tshowPanelReloading() {\n\t\tif (this.panels.size === 0) return;\n\t\tlog(`[panel-restore] show reloading state for ${this.panels.size} panel(s)`);\n\t\tfor (const [webviewId, panel] of this.panels) try {\n\t\t\tpanel.webview.html = this.getHtml(webviewId, panel.webview, this.panelKeys.get(webviewId));\n\t\t} catch (error) {\n\t\t\tlog(`[panel-restore] show reloading failed: ${error instanceof Error ? error.message : String(error)}`);\n\t\t}\n\t}\n\t/** 「Restart Extension Host」不会重放编辑器 ⇒ 旧标签页会留下但已断链，按记录原位重建并清掉死标签（本机补丁 panel-resurrect）。 */\n\tschedulePanelResurrection() {\n\t\tsetTimeout(() => {\n\t\t\tthis.resurrectPanels().catch(() => void 0);\n\t\t}, 5e3);\n\t}\n\tcollectStalePanelTabs() {\n\t\tconst stale = [];\n\t\ttry {\n\t\t\tfor (const group of vscode.window.tabGroups.all) {\n\t\t\t\tfor (const tab of group.tabs) {\n\t\t\t\t\tconst input = tab.input;\n\t\t\t\t\t// 注意：VS Code 把 webview 标签的 viewType 报成 "mainThreadWebview-kimiPanel"，\n\t\t\t\t\t// 不能做全等比较（Claude Code 那边用的是 viewType.includes("claudeVSCodePanel")）。\n\t\t\t\t\tconst viewType = input && typeof input === "object" ? input.viewType : void 0;\n\t\t\t\t\tif (typeof viewType === "string" && viewType.includes("kimiPanel")) stale.push({\n\t\t\t\t\t\ttab,\n\t\t\t\t\t\tcolumn: group.viewColumn\n\t\t\t\t\t});\n\t\t\t\t}\n\t\t\t}\n\t\t} catch (error) {\n\t\t\tlog(`[panel-restore] collectStalePanelTabs failed: ${error instanceof Error ? error.message : String(error)}`);\n\t\t}\n\t\treturn stale;\n\t}\n\tasync resurrectPanels() {\n\t\tconst records = this.readPanelRecords();\n\t\tlog(`[panel-restore] resurrect check: restart=${this.extensionRestartConfirmed} sawDeserialize=${this.sawPanelDeserialize} panels=${this.panels.size} records=${records.length}`);\n\t\t// 2026-10-26 v4：不再要求「确认是宿主重启」。换一台客户端（如 PC2）连同一台远端时，\n\t\t// VS Code 会新开一个窗口、既不重放编辑器也不调序列化器，而 sessionId 也变了 ⇒\n\t\t// 旧逻辑的 restart 判定必然为 false，补丁在那里就完全不生效。\n\t\t// 现在改为：只要「本次没走序列化器」且「本窗口一个面板都没有」且「有记录」，就补建。\n\t\t// 安全性由另外两道闸保证：① 用户手动关面板会在 30s 后删记录；② 同窗口重复开窗已被 panels.size 挡住。\n\t\tif (this.sawPanelDeserialize) return;\n\t\tif (this.panels.size > 0) return;\n\t\tif (records.length === 0) return;\n\t\tconst stale = this.collectStalePanelTabs();\n\t\tlog(`[panel-restore] stale kimi tabs=${stale.length} labels=${JSON.stringify(stale.map((item) => item.tab.label))}`);\n\t\tif (stale.length === 0) try {\n\t\t\tconst dump = vscode.window.tabGroups.all.flatMap((group) => group.tabs.map((tab) => ({\n\t\t\t\tcol: group.viewColumn,\n\t\t\t\tlabel: tab.label,\n\t\t\t\tviewType: tab.input && typeof tab.input === "object" ? tab.input.viewType ?? null : null,\n\t\t\t\tkind: tab.input === null || tab.input === void 0 ? "null" : tab.input.constructor?.name ?? typeof tab.input\n\t\t\t})));\n\t\t\tlog(`[panel-restore] tab dump: ${JSON.stringify(dump).slice(0, 600)}`);\n\t\t} catch (error) {\n\t\t\tlog(`[panel-restore] tab dump failed: ${error instanceof Error ? error.message : String(error)}`);\n\t\t}\n\t\tconst claimed = new Set(this.panelKeys.values());\n\t\tconst created = [];\n\t\tlet index = 0;\n\t\tfor (const record of records) {\n\t\t\tindex += 1;\n\t\t\tif (claimed.has(record.key)) continue;\n\t\t\tconst sessionId = typeof record.sessionId === "string" && record.sessionId.length > 0 ? record.sessionId : void 0;\n\t\t\tif (sessionId !== void 0) {\n\t\t\t\tlet exists = true;\n\t\t\t\ttry {\n\t\t\t\t\texists = (await this.harness.listSessions({ sessionId })).length > 0;\n\t\t\t\t} catch {\n\t\t\t\t\texists = true;\n\t\t\t\t}\n\t\t\t\tif (!exists) continue;\n\t\t\t}\n\t\t\tconst title = typeof record.title === "string" && record.title.length > 0 ? record.title : "Kimi Code";\n\t\t\tconst fallbackColumn = stale[index - 1]?.column;\n\t\t\tconst column = typeof record.viewColumn === "number" && record.viewColumn > 0 ? record.viewColumn : fallbackColumn ?? vscode.ViewColumn.One;\n\t\t\tconst webviewId = `panel_${crypto.randomUUID()}`;\n\t\t\tconst panel = vscode.window.createWebviewPanel("kimiPanel", title, column, {\n\t\t\t\tenableScripts: true,\n\t\t\t\tretainContextWhenHidden: true,\n\t\t\t\tlocalResourceRoots: [this.extensionUri]\n\t\t\t});\n\t\t\tthis.registerPanel(webviewId, panel, record.key);\n\t\t\tthis.setPanelTitle(webviewId, title);\n\t\t\tif (sessionId !== void 0) this.pendingPanelOpens.set(webviewId, sessionId);\n\t\t\tcreated.push(record.key);\n\t\t}\n\t\tif (created.length > 0 && stale.length > 0) try {\n\t\t\tconst closed = await vscode.window.tabGroups.close(stale.map((item) => item.tab));\n\t\t\tlog(`[panel-restore] close stale tabs -> ${closed}`);\n\t\t} catch (error) {\n\t\t\tlog(`[panel-restore] close stale tabs failed: ${error instanceof Error ? error.message : String(error)}`);\n\t\t}\n\t}'

R5_ANCHOR = """		this.writePanelRecord({
			key: this.panelKeys.get(webviewId),
			sessionId: null,
			title: "Kimi Code"
		});"""

R5_REPL = """		this.writePanelRecord({
			key: this.panelKeys.get(webviewId),
			sessionId: null,
			title: "Kimi Code",
			viewColumn: panel.viewColumn ?? 1
		});"""

# 有些编辑会把锚点本身改掉（例如 E11 把 readPanelRecords 的键换成带 scope 的版本），
# 此时「repl in text」判不出来 ⇒ 用特征串判定「已打过」。
SKIP_SIG = {
    "E11": "get clientScopeKey()",
    "E12": "update(`kimi.panelSessions.${this.clientScopeKey}`",
}

EDITS = [
    ("R1", R1_ANCHOR, R1_REPL, 1),
    ("R2", R2_ANCHOR, R2_REPL, 1),
    ("R3", R3_ANCHOR, R3_REPL, 1),
    ("R4", R4_ANCHOR, R4_REPL, 1),
    ("R5", R5_ANCHOR, R5_REPL, 1),
]


# —— v3–v8 追加编辑（宿主重启判定 / 序列化器前移 / 死标签识别 / 重载画面） ——
E6_ANCHOR = """	provider = new KimiWebviewProvider(context.extensionUri, context, () => outputChannel?.show(), (message) => log(message));"""
E6_REPL = '\tconst previousActivationSessionId = context.workspaceState.get("kimi.lastActivationSessionId");\n\tconst currentActivationSessionId = vscode.env.sessionId;\n\tconst activationIsRestart = typeof previousActivationSessionId === "string" && previousActivationSessionId === currentActivationSessionId;\n\tcontext.workspaceState.update("kimi.lastActivationSessionId", currentActivationSessionId).then(void 0, () => void 0);\n\tlog(`[panel-restore] activate: restart=${activationIsRestart}`);\n' + E6_ANCHOR + """
	provider.extensionRestartConfirmed = activationIsRestart;"""

E7_ANCHOR = """	sawPanelDeserialize = false;"""
E7_REPL = """	sawPanelDeserialize = false;
	extensionRestartConfirmed = false;
	cachedClientScope = void 0;"""

E8_ANCHOR = """	outputChannel = vscode.window.createOutputChannel("Kimi Code");"""
E8_REPL = E8_ANCHOR + """
""" + '\t// 本机补丁 panel-resurrect：序列化器必须在 activate 最早处注册 —— 宿主重启时 VS Code 会立刻\n\t// 请求恢复 webview，晚注册（原先在 activate 尾部）会被判定「无序列化器」而丢弃面板。\n\tcontext.subscriptions.push(vscode.window.registerWebviewPanelSerializer("kimiPanel", {\n\t\tdeserializeWebviewPanel: async (panel, state) => {\n\t\t\tlog(`[panel-restore] serializer invoked (title=${panel?.title ?? "?"})`);\n\t\t\tfor (let wait = 0; wait < 600 && provider === void 0; wait += 1) await new Promise((resolve) => setTimeout(resolve, 50));\n\t\t\tif (provider === void 0) {\n\t\t\t\tlog("[panel-restore] serializer aborted: provider not ready");\n\t\t\t\treturn;\n\t\t\t}\n\t\t\tawait provider.restorePanel(panel, state);\n\t\t}\n\t}));\n'

E9_ANCHOR = """	}), vscode.window.registerWebviewPanelSerializer("kimiPanel", {
		deserializeWebviewPanel: (panel, state) => provider === void 0 ? Promise.resolve() : provider.restorePanel(panel, state)
	}), vscode.window.registerWebviewViewProvider("kimi.webview", provider, { webviewOptions: { retainContextWhenHidden: true } }));"""
E9_REPL = """	}), vscode.window.registerWebviewViewProvider("kimi.webview", provider, { webviewOptions: { retainContextWhenHidden: true } }));"""

E10_ANCHOR = """async function deactivate() {
	log("Kimi Code deactivating");
	try {
		await provider?.shutdown();"""
E10_REPL = """async function deactivate() {
	log("Kimi Code deactivating");
	try {
		provider?.showPanelReloading();
		await provider?.shutdown();"""

EDITS.extend([
    ("E6", E6_ANCHOR, E6_REPL, 1),
    ("E7", E7_ANCHOR, E7_REPL, 1),
    ("E8", E8_ANCHOR, E8_REPL, 1),
    ("E9", E9_ANCHOR, E9_REPL, 1),
    ("E10", E10_ANCHOR, E10_REPL, 1),
])

# —— v5：面板记录按「客户端」分桶（借鉴 Claude Code 各窗口各记一份）——
# 干净链路上 panel-restore 先建了不带 scope 的 readPanelRecords；这里把它换成带 clientScopeKey 的版本。
E11_ANCHOR = """	readPanelRecords() {
		const raw = this.workspaceState.get("kimi.panelSessions");"""
E11_REPL = '\t/** 按客户端隔离的存储键：Claude Code 用 workspaceState 各窗口各记一份，我们按「客户端」分桶，\n\t *  这样 PC1 只记 PC1 上次关掉的面板、PC2 只记 PC2 的，互不覆盖（2026-10-26 用户观察后要求借鉴）。\n\t *  客户端标识优先取 SSH 来源（远程场景每台客户端不同），退化为 VS Code 的 machineId / 远端名。 */\n\tget clientScopeKey() {\n\t\tif (this.cachedClientScope !== void 0) return this.cachedClientScope;\n\t\tlet tag;\n\t\ttry {\n\t\t\tconst ssh = process.env.SSH_CONNECTION || process.env.SSH_CLIENT || "";\n\t\t\tif (ssh.trim().length > 0) tag = `ssh_${ssh.trim().replace(/\\s+/g, "_")}`;\n\t\t} catch {}\n\t\tif (tag === void 0) try {\n\t\t\ttag = `machine_${vscode.env.machineId ?? "unknown"}`;\n\t\t} catch {\n\t\t\ttag = "machine_unknown";\n\t\t}\n\t\tthis.cachedClientScope = tag;\n\t\tlog(`[panel-restore] client scope = ${tag}`);\n\t\treturn tag;\n\t}\n' + """
	readPanelRecords() {
		const raw = this.workspaceState.get(`kimi.panelSessions.${this.clientScopeKey}`);"""

E12_ANCHOR = """			await this.workspaceState.update("kimi.panelSessions", next);"""
E12_REPL = """			await this.workspaceState.update(`kimi.panelSessions.${this.clientScopeKey}`, next);"""

EDITS.extend([
    ("E11", E11_ANCHOR, E11_REPL, 1),
    ("E12", E12_ANCHOR, E12_REPL, 1),
])


def resolve_bundle(argv):
    if len(argv) > 1:
        return Path(argv[1]).expanduser()
    cands = [Path(p) for p in glob.glob(DEFAULT_GLOB)]
    if not cands:
        sys.exit("FATAL: 没找到任何 kimi-code 扩展安装，glob=%s" % DEFAULT_GLOB)
    cands.sort(key=lambda p: p.stat().st_mtime)
    return cands[-1]


def version_of(bundle):
    m = re.search(r"moonshot-ai\.kimi-code-([0-9][^-]*)-", bundle.parent.parent.name)
    return m.group(1) if m else "unknown"


def main():
    bundle = resolve_bundle(sys.argv)
    print("目标 bundle: %s（版本 %s）" % (bundle, version_of(bundle)))
    text = bundle.read_text(encoding="utf-8")
    changed = []
    for name, anchor, repl, expect in EDITS:
        if repl in text or (name in SKIP_SIG and SKIP_SIG[name] in text):
            print("[%s] skip（已打过）" % name)
            continue
        count = text.count(anchor)
        if count != expect:
            sys.exit("FATAL: [%s] 锚点命中 %d 次（应为 %d）→ 中止，未写文件" % (name, count, expect))
        text = text.replace(anchor, repl, expect)
        changed.append(name)
    if not changed:
        print("全部编辑已存在，无需改动。")
        return
    print("备份: %s" % patch_common.make_backup(str(bundle), SUFFIX))
    if not patch_common.write_validated(str(bundle), text, patch_common.node_check, label="extension.js"):
        sys.exit("FATAL: 校验失败，原文件未改动")
    new_text = bundle.read_text(encoding="utf-8")
    checks = {
        "标记字段": "sawPanelDeserialize = false;",
        "构造调用": "this.schedulePanelResurrection();",
        "补建方法": "\tasync resurrectPanels() {",
        "列位记录": "viewColumn: panel.viewColumn ?? 1",
    }
    missing = [k for k, v in checks.items() if v not in new_text]
    if missing:
        sys.exit("FATAL: 落地后自检失败，缺：%s" % ", ".join(missing))
    print("已应用: %s" % ", ".join(changed))
    print("提示：改动 bundle 后必须 Reload Window 才生效。")


if __name__ == "__main__":
    main()
