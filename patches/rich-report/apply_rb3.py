#!/usr/bin/env python3
"""apply_rb3.py —— P-RB3：内联 SVG 撑满容器宽度并居中（rich-report batch）

背景：P-RB1 的尺寸策略是「用 viewBox 固有宽做上限、容器更宽不放大」——防正文图被放大是对的，
但工具卡片内容区远宽于固有宽 ⇒ 图被钉死、再加卡片 padding 就变成「小且偏左上」。
修法：给 `__rb1Wrap` / `__rb1Block` 加一个 `fill` 模式，只有工具卡片调用点开启：
  - 根元素 `width:100%;max-width:100%`；
  - **不**写死 `width="vbw"` 属性（否则属性与 CSS 打架）；
  - 容器 `w-full` + flex 居中。

前置：P-RB1、P-RB2 已打（`__rb1Wrap` / `__rb1Block` 存在）。
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
SUFFIX = "before-rb3-fill"
MARK = "function(s,fill)"
OLD_WRAP='const __rb1Wrap=function(s){let x=__rb1Clean(__rb1Extract(s));if(!x)return"";\nif(!/<\\s*svg[\\s>]/i.test(x))x=\'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 450">\'+x+"</svg>";\nconst head=x.slice(0,x.indexOf(">")+1);\nif(!/xmlns=/i.test(head))x=x.replace(/<svg/i,\'<svg xmlns="http://www.w3.org/2000/svg"\');\nx=x.replace(/<svg([^>]*)>/i,function(m,a){\nconst vb=/(?:^|\\s)viewBox\\s*=\\s*["\']([^"\']*)["\']/i.exec(a);\nconst wm=/(?:^|\\s)width\\s*=\\s*["\']([^"\']*)["\']/i.exec(a);\nlet vbw=0,vbh=0;\nif(vb){const p=String(vb[1]).trim().split(/[\\s,]+/).map(Number);\nif(p.length===4&&p[2]>0&&p[3]>0){vbw=p[2];vbh=p[3];}}\nlet attrs=a;\n/* 用 viewBox 的固有宽做上限：没写 width、或只写百分比（"100%"）时改成固有宽。\n   配合 max-width:100% ⇒ min(容器宽, 固有宽)：容器更宽时不放大（字不变大），更窄时才缩。 */\nif((!wm||/%/.test(wm[1]))&&vbw>0){\nattrs=attrs.replace(/(?:^|\\s)width\\s*=\\s*["\'][^"\']*["\']/i,"")\n           .replace(/(?:^|\\s)height\\s*=\\s*["\'][^"\']*["\']/i,"");\nattrs+=\' width="\'+vbw+\'"\'+(vbh>0?\' height="\'+vbh+\'"\':\'\');\n}\nreturn "<svg"+attrs+\' style="max-width:100%;height:auto;display:block;margin:0 auto;font-family:inherit">\';\n});\nreturn x;}'
NEW_WRAP='const __rb1Wrap=function(s,fill){let x=__rb1Clean(__rb1Extract(s));if(!x)return"";\nif(!/<\\s*svg[\\s>]/i.test(x))x=\'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 450">\'+x+"</svg>";\nconst head=x.slice(0,x.indexOf(">")+1);\nif(!/xmlns=/i.test(head))x=x.replace(/<svg/i,\'<svg xmlns="http://www.w3.org/2000/svg"\');\nx=x.replace(/<svg([^>]*)>/i,function(m,a){\nconst vb=/(?:^|\\s)viewBox\\s*=\\s*["\']([^"\']*)["\']/i.exec(a);\nconst wm=/(?:^|\\s)width\\s*=\\s*["\']([^"\']*)["\']/i.exec(a);\nlet vbw=0,vbh=0;\nif(vb){const p=String(vb[1]).trim().split(/[\\s,]+/).map(Number);\nif(p.length===4&&p[2]>0&&p[3]>0){vbw=p[2];vbh=p[3];}}\nlet attrs=a;\n/* 用 viewBox 的固有宽做上限：没写 width、或只写百分比（"100%"）时改成固有宽。\n   配合 max-width:100% ⇒ min(容器宽, 固有宽)：容器更宽时不放大（字不变大），更窄时才缩。 */\nif((!wm||/%/.test(wm[1]))&&vbw>0){\nattrs=attrs.replace(/(?:^|\\s)width\\s*=\\s*["\'][^"\']*["\']/i,"")\n           .replace(/(?:^|\\s)height\\s*=\\s*["\'][^"\']*["\']/i,"");\n/* fill 模式不要写死 width：由 style 的 width:100% 撑满容器，否则属性会和 CSS 打架 */\nif(!fill)attrs+=\' width="\'+vbw+\'"\'+(vbh>0?\' height="\'+vbh+\'"\':\'\');\n}\nreturn "<svg"+attrs+\' style="\'+(fill?\'width:100%;max-width:100%;\':\'max-width:100%;\')+\'height:auto;display:block;margin:0 auto;font-family:inherit">\';\n});\nreturn x;}'
OLD_BLOCK='const __rb1Block=S.memo(function({src:e}){\nconst html=S.useMemo(function(){\nif(!/<\\s*\\/\\s*svg\\s*>/i.test(String(e||"")))return"";   /* 流式未闭合：先按源码显示，闭合后再换成图 */\nreturn __rb1Wrap(e);},[e]);\nif(!html)return null;\nreturn d.jsx("div",{className:"my-2 rounded-md border border-border bg-muted/20 p-2 overflow-auto",style:{color:"var(--foreground)"},dangerouslySetInnerHTML:{__html:html}});\n}'
NEW_BLOCK='const __rb1Block=S.memo(function({src:e,fill:f}){\nconst html=S.useMemo(function(){\nif(!/<\\s*\\/\\s*svg\\s*>/i.test(String(e||"")))return"";   /* 流式未闭合：先按源码显示，闭合后再换成图 */\nreturn __rb1Wrap(e,f);},[e,f]);\nif(!html)return null;\n/* fill 模式（工具卡片）：容器撑满 + 图居中；非 fill（正文代码块）：保持原样 */\nreturn d.jsx("div",{\nclassName:"my-2 rounded-md border border-border bg-muted/20 p-2 overflow-auto"+(f?" w-full":""),\nstyle:f?{color:"var(--foreground)",display:"flex",justifyContent:"center",alignItems:"flex-start",width:"100%"}:{color:"var(--foreground)"},\ndangerouslySetInnerHTML:{__html:html}});\n}'

EDITS = [
    ("__rb1Wrap 加 fill 参数", OLD_WRAP, NEW_WRAP),
    ("__rb1Block 透传 fill", OLD_BLOCK, NEW_BLOCK),
    ("工具卡片调用点开启 fill", 'd.jsx(__rb1Block,{src:i.output})',
     'd.jsx(__rb1Block,{src:i.output,fill:!0})'),
]


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
        print("已打过补丁（找到 fill 模式），跳过")
        return 0
    out = src
    for name, old, new in EDITS:
        n = out.count(old)
        if n != 1:
            print("✗ [%s] 锚点命中 %d 次，中止（未写入）" % (name, n))
            return 1
        out = out.replace(old, new, 1)

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
    print("已打补丁：P-RB3（fill 模式）")
    print("备份：%s" % bak)
    return 0


if __name__ == "__main__":
    sys.exit(main())
