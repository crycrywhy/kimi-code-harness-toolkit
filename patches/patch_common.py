#!/usr/bin/env python3
"""patch_common.py —— patch_* 系列共用的「安全落盘」工具（2026-10-24 审查后新增）。

背景（审查报告 #1）：原版各 patch 脚本对目标文件（server.mjs / 3MB extension.js /
mcp.json）直接 open(path, "w") 截断重写，且语法校验要么没有、要么在覆盖之后才做
——写一半崩溃或替换引入语法错误都会留下坏文件（代理起不来 / 扩展损坏），备份只能
靠人工恢复。

本模块把落盘做成闭环：
  1. 锚点替换全部先在内存完成（任一锚点匹配数 != 1 立即中止，不写文件）；
  2. 写「同目录 + 以原扩展名结尾」的临时文件（node --check 按扩展名决定解析模式，
     所以临时文件必须以 .js / .mjs / .json 结尾）；
  3. 校验临时文件（JS/MJS 用 node --check；JSON 用 json.loads）；
  4. 校验失败：删临时文件，原文件一个字节未动（无需回滚）；
     校验成功：os.replace 原子上位（不存在半成品窗口）。

另含时间戳备份工具（同后缀只保留最近 keep 份，审查报告 #13：防 .bak-* 无限堆积）。
"""
import glob
import json
import os
import shutil
import subprocess
import time


def resolve_anchor_replacements(text, patches):
    """逐条做锚点替换；replacement 已在文本中 ⇒ skip；匹配数 != 1 ⇒ SystemExit。

    patches: [(name, anchor, replacement), ...]
    返回 (new_text, applied_names, skipped_names)。任何失败都不写盘。
    """
    applied, skipped = [], []
    for name, anchor, replacement in patches:
        if replacement in text:
            skipped.append(name)
            continue
        count = text.count(anchor)
        if count != 1:
            raise SystemExit(
                "FATAL: [%s] 锚点匹配 %d 次（应为 1）→ 中止，未写文件" % (name, count))
        text = text.replace(anchor, replacement, 1)
        applied.append(name)
    return text, applied, skipped


def node_check(path):
    """node --check 语法校验；返回 (ok, err)。"""
    try:
        rc = subprocess.run(["node", "--check", path],
                            capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, "无法运行 node --check：%s" % e
    if rc.returncode != 0:
        return False, (rc.stderr or "")[:2000]
    return True, ""


def json_check(path):
    try:
        with open(path, encoding="utf-8") as f:
            json.load(f)
    except Exception as e:  # noqa: BLE001
        return False, str(e)
    return True, ""


def tmp_sibling(path):
    """同目录、以原扩展名结尾的临时文件名。"""
    ext = os.path.splitext(path)[1] or ".tmp"
    return "%s.tmp%d%s" % (path, os.getpid(), ext)


def write_validated(path, text, check, label=""):
    """写临时文件 → check(tmp) → 通过才 os.replace。失败返回 False，原文件不动。"""
    tmp = tmp_sibling(path)
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        ok, err = check(tmp)
        if not ok:
            print("FATAL: %s 校验失败（%s 未改动）：%s"
                  % (label or os.path.basename(path), path, err))
            return False
        os.replace(tmp, path)
        return True
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def make_backup(path, suffix, keep=5):
    """时间戳备份（不覆盖已有），同后缀只保留最近 keep 份。返回备份路径。"""
    backup = "%s.bak-%s-%s" % (path, time.strftime("%Y%m%d-%H%M%S"), suffix)
    shutil.copy2(path, backup)
    prune_backups(path, suffix, keep)
    return backup


def prune_backups(path, suffix, keep=5):
    """删掉同后缀较旧的备份，只留最近 keep 份。

    按备份**文件名**排序（名字里的 %Y%m%d-%H%M%S 字典序即时间序），不用 mtime：
    同秒创建的文件 mtime 相同，排序不定，刚生成的备份可能被误删。"""
    olds = sorted(glob.glob("%s.bak-*-%s" % (path, suffix)), reverse=True)
    for old in olds[keep:]:
        try:
            os.remove(old)
        except OSError:
            pass
