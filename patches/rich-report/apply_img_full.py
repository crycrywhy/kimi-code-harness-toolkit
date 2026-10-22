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
"""apply_img_full.py —— P-IMG-FULL：大图按容器全宽显示，不再压成 128px 缩略图（<DATE>）

**问题**：webview 的图片渲染器 `tY` 把每张图的高度写死 `jk=128`，再按宽高比算宽度、
`object-cover` 裁切。于是 markdown 图片（`![](path)`）在对话里都是一条 128px 高的缩略图：
图墙只有一指高、图表小字全糊，必须点开预览才能看。用户原话「效果还不如图上这版」。

**改法**：只动 `tY` 一个函数——
  - 载图时记录真实宽高；`naturalWidth >= BIG_MIN`（480）判为大图；
  - 大图渲染成 `width:100%; max-width:<真实宽>; height:auto`（跟随容器、不放大超过原图）；
  - 小图保持原行为（128px 高），免得图标/表情被拉满一行；
  - 点击仍然走 `onPreview`（`Hk` 全屏预览，max-w/h 90vw/90vh）。

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
SUFFIX = "before-img-full"
MARK = "__imgBig"

NEW_FN = (
    'function tY({src:e,alt:i,onPreview:r,onError:oe}){'
    'const[t,n]=S.useState(()=>rY.get(e)??null);'
    'return S.useLayoutEffect(()=>{if(t!==null)return;let done=!1;const a=new Image;'
    'a.onload=()=>{if(done)return;done=!0;'
    'const W=a.naturalWidth,H=a.naturalHeight;'
    '/* [__imgBig] 大图按容器全宽：小图仍旧 128px 高缩略图 */'
    'const v=(W>=480)?{big:1,w:W,h:H}:Math.round(jk*(W/Math.max(1,H)));'
    'rY.set(e,v),n(v)},'
    'a.onerror=()=>{if(done)return;done=!0;'
    'try{Qe.getImageWebviewUri("__diag__:preloadFail "+String(e).slice(0,160)).catch(()=>{})}catch(_){}'
    'rY.set(e,jk),n(jk)},a.src=e},[e,t]),'
    't===null?d.jsx(_3e,{}):'
    '(t&&t.big)?d.jsx("img",{src:e,alt:i||"",loading:"lazy",'
    'style:{width:"100%",maxWidth:t.w,height:"auto"},'
    'className:"rounded my-2 cursor-pointer hover:opacity-90 transition-opacity block",'
    'onClick:()=>r(e)}):'
    'd.jsx("img",{src:e,alt:i||"",style:{width:t,height:jk},onError:oe,'
    'className:"rounded my-2 cursor-pointer hover:opacity-90 transition-opacity object-cover",'
    'onClick:()=>r(e)})}'
)


def find_fn(src, marker="function tY({src:e,alt:i,onPreview:r,onError:oe}){"):
    """按大括号配平截出整个函数。

    注意：marker 的第一个 `{` 是**参数解构**，不是函数体 ⇒ 必须从 `){` 之后开始配平，
    否则会在参数结束时提前收尾（第一次就是这么把函数头替换掉、留下裸函数体的）。
    """
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
                return i, k + 1
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--revert", action="store_true")
    a = ap.parse_args()

    src = open(WV, encoding="utf-8").read()

    if a.revert:
        baks = sorted(f for f in os.listdir(DIST)
                      if f.startswith("webview.js.bak-") and SUFFIX in f)
        if not baks:
            print("没有可回退的备份（%s）" % SUFFIX)
            return 1
        shutil.copy2(os.path.join(DIST, baks[-1]), WV)
        print("已回退：%s" % baks[-1])
        return 0

    if MARK in src:
        print("已打过补丁（找到 %s），跳过" % MARK)
        return 0

    span = find_fn(src)
    if not span:
        print("找不到 tY 函数，放弃")
        return 1
    i, k = span
    old = src[i:k]
    out = src[:i] + NEW_FN + src[k:]

    bak = os.path.join(DIST, "webview.js.bak-%s-%s" % (STAMP, SUFFIX))
    shutil.copy2(WV, bak)
    open(WV, "w", encoding="utf-8").write(out)

    # 语法检查：必须复制成 .mjs（Node 22 对含 import 的 .js 会假通过）
    tmp = tempfile.mktemp(suffix=".mjs")
    shutil.copy2(WV, tmp)
    r = subprocess.run(["node", "--check", tmp], capture_output=True, text=True)
    os.unlink(tmp)
    if r.returncode != 0:
        shutil.copy2(bak, WV)
        print("语法检查失败，已回滚：\n%s" % r.stderr[:800])
        return 1

    print("已打补丁：tY 大图全宽（%d 字节 → %d 字节）" % (len(old), len(NEW_FN)))
    print("备份：%s" % bak)
    return 0


if __name__ == "__main__":
    sys.exit(main())
