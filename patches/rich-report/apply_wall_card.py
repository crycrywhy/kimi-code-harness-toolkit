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
"""apply_wall_card.py —— P-WALL-CARD：工具卡片直接渲染成 Figure 块（<DATE>）

用户问：为什么不能把工具卡片搬进 Figure 块、让图就长在工具调用这里？

做法：`bY`（工具卡片）在渲染前先看结果里有没有 ```wall 规格——有就**整个卡片替换成
Figure 块**（`__wallBlock`），不再显示「render_diagram」那张卡片 + 一段「请把下面贴进回复」。
好处：
  - 图和它的来源（哪次工具调用）是同一个单元，不会重复贴两遍；
  - 工具调用本来就在 step 里，Figure 块自动继承 step 的点/线/呼吸；
  - agent 不用再复制粘贴，省 token 也少一处出错点。

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
SUFFIX = "before-wall-card"
MARK = "__wallPick"

HELPER = (
    '/* [__wallPick] 从工具结果里取出 ```wall 规格（build_image_wall / render_diagram 都用它） */'
    'const __wallPick=function(r){try{'
    'if(!r||r.is_error)return null;let o=r.output;'
    'if(o&&typeof o=="object")o=Array.isArray(o)?o.map(function(p){return p&&p.type==="text"?p.text:""}).join(""):(o.text||o.content||"");'
    'const s=String(o==null?"":o);'
    'const m=s.match(/```wall[\\s\\S]*?\\n([\\s\\S]*?)```/);'
    'const j=(m?m[1]:s).trim();'
    'const o2=JSON.parse(j);'
    'return (o2&&Array.isArray(o2.images)&&o2.images.length>0)?j:null}catch(_){return null}};'
)

OLD = ('};return d.jsxs("div",{className:"rounded-lg border border-border overflow-hidden",children:[d.jsxs("button",{onClick:()=>n(!t)')
NEW = ('};const __w=__wallPick(i);if(__w)return d.jsx(__wallBlock,{code:__w});'
       'return d.jsxs("div",{className:"rounded-lg border border-border overflow-hidden",children:[d.jsxs("button",{onClick:()=>n(!t)')


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
    if src.count(OLD) != 1:
        print("锚点不唯一（%d 处），放弃" % src.count(OLD))
        return 1

    out = src.replace(OLD, NEW, 1)
    k = out.find("const __wallCell=function")
    if k < 0:
        print("找不到 __wallCell，放弃")
        return 1
    out = out[:k] + HELPER + out[k:]

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

    print("已打补丁：工具卡片遇到 ```wall 规格时整卡替换成 Figure 块")
    print("备份：%s" % bak)
    return 0


if __name__ == "__main__":
    sys.exit(main())
