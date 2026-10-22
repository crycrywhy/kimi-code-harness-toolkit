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
"""apply_wall_block5.py —— P-WALL-BLOCK5：给 Figure 块补上时间线轨道（<DATE>）

用户要求：块最左边要有和其它块一样的「点 + 线」——
加载中蓝点呼吸（`animate-pulse` + 蓝色光晕），加载完呼吸停止；点下方接一条 1px 竖线。

照抄 harness 里 step 轨道的类名（webview 内部实现）：
    w-5 flex-col items-center relative
    size-1.5 rounded-full mt-2
    pending: bg-blue-400 shadow-[0_0_8px_rgba(59,130,246,0.6)] animate-pulse
    done:    bg-blue-400
    line:    absolute left-1/2 w-px translateX(-50%)

「加载完」的判定：每张图的 `<img onLoad>` 计数达到图片总数。

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
SUFFIX = "before-wall-block5"
MARK = "__wallRail"

CELL = (
    '/* [__wallRail] 网格单元格：图 + 图注，onLoad 回调用于轨道状态 */'
    'const __wallCell=function({src:e,label,onLoad}){'
    'const{data:t}=Qf({queryKey:["localImage",e],'
    'queryFn:async()=>{const r=await Qe.getImageDataUri(e);if(!r)throw new Error("image-unavailable");return r},'
    'staleTime:1e4,retry:6,retryDelay:n=>Math.min(1500*n,5000)});'
    'const[p,setP]=S.useState(null);'
    'return d.jsxs("figure",{style:{margin:0,minWidth:0,display:"flex",flexDirection:"column",alignItems:"center"},children:['
    't?d.jsx("img",{src:t,alt:label||"",style:{width:"100%",height:"auto",display:"block",cursor:"pointer"},'
    'className:"rounded border border-zinc-200/50 dark:border-zinc-800/50",onLoad:onLoad,onClick:()=>setP(t)}):'
    'd.jsx("div",{className:"w-full rounded bg-muted/40 animate-pulse",style:{aspectRatio:"2.5"}}),'
    'label?d.jsx("figcaption",{className:"text-[11px] text-zinc-500 dark:text-zinc-400 mt-1 text-center",children:label}):null,'
    'p?d.jsx(Hk,{src:p,onClose:()=>setP(null)}):null]});};'
)

BLOCK = (
    'const __wallBlock=S.memo(function({code:i}){'
    'let sp=null;try{sp=JSON.parse(String(i))}catch(_){sp=null}'
    'const[op,setOp]=S.useState(!0);const[rdy,setRdy]=S.useState(0);'
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
    'const rail=d.jsxs("div",{className:"hidden @[420px]:flex shrink-0 w-5 flex-col items-center relative",children:['
    'd.jsx("div",{className:Be("size-1.5 rounded-full mt-2 shrink-0 relative z-10",'
    'done?"bg-blue-400":"bg-blue-400 shadow-[0_0_8px_rgba(59,130,246,0.6)] animate-pulse")}),'
    'd.jsx("div",{className:Be("absolute left-1/2 w-px",'
    'done?"bg-zinc-300 dark:bg-zinc-600":"bg-gradient-to-b from-zinc-300 to-transparent dark:from-zinc-600 dark:to-transparent"),'
    'style:{top:"calc(0.5rem + 0.1875rem)",bottom:"calc(-0.5rem)",transform:"translateX(-50%)"}})]});'
    'return d.jsx("div",{className:"my-2 w-full flex",children:[rail,'
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
    a1 = find_span(src, "const __wallCell=function({src:e,label}){")
    b1 = find_span(src, "const __wallBlock=S.memo(function({code:i}){")
    if not a1 or not b1:
        print("找不到 __wallCell/__wallBlock，放弃")
        return 1
    out = src[:a1[0]] + CELL + BLOCK + src[b1[1]:]

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

    print("已打补丁：Figure 块加时间线轨道（点 + 线 + 呼吸）")
    print("备份：%s" % bak)
    return 0


if __name__ == "__main__":
    sys.exit(main())
