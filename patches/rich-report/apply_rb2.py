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
"""apply_rb2.py —— P-RB2：工具结果里的 SVG 直接画在卡片上（rich-report batch 第 2 步，<DATE>）

配 `rich_report_mcp` 的 `render_diagram`：那个工具的 `output` 是**一段裸 SVG**（以 `<svg`
开头、`</svg>` 结尾）。本补丁让宿主认出这种输出，把图渲染在**工具卡片头部下方**，
而不是像其它工具那样塞进折叠区、走 `<pre>` 纯文本。

为什么要改两处（缺一不可）：
  1. `bY`（工具卡片）—— 图必须在**折叠区外面**。放里面的话，用户得先点开卡片才看得到图，
     等于没做（`t && …` 才渲染折叠内容）。
  2. `ibe`（默认工具结果渲染器）—— 展开时别再把 SVG 当文本糊一遍。

净化与尺寸策略复用 P-RB1 的 `__rb1Wrap`（同一份代码，不复制）：`apply_rb1.py` 必须先跑。

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
SUFFIX = "before-rb2-tool-svg"
MARK = "__rb2ToolSvg"

# 锚点 1：bY 的折叠内容行 —— 在它前面插入「裸 SVG 就画图」的分支
OLD1 = ('}),t&&d.jsx("div",{className:"@container px-3 py-0.5 border-t border-border",children:A()})]})}')
NEW1 = ('}),__rb2ToolSvg(i)&&d.jsx("div",{className:"border-t border-border px-3 py-2",'
        'children:d.jsx(__rb1Block,{src:i.output})}),'
        't&&d.jsx("div",{className:"@container px-3 py-0.5 border-t border-border",children:A()})]})}')

# 锚点 2：ibe 的 OUT 分支 —— 裸 SVG 时不再当文本显示
OLD2 = 't?d.jsx(Va,{content:t})'
NEW2 = '__rb2ToolSvg(i)?d.jsx("span",{className:"text-xs text-muted-foreground",children:"（图已显示在卡片上）"}):t?d.jsx(Va,{content:t})'

HELPER = '''const __rb2ToolSvg=function(r){
try{
if(!r||r.is_error)return!1;
let o=r.output;
if(o&&typeof o=="object")o=Array.isArray(o)?o.map(function(p){return p&&p.type==="text"?p.text:"";}).join(""):(o.text||o.content||"");
o=String(o==null?"":o).trim();
if(o.length<40||o.length>400000)return!1;              /* 太小=不是图；太大=别卡渲染 */
if(o.charCodeAt(0)!==60)return!1;                      /* 必须以 "<" 开头，省掉大段字符串的 regex */
if(!/^<svg[\\s>]/i.test(o))return!1;
if(!/<\\s*\\/\\s*svg\\s*>\\s*$/i.test(o))return!1;
return /viewBox\\s*=\\s*["'][^"']+["']/i.test(o);
}catch(_){return!1;}};
'''

# __rb1Block 定义之后（RB1 保证它存在）插入 helper
ANCHOR_HELPER = 'const __rb1Block=S.memo(function({src:e}){'


def syntax_gate(path):
    with tempfile.NamedTemporaryFile(suffix=".mjs", delete=False) as tf:
        shutil.copyfileobj(open(path, "rb"), tf)
        tmp = tf.name
    r = subprocess.run(["node", "--check", tmp], capture_output=True, text=True)
    os.unlink(tmp)
    return r.returncode == 0, r.stderr[:900]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--revert", action="store_true")
    a = ap.parse_args()
    if a.revert:
        baks = sorted(p for p in os.listdir(DIST)
                      if p.startswith("webview.js.bak-") and p.endswith(SUFFIX))
        if not baks:
            print("no backup")
            return 1
        shutil.copy2(os.path.join(DIST, baks[-1]), WV)
        print("restored from", baks[-1])
        return 0

    src = open(WV, encoding="utf-8").read()
    if MARK in src:
        print("already patched")
        return 0
    if '__rb1Block' not in src:
        print("P-RB1 未安装：先跑 apply_rb1.py（本补丁复用它的 __rb1Wrap）")
        return 1
    for old, tag in ((OLD1, "bY-fold"), (OLD2, "ibe-out"), (ANCHOR_HELPER, "rb1block")):
        n = src.count(old)
        if n != 1:
            print("anchor", tag, "count", n)
            return 1
    src = src.replace(ANCHOR_HELPER, HELPER + ANCHOR_HELPER, 1)
    src = src.replace(OLD1, NEW1, 1)
    src = src.replace(OLD2, NEW2, 1)

    bak = f"{WV}.bak-{STAMP}-{SUFFIX}"
    shutil.copy2(WV, bak)
    open(WV, "w", encoding="utf-8").write(src)
    ok, err = syntax_gate(WV)
    print(("OK  " if ok else "FAIL") + " node --check webview.js")
    if not ok:
        print(err)
        shutil.copy2(bak, WV)
        return 1
    print("backup:", os.path.basename(bak))
    return 0


if __name__ == "__main__":
    sys.exit(main())
