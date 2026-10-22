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
"""apply_wall_block.py —— P-WALL-BLOCK：```wall 代码块渲染成居中的图网格（<DATE>）

用户要求：多图输出要有**一个专门的块**把它们框住，块内怎么排由 agent 决定，
块的大小约等于一张图，并且**居中**（像内联 SVG/HTML 图那样）。

做法：
  1. 在 webview 里新增 `__wallBlock` 组件——解析 ```wall 代码块里的 JSON：
     `{"aspect":0.386,"images":[{"src":"tmp/...png","x":0,"y":0,"w":0.5,"h":0.5,"label":"…"}]}`
     `x/y/w/h` 是 0–1 的比例。容器 `max-width:1100px; margin:0 auto`（居中），
     高度由 `aspect` 决定；每格是一张本地图（`Qe.getImageDataUri` 解析，走 react-query 缓存），
     `object-fit:contain`，**点击开 `Hk` 全屏预览**。
  2. `R3e`（代码块渲染器）遇到 `language === "wall"` 时走这个组件。

JSON 解析失败或结构不对 → 退回普通代码块，不会白屏。

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
SUFFIX = "before-wall-block"
MARK = "__wallBlock"

COMP = (
    'const __wallCell=function({src:e,x,y,w,h,label}){'
    'const qc=Vf();const{data:t}=Qf({queryKey:["localImage",e],'
    'queryFn:async()=>{const r=await Qe.getImageDataUri(e);if(!r)throw new Error("image-unavailable");return r},'
    'staleTime:1e4,retry:6,retryDelay:n=>Math.min(1500*n,5000)});'
    'const[p,setP]=S.useState(null);'
    'return d.jsxs("div",{style:{position:"absolute",left:(x*100)+"%",top:(y*100)+"%",'
    'width:(w*100)+"%",height:(h*100)+"%",padding:"3px",boxSizing:"border-box"},children:['
    't?d.jsx("img",{src:t,alt:label||"",style:{width:"100%",height:"100%",objectFit:"contain",cursor:"pointer"},'
    'className:"rounded hover:opacity-95",onClick:()=>setP(t)}):'
    'd.jsx("div",{className:"w-full h-full rounded bg-muted/40 animate-pulse"}),'
    'label?d.jsx("div",{className:"text-xs text-muted-foreground",'
    'style:{position:"absolute",left:"8px",bottom:"4px",pointerEvents:"none"},children:label}):null,'
    'p?d.jsx(Hk,{src:p,onClose:()=>setP(null)}):null]});};'
    'const __wallBlock=S.memo(function({code:i}){'
    'let sp=null;try{sp=JSON.parse(String(i))}catch(_){sp=null}'
    'const fallback=d.jsx("pre",{className:"bg-muted rounded px-2 py-1 overflow-x-auto text-[11px]",'
    'children:d.jsx("code",{children:i})});'
    'if(!sp||!Array.isArray(sp.images)||sp.images.length===0)return fallback;'
    'const ar=Number(sp.aspect)>0?Number(sp.aspect):1.5;'
    'const mw=Number(sp.maxWidth)>0?Number(sp.maxWidth):1100;'
    'return d.jsx("div",{className:"my-3 w-full flex justify-center",children:'
    'd.jsx("div",{className:"rounded-lg border border-border bg-muted/10",'
    'style:{position:"relative",width:"100%",maxWidth:mw+"px",aspectRatio:String(ar)},children:'
    'sp.images.map(function(im,k){return d.jsx(__wallCell,{key:k,src:im.src,x:Number(im.x)||0,'
    'y:Number(im.y)||0,w:Number(im.w)||1,h:Number(im.h)||1,label:im.label},k)})})});});'
)

OLD = '{return d.jsxs("div",{className:"relative group/code"'
NEW = '{if(r==="wall")return d.jsx(__wallBlock,{code:i});return d.jsxs("div",{className:"relative group/code"'
ANCHOR = "const R3e=S.memo(function({code:i,language:r,enableHighlight:t,style:n})"


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
    if ANCHOR not in src:
        print("找不到 R3e 锚点，放弃")
        return 1
    if src.count(OLD) < 1:
        print("找不到 R3e 函数体锚点，放弃")
        return 1

    out = src.replace(ANCHOR, COMP + ANCHOR, 1)
    out = out.replace(OLD, NEW, 1)

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

    print("已打补丁：```wall 块渲染器（+%d 字节）" % len(COMP))
    print("备份：%s" % bak)
    return 0


if __name__ == "__main__":
    sys.exit(main())
