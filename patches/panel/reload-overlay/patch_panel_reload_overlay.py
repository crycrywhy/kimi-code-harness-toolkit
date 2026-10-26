#!/usr/bin/env python3
"""Kimi Code VS Code — 宿主重启时让 webview 自己显示 "Reloading" 画面(2026-10-26)。

背景：实测 `deactivate()` 在「Restart Extension Host」时**根本不会被调用**（日志里没有任何
"Kimi Code deactivating"），所以宿主侧没法在临死前把面板切到 splash。改由 **webview 侧自检**：
每 800 ms 发一次极廉价的 `checkWorkspace` RPC（宿主侧只是返回三个字段），
若连续 2 次在 700 ms 内没有响应，就铺一层全屏 "KIMI / Reloading…" 覆盖层。
宿主活着时 RPC 毫秒级返回，覆盖层不会出现；宿主被杀后 1.5–3 s 内出现。

注入点：webview.js 的 IIFE 末尾（`console.error("[panel-restore] init failed",__err)` 之后、`})();` 之前），
那里能拿到 bridge 客户端 `Qe`。幂等：已打过则 skip。
"""
import glob
import re
import sys
from pathlib import Path

import patch_common

DEFAULT_GLOB = str(
    Path.home() / ".vscode-server/extensions/moonshot-ai.kimi-code-*/dist/webview.js"
)
SUFFIX = "reload-overlay"

ANCHOR = 'console.error("[panel-restore] init failed",__err)}})();'

SNIPPET = r''';try{(function(){
var __ovId="__kimi_host_reload",__miss=0,__born=Date.now();
function __ov(on){
  var el=document.getElementById(__ovId);
  if(!on){ if(el&&el.parentNode) el.parentNode.removeChild(el); return; }
  if(el) return;
  el=document.createElement("div");
  el.id=__ovId;
  el.style.cssText="position:fixed;inset:0;z-index:2147483647;background:var(--vscode-editor-background,#0b0b0c);display:flex;flex-direction:column;align-items:center;justify-content:center;gap:20px;color:#cfcfcf;font:14px system-ui,sans-serif";
  el.innerHTML='<div style="font-size:46px;letter-spacing:8px;font-weight:700;color:#e9e9e9">KIMI</div><div style="width:200px;height:3px;background:rgba(127,127,127,.25);overflow:hidden;border-radius:2px"><div style="width:70px;height:100%;background:#4b8bf5;animation:__kimiReload 1.1s infinite linear"></div></div><div style="opacity:.65">Reloading…</div><style>@keyframes __kimiReload{0%{transform:translateX(-70px)}100%{transform:translateX(200px)}}</style>';
  document.body.appendChild(el);
}
function __busy(){ try{ var s=J0.getState(); return !!(s&&s.isStreaming); }catch(e){ return false; } }
function __ping(){
  var done=false;
  var timer=setTimeout(function(){
    if(done) return;
    if(Date.now()-__born<15000) return;
    if(__busy()) return;
    __miss+=1;
    if(__miss>=3) __ov(true);
  },900);
  try{
    Qe.checkWorkspace().then(function(){ done=true; clearTimeout(timer); __miss=0; __ov(false); },function(){ done=true; clearTimeout(timer); __miss=0; __ov(false); });
  }catch(e){}
}
setInterval(__ping,800);__ping();
})()}catch(__e){}'''

REPL = ANCHOR[:-5] + SNIPPET + "})();"   # ANCHOR[-5:] == "})();" —— 保留 catch 的收尾大括号


def resolve(path_glob):
    cands = [Path(p) for p in glob.glob(path_glob)]
    if not cands:
        sys.exit("FATAL: 没找到任何 kimi-code webview.js，glob=%s" % path_glob)
    cands.sort(key=lambda p: p.stat().st_mtime)
    return cands[-1]


def main():
    wv = resolve(DEFAULT_GLOB)
    print("目标 webview.js: %s" % wv)
    text = wv.read_text(encoding="utf-8")
    if SNIPPET in text:
        print("已打过，跳过。")
        return
    if text.count(ANCHOR) != 1:
        sys.exit("FATAL: 锚点命中 %d 次（应为 1）→ 中止，未写文件" % text.count(ANCHOR))
    new_text = text.replace(ANCHOR, REPL, 1)
    print("备份: %s" % patch_common.make_backup(str(wv), SUFFIX))
    if not patch_common.write_validated(str(wv), new_text, patch_common.node_check, label="webview.js"):
        sys.exit("FATAL: 校验失败，原文件未改动")
    print("已注入 reload overlay（每 800ms 心跳，连续 2 次 700ms 无响应即显示 Reloading）。")
    print("提示：改动 webview 后必须 Reload Window 才生效。")


if __name__ == "__main__":
    main()
