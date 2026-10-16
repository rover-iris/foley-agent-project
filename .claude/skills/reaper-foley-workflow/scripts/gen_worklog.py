# -*- coding: utf-8 -*-
"""gen_worklog.py —— 工时日志成账器（提速实施案 P0-1）。

扫任务文件夹已知产物 mtime 锚定各环节结束时间；官方耗时从 usage.jsonl 合并
（主对话每收到子 agent 完成通知即追加一行，见下）。只记实测不编数：
mtime 锚不到的字段留「—」，usage 没有的环节官方耗时留「—」。

用法：
  python gen_worklog.py --task-dir <任务文件夹> [--out 工时日志.md]
usage.jsonl（放 cues\，每行一条）：
  {"stage": "逐秒分析", "ep": "1", "duration_ms": 1140826, "tokens": 124012, "tool_uses": 116}

产物映射（mtime 锚点）：
  reports/batch_manifest.json → 开工建场
  reports/ep<N>_逐秒分析报告_*.md → 逐秒分析 epN
  cues/ep<N>_交班报告_*.md → 施工 epN（交班时刻）
  sessions/*工程搭建后*.rpp → 工程搭建
  cues/bgm/*.mp3 → BGM 生成（逐集）
  cues/配乐线_*.md → 配乐线交付
  cues/*整改报告*.md → 整改
  sessions/<工程>.rpp → 最近保存时刻
"""
import argparse
import glob
import json
import os
import re
import sys


def _ep_of(path, prefix="ep"):
    """从文件名提取集号（ep 后连续数字），取不到返回 ''。"""
    m = re.search(prefix + r"(\d+)", os.path.basename(path))
    return m.group(1) if m else ""


def _mtime(p):
    import datetime
    return datetime.datetime.fromtimestamp(os.path.getmtime(p)).strftime("%H:%M:%S")


def scan(td):
    rows = []

    def add(stage, ep, pattern, note="", multi=False):
        hits = sorted(glob.glob(os.path.join(td, pattern)), key=os.path.getmtime)
        if not hits:
            return
        if multi:
            for h in hits:
                rows.append({"stage": stage, "ep": os.path.splitext(os.path.basename(h))[0],
                             "end": _mtime(h), "note": note, "src": os.path.basename(h)})
        else:
            rows.append({"stage": stage, "ep": ep, "end": _mtime(hits[-1]),
                         "note": note, "src": os.path.basename(hits[-1])})

    add("开工建场", "全批", "reports/batch_manifest.json")
    add("工程搭建", "全批", "sessions/*工程搭建后*.rpp")
    for ep in sorted({_ep_of(p) for p in glob.glob(os.path.join(td, "reports", "ep*_逐秒分析报告_*.md"))} - {""}):
        add("逐秒分析", ep, f"reports/ep{ep}_逐秒分析报告_*.md")
    add("配乐线交付", "全批", "cues/配乐线_*.md")
    add("BGM 生成", "", "cues/bgm/*.mp3", multi=True)
    for ep in sorted({_ep_of(p) for p in glob.glob(os.path.join(td, "cues", "ep*_交班报告_*.md")) if "骨架" not in p} - {""}):
        add("施工（交班）", ep, f"cues/ep{ep}_交班报告_*.md")
    add("整改", "", "cues/*整改报告*.md", multi=True)
    add("BGM 贴入", "全批", "cues/BGM贴入*.py")
    return rows


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-dir", required=True)
    ap.add_argument("--out")
    a = ap.parse_args()
    td = os.path.abspath(a.task_dir)

    usage = {}
    uf = os.path.join(td, "cues", "usage.jsonl")
    if os.path.exists(uf):
        for line in open(uf, encoding="utf-8"):
            line = line.strip()
            if line:
                u = json.loads(line)
                usage[(u.get("stage"), str(u.get("ep", "")))] = u

    rows = scan(td)
    L = ["# 工时日志（gen_worklog.py 成账，mtime 锚点 + usage.jsonl 官方耗时）", "",
         "| 环节 | 集 | 结束(mtime) | 官方耗时 | tokens | 来源产物 |", "|---|---|---|---|---|---|"]
    for r in rows:
        u = usage.get((r["stage"].replace("（交班）", ""), str(r["ep"]))) or \
            usage.get((r["stage"], str(r["ep"])))
        dur = f"{u['duration_ms']/60000:.1f} 分钟" if u and u.get("duration_ms") else "—"
        tok = str(u.get("tokens", "—")) if u else "—"
        L.append(f"| {r['stage']} | {r['ep']} | {r['end']} | {dur} | {tok} | `{r['src']}` |")
    L.append("")
    md = "\n".join(L)
    out = a.out or os.path.join(td, "cues", "工时日志_成账.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(md)
    print("RESULT " + json.dumps({"status": "pass", "rows": len(rows), "out": out},
                                 ensure_ascii=False))


if __name__ == "__main__":
    main()
