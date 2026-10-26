#!/usr/bin/env python3
"""就地**替换函数块**（把 bundle 里已嵌入的 stream-newline-fix 块换成 newline_fix.js + md_normalize.js 的当前内容）。

为什么不用 `apply.py`：`apply.py --revert` 会整文件回退到"它自己备份的那一刻"，
线上还叠着 `fast-session-restore` 等其它补丁，整文件回退会把它们一并抹掉。而**块替换**只动
`// === LOCAL PATCH: stream newline fix` … `// === END LOCAL PATCH: markdown normalize ===`
这两个标记之间的字节，块外一个字节不碰。

用法：
  python3 sync_block.py --check                                   # 只报现状（块是否存在、是否已是当前内容）
  python3 sync_block.py                                           # 直接替换
  python3 sync_block.py --dry-run                                 # 完整走一遍但不落盘

纪律（硬）：两个标记在 bundle 里必须各命中**恰好 1 次**，否则**中止、一字节都不写**；
替换后必须过 `.mjs` 真语法探针（`cp` 成 `.mjs` 再 `node --check`——Node 22 对首行含 import 的
`.js` 会**假通过**），不过则自动回滚。
"""
import argparse, hashlib, os, re, shutil, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
EXT_082 = os.path.expanduser(
    "~/.vscode-server/extensions/moonshot-ai.kimi-code-0.8.2-linux-x64/dist/extension.js")
EXT_075 = os.path.expanduser(
    "~/.vscode-server/extensions/moonshot-ai.kimi-code-0.7.5-linux-x64/dist/extension.js")
EXT = EXT_082 if os.path.exists(EXT_082) else EXT_075

NL_HELP = open(os.path.join(HERE, "newline_fix.js"), encoding="utf-8").read()
MD_HELP = open(os.path.join(HERE, "md_normalize.js"), encoding="utf-8").read()
HELP = NL_HELP.rstrip("\n") + "\n\n" + MD_HELP.strip() + "\n"

START = "// === LOCAL PATCH: stream newline fix"
END_MD = "// === END LOCAL PATCH: markdown normalize ==="
END_NL = "// === END LOCAL PATCH: stream newline fix ==="
BAK_SUFFIX = "-before-stream-newline-fix-v7p3p1block"


def md5(s):
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def node_check(path):
    """真语法闸门：先 cp 成 .mjs 再 node --check。"""
    with tempfile.TemporaryDirectory(prefix="nlblock-check-") as tmp:
        probe = os.path.join(tmp, "extension.mjs")
        shutil.copyfile(path, probe)
        r = subprocess.run(["node", "--check", probe], capture_output=True, text=True)
    print("node --check (.mjs probe):", "OK" if r.returncode == 0 else "FAILED\n" + r.stderr[:800])
    return r.returncode == 0


def locate(src):
    """返回 (i, j, 块文本)；标记命中数 ≠1 ⇒ 返回 None。"""
    if src.count(START) != 1:
        print("标记计数异常：START×%d（必须是 1）" % src.count(START))
        return None
    if END_MD in src:
        if src.count(END_MD) != 1:
            print("标记计数异常：END_MD×%d（必须是 1）" % src.count(END_MD))
            return None
        i = src.index(START)
        j = src.index(END_MD) + len(END_MD)
        return i, j, src[i:j]
    elif END_NL in src:
        if src.count(END_NL) != 1:
            print("标记计数异常：END_NL×%d（必须是 1）" % src.count(END_NL))
            return None
        i = src.index(START)
        j = src.index(END_NL) + len(END_NL)
        return i, j, src[i:j]
    else:
        print("未找到 END 标记")
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--expect-preimage", default=None,
                    help="校验当前块必须逐字节等于该文件（去尾换行）再动手")
    ap.add_argument("--file", default=EXT)
    a = ap.parse_args()

    if not os.path.exists(a.file):
        sys.exit("找不到 bundle: %s" % a.file)
    src = open(a.file, encoding="utf-8").read()
    loc = locate(src)
    if loc is None:
        return 2
    i, j, cur = loc
    new = HELP.rstrip("\n")
    print("bundle: %s\n  大小 %d 字节｜块位置 [%d, %d)｜块长 %d"
          % (a.file, len(src.encode("utf-8")), i, j, len(cur)))
    print("  当前块 md5 %s｜当前新 helper 块 md5 %s" % (md5(cur), md5(new)))
    same = (cur == new)
    print("  状态：", "已是当前内容（幂等，无需替换）" if same else "与新 helper 块不同 ⇒ 需要替换")
    if same:
        return 0
    if a.expect_preimage:
        exp = open(os.path.join(HERE, a.expect_preimage), encoding="utf-8").read().rstrip("\n")
        okp = (cur == exp)
        print("  前像校验（%s）：%s" % (a.expect_preimage, "一致" if okp else "**不一致，中止**"))
        if not okp:
            return 3
    if a.check or a.dry_run:
        if a.dry_run:
            out = src[:i] + new + src[j:]
            print("  dry-run：块外不变=%s｜新大小 %d 字节｜新 md5 %s"
                  % (out[:i] == src[:i] and out[i + len(new):] == src[j:],
                     len(out.encode("utf-8")), md5(out)))
        return 0

    bak = a.file + ".bak-" + time.strftime("%Y%m%d-%H%M%S") + BAK_SUFFIX
    shutil.copy2(a.file, bak)
    print("备份: %s" % bak)
    out = src[:i] + new + src[j:]
    outside_ok = (out[:i] == src[:i] and out[i + len(new):] == src[j:])
    print("块外逐字节不变:", outside_ok, "｜替换命中数: 1")
    if not outside_ok:
        sys.exit("块外字节被动到，中止（未写盘）")
    open(a.file, "w", encoding="utf-8").write(out)
    print("已写入。新 md5 %s｜大小 %d 字节" % (md5(out), len(out.encode("utf-8"))))
    if not node_check(a.file):
        shutil.copy2(bak, a.file)
        sys.exit("node --check 失败，已自动回滚")
    print("完成。**请重载 VS Code 窗口**生效（本脚本不重启 VS Code）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
