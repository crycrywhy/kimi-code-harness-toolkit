#!/usr/bin/env python3
"""apply_wall_icon2.py —— P-WALL-ICON2：图块标题栏图标简化

原来的图标是「坐标轴 + 柱状图 + 钟形曲线」，14px 下糊成一团。
改成 **xy 轴 + 三根实心柱**（柱用 fill=currentColor），14px 下可辨。

前置：P-WALL 系列已打（标题栏里存在 `children:"Figure"` 的图标 svg）。
幂等；`--revert` 还原。
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time

DIST = os.environ.get("KIMI_DIST") or os.path.join(os.path.expanduser("~"),
    ".vscode-server/extensions/moonshot-ai.kimi-code-0.8.2-linux-x64/dist")
WV = os.path.join(DIST, "webview.js")
STAMP = time.strftime("%Y%m%d-%H%M%S")
SUFFIX = "before-wall-icon2"
MARK = "__wallIcon2"
OLD='children:[d.jsx("path",{d:"M4 3.5v16.5h16.5"}),d.jsx("path",{d:"M7.5 20v-6.5h2.8V20"}),d.jsx("path",{d:"M11.2 20V9.5h2.8V20"}),d.jsx("path",{d:"M14.9 20v-5h2.8v5"}),d.jsx("path",{d:"M5.6 9.2c1.8-3.6 4-4.6 5.8-3.1 1.9 1.5 3.6 1.1 5.4-.9"})]'
NEW='children:[/* [__wallIcon2] 简化图标：xy 轴 + 三根实心柱，14px 下也认得出 */d.jsx("path",{d:"M4 3.5v16.5h16.5",strokeWidth:2}),d.jsx("rect",{x:7,y:12.5,width:3,height:7.5,fill:"currentColor",stroke:"none"}),d.jsx("rect",{x:11.5,y:8,width:3,height:12,fill:"currentColor",stroke:"none"}),d.jsx("rect",{x:16,y:14,width:3,height:6,fill:"currentColor",stroke:"none"})]'

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
    n = src.count(OLD)
    if n != 1:
        print("✗ 锚点命中 %d 次，中止（未写入）" % n)
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
        print("语法检查失败，已回滚：\n%s" % r.stderr[:800])
        return 1
    print("已打补丁：图标简化（xy 轴 + 三根实心柱）")
    print("备份：%s" % bak)
    return 0


if __name__ == "__main__":
    sys.exit(main())
