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
"""apply_wall_icon.py —— P-WALL-ICON：换图标 + 历史块默认收起（<DATE>）

1) 图标换成「坐标轴 + 柱状图 + 钟形曲线」，纯描边（`stroke:currentColor`），
   颜色用 `text-muted-foreground`，和 ReadFile/Shell 等图标同一色调。

2) 历史 Figure 块默认收起、新生成的默认展开：
   用 localStorage 记「这个代码块见过没」——首次出现（新生成）展开并记一笔；
   重载后同一段代码再渲染时已经记过 ⇒ 默认收起。

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
SUFFIX = "before-wall-icon"
MARK = "__wallSeen"

OLD_ICON = (
    'd.jsx("rect",{x:3,y:4,width:18,height:16,rx:2}),'
    'd.jsx("circle",{cx:8.5,cy:9.5,r:1.5}),'
    'd.jsx("path",{d:"M4 17l5-5 4 4 3-3 4 4"})'
)
NEW_ICON = (
    'd.jsx("path",{d:"M4 3.5v16.5h16.5"}),'
    'd.jsx("path",{d:"M7.5 20v-6.5h2.8V20"}),'
    'd.jsx("path",{d:"M11.2 20V9.5h2.8V20"}),'
    'd.jsx("path",{d:"M14.9 20v-5h2.8v5"}),'
    'd.jsx("path",{d:"M5.6 9.2c1.8-3.6 4-4.6 5.8-3.1 1.9 1.5 3.6 1.1 5.4-.9"})'
)

OLD_STATE = 'const[op,setOp]=S.useState(!0);'
NEW_STATE = (
    '/* [__wallSeen] 新块默认展开，历史块（localStorage 里记过）默认收起 */'
    'const[op,setOp]=S.useState(function(){try{'
    'let h=5381;const t=String(i);for(let q=0;q<t.length;q++){h=((h*33)^t.charCodeAt(q))>>>0}'
    'const k="wallSeen:"+h;'
    'if(localStorage.getItem(k))return false;'
    'localStorage.setItem(k,"1");return true}catch(_){return true}});'
)


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
    if OLD_ICON not in src or OLD_STATE not in src:
        print("锚点缺失（icon=%d state=%d），放弃" % (OLD_ICON in src, OLD_STATE in src))
        return 1

    out = src.replace(OLD_ICON, NEW_ICON, 1).replace(OLD_STATE, NEW_STATE, 1)

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

    print("已打补丁：图标替换 + 历史块默认收起")
    print("备份：%s" % bak)
    return 0


if __name__ == "__main__":
    sys.exit(main())
