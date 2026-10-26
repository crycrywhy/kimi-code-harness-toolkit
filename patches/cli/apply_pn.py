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
"""apply_pn.py —— P-N：给 kimi-code 输入框右下角加「权限模式」切换按钮

需求（用户 <DATE>）：能在 GUI 里手动切换 manual / auto / yolo，不用打 /auto、/yolo。

实现（跨两个 bundle，各 2~3 处锚点）：
  extension.js（宿主）
    1. Methods$1 增 GetPermissionMode / SetPermissionMode
    2. 新增两个 handler 并挂进 chatHandlers（复用 runtime.session.getStatus / setPermission，
       与 CLI 的 /status、/yolo 走同一条链路）
  webview.js（界面）
    3. RPC 客户端类增两个方法
    4. Methods 枚举同名增两项
    5. 新增 PModeChip 组件（按钮，点击在 manual→auto→yolo 间循环），插到输入框右下角
       「重载渲染」按钮旁边
"""
import argparse
import os
import shutil
import subprocess
import tempfile
import time

DIST = _KIMI_DIST
EXT = os.path.join(DIST, "extension.js")
WEB = os.path.join(DIST, "webview.js")
STAMP = time.strftime("%Y%m%d-%H%M%S")
SUFFIX = "before-pn-permission-chip"

# ---------- extension.js ----------
E1_OLD = '\tSetPlanMode: "setPlanMode",\n'
E1_NEW = ('\tSetPlanMode: "setPlanMode",\n'
          '\tGetPermissionMode: "getPermissionMode",\n'
          '\tSetPermissionMode: "setPermissionMode",\n')

E2_OLD = 'const chatHandlers = {\n'
E2_NEW = '''const getPermissionMode = async (_, ctx) => {
\tconst runtime = ctx.getSession();
\tif (runtime === void 0) return {
\t\tok: false,
\t\tmode: "manual"
\t};
\ttry {
\t\tconst status = await runtime.session.getStatus();
\t\treturn {
\t\t\tok: true,
\t\t\tmode: status && status.permission ? status.permission : "manual"
\t\t};
\t} catch (error) {
\t\treturn {
\t\t\tok: false,
\t\t\tmode: "manual",
\t\t\terror: error instanceof Error ? error.message : String(error)
\t\t};
\t}
};
const setPermissionMode = async (params, ctx) => {
\tconst runtime = ctx.getSession();
\tif (runtime === void 0) return { ok: false };
\tawait runtime.session.setPermission(params && params.mode);
\treturn {
\t\tok: true,
\t\tmode: params && params.mode
\t};
};
const chatHandlers = {
\t[Methods$1.GetPermissionMode]: getPermissionMode,
\t[Methods$1.SetPermissionMode]: setPermissionMode,
'''

# ---------- webview.js ----------
W1_OLD = 'SetPlanMode:"setPlanMode",'
W1_NEW = 'SetPlanMode:"setPlanMode",GetPermissionMode:"getPermissionMode",SetPermissionMode:"setPermissionMode",'

W2_OLD = 'setPlanMode(i){return this.call(n0.SetPlanMode,{enabled:i})}'
W2_NEW = ('setPlanMode(i){return this.call(n0.SetPlanMode,{enabled:i})}'
          'getPermissionMode(){return this.call(n0.GetPermissionMode)}'
          'setPermissionMode(i){return this.call(n0.SetPermissionMode,{mode:i})}')

W3_ANCHOR = 'const ere={yoloMode:!1,'
W3_NEW = '''function PModeChip(){
\tconst[mode,setMode]=S.useState("manual");
\tS.useEffect(()=>{let alive=!0;Qe.getPermissionMode().then(r=>{if(alive&&r&&r.mode)setMode(r.mode)}).catch(()=>{});return()=>{alive=!1}},[]);
\tconst next=mode==="manual"?"auto":mode==="auto"?"yolo":"manual";
\tconst label=mode==="yolo"?"YOLO":mode==="auto"?"AUTO":"MANUAL";
\tconst cls=mode==="yolo"?"text-red-500":mode==="auto"?"text-blue-500":"text-muted-foreground";
\tconst onClick=()=>{Qe.setPermissionMode(next).then(r=>{setMode((r&&r.mode)||next)}).catch(()=>{})};
\treturn d.jsx(o0,{variant:"ghost",size:"icon-xs",className:"h-6 px-1.5 text-[10px] font-semibold tracking-wide "+cls,title:"权限模式："+label+"（点击切换 manual → auto → yolo）",onClick:onClick,children:label});
}
''' + W3_ANCHOR

W4_OLD = 'children:d.jsx(Uf,{className:"size-3.5"})}),d.jsx(Rbe,{onAuthAction:e}),'
W4_NEW = 'children:d.jsx(Uf,{className:"size-3.5"})}),d.jsx(PModeChip,{}),d.jsx(Rbe,{onAuthAction:e}),'


def check(path):
    with tempfile.NamedTemporaryFile(suffix=".mjs", delete=False) as tf:
        shutil.copyfile(path, tf.name)
        tmp = tf.name
    r = subprocess.run(["node", "--check", tmp], capture_output=True, text=True)
    os.unlink(tmp)
    return r.returncode == 0, (r.stderr or r.stdout)[:500]


def apply(path, patches, dry):
    src = open(path, encoding="utf-8").read()
    for name, old, new in patches:
        if src.count(new) >= 1:
            print(f"  SKIP {name}: already applied")
            continue
        n = src.count(old)
        if n != 1:
            print(f"  FAIL {name}: anchor count = {n} (expected 1)")
            return None
        src = src.replace(old, new, 1)
        print(f"  OK   {name}")
    if dry:
        return src
    bak = f"{path}.bak-{STAMP}-{SUFFIX}"
    shutil.copy2(path, bak)
    out = path + ".tmp-pn.js"
    open(out, "w", encoding="utf-8").write(src)
    ok, err = check(out)
    if not ok:
        os.remove(out)
        print("  FAIL node --check:", err)
        return None
    os.replace(out, path)
    print(f"  PATCHED {path} (backup {os.path.basename(bak)})")
    return src


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    print("== extension.js ==")
    apply(EXT, [("Methods 枚举", E1_OLD, E1_NEW), ("handler + chatHandlers", E2_OLD, E2_NEW)], a.check)
    print("== webview.js ==")
    apply(WEB, [("Methods 枚举", W1_OLD, W1_NEW), ("RPC 客户端方法", W2_OLD, W2_NEW),
                ("PModeChip 组件", W3_ANCHOR, W3_NEW), ("挂到输入框右下角", W4_OLD, W4_NEW)], a.check)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
