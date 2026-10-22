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
"""apply_wall_block6.py —— P-WALL-BLOCK6：轨道自动对齐 + 竖线不越出块（<DATE>）

用户实测：
  1. 块整体偏移（markdown 容器的左内边距 ≠ step 块的左内边距）；
  2. 图块的点没有和上面块的点连成一条竖线；
  3. 下面没有步骤了，竖线还继续往下画。

修法：
  - **自动对齐**：渲染后量一下页面上其它块的轨道（`w-5 flex-col items-center`），
    和本块的轨道比较 `getBoundingClientRect().left`，差多少就用 `margin-left` 补多少。
    不写死 padding，换主题/换布局也能自愈。
  - **竖线只到块底**：`bottom:"0"`，不再用负值往下探。
  - 线仍然只在「加载中」用渐隐、完成后用实色。

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
SUFFIX = "before-wall-block6"
MARK = "__wallAlign"

BLOCK = (
    'const __wallBlock=S.memo(function({code:i}){'
    'let sp=null;try{sp=JSON.parse(String(i))}catch(_){sp=null}'
    'const[op,setOp]=S.useState(!0);const[rdy,setRdy]=S.useState(0);'
    'const rf=S.useRef(null);'
    '/* [__wallAlign] 量其它块的轨道，把自己的左偏移补成一致 */'
    'S.useLayoutEffect(()=>{const host=rf.current;if(!host)return;'
    'const mine=host.querySelector("[data-wall-rail]");if(!mine)return;'
    'const all=[].slice.call(document.querySelectorAll(\'[class*="w-5"][class*="flex-col"][class*="items-center"]\'));'
    'const other=all.filter(function(n){return !host.contains(n)})[0];if(!other)return;'
    'const d=other.getBoundingClientRect().left-mine.getBoundingClientRect().left;'
    'if(Math.abs(d)>0.5){const cur=parseFloat(getComputedStyle(host).marginLeft)||0;host.style.marginLeft=(cur+d)+"px"}},[op,rdy]);'
    'const fb=d.jsx("pre",{className:"bg-muted rounded px-2 py-1 overflow-x-auto text-[11px]",'
    'children:d.jsx("code",{children:i})});'
    'if(!sp||!Array.isArray(sp.images)||sp.images.length===0)return fb;'
    'const cols=Math.max(1,Math.min(4,Number(sp.cols)||2));'
    'const done=rdy>=sp.images.length;'
    'const grid=d.jsx("div",{style:{display:"grid",gridTemplateColumns:"repeat("+cols+",minmax(0,1fr))",'
    'gap:"10px",alignItems:"start",justifyItems:"center"},children:'
    'sp.images.map(function(im,k){return d.jsx(__wallCell,{key:k,src:im.src,label:im.label,'
    'onLoad:()=>setRdy(function(v){return v+1})},k)})});'
    'const head=d.jsxs("button",{type:"button",onClick:()=>setOp(!op),'
    'className:"w-full flex items-center gap-2 px-3 py-1.5 hover:bg-zinc-100/50 dark:hover:bg-zinc-800/20 transition-colors",'
    'children:['
    'd.jsx("svg",{viewBox:"0 0 24 24",fill:"none",stroke:"currentColor",strokeWidth:1.8,'
    'strokeLinecap:"round",strokeLinejoin:"round",className:"size-3.5 text-zinc-500 shrink-0",children:['
    'd.jsx("rect",{x:3,y:4,width:18,height:16,rx:2}),'
    'd.jsx("circle",{cx:8.5,cy:9.5,r:1.5}),'
    'd.jsx("path",{d:"M4 17l5-5 4 4 3-3 4 4"})]}),'
    'd.jsx("span",{className:"text-xs font-semibold text-zinc-800 dark:text-zinc-200",children:"Figure"}),'
    'd.jsx("span",{className:"text-xs text-zinc-500 truncate text-left",children:sp.title||"figure"}),'
    'd.jsx("svg",{viewBox:"0 0 24 24",fill:"none",stroke:"currentColor",strokeWidth:2,'
    'strokeLinecap:"round",strokeLinejoin:"round",'
    'className:"size-3.5 text-zinc-400 shrink-0 ml-auto transition-transform",'
    'style:{transform:op?"rotate(180deg)":"none"},children:d.jsx("path",{d:"M6 9l6 6 6-6"})})]});'
    'const rail=d.jsxs("div",{"data-wall-rail":"1",'
    'className:"hidden @[420px]:flex shrink-0 w-5 flex-col items-center relative",children:['
    'd.jsx("div",{className:Be("size-1.5 rounded-full mt-2 shrink-0 relative z-10",'
    'done?"bg-blue-400":"bg-blue-400 shadow-[0_0_8px_rgba(59,130,246,0.6)] animate-pulse")}),'
    'd.jsx("div",{className:Be("absolute left-1/2 w-px",'
    'done?"bg-zinc-300 dark:bg-zinc-600":"bg-gradient-to-b from-zinc-300 to-transparent dark:from-zinc-600 dark:to-transparent"),'
    'style:{top:"calc(0.5rem + 0.1875rem)",bottom:"0",transform:"translateX(-50%)"}})]});'
    'return d.jsx("div",{ref:rf,className:"my-2 w-full flex",children:[rail,'
    'd.jsx("div",{className:"min-w-0 flex-1",children:'
    'd.jsxs("div",{className:"rounded-lg border border-zinc-200/50 dark:border-zinc-800/50 '
    'bg-zinc-50/50 dark:bg-zinc-900/10 overflow-hidden",children:[head,'
    'op?d.jsx("div",{className:"px-2 pb-2",children:grid}):null]})})]});});'
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
    b1 = find_span(src, "const __wallBlock=S.memo(function({code:i}){")
    if not b1:
        print("找不到 __wallBlock，放弃")
        return 1
    out = src[:b1[0]] + BLOCK + src[b1[1]:]

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

    print("已打补丁：轨道自动对齐 + 竖线止于块底")
    print("备份：%s" % bak)
    return 0


if __name__ == "__main__":
    sys.exit(main())
