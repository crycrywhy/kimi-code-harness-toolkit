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
"""apply_pp.py —— P-P：权限模式改为「打开会话时加载」（去掉 10 秒轮询）

用户 <DATE>：轮询改成在会话打开时加载。
实现：订阅聊天 store J0 的 sessionId，sessionId 变化（含首次挂载）时查一次；
      打开下拉菜单时也顺带刷新一次（手工 /auto 后显示不至于过时）。
"""
import argparse, os, shutil, subprocess, tempfile, time

WEB = os.path.join(_KIMI_DIST, "webview.js")
STAMP = time.strftime("%Y%m%d-%H%M%S")
SUFFIX = "before-pp-load-on-session"

OLD = '\tS.useEffect(()=>{refresh();const t=setInterval(refresh,10000);return()=>clearInterval(t)},[]);\n'
NEW = '\tconst{sessionId:sid}=J0();\n\tS.useEffect(()=>{refresh()},[sid]);\n'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    src = open(WEB, encoding="utf-8").read()
    if src.count(NEW) >= 1 and src.count(OLD) == 0:
        print("SKIP: already applied")
        return 0
    if src.count(OLD) != 1:
        print(f"FAIL: anchor count = {src.count(OLD)}")
        return 1
    src = src.replace(OLD, NEW, 1)
    if a.check:
        print("OK anchor unique")
        return 0
    bak = f"{WEB}.bak-{STAMP}-{SUFFIX}"
    shutil.copy2(WEB, bak)
    tmp = WEB + ".tmp-pp.js"
    open(tmp, "w", encoding="utf-8").write(src)
    with tempfile.NamedTemporaryFile(suffix=".mjs", delete=False) as tf:
        shutil.copyfile(tmp, tf.name)
        chk = tf.name
    r = subprocess.run(["node", "--check", chk], capture_output=True, text=True)
    os.unlink(chk)
    if r.returncode != 0:
        os.remove(tmp)
        print("FAIL node --check:", (r.stderr or r.stdout)[:400])
        return 2
    os.replace(tmp, WEB)
    print(f"PATCHED {WEB}\nbackup  {bak}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
