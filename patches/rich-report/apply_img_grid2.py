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
"""apply_img_grid2.py —— P-IMG-GRID2：用 JS 把同段多图排成网格（<DATE>）

P-IMG-GRID 用的是 CSS `p:has(>img~img)>img{width:50%}`，实测在 webview 里**没生效**
（图片仍是各自整宽、上下堆叠）——大概率是 webview 的 Chromium 不支持 `:has()`
或图片的父节点不是 `<p>`。

改法：在 `tY` 组件里用 JS 判断——渲染后看 `parentElement` 下的 `<img>` 数量，
>1 张就把自己设成 `calc(50% - 6px)`（3 张时 `33.333%`）、`inline-block`、顶对齐。
不依赖任何 CSS 选择器。单图行为不变。

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
SUFFIX = "before-img-grid2"
MARK = "__imgGrid2"

NEW_FN = (
    'function tY({src:e,alt:i,onPreview:r,onError:oe}){'
    'const[t,n]=S.useState(()=>rY.get(e)??null);const rf=S.useRef(null);'
    'S.useLayoutEffect(()=>{if(t!==null)return;let done=!1;const a=new Image;'
    'a.onload=()=>{if(done)return;done=!0;const W=a.naturalWidth,H=a.naturalHeight;'
    'const v=(W>=480)?{big:1,w:W,h:H}:Math.round(jk*(W/Math.max(1,H)));rY.set(e,v),n(v)},'
    'a.onerror=()=>{if(done)return;done=!0;'
    'try{Qe.getImageWebviewUri("__diag__:preloadFail "+String(e).slice(0,160)).catch(()=>{})}catch(_){}'
    'rY.set(e,jk),n(jk)},a.src=e},[e,t]);'
    '/* [__imgGrid2] 同一父节点下多张图 ⇒ 排成网格（2/4 张两列，3 张三列） */'
    'S.useLayoutEffect(()=>{const el=rf.current;if(!el||!el.parentElement)return;'
    'const imgs=el.parentElement.querySelectorAll(":scope > img");'
    'if(imgs.length>1){const w=imgs.length===3?"33.333%":"50%";'
    'el.style.width="calc("+w+" - 6px)";el.style.maxWidth="calc("+w+" - 6px)";'
    'el.style.display="inline-block";el.style.verticalAlign="top";el.style.margin="3px"}},[e,t]);'
    'if(t===null)return d.jsx(_3e,{});'
    'if(t&&t.big)return d.jsx("img",{ref:rf,src:e,alt:i||"",loading:"lazy",'
    'style:{width:"100%",maxWidth:t.w,height:"auto"},'
    'className:"rounded my-2 cursor-pointer hover:opacity-90 transition-opacity block",'
    'onClick:()=>r(e)});'
    'return d.jsx("img",{src:e,alt:i||"",style:{width:t,height:jk},onError:oe,'
    'className:"rounded my-2 cursor-pointer hover:opacity-90 transition-opacity object-cover",'
    'onClick:()=>r(e)})}'
)


def find_fn(src, marker="function tY({src:e,alt:i,onPreview:r,onError:oe}){"):
    i = src.find(marker)
    if i < 0:
        return None
    j = src.index("){", i) + 1      # 跳过参数解构，从函数体的 { 开始配平
    depth = 0
    for k in range(j, len(src)):
        c = src[k]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i, k + 1
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
    span = find_fn(src)
    if not span:
        print("找不到 tY 函数，放弃")
        return 1
    i, k = span
    out = src[:i] + NEW_FN + src[k:]

    bak = os.path.join(DIST, "webview.js.bak-%s-%s" % (STAMP, SUFFIX))
    shutil.copy2(WV, bak)
    open(WV, "w", encoding="utf-8").write(out)

    tmp = tempfile.mktemp(suffix=".mjs")
    shutil.copy2(WV, tmp)
    r = subprocess.run(["node", "--check", tmp], capture_output=True, text=True)
    os.unlink(tmp)
    if r.returncode != 0:
        shutil.copy2(bak, WV)
        print("语法检查失败，已回滚：\n%s" % r.stderr[:800])
        return 1

    print("已打补丁：同段多图用 JS 排网格（%d → %d 字节）" % (k - i, len(NEW_FN)))
    print("备份：%s" % bak)
    return 0


if __name__ == "__main__":
    sys.exit(main())
