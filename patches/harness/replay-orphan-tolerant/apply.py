#!/usr/bin/env python3
"""Kimi Code VS Code 0.8.2 — replay orphan tolerant, v1 (2026-10-16).

治 bugs #173：**历史会话打开后视窗空白**、`/view` 失灵。

根因（已实测复现，非推测）
  上游在**中断路径**上会漏写 `step.begin`：turn 被 cancel 时，
  该 step 的 `content.part` 与 `step.end(finishReason:"interrupted")` 照样落盘，
  但 `step.begin` 没了（steps 1..n-1 的 begin/end 都成对，只有最后一个残缺）。
  而回放折叠器 `WireReplayFoldState.appendLoopEvent` 对「无 open `step.begin` 的
  `content.part` / `tool.call`」是 **hard throw**：
      throw new Error(`Received content_part for unknown step_uuid '…' (no open step_begin)`);
  这个 throw 被两级 `catch {}` 吞成空回放 ——
      `foldAgentWireReplay`      (§:227696) `catch {}` ⇒ 回落全量路径；
      `foldAgentWireReplayFull`  (§:227706) 在同一处再抛 ⇒ 被自身 `catch` 吞 ⇒ `EMPTY_FOLD`
  ⇒ `replay: []` ⇒ 视窗一条都渲染不出来（会话数据本身完好，104 个用户轮都在盘上）。

本补丁做什么（**只改折叠端的容错，不碰上游写盘时序**）
  E1: `content.part` 命中孤儿 stepUuid ⇒ **跳过该事件**（不再 throw）。
  E2: `tool.call`    命中孤儿 stepUuid ⇒ **跳过该事件**（不再 throw）。

  为什么改折叠端而不是上游：① 上游写盘时序在中断路径上，改它要动 turn/cancel 逻辑，
  风险面大且难验证；② 折叠端本就该对残缺 wire 容错 —— 同一函数里 `tool.result`
  面对未知 `toolCallId` 就是静默 `return`（见 `case "tool.result"`），
  `step.end` 对未知 uuid 也是静默 `delete`，本包只是把这两处的口径补齐。
  ⇒ **语义变更是"不可解析的残缺记录不再令整个会话不可读"，只影响坏记录本身。**

  顺带：`step.end` / `tool.result` 的既有静默行为**不动**（它们原本就不抛）。
  故打过本包后，bugs #173 那两个受害会话即使**不**做数据侧修复也能正常打开。

⚠️ 与 `fast-session-restore` 的关系：两者改的是不同函数、文本不重叠，可共存；
   但按台账重打顺序，本包应在 `stream-newline-fix` 之后、`fast-session-restore` 之前打
   （剪枝包仍须最后）。撤销本包**不动**其它包。

用法：
    python3 apply.py --check  [bundle]   # 只读：报告 E1/E2 命中状态
    python3 apply.py          [bundle]   # 打；幂等；先备份；node --check 失败自动回滚
    python3 apply.py --revert [bundle]   # 撤销本包全部编辑

bundle 缺省 = 最新安装的 `~/.vscode-server/extensions/moonshot-ai.kimi-code-*/dist/extension.js`。
"""
import glob
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

DEFAULT_GLOB = str(
    Path.home() / ".vscode-server/extensions/moonshot-ai.kimi-code-*/dist/extension.js"
)
PATCH_ID = "replay-orphan-tolerant"
VERSION = 1
CST = timezone(timedelta(hours=8))
MARKER = "LOCAL PATCH: replay orphan tolerant"

# =====================================================================================
# 编辑定义：每项 = (名字, 原文, 替换文, 打过后用于幂等判定的签名)
# =====================================================================================

E1_ANCHOR = "\n".join([
    '\t\t\tcase "content.part": {',
    '\t\t\t\tconst openStep = this.openSteps.get(event.stepUuid);',
    '\t\t\t\tif (openStep === void 0) throw new Error(`Received content_part for unknown step_uuid \'${event.stepUuid}\' (no open step_begin)`);',
    '\t\t\t\topenStep.content.push(event.part);',
])

E1_REPLACE = "\n".join([
    '\t\t\tcase "content.part": {',
    '\t\t\t\tconst openStep = this.openSteps.get(event.stepUuid);',
    '\t\t\t\t// === LOCAL PATCH: replay orphan tolerant (2026-10-16) — bugs #173 ===',
    '\t\t\t\t// 上游在中断路径上会漏写 step.begin；此处**跳过**孤儿事件而不是 throw，',
    '\t\t\t\t// 否则整个会话折叠成空回放（视窗空白）。口径与下方 tool.result / step.end 的静默忽略一致。',
    '\t\t\t\tif (openStep === void 0) return;',
    '\t\t\t\t// === END LOCAL PATCH: replay orphan tolerant ===',
    '\t\t\t\topenStep.content.push(event.part);',
])

E2_ANCHOR = "\n".join([
    '\t\t\tcase "tool.call": {',
    '\t\t\t\tconst openStep = this.openSteps.get(event.stepUuid);',
    '\t\t\t\tif (openStep === void 0) throw new Error(`Received tool_call for unknown step_uuid \'${event.stepUuid}\' (no open step_begin)`);',
    '\t\t\t\topenStep.toolCalls.push({',
])

E2_REPLACE = "\n".join([
    '\t\t\tcase "tool.call": {',
    '\t\t\t\tconst openStep = this.openSteps.get(event.stepUuid);',
    '\t\t\t\t// === LOCAL PATCH: replay orphan tolerant (2026-10-16) — bugs #173 ===',
    '\t\t\t\t// 同 E1：孤儿 tool.call 静默跳过（其配对的 tool.result 会因 pendingToolResultIds 不含该 id 而自然被忽略）。',
    '\t\t\t\tif (openStep === void 0) return;',
    '\t\t\t\t// === END LOCAL PATCH: replay orphan tolerant ===',
    '\t\t\t\topenStep.toolCalls.push({',
])

EDITS = [
    ("E1", "content.part 孤儿 ⇒ 跳过（原 throw）", E1_ANCHOR, E1_REPLACE),
    ("E2", "tool.call 孤儿 ⇒ 跳过（原 throw）", E2_ANCHOR, E2_REPLACE),
]

SIGNATURE = "if (openStep === void 0) return;"


def now_cst() -> str:
    return datetime.now(CST).strftime("%Y%m%d-%H%M%S")


def resolve_bundle(arg):
    if arg:
        return Path(arg).expanduser().resolve()
    cands = sorted(glob.glob(DEFAULT_GLOB), key=lambda p: Path(p).stat().st_mtime, reverse=True)
    if not cands:
        sys.exit(f"找不到 bundle：{DEFAULT_GLOB}")
    return Path(cands[0]).resolve()


def node_check(text):
    """真语法闸门：Node 对首行含 import 的 .js 会跳过函数体解析，必须复制为 .mjs。"""
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False) as fh:
        fh.write(text)
        tmp = fh.name
    try:
        r = subprocess.run(["node", "--check", tmp], capture_output=True, text=True)
        return r.returncode == 0, (r.stderr or r.stdout).strip()
    finally:
        Path(tmp).unlink(missing_ok=True)


def count(text, needle):
    return text.count(needle)


def status(text):
    """返回 {name: (applied:int, anchor:int, sig:int)}"""
    out = {}
    for name, desc, anchor, replace in EDITS:
        out[name] = (count(text, replace), count(text, anchor), count(text, SIGNATURE))
    return out


def do_check(bundle):
    text = bundle.read_text(encoding="utf-8")
    st = status(text)
    print(f"bundle : {bundle}")
    print(f"补丁   : {PATCH_ID} v{VERSION}")
    print()
    all_done = True
    for name, desc, _, _ in EDITS:
        applied, anchor, sig = st[name]
        if applied:
            state = "已打"
        elif anchor:
            state = "未打（锚点在）"
            all_done = False
        else:
            state = "**锚点缺失**（版本可能又变了，需人工适配）"
            all_done = False
        print(f"  {name} {desc}\n      applied={applied} anchor={anchor} sig={sig}  ⇒ {state}")
    print()
    print("全部已打 ✅" if all_done and all(st[n][0] for n, *_ in EDITS) else "尚有未打项（见上）")


def do_apply(bundle):
    text = bundle.read_text(encoding="utf-8")
    orig = text
    st = status(text)

    if all(st[name][0] for name, *_ in EDITS):
        print("本包已全部打过，幂等退出（bundle 未改动）。")
        return 0

    for name, desc, anchor, replace in EDITS:
        applied, anchor_cnt, _ = st[name]
        if applied:
            print(f"  {name} 已打，跳过")
            continue
        if anchor_cnt == 0:
            sys.exit(f"❌ {name} 锚点未命中 —— 中止（bundle 未改动）：{desc}")
        if anchor_cnt != 1:
            sys.exit(f"❌ {name} 锚点命中 {anchor_cnt} 次（应恰好 1 次）—— 中止：{desc}")
        text = text.replace(anchor, replace, 1)
        print(f"  {name} 已替换：{desc}")

    ok, err = node_check(text)
    if not ok:
        sys.exit(f"❌ node --check 失败，未写入（bundle 未改动）：\n{err}")

    backup = bundle.with_name(f"{bundle.name}.bak-{now_cst()}-before-{PATCH_ID}")
    shutil.copy2(bundle, backup)
    bundle.write_text(text, encoding="utf-8")
    print()
    print(f"✅ 已写入 {bundle}")
    print(f"   备份：{backup}")
    print(f"   改动：+{len(text) - len(orig)} 字符")
    print()
    print("⚠️ 需 Reload VS Code 窗口才生效。")
    return 0


def do_revert(bundle):
    text = bundle.read_text(encoding="utf-8")
    st = status(text)
    if not any(st[name][0] for name, *_ in EDITS):
        print("本包未打过，无需撤销（bundle 未改动）。")
        return 0
    orig = text
    for name, desc, anchor, replace in EDITS:
        if st[name][0]:
            if text.count(replace) != 1:
                sys.exit(f"❌ {name} 替换块出现 {text.count(replace)} 次，无法安全定位 —— 中止")
            text = text.replace(replace, anchor, 1)
            print(f"  {name} 已撤销：{desc}")
    if text != orig:
        ok, err = node_check(text)
        if not ok:
            sys.exit(f"❌ 撤销后 node --check 失败，未写入：\n{err}")
        backup = bundle.with_name(f"{bundle.name}.bak-{now_cst()}-before-revert-{PATCH_ID}")
        shutil.copy2(bundle, backup)
        bundle.write_text(text, encoding="utf-8")
        print(f"\n✅ 已撤销并写入 {bundle}\n   备份：{backup}")
        print("\n⚠️ 需 Reload VS Code 窗口才生效。")
    return 0


def main():
    args = [a for a in sys.argv[1:]]
    mode = "apply"
    if "--check" in args:
        mode = "check"; args.remove("--check")
    elif "--revert" in args:
        mode = "revert"; args.remove("--revert")
    bundle = resolve_bundle(args[0] if args else None)
    if not bundle.is_file():
        sys.exit(f"不是文件：{bundle}")
    if mode == "check":
        do_check(bundle)
    elif mode == "revert":
        do_revert(bundle)
    else:
        do_apply(bundle)


if __name__ == "__main__":
    sys.exit(main() or 0)
