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
"""apply_wall_block2.py —— P-WALL-BLOCK2：图块改成 harness 同款「可展开块」（<DATE>）

用户指出：harness 里 Thinking / 工具调用都是独立的可展开块，图块应该用同一套外观。

改法（只替换 `__wallBlock` 的渲染部分）：
  - 外框对齐 Thinking 块：`rounded-lg border border-zinc-200/50 dark:border-zinc-800/50
    bg-zinc-50/50 dark:bg-zinc-900/10 overflow-hidden`
  - 顶部一行标题栏（「图墙 · N 张」+ 收起/展开），点击折叠
  - 容器 `mx-auto w-full` + `max-width` ⇒ 居中、宽度约等于一张全宽图
  - 内容区仍是绝对定位网格 + 每格独立可点放大

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
SUFFIX = "before-wall-block2"
MARK = "__wallBlock2"

OLD_START = 'const __wallBlock=S.memo(function({code:i}){'

NEW = (
    'const __wallBlock=S.memo(function({code:i}){'
    'let sp=null;try{sp=JSON.parse(String(i))}catch(_){sp=null}'
    'const[op,setOp]=S.useState(!0);'
    'const fb=d.jsx("pre",{className:"bg-muted rounded px-2 py-1 overflow-x-auto text-[11px]",'
    'children:d.jsx("code",{children:i})});'
    'if(!sp||!Array.isArray(sp.images)||sp.images.length===0)return fb;'
    'const ar=Number(sp.aspect)>0?Number(sp.aspect):1.5;'
    'const mw=Number(sp.maxWidth)>0?Number(sp.maxWidth):1100;'
    'const inner=d.jsx("div",{style:{position:"relative",width:"100%",aspectRatio:String(ar)},'
    'children:sp.images.map(function(im,k){return d.jsx(__wallCell,{key:k,src:im.src,'
    'x:Number(im.x)||0,y:Number(im.y)||0,w:Number(im.w)||1,h:Number(im.h)||1,label:im.label},k)})});'
    'const body=op?d.jsx("div",{className:"px-2 pb-2",children:inner}):null;'
    'const head=d.jsxs("button",{type:"button",onClick:()=>setOp(!op),'
    'className:"w-full flex items-center gap-2 px-3 py-1.5 hover:bg-zinc-100/50 '
    'dark:hover:bg-zinc-800/20 transition-colors",children:['
    'd.jsx("span",{className:"text-xs font-medium text-zinc-700 dark:text-zinc-300",'
    'children:"图墙 · "+sp.images.length+" 张"}),'
    'd.jsx("span",{className:"text-[11px] text-zinc-400 ml-auto",children:op?"收起":"展开"})]});'
    'return d.jsx("div",{className:"my-3 mx-auto w-full",style:{maxWidth:mw+"px"},'
    'children:d.jsxs("div",{className:"rounded-lg border border-zinc-200/50 dark:border-zinc-800/50 '
    'bg-zinc-50/50 dark:bg-zinc-900/10 overflow-hidden",children:[head,body]})});'
    '});'
)


def find_block(src, marker=OLD_START):
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
    span = find_block(src)
    if not span:
        print("找不到 __wallBlock，放弃")
        return 1
    i, k = span
    out = src[:i] + NEW + src[k:]

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

    print("已打补丁：图块改成可展开块（%d → %d 字节）" % (k - i, len(NEW)))
    print("备份：%s" % bak)
    return 0


if __name__ == "__main__":
    sys.exit(main())
