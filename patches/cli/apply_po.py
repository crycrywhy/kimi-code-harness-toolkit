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
"""apply_po.py —— P-O：权限模式芯片改为下拉列表（P-N 的迭代）

用户 <DATE> 反馈：
  1. 初始显示 MANUAL，实际是 auto（会话未就绪时读到默认值）→ 加轮询 + 打开菜单时刷新
  2. 不要循环切换，要下拉列表选择（仿 Claude Code 的 Modes 菜单）
  3. 按钮框随文字自适应（原来 size=icon-xs 定死宽度）
  4. 挪到「Add files or media」左侧，避免宽度变化顶到其它按钮
  5. 标签首字母大写（Manual / Auto / Yolo）
"""
import argparse
import os
import shutil
import subprocess
import tempfile
import time

WEB = os.path.join(_KIMI_DIST, "webview.js")
STAMP = time.strftime("%Y%m%d-%H%M%S")
SUFFIX = "before-po-mode-dropdown"

OLD_FN = '''function PModeChip(){
\tconst[mode,setMode]=S.useState("manual");
\tS.useEffect(()=>{let alive=!0;Qe.getPermissionMode().then(r=>{if(alive&&r&&r.mode)setMode(r.mode)}).catch(()=>{});return()=>{alive=!1}},[]);
\tconst next=mode==="manual"?"auto":mode==="auto"?"yolo":"manual";
\tconst label=mode==="yolo"?"YOLO":mode==="auto"?"AUTO":"MANUAL";
\tconst cls=mode==="yolo"?"text-red-500":mode==="auto"?"text-blue-500":"text-muted-foreground";
\tconst onClick=()=>{Qe.setPermissionMode(next).then(r=>{setMode((r&&r.mode)||next)}).catch(()=>{})};
\treturn d.jsx(o0,{variant:"ghost",size:"icon-xs",className:"h-6 px-1.5 text-[10px] font-semibold tracking-wide "+cls,title:"权限模式："+label+"（点击切换 manual → auto → yolo）",onClick:onClick,children:label});
}
'''

NEW_FN = '''function PModeChip(){
\tconst[mode,setMode]=S.useState("manual");
\tconst refresh=()=>{Qe.getPermissionMode().then(r=>{if(r&&r.mode)setMode(r.mode)}).catch(()=>{})};
\tS.useEffect(()=>{refresh();const t=setInterval(refresh,10000);return()=>clearInterval(t)},[]);
\tconst MODES=[["manual","Manual","Ask for approval before each action"],["auto","Auto","Approve routine actions, pause for risky ones"],["yolo","Yolo","Approve everything, never ask"]];
\tconst cur=MODES.find(m=>m[0]===mode)||MODES[0];
\tconst cls=mode==="yolo"?"text-red-500":mode==="auto"?"text-blue-500":"text-muted-foreground";
\tconst pick=u=>{Qe.setPermissionMode(u).then(()=>setMode(u)).catch(()=>{})};
\treturn d.jsxs(jc,{children:[
\t\td.jsxs(wr,{children:[
\t\t\td.jsx(yr,{asChild:!0,children:d.jsx(Hc,{asChild:!0,onClick:refresh,children:d.jsxs(o0,{variant:"ghost",size:"xs",className:"h-6 px-1.5 gap-1 text-xs font-medium",children:[d.jsx("span",{className:cls,children:cur[1]}),d.jsx(In,{className:"size-3 shrink-0 opacity-60"})]})})}),
\t\t\td.jsx(_r,{children:"Permission mode"})
\t\t]}),
\t\td.jsxs(Pc,{className:"w-72!",align:"start",children:MODES.map(m=>d.jsxs(Jc,{onClick:()=>pick(m[0]),className:"text-xs gap-2 items-start",children:[
\t\t\td.jsx(En,{className:Be("size-3 mt-0.5",m[0]!==mode&&"opacity-0")}),
\t\t\td.jsxs("div",{className:"flex flex-col gap-0.5",children:[
\t\t\t\td.jsx("span",{className:"font-medium",children:m[1]}),
\t\t\t\td.jsx("span",{className:"text-[10px] leading-snug text-muted-foreground whitespace-normal",children:m[2]})
\t\t\t]})
\t\t]},m[0]))})
\t]});
}
'''

OLD_JSX = 'd.jsx(PModeChip,{}),d.jsx(Rbe,{onAuthAction:e})'
NEW_JSX = 'd.jsx(Rbe,{onAuthAction:e})'

POS_OLD = ('children:[d.jsxs(wr,{children:[d.jsx(yr,{asChild:!0,children:d.jsx(o0,'
           '{variant:"ghost",size:"icon-xs",onClick:Je,')
POS_NEW = ('children:[d.jsx(PModeChip,{}),d.jsxs(wr,{children:[d.jsx(yr,{asChild:!0,children:d.jsx(o0,'
           '{variant:"ghost",size:"icon-xs",onClick:Je,')


def check(path):
    with tempfile.NamedTemporaryFile(suffix=".mjs", delete=False) as tf:
        shutil.copyfile(path, tf.name)
        tmp = tf.name
    r = subprocess.run(["node", "--check", tmp], capture_output=True, text=True)
    os.unlink(tmp)
    return r.returncode == 0, (r.stderr or r.stdout)[:400]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    src = open(WEB, encoding="utf-8").read()

    steps = [
        ("换成新组件", OLD_FN, NEW_FN),
        ("移除旧位置", OLD_JSX, NEW_JSX),
        ("插到 add files 左侧", POS_OLD, POS_NEW),
    ]
    for name, old, new in steps:
        if src.count(new) >= 1 and src.count(old) == 0:
            print(f"SKIP {name}: already applied")
            continue
        n = src.count(old)
        if n != 1:
            print(f"FAIL {name}: anchor count = {n} (expected 1)")
            return 1
        src = src.replace(old, new, 1)
        print(f"OK   {name}")

    if a.check:
        return 0
    bak = f"{WEB}.bak-{STAMP}-{SUFFIX}"
    shutil.copy2(WEB, bak)
    tmp = WEB + ".tmp-po.js"
    open(tmp, "w", encoding="utf-8").write(src)
    ok, err = check(tmp)
    if not ok:
        os.remove(tmp)
        print("FAIL node --check:", err)
        return 2
    os.replace(tmp, WEB)
    print(f"PATCHED {WEB}\nbackup  {bak}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
