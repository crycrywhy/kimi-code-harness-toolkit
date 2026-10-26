#!/usr/bin/env python3
"""apply_ct2.py —— CT-2：摘要里的每条事实必须带日期（2026-10-21）

**漏洞**：compaction-timestamps（CT-1）给**保留的用户消息**加了 `[YYYY-MM-DD HH:MM UTC+8]`
前缀，并在摘要开头写 `Compaction performed at …`。但摘要**正文**里的事实性断言没有任何
时间锚——只有开头一个"压缩时刻"，说明不了"摘要里这句话是什么时候的事实"。

**后果（真实发生过）**：2026-10-15 用户说"我准备更新 kimicode 到 0.8.2"，这条消息带着
`[2026-10-15 14:30 UTC+8]` 前缀留在历史里。但摘要若把它写成裸的"用户即将升级 0.8.2"，
后续每轮都会把它当成**当前状态**——2026-10-21（早已是 0.8.2）我仍拿它当"马上要升"的
理由。根因不在时间戳缺失，而在**摘要撰写时没被要求给事实加日期**。

**修法**：只改给摘要模型的**指令**（`compaction_instruction_default` 模板），加一段
"Time anchoring" 要求。不动摘要正文格式、不动 CT-1 的任何锚点、不动摘要后处理。

幂等；`--revert` 还原。
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

# 【2026-10-25】版本目录改为自动查找最新（原硬编码 0.8.2，升级 0.8.3 后打错目录）。
# 可用 KIMI_DIST 覆盖；否则取按版本号排序最大的 moonshot-ai.kimi-code-* 目录。
def _kimi_dist():
    import glob as _g, os as _o
    if _o.environ.get("KIMI_DIST"):
        return _o.environ["KIMI_DIST"]
    cands = _g.glob(_o.path.expanduser("~/.vscode-server/extensions/moonshot-ai.kimi-code-*/dist"))
    def _ver(p):
        m = re.search(r"kimi-code-([0-9][^-]*)-", p)
        try:
            return tuple(int(x) for x in (m.group(1).split(".") if m else []))
        except ValueError:
            return (0,)
    return sorted(cands, key=_ver)[-1]
DIST = _kimi_dist()
EXT = os.path.join(DIST, "extension.js")
STAMP = time.strftime("%Y%m%d-%H%M%S")
SUFFIX = "before-ct2-time-anchor"
MARK = "Time anchoring"

# 锚点：模板里 "Be honest about uncertainty" 那一段之前插入新段（该段在模板中唯一）
ANCHOR = ('Be honest about uncertainty. If an earlier step claimed something was done but\\n'
          'was never verified (tests \\"passing\\", a fix \\"working\\", a file \\"created\\"), say so\\n'
          'plainly and treat it as unverified rather than fact — re-check before relying\\n'
          'on it.\\n\\n')

INSERT = ('Time anchoring: the preserved user messages are prefixed with their original\\n'
          'timestamps, and this summary is written at the moment shown at its top. Use\\n'
          'those two anchors. Every factual claim you record that has a date attached to\\n'
          'it — when the user said something, when a state was observed, when a version\\n'
          'was current — must carry that date inline, e.g. \\"2026-10-15: user said they\\n'
          'were about to upgrade to 0.8.2 (0.7.5 was current then)\\" rather than a bare\\n'
          '\\"user is about to upgrade to 0.8.2\\". A bare statement about the present\\n'
          'becomes a false statement about the present as soon as time passes, and the\\n'
          'next turn has no way to tell which it is. If you cannot tell when something\\n'
          'happened, say so instead of writing it as current fact.\\n\\n')


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
    ap.add_argument("--check", action="store_true", help="只验锚点，不改文件")
    a = ap.parse_args()

    if a.revert:
        baks = sorted(p for p in os.listdir(DIST)
                      if p.startswith("extension.js.bak-") and p.endswith(SUFFIX))
        if not baks:
            print("no backup")
            return 1
        shutil.copy2(os.path.join(DIST, baks[-1]), EXT)
        print("restored from", baks[-1])
        return 0

    src = open(EXT, encoding="utf-8").read()
    if MARK in src:
        print("already patched")
        return 0

    n = src.count(ANCHOR)
    print("anchor count:", n)
    if a.check:
        return 0 if n == 1 else 1
    if n != 1:
        print("anchor not unique — 停手，人工核对")
        return 1

    src = src.replace(ANCHOR, INSERT + ANCHOR, 1)

    bak = f"{EXT}.bak-{STAMP}-{SUFFIX}"
    shutil.copy2(EXT, bak)
    open(EXT, "w", encoding="utf-8").write(src)
    ok, err = syntax_gate(EXT)
    print(("OK  " if ok else "FAIL") + " node --check extension.js")
    if not ok:
        print(err)
        shutil.copy2(bak, EXT)
        return 1
    print("backup:", os.path.basename(bak))
    return 0


if __name__ == "__main__":
    sys.exit(main())
