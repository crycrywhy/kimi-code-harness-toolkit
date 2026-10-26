#!/usr/bin/env python3
"""compaction-timestamps 补丁 P2：用 WeakMap 兜住被冻结消息，并加落盘诊断。

背景：P1（apply.py）在 2026-10-18 21:20 的压缩里 F 段生效（摘要头有
"Compaction performed at ... UTC+8"），但 D 段没有任何一条 [时间] 前缀 ——
说明 stampCompactionMessage 里 `__wireTime` 取不到。最可疑的是消息对象
已被冻结/复制，Object.defineProperty 静默失败或被 try/catch 吞掉。

P2 做法：
  1) 增加模块级 WeakMap（对象身份即可，不受冻结影响），mark/read 两函数；
  2) A/K/B 三处改走 __markWireTime / __readWireTime；属性仍尽量写（兼容旧值）；
  3) stampCompactionMessage 改用 __readWireTime；
  4) 压缩时把 kept/real/miss 计数追加到
     ~/.kimi-code/cache/compaction_stamp.log，下次压缩即可判定。
"""
import argparse
import os
import shutil
import subprocess
import sys
import re
import time

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
LOG = os.path.expanduser("~/.kimi-code/cache/compaction_stamp.log")

HELPERS = '''const __wireTimes = /* @__PURE__ */ new WeakMap();
function __markWireTime(message, time) {
	if (typeof time !== "number" || !Number.isFinite(time)) return;
	try {
		__wireTimes.set(message, time);
	} catch {}
	try {
		if (!Object.isFrozen(message) && message.__wireTime === void 0) Object.defineProperty(message, "__wireTime", {
			value: time,
			enumerable: false,
			configurable: true
		});
	} catch {}
}
function __readWireTime(message) {
	if (message && message.__wireTime !== void 0) return message.__wireTime;
	try {
		return __wireTimes.get(message);
	} catch {
		return void 0;
	}
}
function __logCompactionStamp(line) {
	try {
		process.getBuiltinModule("node:fs").appendFileSync("''' + LOG + '''", `${new Date().toISOString()} ${line}\\n`);
	} catch {}
}
'''

A_OLD = '''\tpushHistory(messages, time) {
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
A_NEW = '''\tpushHistory(messages, time) {
\t\tfor (const message of messages) {
\t\t\t__markWireTime(message, time);
'''

K_OLD = '''\t\tpushMessage: (message, time) => {
\t\t\tif (typeof time === "number" && Number.isFinite(time)) try {
\t\t\t\tObject.defineProperty(message, "__wireTime", {
\t\t\t\t\tvalue: time,
\t\t\t\t\tenumerable: false,
\t\t\t\t\tconfigurable: true
\t\t\t\t});
\t\t\t} catch {}
'''
K_NEW = '''\t\tpushMessage: (message, time) => {
\t\t\tif (message && message.role === "user" && typeof time !== "number") __logCompactionStamp(`sink-user-no-time id=${message.id || "?"}`);
\t\t\t__markWireTime(message, time);
'''

B_OLD = '''\tif (message.__wireTime !== void 0) try {
\t\tObject.defineProperty(replaced, "__wireTime", {
\t\t\tvalue: message.__wireTime,
\t\t\tenumerable: false,
\t\t\tconfigurable: true
\t\t});
\t} catch {}
'''
B_NEW = '''\t__markWireTime(replaced, __readWireTime(message));
'''

T_OLD = '''\tconst t = message.__wireTime;
'''
T_NEW = '''\tconst t = __readWireTime(message);
'''

D_OLD = '''\tconst keptMessages = rawKeptMessages.map(stampCompactionMessage);
'''
D_NEW = '''\tconst keptMessages = rawKeptMessages.map(stampCompactionMessage);
\tif (typeof __logCompactionStamp === "function") {
\t\tconst __real = rawKeptMessages.filter((m) => isRealUserInput$1(m));
\t\tconst __miss = __real.filter((m) => typeof __readWireTime(m) !== "number");
\t\t__logCompactionStamp(`compaction kept=${keptMessages.length} real=${__real.length} miss=${__miss.length} sample=${__miss.slice(0, 3).map((m) => m.id || Object.keys(m).join(",")).join("|")}`);
\t}
'''

PATCHES = [
    ("P2-0 WeakMap 辅助", "function formatWireTimeUtc8(ms) {", HELPERS + "function formatWireTimeUtc8(ms) {"),
    ("P2-A pushHistory 走 WeakMap", A_OLD, A_NEW),
    ("P2-K fold sink 走 WeakMap", K_OLD, K_NEW),
    ("P2-B replaceMessageText 复制时间", B_OLD, B_NEW),
    ("P2-T 读取走 WeakMap", T_OLD, T_NEW),
    ("P2-D 压缩落盘诊断", D_OLD, D_NEW),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default=BUNDLE)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()

    with open(a.target, encoding="utf-8") as f:
        src = f.read()

    for name, old, new in PATCHES:
        if src.count(new) >= 1:
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
    backup = f"{a.target}.bak-{stamp}-before-compaction-timestamps-p2"
    shutil.copy2(a.target, backup)
    tmp = a.target + ".tmp-patch.js"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(src)
    r = subprocess.run(["node", "--check", tmp], capture_output=True, text=True)
    if r.returncode != 0:
        print("FAIL node --check:\n" + (r.stderr or r.stdout))
        return 2
    import os
    os.replace(tmp, a.target)
    print(f"PATCHED {a.target}\nbackup  {backup}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
