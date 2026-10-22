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
"""apply_wall_step.py —— P-WALL-STEP：Figure 块「注册成 step」，用 harness 自带轨道（<DATE>）

用户指出：正统做法是把 Figure 当成一个 step，而不是自己在块里画点+线。

做法（两处，缺一不可）：
  1. `ube`（step 渲染器）的轨道判定 `n` 增加一种情况：
     **item 是 text 且正文里含 ```wall** —— 这种 step 也画轨道（点 + 连接线）。
     这样 Figure 所在 step 就有了和其它块完全同源的轨道：位置、缩进、呼吸态、连线语义。
  2. `__wallBlock` 删掉自绘轨道，只渲染卡片本身（`w-full`），让它像工具卡片一样躺在 step 内容区里。

效果：点/线由 step 提供 ⇒ 天然对齐；块宽度 = step 内容宽 ⇒ 与工具卡片同宽；
流式时 `a`（有未完成 item）→ 点呼吸，结束后停止。

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
WV = os.path.join(DIST, "webview.js")
STAMP = time.strftime("%Y%m%d-%H%M%S")
SUFFIX = "before-wall-step"
MARK = "__wallIsFigureStep"

OLD_N = 'n=e.items.some(A=>A.type==="tool_use"||A.type==="thinking"||A.type==="compaction")'
NEW_N = ('n=e.items.some(A=>A.type==="tool_use"||A.type==="thinking"||A.type==="compaction"||'
         '(A.type==="text"&&__wallIsFigureStep(A.content)))')

HELPER = (
    '/* [__wallIsFigureStep] 纯文本 step 里如果只有 ```wall 图块，也给它画轨道 */'
    'const __wallIsFigureStep=function(c){'
    'try{const t=typeof c=="string"?c:((c&&c.text)||(Array.isArray(c)?c.map(function(p){return p&&p.text||""}).join(""):""));'
    'return typeof t=="string"&&t.indexOf("```wall")>=0}catch(_){return!1}};'
)

BLOCK = (
    'const __wallBlock=S.memo(function({code:i}){'
    'let sp=null;try{sp=JSON.parse(String(i))}catch(_){sp=null}'
    'const[op,setOp]=S.useState(!0);'
    'const fb=d.jsx("pre",{className:"bg-muted rounded px-2 py-1 overflow-x-auto text-[11px]",'
    'children:d.jsx("code",{children:i})});'
    'if(!sp||!Array.isArray(sp.images)||sp.images.length===0)return fb;'
    'const cols=Math.max(1,Math.min(4,Number(sp.cols)||2));'
    'const grid=d.jsx("div",{style:{display:"grid",gridTemplateColumns:"repeat("+cols+",minmax(0,1fr))",'
    'gap:"10px",alignItems:"start",justifyItems:"center"},children:'
    'sp.images.map(function(im,k){return d.jsx(__wallCell,{key:k,src:im.src,label:im.label},k)})});'
    'const head=d.jsxs("button",{type:"button",onClick:()=>setOp(!op),'
    'className:"w-full flex items-center gap-2 px-3 py-2 hover:bg-muted/50 transition-colors",children:['
    'd.jsx("svg",{viewBox:"0 0 24 24",fill:"none",stroke:"currentColor",strokeWidth:1.8,'
    'strokeLinecap:"round",strokeLinejoin:"round",className:"size-3.5 text-muted-foreground shrink-0",children:['
    'd.jsx("rect",{x:3,y:4,width:18,height:16,rx:2}),'
    'd.jsx("circle",{cx:8.5,cy:9.5,r:1.5}),'
    'd.jsx("path",{d:"M4 17l5-5 4 4 3-3 4 4"})]}),'
    'd.jsx("span",{className:"text-xs font-medium",children:"Figure"}),'
    'd.jsx("span",{className:"text-xs text-muted-foreground truncate flex-1 text-left",children:sp.title||"figure"}),'
    'd.jsx("svg",{viewBox:"0 0 24 24",fill:"none",stroke:"currentColor",strokeWidth:2,'
    'strokeLinecap:"round",strokeLinejoin:"round",'
    'className:"size-3.5 text-muted-foreground shrink-0 ml-auto transition-transform",'
    'style:{transform:op?"rotate(180deg)":"none"},children:d.jsx("path",{d:"M6 9l6 6 6-6"})})]});'
    'return d.jsx("div",{className:"my-2 w-full",children:'
    'd.jsxs("div",{className:"rounded-lg border border-border overflow-hidden",children:[head,'
    'op?d.jsx("div",{className:"px-2 pb-2",children:grid}):null]})});});'
)


def find_span(src, marker):
    i = src.find(marker)
    if i < 0:
        return None
    j = src.index("){", i) + 1
    depth = 0
    for k in range(j, len(src)):
        c = src[k]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                end = k + 1
                while end < len(src) and src[end] in ")":
                    end += 1
                return i, end
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--revert", action="store_true")
    a = ap.parse_args()

    if a.revert:
        baks = sorted(f for f in os.listdir(DIST)
                      if f.startswith("webview.js.bak-") and SUFFIX in f)
        if not baks:
            print("没有可回退的备份（%s）" % SUFFIX)
            return 1
        shutil.copy2(os.path.join(DIST, baks[-1]), WV)
        print("已回退：%s" % baks[-1])
        return 0

    src = open(WV, encoding="utf-8").read()
    if MARK in src:
        print("已打过补丁（找到 %s），跳过" % MARK)
        return 0
    if OLD_N not in src:
        print("找不到 ube 的轨道判定，放弃")
        return 1

    # 1) ube 判定
    out = src.replace(OLD_N, NEW_N, 1)
    # 2) helper 放 module 作用域（插在 __wallCell 定义之前）
    k = out.find("const __wallCell=function")
    if k < 0:
        print("找不到 __wallCell，放弃")
        return 1
    out = out[:k] + HELPER + out[k:]
    # 3) 去掉自绘轨道
    b = find_span(out, "const __wallBlock=S.memo(function({code:i}){")
    if not b:
        print("找不到 __wallBlock，放弃")
        return 1
    out = out[:b[0]] + BLOCK + out[b[1]:]

    bak = os.path.join(DIST, "webview.js.bak-%s-%s" % (STAMP, SUFFIX))
    shutil.copy2(WV, bak)
    open(WV, "w", encoding="utf-8").write(out)

    tmp = tempfile.mktemp(suffix=".mjs")
    shutil.copy2(WV, tmp)
    r = subprocess.run(["node", "--check", tmp], capture_output=True, text=True)
    os.unlink(tmp)
    if r.returncode != 0:
        shutil.copy2(bak, WV)
        print("语法检查失败，已回滚：\n%s" % r.stderr[:900])
        return 1

    print("已打补丁：Figure step 化（ube 判定 + 去自绘轨道）")
    print("备份：%s" % bak)
    return 0


if __name__ == "__main__":
    sys.exit(main())
