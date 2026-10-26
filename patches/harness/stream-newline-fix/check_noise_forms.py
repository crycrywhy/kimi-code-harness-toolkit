#!/usr/bin/env python3
"""第 2 个监测指标：**分片头以单 \\n 或 \\n\\n 开头的分片占比**。

为什么需要它：原来的"换行密度"只对"多出来的换行"敏感，对"分片头贴了东西但换行总数没变多"
的形态（例如空格开头，或换行被劈到分片头/尾两端）会**失明**；而且密度要等整段跑完才有意义。
"分片头噪声前缀占比"是**尽早可见**的指标：只要一小段流里这个占比 >0 就说明上游又在分片头贴东西了。

用法：
  python3 check_noise_forms.py                  # 扫 /tmp/zen_tap/*.sse（本机透传中继落原始字节的目录）
  python3 check_noise_forms.py <file.sse> ...   # 指定文件
  python3 check_noise_forms.py <目录>            # 扫该目录下的 *.sse

指标定义（每个通道分别算）：
  n          非空 content 分片数
  med        分片长度中位数
  lead\\n\\n    以 "\\n\\n" 开头的分片数
  lead\\n      以**单个** "\\n" 开头（后一个字符不是 \\n）的分片数
  lead┬Ā      以空格开头的分片数
  **指标**     (lead\\n\\n + lead\\n) / n         ← 第 2 个监测指标
  密度        100 * 换行数 / 字符数              ← 老指标（保留对照）
  判定        指标 ≥ 25% 或（med < 6 且 lead┬Ā ≥ 10%）⇒ 疑似噪声形态发作

实测基线（2026-10-14）：
  健康：指标 0–0.8%（`/tmp/steps.json` 的 10 个健康 step + sse healthy_0001）——健康流里几乎不出现"分片头换行"。
  形态① \\n\\n 噪声：dirty_0004 41.9%、steps 9/3 41.9%、7/6 52.2%。
  形态② 单 \\n 噪声：zen_tap 0654 40.4%、0652 27.3%（该流里 \\n\\n 占比 0）。
  说明：**空格开头不能当噪声指标**——reasoning 通道里 ` the` / ` strings` / ` data:` 之类合法 BPE 分词
  常态就有 20–50%（例如 sse healthy_0001 的 reasoning 通道 48/195 ≈ 25%），故 lead┬Ā 只作参考、不参与判定。
"""
import glob, json, os, sys

DATA_PREFIX = "data:"


def frags(path, channel="content"):
    """取某通道的非空分片序列（不做任何规整）。"""
    out = []
    for line in open(path, errors="replace"):
        s = line.strip()
        if not s.startswith(DATA_PREFIX):
            continue
        d = s[len(DATA_PREFIX):].strip()
        if d == "[DONE]":
            break
        try:
            o = json.loads(d)
        except Exception:
            continue
        delta = (o.get("choices") or [{}])[0].get("delta") or {}
        v = delta.get(channel)
        if v:
            out.append(v)
    return out


def stat(ds):
    n = len(ds)
    if not n:
        return None
    text = "".join(ds)
    lens = sorted(len(x) for x in ds)
    lead2 = sum(1 for x in ds if x.startswith("\n\n"))
    lead1 = sum(1 for x in ds if x[0] == "\n" and not x.startswith("\n\n"))
    leadsp = sum(1 for x in ds if x[0] == " ")
    return dict(n=n, med=lens[n // 2], lead2=lead2, lead1=lead1, leadsp=leadsp,
                metric=(lead1 + lead2) / n, dens=100 * text.count("\n") / max(len(text), 1),
                chars=len(text))


def verdict(s):
    """判定只看第 2 个指标（分片头单 \\n 或 \\n\\n 占比 ≥25%）。
    刻意**不**把"空格开头"计入判定——那是合法英文 BPE 分词的常态（见文件头）。"""
    return s["metric"] >= 0.25


def main():
    args = sys.argv[1:]
    files = []
    for a in args:
        if os.path.isdir(a):
            files += sorted(glob.glob(os.path.join(a, "*.sse")))
        else:
            files.append(a)
    if not files:
        files = sorted(glob.glob("/tmp/zen_tap/*.sse"))
        if not files:
            print("没有指定文件，且 /tmp/zen_tap/*.sse 为空（中继没在跑？）")
            return 1
        print("（未给参数，扫 /tmp/zen_tap/*.sse）\n")

    print("%-16s %-10s %5s %5s %7s %7s %8s %8s | %7s %7s  %s" %
          ("file", "chan", "n", "med", "lead\\n\\n", "lead\\n", "lead␠", "**指标**", "密度", "字数", "判定"))
    hit = 0
    for f in files:
        for ch in ("content", "reasoning_content"):
            ds = frags(f, ch)
            s = stat(ds)
            if not s:
                continue
            v = verdict(s)
            hit += 1 if v else 0
            print("%-16s %-10s %5d %5d %7d %7d %8d %7.1f%% | %6.2f %7d  %s" %
                  (os.path.basename(f), "content" if ch == "content" else "reason",
                   s["n"], s["med"], s["lead2"], s["lead1"], s["leadsp"],
                   100 * s["metric"], s["dens"], s["chars"],
                   "⚠ 疑似噪声" if v else "ok"))
    print("\n合计 {} 个通道样本（{} 个文件 × content/reasoning），其中 {} 个触发判定"
          "（第 2 个指标 ≥25%）。".format(len(files) * 2, len(files), hit))
    print("注：「空格开头」列只作参考，不参与判定——reasoning 通道里合法 BPE 分词常态就有 20–50%。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
