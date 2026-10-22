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
"""apply_pq.py —— P-Q：模式按钮改为「发 /auto、/yolo 指令」而非新 RPC

用户 <DATE>：手动点按钮等价于在对话框输入 /auto、/yolo；按钮只反映当前 session 的模式。
实现：菜单选择 → 用聊天 store 的 sendMessage 发对应斜杠命令（与手打完全同一条链路）；
      选完 1.2 秒后刷新一次显示；按钮的模式始终由 getPermissionMode（session 级）读取。
"""
import argparse, os, shutil, subprocess, tempfile, time

WEB = os.path.join(_KIMI_DIST, "webview.js")
STAMP = time.strftime("%Y%m%d-%H%M%S")
SUFFIX = "before-pq-slash-command"

OLD = '\tconst pick=u=>{Qe.setPermissionMode(u).then(()=>setMode(u)).catch(()=>{})};\n'
NEW = ('\tconst pick=u=>{if(u===mode)return;'
       'const cmd=u==="auto"?"/auto":u==="yolo"?"/yolo":(mode==="auto"?"/auto":"/yolo");'
       'try{J0.getState().sendMessage(cmd)}catch(_){}'
       'setTimeout(refresh,1200)};\n')


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
    tmp = WEB + ".tmp-pq.js"
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
