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
"""apply_img_grid.py —— P-IMG-GRID：同一段里的多张图自动排成网格（<DATE>）

`build_image_wall` 的 `split` 模式会生成 N 张**独立** PNG（每格一张、各自可单击放大），
并在回复里用一行 markdown 贴出（`![a](x) ![b](y) …`）。但 preflight 把 `img` 设成
`display:block`，一行里的图会各占一整行。

本补丁往 webview 的 CSS 里加两条规则：

    p:has(>img ~ img)      > img { width:50%    }   /* 2 张 → 一行两张 */
    p:has(>img ~ img ~ img)> img { width:33.33% }   /* 3 张 → 一行三张 */
    （4 张以上回到 50%，自然排成 2×2）

只影响「同一段里有 ≥2 张图」的段落，单图和图标不受影响；每张仍是独立 `<img>`，
点击各开自己的全屏预览（P-IMG-FULL 的 `onPreview`）。

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
SUFFIX = "before-img-grid"
MARK = "__imgGrid"

OLD = "img,video{max-width:100%;height:auto}"
NEW = (
    "img,video{max-width:100%;height:auto}"
    "/* [__imgGrid] 同一段里多张图排成网格：2 张→两列，3 张→三列，4 张→2×2 */"
    "p:has(>img~img)>img{width:calc(50% - 6px)!important;max-width:calc(50% - 6px)!important;"
    "height:auto!important;display:inline-block!important;vertical-align:top;margin:3px}"
    "p:has(>img~img~img)>img{width:calc(33.333% - 6px)!important;max-width:calc(33.333% - 6px)!important}"
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
        print("锚点不唯一（找到 %d 处），放弃" % src.count(OLD))
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

    print("已打补丁：多图段落网格化")
    print("备份：%s" % bak)
    return 0


if __name__ == "__main__":
    sys.exit(main())
