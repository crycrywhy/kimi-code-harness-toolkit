#!/usr/bin/env python3
"""Static + behavioural self-check for the fast-session-restore patch package.
v3 (2026-10-14).

不写盘。给定 bundle（缺省＝最新安装的 kimi-code extension.js），报告：
  1. v1 / v2 / v3 状态（`apply.state_of`）；
  2. v1 两处锚点的出现次数（预期：未打时各 1；已打时 anchor 变 0、signature 变 1）；
  3. 6 条编辑（E1..E6）各自的前像 / 后像命中次数；
  4. 关键常量值（兜底=0、总开关=false、递归深度=1、尾部=8 MiB）；
  5. N 的三个来源是否都可达（#3 slash-view 的读取器 / 设置项 / 环境变量名）；
  6. `node --check`（经 .mjs probe，真解析）；
  7. 行为测试：`test_v2_selection.mjs`（选择语义）+ `test_fast_restore.mjs`
     （头尾读取/切片/T 优先级）+ `test_v3_no_subagents.mjs`（E4 门控 + 缺 subagent 不崩）。

用法：python3 selfcheck.py [bundle]
"""
import glob
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import apply as P  # noqa: E402  (reuse the exact anchors/edits, no duplication)

CONSTANTS = [
    ("FAST_RESTORE_FALLBACK_SUBAGENTS = 0;", "兜底上限（0 = 关闭兜底）"),
    ("FAST_RESTORE_LOAD_SUBAGENTS = false;", "E4 总开关（false = 不加载任何 subagent）"),
    ("FAST_RESTORE_MAX_AGENT_DEPTH = 1;", "E5 递归深度（1 = 不找孙级）"),
    ("FAST_RESTORE_TAIL_BYTES = 8 * 1024 * 1024;", "E6 尾部读取窗口（8 MiB）"),
]


def resolve(argv):
    positional = [a for a in argv if not a.startswith("--")]
    if positional:
        return Path(positional[0]).expanduser().resolve()
    cands = [Path(p) for p in glob.glob(P.DEFAULT_GLOB)]
    if not cands:
        sys.exit("FATAL: no kimi-code extension install found: %s" % P.DEFAULT_GLOB)
    cands.sort(key=lambda p: p.stat().st_mtime)
    return cands[-1].resolve()


def run_node(args):
    try:
        res = subprocess.run(args, capture_output=True, text=True)
    except FileNotFoundError:
        print("    SKIPPED (node not on PATH)")
        return None
    tail = [ln for ln in res.stdout.splitlines() if ln.strip()][-1:] if res.stdout else []
    if res.returncode != 0:
        sys.stdout.write(res.stdout)
        sys.stderr.write(res.stderr)
    else:
        print("    %s" % (tail[0] if tail else "OK"))
    return res.returncode


def main():
    bundle = resolve(sys.argv[1:])
    if not bundle.exists():
        print("FATAL: bundle not found: %s" % bundle)
        return 2
    text = bundle.read_text(encoding="utf-8")
    state = P.state_of(text)
    print("bundle : %s (%d bytes)" % (bundle, len(text.encode("utf-8"))))
    print("state  : %s   (patch %s v%d)" % (state, P.PATCH_ID, P.VERSION))
    problems = []

    if state == "none":
        for name, anchor, _repl, signature in P.V1_PATCHES:
            count = text.count(anchor)
            print("  [v1 %s] anchor x%d  (expected 1)" % (name, count))
            if count != 1:
                problems.append("v1 anchor %s matched %d (expected 1)" % (name, count))
    else:
        for name, anchor, _repl, signature in P.V1_PATCHES:
            print("  [v1 %s] anchor x%d  signature=%s" % (name, text.count(anchor), signature in text))
            if signature not in text:
                problems.append("v1 signature of %s missing in a %s bundle" % (name, state))

    present_posts = [new for _g, _l, _o, new in P.V2_EDITS if text.count(new) == 1]
    edits = list(P.V2_EDITS)

    def superseded(index, new):
        """A missing post-image is fine when the NEXT edit in the chain has been applied
        (e.g. E3's text is rewritten again by E4-2, so E3's post-image no longer matches
        verbatim) — the chain simply moved on."""
        if any(new in other and other != new for other in present_posts):
            return True
        nxt = index + 1
        return nxt < len(edits) and text.count(edits[nxt][3]) == 1

    for index, (group, label, old, new) in enumerate(edits):
        counts = (text.count(old), text.count(new))
        note = ""
        if state == "v2" and group == "v3":
            note = "   (v3 编辑：v2 状态下本就未打，n/a)"
        print("  [%-2s %-52s] pre x%d  post x%d%s" % (group, label, counts[0], counts[1], note))
        if state == "v1" and group == "v2" and counts[0] != 1:
            problems.append("edit %s: pre-image matched %d (expected 1)" % (label, counts[0]))
        if state == "v2" and group == "v3":
            continue
        if state in ("v2", "v3") and counts[1] == 0 and not superseded(index, new):
            problems.append("edit %s: post-image matched 0 and the chain did not advance past it" % label)

    print("  constants:")
    for needle, label in CONSTANTS:
        found = needle in text
        print("    %-46s %s   (%s)" % (needle, "present" if found else "ABSENT", label))
        if state in ("v2", "v3") and needle.startswith("FAST_RESTORE_FALLBACK_SUBAGENTS") and not found:
            problems.append("constant missing: %s" % needle)
        if state == "v3" and needle.startswith(("FAST_RESTORE_LOAD_SUBAGENTS", "FAST_RESTORE_MAX_AGENT_DEPTH", "FAST_RESTORE_TAIL_BYTES")) and not found:
            problems.append("constant missing: %s" % needle)

    if "function readReplayTurnLimitSetting" in text:
        print("  dependency #3 slash-view: readReplayTurnLimitSetting() present -> reused")
    else:
        print("  dependency #3 slash-view: ABSENT (falls back to getConfig()/default)")
    for token, label in (("KIMI_CODE_REPLAY_TURNS", "env var name"), ("replayTurnLimit", "setting key")):
        print("  %s %r: %s" % (label, token, "present" if token in text else "ABSENT"))

    print("  node --check:")
    try:
        if not P.run_node_check(bundle):
            problems.append("node --check failed")
    except Exception as error:  # noqa: BLE001
        print("    SKIPPED (%s)" % error)

    print("  behavioural tests:")
    if state in ("v2", "v3"):
        names = ["test_v2_selection.mjs", "test_fast_restore.mjs"]
        if state == "v3":
            names.append("test_v3_no_subagents.mjs")
        for name in names:
            path = HERE / name
            if not path.exists():
                problems.append("%s missing" % name)
                continue
            print("    %s:" % name)
            rc = run_node(["node", str(path), str(bundle)])
            if rc is not None and rc != 0:
                problems.append("%s failed" % name)
    else:
        print("    (skipped — bundle is not v2/v3)")

    if problems:
        print("\nPROBLEMS: %s" % "; ".join(problems))
        return 1
    print("\nself-check: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
