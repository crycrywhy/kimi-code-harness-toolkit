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
"""apply_wall_svg.py —— P-WALL-SVG：````wall`` 块支持直接内嵌手写 SVG（<DATE> 用户定）

用户要求：手写 SVG（`render_diagram`）也装进 Figure 块，不要单独挂在外面。

做法：
  - ```wall JSON 的每个 cell 支持 `{"svg": "<svg …>…</svg>"}`；
  - `__wallCell` 见到 `svg` 就用 P-RB1 的 `__rb1Wrap(svg, true)` 内联渲染
    （走净化器、保留 `currentColor` 跟随主题），并支持点击放大（data URI 预览）；
  - 没有 `svg` 的仍按原来的本地图片路径走。

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
SUFFIX = "before-wall-svg"
MARK = "__wallSvg"

CELL = (
    '/* [__wallSvg] 单元格：本地图片 或 内联手写 SVG */'
    'const __wallCell=function({src:e,label,svg,maxW}){'
    'const sp=__wallRel(e);const qc=Vf();'
    'const{data:t}=Qf({queryKey:["localImage",sp],enabled:!svg,'
    'queryFn:async()=>{const r=await Qe.getImageDataUri(sp);if(!r)throw new Error("image-unavailable");return r},'
    'staleTime:1e4,retry:6,retryDelay:n=>Math.min(1500*n,5000)});'
    'S.useEffect(function(){if(svg)return;const f=function(){try{qc.invalidateQueries({queryKey:["localImage",sp]})}catch(_){}};'
    'window.addEventListener("kimi:rerender",f);return function(){window.removeEventListener("kimi:rerender",f)}},[sp,svg]);'
    'const[p,setP]=S.useState(null);'
    'const html=S.useMemo(function(){return svg?__rb1Wrap(svg,true):""},[svg]);const __mw=Number(maxW)>0?Number(maxW):760;'
    'const open=function(){try{setP(svg?"data:image/svg+xml;charset=utf-8,"+encodeURIComponent(svg):t)}catch(_){}};'
    'const inner=svg'
    '?d.jsx("div",{style:{width:"100%",maxWidth:__mw+"px",margin:"0 auto",userSelect:"text",cursor:"auto"},dangerouslySetInnerHTML:{__html:html}})'
    ':(t?d.jsx("img",{src:t,alt:label||"",style:{width:"100%",height:"auto",display:"block",cursor:"pointer"},'
    'className:"rounded border border-border",onClick:open})'
    ':d.jsx("div",{className:"w-full rounded bg-muted/40 animate-pulse",style:{aspectRatio:"2.5"}}));'
    'return d.jsxs("figure",{style:{margin:0,minWidth:0,width:svg?"100%":void 0,display:"flex",flexDirection:"column",alignItems:"center"},children:['
    'inner,'
    'label?d.jsx("figcaption",{className:"text-[11px] text-muted-foreground mt-1 text-center",children:label}):null,'
    'p?d.jsx(Hk,{src:p,onClose:()=>setP(null)}):null]});};'
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
    span = find_span(src, "const __wallCell=function({src:e,label}){")
    if not span:
        print("找不到 __wallCell，放弃")
        return 1
    out = src[:span[0]] + CELL + src[span[1]:]

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

    print("已打补丁：wall 块支持内嵌 SVG")
    print("备份：%s" % bak)
    return 0


if __name__ == "__main__":
    sys.exit(main())
