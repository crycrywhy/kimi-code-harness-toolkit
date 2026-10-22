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
"""apply_wall_block3.py —— P-WALL-BLOCK3：图块标题栏改成「图标 + Figure + 名字」（<DATE>）

用户要的样式（对齐工具调用块）：`[图片图标] **Figure**  agent 起的名字`。
名字来自 ```wall JSON 的 `title` 字段（`build_image_wall` 的 `title` 参数）。

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
SUFFIX = "before-wall-block3"
MARK = "__wallFigHead"

OLD = (
    'd.jsx("span",{className:"text-xs font-medium text-zinc-700 dark:text-zinc-300",'
    'children:"图墙 · "+sp.images.length+" 张"}),'
)

NEW = (
    '/* [__wallFigHead] 标题栏：图片图标 + Figure + agent 起的名字 */'
    'd.jsx("svg",{viewBox:"0 0 24 24",fill:"none",stroke:"currentColor",strokeWidth:1.8,'
    'strokeLinecap:"round",strokeLinejoin:"round",className:"size-3.5 text-zinc-500 shrink-0",children:['
    'd.jsx("rect",{x:3,y:4,width:18,height:16,rx:2}),'
    'd.jsx("circle",{cx:8.5,cy:9.5,r:1.5}),'
    'd.jsx("path",{d:"M4 17l5-5 4 4 3-3 4 4"})]}),'
    'd.jsx("span",{className:"text-xs font-semibold text-zinc-800 dark:text-zinc-200",children:"Figure"}),'
    'd.jsx("span",{className:"text-xs text-zinc-500 truncate",'
    'children:(sp.title||"figure")+" · "+sp.images.length+" 张"}),'
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
    if src.count(OLD) != 1:
        print("锚点不唯一（%d 处），放弃" % src.count(OLD))
        return 1

    out = src.replace(OLD, NEW, 1)
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

    print("已打补丁：Figure 标题栏")
    print("备份：%s" % bak)
    return 0


if __name__ == "__main__":
    sys.exit(main())
