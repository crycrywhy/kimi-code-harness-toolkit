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
"""apply_rb1.py —— P-RB1：```svg 代码块内联渲染（rich-report batch 第 1 步，<DATE>）

目标：让 agent 手写的 SVG 直接以图形出现在对话流里（同类宿主 那条路的等价物），
而不是像现在这样只当源码高亮显示。

为什么不用 rehype-raw：0.8.2 的 webview bundle 里 **没有** parse5 / hast-util-raw
（grep 全为 0），塞进去等于重打前端。而 remark-rehype 默认 `allowDangerousHtml:!0`
产出的 `raw` 节点会被 `R4e()` 改写成 `{type:"text"}` —— 所以原始 HTML/SVG 一定被丢弃。

走法：在已有的 `code:` 渲染器里加一条 `language-svg` 分支，把源码净化后
`dangerouslySetInnerHTML`。零新依赖，两处锚点。

安全边界（三层，任一层单独成立即安全）：
  1. webview CSP 为 `default-src 'none'` + `script-src 'nonce-…'`（无 unsafe-inline）
     ⇒ 注入的 `<script>` 与 `on*=` 处理器本来就不会执行；
  2. 净化器仍然主动剥掉 script / foreignObject / on* / javascript: URL（纵深防御）；
  3. 不放开 `allowedElements`，不影响 markdown 里其它 HTML 的处理方式。

主题跟随：容器设 `color: var(--foreground)`，模型写 `fill="currentColor"` 即随明暗切换。

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
SUFFIX = "before-rb1-inline-svg"
MARK = "__rb1Block"

# 锚点 1：R3e（代码块渲染器）定义之前 —— 插入净化器与内联块组件
ANCHOR_DEF = 'const R3e=S.memo(function({code:i,language:r,enableHighlight:t,style:n})'

HELPER = '''const __rb1Extract=function(s){let x=String(s==null?"":s);
/* 兼容把 SVG 包在 html/xml 外壳里（同类宿主 落盘就是裸 SVG，但用户也常存成 .html） */
const m=x.match(/<\\s*svg[\\s\\S]*?<\\s*\\/\\s*svg\\s*>/i);
if(m)return m[0];
const m2=x.match(/<\\s*svg[\\s>][\\s\\S]*$/i);
return m2?m2[0]:x;};
const __rb1Clean=function(s){let x=String(s==null?"":s);
x=x.replace(/<\\s*script[\\s\\S]*?<\\s*\\/\\s*script\\s*>/gi,"");
x=x.replace(/<\\s*script[^>]*\\/?>/gi,"");
x=x.replace(/<\\s*foreignObject[\\s\\S]*?<\\s*\\/\\s*foreignObject\\s*>/gi,"");
x=x.replace(/<\\s*foreignObject[^>]*\\/?>/gi,"");
x=x.replace(/<\\s*(iframe|object|embed|audio|video)[\\s\\S]*?<\\s*\\/\\s*\\1\\s*>/gi,"");
x=x.replace(/<\\s*(iframe|object|embed|audio|video)[^>]*\\/?>/gi,"");
x=x.replace(/\\son[a-zA-Z]+\\s*=\\s*("[^"]*"|'[^']*'|[^\\s>]+)/g,"");
x=x.replace(/((?:xlink:)?href)\\s*=\\s*(["'])\\s*javascript:[\\s\\S]*?\\2/gi,"");
x=x.replace(/<\\s*(style|link)[\\s\\S]*?<\\s*\\/\\s*style\\s*>/gi,"");
x=x.replace(/<\\s*(style|link)[^>]*\\/?>/gi,"");
x=x.replace(/style\\s*=\\s*("|')?[^"'>]*expression\\s*\\(/gi,"data-rb1-drop=\\"1\\" style=\\"");
return x.trim();};
const __rb1Wrap=function(s){let x=__rb1Clean(__rb1Extract(s));if(!x)return"";
if(!/<\\s*svg[\\s>]/i.test(x))x='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 450">'+x+"</svg>";
const head=x.slice(0,x.indexOf(">")+1);
if(!/xmlns=/i.test(head))x=x.replace(/<svg/i,'<svg xmlns="http://www.w3.org/2000/svg"');
x=x.replace(/<svg([^>]*)>/i,function(m,a){
const vb=/(?:^|\\s)viewBox\\s*=\\s*["']([^"']*)["']/i.exec(a);
const wm=/(?:^|\\s)width\\s*=\\s*["']([^"']*)["']/i.exec(a);
let vbw=0,vbh=0;
if(vb){const p=String(vb[1]).trim().split(/[\\s,]+/).map(Number);
if(p.length===4&&p[2]>0&&p[3]>0){vbw=p[2];vbh=p[3];}}
let attrs=a;
/* 用 viewBox 的固有宽做上限：没写 width、或只写百分比（"100%"）时改成固有宽。
   配合 max-width:100% ⇒ min(容器宽, 固有宽)：容器更宽时不放大（字不变大），更窄时才缩。 */
if((!wm||/%/.test(wm[1]))&&vbw>0){
attrs=attrs.replace(/(?:^|\\s)width\\s*=\\s*["'][^"']*["']/i,"")
           .replace(/(?:^|\\s)height\\s*=\\s*["'][^"']*["']/i,"");
attrs+=' width="'+vbw+'"'+(vbh>0?' height="'+vbh+'"':'');
}
return "<svg"+attrs+' style="max-width:100%;height:auto;display:block;margin:0 auto;font-family:inherit">';
});
return x;};
const __rb1Block=S.memo(function({src:e}){
const html=S.useMemo(function(){
if(!/<\\s*\\/\\s*svg\\s*>/i.test(String(e||"")))return"";   /* 流式未闭合：先按源码显示，闭合后再换成图 */
return __rb1Wrap(e);},[e]);
if(!html)return null;
return d.jsx("div",{className:"my-2 rounded-md border border-border bg-muted/20 p-2 overflow-auto",style:{color:"var(--foreground)"},dangerouslySetInnerHTML:{__html:html}});
});
'''

# 锚点 2：code 渲染器出口 —— 认出 language-svg 就走内联块
OLD2 = ('return d.jsx(R3e,{code:_,language:E==null?void 0:E[1],'
        'enableHighlight:t&&!!E,style:h})')
NEW2 = ('const __lg=E==null?void 0:E[1];'
        'if(__lg&&/^(svg|svg-inline|html|htm|xhtml|xml)$/i.test(__lg)'
        '&&/<\\s*svg[\\s>]/i.test(_)&&/<\\s*\\/\\s*svg\\s*>/i.test(_))'
        'return d.jsx(__rb1Block,{src:_});'
        'return d.jsx(R3e,{code:_,language:__lg,'
        'enableHighlight:t&&!!E,style:h})')


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
    for old, tag in ((ANCHOR_DEF, "R3e-def"), (OLD2, "code-exit")):
        n = src.count(old)
        if n != 1:
            print("anchor", tag, "count", n)
            return 1
    src = src.replace(ANCHOR_DEF, HELPER + ANCHOR_DEF, 1)
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
