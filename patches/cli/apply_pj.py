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
"""apply_pj.py —— P-J：把 `/title` 的功能搬到会话列表的 GUI（Delete 旁边的 Rename）

用户 <DATE> 要求：像刷新键一样，把 `/title`（设置会话标题）搬到 History 下拉里每行的
"Delete" 旁边。

实现：
- **host**（extension.js）：新增 RPC `setSessionTitle`（枚举 + 参数校验 + handler），
  handler 复用 `/title` 的命令实现 `ctx.harness.renameSession({id,title})`，并做与
  deleteSession 相同的**工作区归属校验**；
- **webview**（webview.js）：`n0` 枚举 + 客户端方法 `Qe.setSessionTitle(id,title)`；
  会话行 `mre` 的菜单在 Delete 上方加一项 **Rename**（铅笔图标）；
  点击后标题变成**行内输入框**，Enter 提交、Esc 取消、失焦取消；
  提交成功后直接改本地 `kimiSessions` 查询缓存，列表即时更新（不重载）。
"""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time

DIST = _KIMI_DIST
EXT = os.path.join(DIST, "extension.js")
WEB = os.path.join(DIST, "webview.js")
STAMP = time.strftime("%Y%m%d-%H%M%S")
SUFFIX = "before-pj-rename-session"

# ---------- host ----------
H1_OLD = '\tDeleteKimiSession: "deleteKimiSession",'
H1_NEW = '\tDeleteKimiSession: "deleteKimiSession",\n\tSetSessionTitle: "setSessionTitle",'

H2_OLD = '\t\tcase Methods$1.DeleteKimiSession: return hasNonEmptyString(params, "sessionId");'
H2_NEW = (H2_OLD + '\n\t\tcase Methods$1.SetSessionTitle: return isPlainObject$8(params) && '
          'hasNonEmptyString(params, "sessionId") && hasNonEmptyString(params, "title");')

H3_OLD = '\t[Methods$1.ForkKimiSession]: async (params, ctx) => {'
H3_NEW = ('\t[Methods$1.SetSessionTitle]: async (params, ctx) => {\n'
          '\t\tif (!isSessionId(params.sessionId) || !ctx.workspaceRoot) return { ok: false };\n'
          '\t\tconst title = String(params.title ?? "").trim().slice(0, 200);\n'
          '\t\tif (title.length === 0) return { ok: false };\n'
          '\t\tconst summary = (await ctx.harness.listSessions({ sessionId: params.sessionId }))[0];\n'
          '\t\tif (summary === void 0 || !isInsideOrEqual(ctx.workspaceRoot, summary.workDir)) return { ok: false };\n'
          '\t\tawait ctx.harness.renameSession({ id: params.sessionId, title });\n'
          '\t\treturn { ok: true, title };\n'
          '\t},\n' + H3_OLD)

# ---------- webview ----------
W1_OLD = 'DeleteKimiSession:"deleteKimiSession",'
W1_NEW = 'DeleteKimiSession:"deleteKimiSession",SetSessionTitle:"setSessionTitle",'

W2_OLD = 'deleteSession(i){return this.call(n0.DeleteKimiSession,{sessionId:i})}'
W2_NEW = (W2_OLD + 'setSessionTitle(i,r){return this.call(n0.SetSessionTitle,{sessionId:i,title:r})}')

W3_OLD = 'const[a,A]=S.useState(!1);return d.jsxs("div",{className:Be("group relative px-2 py-1 rounded-md'
W3_NEW = ('const[a,A]=S.useState(!1),[ed,setEd]=S.useState(!1),[dr,setDr]=S.useState(""),qc=Vf();'
          'const commit=async()=>{const v=dr.trim();setEd(!1);'
          'if(!v||v===(e.brief||""))return;'
          'try{await Qe.setSessionTitle(e.id,v);'
          'qc.setQueryData(oR,W=>W?W.map(X=>X.id===e.id?{...X,brief:v}:X):W)}catch(_){}};'
          'return d.jsxs("div",{className:Be("group relative px-2 py-1 rounded-md')

W4_OLD = 'children:[d.jsx("p",{className:"text-xs leading-relaxed line-clamp-3 text-foreground",children:s4(e.brief)||"Untitled"}),'
W4_NEW = ('children:[ed?d.jsx("input",{autoFocus:!0,value:dr,onChange:W=>setDr(W.target.value),'
          'onClick:W=>W.stopPropagation(),'
          'onKeyDown:W=>{if(W.key==="Enter"){W.stopPropagation(),commit()}'
          'else if(W.key==="Escape"){W.stopPropagation(),setEd(!1)}},'
          'onBlur:()=>setEd(!1),'
          'className:"w-full text-xs leading-relaxed bg-background border border-border rounded px-1 py-0.5 text-foreground outline-none"}):'
          'd.jsx("p",{className:"text-xs leading-relaxed line-clamp-3 text-foreground",children:s4(e.brief)||"Untitled"}),')

W5_OLD = ('d.jsx(Pc,{align:"end",className:"w-32",children:d.jsxs(Jc,{className:"text-xs text-destructive '
          'focus:text-destructive cursor-pointer",onClick:l=>{l.stopPropagation(),t()},'
          'children:[d.jsx(D8,{className:"size-3.5 mr-2"}),"Delete"]})})')
W5_NEW = ('d.jsx(Pc,{align:"end",className:"w-32",children:d.jsxs(d.Fragment,{children:['
          'd.jsxs(Jc,{className:"text-xs cursor-pointer",onClick:l=>{l.stopPropagation(),setDr(e.brief||""),setEd(!0)},'
          'children:[d.jsx($P,{className:"size-3.5 mr-2"}),"Rename"]}),'
          'd.jsxs(Jc,{className:"text-xs text-destructive focus:text-destructive cursor-pointer",'
          'onClick:l=>{l.stopPropagation(),t()},'
          'children:[d.jsx(D8,{className:"size-3.5 mr-2"}),"Delete"]})]})})')

EDITS = [
    (EXT, "P-J1 host: Methods 枚举", H1_OLD, H1_NEW),
    (EXT, "P-J2 host: 参数校验", H2_OLD, H2_NEW),
    (EXT, "P-J3 host: setSessionTitle handler", H3_OLD, H3_NEW),
    (WEB, "P-J4 webview: 枚举", W1_OLD, W1_NEW),
    (WEB, "P-J5 webview: 客户端方法", W2_OLD, W2_NEW),
    (WEB, "P-J6 webview: 行内编辑状态", W3_OLD, W3_NEW),
    (WEB, "P-J7 webview: 标题↔输入框", W4_OLD, W4_NEW),
    (WEB, "P-J8 webview: 菜单加 Rename", W5_OLD, W5_NEW),
]


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()[:16]


def syntax_gate(path):
    with tempfile.NamedTemporaryFile(suffix=".mjs", delete=False) as tf:
        shutil.copyfileobj(open(path, "rb"), tf)
        tmp = tf.name
    r = subprocess.run(["node", "--check", tmp], capture_output=True, text=True)
    os.unlink(tmp)
    return r.returncode == 0, r.stderr[:800]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    print("=== 锚点校验 ===")
    ok = True
    for path, label, old, _new in EDITS:
        n = open(path, encoding="utf-8").read().count(old)
        print(f"  {'✓' if n == 1 else '✗'} [{label}] 命中 {n} 次")
        ok = ok and n == 1
    if not ok:
        print("锚点不满足，中止")
        return 1
    if a.dry_run:
        print("dry-run：未写文件")
        return 0

    print(f"\n改动前: extension={sha(EXT)} webview={sha(WEB)}")
    print("\n=== 备份 ===")
    for p in (EXT, WEB):
        bak = f"{p}.bak-{STAMP}-{SUFFIX}"
        shutil.copy2(p, bak)
        print("  ", bak)

    print("\n=== 应用 ===")
    for path, label, old, new in EDITS:
        s = open(path, encoding="utf-8").read()
        assert s.count(old) == 1, label
        open(path, "w", encoding="utf-8").write(s.replace(old, new, 1))
        print(f"  ✓ {label}")

    print("\n=== .mjs 真语法闸门 ===")
    bad = False
    for p in (EXT, WEB):
        ok2, err = syntax_gate(p)
        print(f"  {'✓' if ok2 else '✗'} {os.path.basename(p)}")
        if not ok2:
            print(err)
            bad = True
    if bad:
        return 1

    print(f"\n改动后: extension={sha(EXT)} webview={sha(WEB)}")
    print("完成。需要重载窗口生效。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
