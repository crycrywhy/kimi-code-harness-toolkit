#!/usr/bin/env python3
"""给 Kimi Code 0.8.2 的 compaction 补丁：保留消息带时间戳 + 摘要带压缩时刻。

背景
  2026-10-18：agent 把 3 天前的用户消息当成"上一条"，因为 compaction 保留的用户消息
  没有时间戳，且摘要里没有压缩时刻。补丁在 bundle 的 11 处注入：

  1) pushHistory：把 wire 记录的 time 以不可枚举属性 __wireTime 挂到消息对象上；
  2) replaceMessageText：复制消息时保留 __wireTime（截断路径会重建消息对象）；
  3) buildContextCompactionShape：保留消息按 __wireTime 前缀 `[YYYY-MM-DD HH:MM UTC+8]`；
     同时 compaction 请求本身也带时间戳，让摘要能引用日期；
  4) buildCompactionSummaryText：摘要开头写入 "Compaction performed at ... UTC+8"。

用法
  python3 apply.py                 # 打补丁到线上 bundle（自动备份）
  python3 apply.py --target X.js   # 对副本试打
  python3 apply.py --check         # 只检查锚点是否齐全
"""
import argparse
import re
import shutil
import subprocess
import sys
import time
import os

def _latest_bundle():
    """默认取已安装的最新 kimi-code 版本目录（0.8.3 起不再硬编码版本号）。"""
    import glob as _g
    cands = _g.glob(os.path.expanduser(
        "~/.vscode-server/extensions/moonshot-ai.kimi-code-*/dist/extension.js"))

    def _ver(p):
        m = re.search(r"kimi-code-([0-9][^-]*)-", p)
        try:
            return tuple(int(x) for x in (m.group(1).split(".") if m else []))
        except ValueError:
            return (0,)
    if not cands:
        return os.path.expanduser(
            "~/.vscode-server/extensions/moonshot-ai.kimi-code-0.8.2-linux-x64/dist/extension.js")
    return sorted(cands, key=_ver)[-1]


BUNDLE = os.environ.get("KIMI_EXT") or _latest_bundle()

A_OLD = '''\tpushHistory(messages, time) {
\t\tfor (const message of messages) {
'''
A_NEW = '''\tpushHistory(messages, time) {
\t\tfor (const message of messages) {
\t\t\tif (typeof time === "number" && Number.isFinite(time)) {
\t\t\t\ttry {
\t\t\t\t\tObject.defineProperty(message, "__wireTime", {
\t\t\t\t\t\tvalue: time,
\t\t\t\t\t\tenumerable: false,
\t\t\t\t\t\tconfigurable: true
\t\t\t\t\t});
\t\t\t\t} catch {}
\t\t\t}
'''

B_OLD = '''function replaceMessageText(message, text) {
\treturn {
\t\t...message,
\t\tcontent: [...message.content.filter((part) => isUserPromptSubmitHookPart$1(part) || isSkillActivationPart$1(part)), {
\t\t\ttype: "text",
\t\t\ttext
\t\t}],
\t\ttoolCalls: []
\t};
}
'''
B_NEW = '''function replaceMessageText(message, text) {
\tconst replaced = {
\t\t...message,
\t\tcontent: [...message.content.filter((part) => isUserPromptSubmitHookPart$1(part) || isSkillActivationPart$1(part)), {
\t\t\ttype: "text",
\t\t\ttext
\t\t}],
\t\ttoolCalls: []
\t};
\tif (message.__wireTime !== void 0) try {
\t\tObject.defineProperty(replaced, "__wireTime", {
\t\t\tvalue: message.__wireTime,
\t\t\tenumerable: false,
\t\t\tconfigurable: true
\t\t});
\t} catch {}
\treturn replaced;
}
'''

C_ANCHOR = 'function buildContextCompactionShape(history, input, estimate = defaultTokenEstimate) {'
C_NEW = '''function formatWireTimeUtc8(ms) {
\tconst d = new Date(ms + 288e5);
\tconst p = (n) => String(n).padStart(2, "0");
\treturn `${String(d.getUTCFullYear())}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())} UTC+8`;
}
function stampCompactionMessage(message) {
\tif (!isRealUserInput$1(message)) return message;
\tconst t = message.__wireTime;
\tif (typeof t !== "number" || !Number.isFinite(t)) return message;
\tconst stamp = `[${formatWireTimeUtc8(t)}]`;
\tconst content = Array.isArray(message.content) ? message.content : [];
\tconst first = content[0];
\tif (first !== void 0 && first.type === "text" && typeof first.text === "string" && first.text.startsWith(stamp)) return message;
\treturn {
\t\t...message,
\t\tcontent: [{
\t\t\ttype: "text",
\t\t\ttext: `${stamp}\\n`
\t\t}, ...content]
\t};
}
function stampCompactionHistory(messages) {
\treturn messages.map(stampCompactionMessage);
}
function buildContextCompactionShape(history, input, estimate = defaultTokenEstimate) {'''

D_OLD = '''\tconst keptMessages = elisionMessage === void 0 ? [...selection.head, ...selection.tail] : [
\t\t...selection.head,
\t\telisionMessage,
\t\t...selection.tail
\t];
'''
D_NEW = '''\tconst rawKeptMessages = elisionMessage === void 0 ? [...selection.head, ...selection.tail] : [
\t\t...selection.head,
\t\telisionMessage,
\t\t...selection.tail
\t];
\tconst keptMessages = rawKeptMessages.map(stampCompactionMessage);
'''

E_OLD = '\t\t\tlet historyForModel = stripDynamicToolContext(originalHistory);\n'
E_NEW = '\t\t\tlet historyForModel = stampCompactionHistory(stripDynamicToolContext(originalHistory));\n'

F_OLD = '''function buildCompactionSummaryText(summary) {
\tconst suffix = summary.trim();
\treturn `${COMPACTION_SUMMARY_PREFIX}\\n${suffix.length > 0 ? suffix : "(no summary available)"}`;
}
'''
F_NEW = '''function buildCompactionSummaryText(summary) {
\tconst suffix = summary.trim();
\tconst stamp = formatWireTimeUtc8(Date.now());
\treturn `${COMPACTION_SUMMARY_PREFIX}\\n\\nCompaction performed at ${stamp}. User messages preserved above are prefixed with their original timestamps (UTC+8).\\n${suffix.length > 0 ? suffix : "(no summary available)"}`;
}
'''

PATCHES = [
    ("A pushHistory 附时间", A_OLD, A_NEW),
    ("B replaceMessageText 复制时间", B_OLD, B_NEW),
    ("C 时间戳辅助函数", C_ANCHOR, C_NEW),
    ("D 保留消息加时间前缀", D_OLD, D_NEW),
    ("E compaction 请求带时间", E_OLD, E_NEW),
    ("F 摘要写压缩时刻", F_OLD, F_NEW),
    ("G fold 事件把 time 传下去（append）",
     '.on(ContextAppendMessage, (s, e) => foldAppendMessage(s, e.message))',
     '.on(ContextAppendMessage, (s, e) => foldAppendMessage(s, e.message, e.time))'),
    ("H fold 事件把 time 传下去（loop）",
     '.on(ContextAppendLoopEvent, (s, e) => foldLoopEvent(s, e.event))',
     '.on(ContextAppendLoopEvent, (s, e) => foldLoopEvent(s, e.event, e.time))'),
    ("I foldAppendMessage 接收 time", '''function foldAppendMessage(state, message) {
\tconst bound = boundOf(state);
\tbound.fold.appendMessage(message, void 0);
''', '''function foldAppendMessage(state, message, time) {
\tconst bound = boundOf(state);
\tbound.fold.appendMessage(message, time);
'''),
    ("J foldLoopEvent 接收 time", '''function foldLoopEvent(state, event) {
\tconst bound = boundOf(state);
\tbound.fold.loopEvent(event, void 0);
''', '''function foldLoopEvent(state, event, time) {
\tconst bound = boundOf(state);
\tbound.fold.loopEvent(event, time);
'''),
    ("K fold sink 给消息挂时间", '''\t\tpushMessage: (message) => {
\t\t\tcurrent = Object.freeze([...current, freeze(message, true)]);
\t\t}
''', '''\t\tpushMessage: (message, time) => {
\t\t\tif (typeof time === "number" && Number.isFinite(time)) try {
\t\t\t\tObject.defineProperty(message, "__wireTime", {
\t\t\t\t\tvalue: time,
\t\t\t\t\tenumerable: false,
\t\t\t\t\tconfigurable: true
\t\t\t\t});
\t\t\t} catch {}
\t\t\tcurrent = Object.freeze([...current, freeze(message, true)]);
\t\t}
'''),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default=BUNDLE)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()

    with open(a.target, encoding="utf-8") as f:
        src = f.read()

    for name, old, new in PATCHES:
        applied = src.count(new)
        if applied >= 1:
            print(f"SKIP {name}: already applied")
            continue
        n = src.count(old)
        if n != 1:
            print(f"FAIL {name}: anchor count = {n} (expected 1)")
            return 1
        if a.check:
            print(f"OK   {name}: anchor unique")
            continue
        src = src.replace(old, new, 1)

    if a.check:
        return 0

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = f"{a.target}.bak-{stamp}-before-compaction-timestamps"
    shutil.copy2(a.target, backup)
    tmp = a.target + ".tmp-patch.js"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(src)
    r = subprocess.run(["node", "--check", tmp], capture_output=True, text=True)
    if r.returncode != 0:
        os.remove(tmp)
        print("FAIL node --check:\n" + (r.stderr or r.stdout))
        return 2
    os.replace(tmp, a.target)
    print(f"PATCHED {a.target}\nbackup  {backup}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
