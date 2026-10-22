#!/usr/bin/env python3

import os as _os, glob as _glob
def _kimi_dist():
    p = _os.environ.get("KIMI_DIST")
    if p:
        return p
    c = sorted(_glob.glob(_os.path.expanduser("~/.vscode-server/extensions/moonshot-ai.kimi-code-*-linux-x64/dist")))
    if not c:
        raise SystemExit("找不到 Kimi Code 扩展目录，请设置 KIMI_DIST 指向 .../dist")
    return c[-1]
_KIMI_DIST = _kimi_dist()
"""apply_qd6.py —— P-QD v6：绝对路径归一化（<DATE>）

问题：模型有时把图片写成**绝对路径**（`~/tmp/.../x.png`）。
`resolveWorkspacePath()` 对绝对路径一律 `return void 0`（0.8.2 原生的越界保护），
于是 `getImageDataUri` 一进门就 notFound，`__pc5Repair` 的所有候选（wire 里也是绝对路径、
`reqDir` 也是绝对路径）同样被拒 ⇒ 修复器对这类路径完全失效，刷新多少次都没用。

修法：入口处把「落在 workDir 之下的绝对路径」转成 workDir 相对路径，其余原样透传
（工作区外的绝对路径仍然被原生保护拒绝，安全边界不变）。

三处锚点：
  1. `checkFilesExist`  —— webview 用它决定渲染 <img> 还是文字 chip，必须先归一化，
     否则绝对路径被判 false，连 <img> 都不会创建。
  2. `getImageDataUri`  —— 真正取图。
  3. `__pc5Repair`      —— 让 wire/text/fs 三条候选分支都能命中。

幂等；`--revert` 还原。
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time

DIST = _KIMI_DIST
EXT = os.path.join(DIST, "extension.js")
STAMP = time.strftime("%Y%m%d-%H%M%S")
SUFFIX = "before-qd6-abs-path"
MARK = "__pc5AbsToRel"

HELPER = '''const __pc5AbsToRel = (workDirUri, p) => {
	try {
		if (typeof p !== "string" || !p.startsWith("/")) return p;
		const wd = workDirUri && workDirUri.fsPath ? workDirUri.fsPath.replace(/\\/+$/, "") : "";
		if (!wd) return p;
		if (p === wd) return p;
		if (p.startsWith(wd + "/")) return p.slice(wd.length + 1);
		return p;   // 工作区外：原样透传，交给原生越界保护拒绝
	} catch (_) { return p; }
};
'''

OLD1 = '''const checkFilesExist = async ({ paths }, ctx) => {
	if (!ctx.workDirUri) return {};
	return Object.fromEntries(await Promise.all(paths.map(async (filePath) => [filePath, await resolveExistingWorkspaceFile(ctx.workDirUri, filePath) !== void 0])));
};'''
NEW1 = HELPER + '''const checkFilesExist = async ({ paths }, ctx) => {
	if (!ctx.workDirUri) return {};
	return Object.fromEntries(await Promise.all(paths.map(async (filePath) => {
		const rel = __pc5AbsToRel(ctx.workDirUri, filePath);
		return [filePath, await resolveExistingWorkspaceFile(ctx.workDirUri, rel) !== void 0];
	})));
};'''

OLD2 = '''const getImageDataUri = async ({ filePath }, ctx) => {
	if (!ctx.workDirUri) { try { ctx.logError(`[pc5] null:noWorkDir ${filePath}`); } catch (_) {} return null; }
	let resolved = await resolveExistingWorkspaceFile(ctx.workDirUri, decodeURIComponent(filePath));'''
NEW2 = '''const getImageDataUri = async ({ filePath }, ctx) => {
	if (!ctx.workDirUri) { try { ctx.logError(`[pc5] null:noWorkDir ${filePath}`); } catch (_) {} return null; }
	const __abs = filePath;
	filePath = __pc5AbsToRel(ctx.workDirUri, filePath);
	if (__abs !== filePath) { try { ctx.logError(`[pc6] abs->rel ${__abs} -> ${filePath}`); } catch (_) {} }
	let resolved = await resolveExistingWorkspaceFile(ctx.workDirUri, decodeURIComponent(filePath));'''

OLD3 = '''const __pc5Repair = async (ctx, filePath) => {
	if (!/\\.(png|jpe?g|gif|webp|svg|bmp|ico|tiff?)$/i.test(filePath)) return null;'''
NEW3 = '''const __pc5Repair = async (ctx, filePath) => {
	filePath = __pc5AbsToRel(ctx.workDirUri, filePath);
	if (!/\\.(png|jpe?g|gif|webp|svg|bmp|ico|tiff?)$/i.test(filePath)) return null;'''


def syntax_gate(path):
    with tempfile.NamedTemporaryFile(suffix=".mjs", delete=False) as tf:
        shutil.copyfileobj(open(path, "rb"), tf)
        tmp = tf.name
    r = subprocess.run(["node", "--check", tmp], capture_output=True, text=True)
    os.unlink(tmp)
    return r.returncode == 0, r.stderr[:900]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--revert", action="store_true")
    a = ap.parse_args()
    if a.revert:
        baks = sorted([p for p in os.listdir(DIST) if p.startswith("extension.js.bak-") and p.endswith(SUFFIX)])
        if not baks:
            print("no backup")
            return 1
        shutil.copy2(os.path.join(DIST, baks[-1]), EXT)
        print("restored from", baks[-1])
        return 0

    src = open(EXT, encoding="utf-8").read()
    if MARK in src:
        print("already patched")
        return 0
    for old, new, tag in ((OLD1, NEW1, "checkFilesExist"), (OLD2, NEW2, "getImageDataUri"), (OLD3, NEW3, "repair")):
        n = src.count(old)
        if n != 1:
            print("anchor", tag, "count", n)
            return 1
        src = src.replace(old, new, 1)

    bak = f"{EXT}.bak-{STAMP}-{SUFFIX}"
    shutil.copy2(EXT, bak)
    open(EXT, "w", encoding="utf-8").write(src)
    ok, err = syntax_gate(EXT)
    print(("✓" if ok else "✗") + " node --check")
    if not ok:
        print(err)
        shutil.copy2(bak, EXT)
        return 1
    print("backup:", os.path.basename(bak))
    return 0


if __name__ == "__main__":
    sys.exit(main())
