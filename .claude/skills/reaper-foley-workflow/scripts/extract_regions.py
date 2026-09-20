# -*- coding: utf-8 -*-
"""离线提取 REAPER 工程 RPP 的全部 Region 基准表（无需启动 REAPER）。

RPP 是纯文本，Region 成对出现：
  MARKER <id> <pos> <名> ... R {GUID} 0   ← Region 起点（带 R 标志）
  MARKER <id> <end> "" 1                  ← Region 终点

用法：
  python extract_regions.py --rpp 工程.RPP --out region_bases.csv
  python extract_regions.py --rpp 工程.RPP --json      # 打到 stdout 不落盘
"""
import argparse
import csv
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rfw_common import setup_stdout

MARKER_RE = re.compile(r"^\s*MARKER (\d+) ([\d.]+) (\"[^\"]*\"|\S+) (.+)$", re.M)


def parse_rpp(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        txt = f.read()
    starts, ends, names = {}, {}, {}
    for m in MARKER_RE.finditer(txt):
        mid, pos, name, rest = int(m.group(1)), float(m.group(2)), m.group(3), m.group(4)
        if re.search(r"\bR \{", rest):
            starts[mid] = pos
            # 坑：MARKER 行首数字是内部 idx（删建过 Region 会漂移），集号以 Region 名字为准
            names[mid] = name.strip('"')
        elif name == '""':
            ends[mid] = pos
    rows = []
    for mid in sorted(set(starts) | set(ends)):
        s, e = starts.get(mid), ends.get(mid)
        ep = int(names[mid]) if names.get(mid, "").isdigit() else mid
        rows.append({
            "集数": ep,
            "marker_id": mid,
            "base_s": s,
            "end_s": e,
            "dur_s": round(e - s, 3) if (s is not None and e is not None) else None,
        })
    return rows


def main():
    setup_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--rpp", required=True, help="REAPER 工程 .RPP 路径")
    ap.add_argument("--out", help="输出 CSV（utf-8-sig，Excel 可开）；不给则只打印")
    ap.add_argument("--json", action="store_true", help="以 JSON 打印")
    a = ap.parse_args()

    rows = parse_rpp(a.rpp)
    assert rows, "未解析到 Region——确认这是含 Region 的工程文件"
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    else:
        print(f"{'集#':>4} {'base(s)':>10} {'end(s)':>10} {'时长(s)':>9}")
        for r in rows:
            print(f"{r['集数']:>4} {r['base_s']:>10.3f} {r['end_s']:>10.3f} {r['dur_s']:>9.3f}")
        print(f"共 {len(rows)} 个 Region，总时长 {rows[-1]['end_s']:.3f}s")
    if a.out:
        with open(a.out, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["集数", "region_start_base_s", "region_end_s", "时长_s"])
            for r in rows:
                w.writerow([r["集数"], r["base_s"], r["end_s"], r["dur_s"]])
        print(f"\n已写入 {os.path.abspath(a.out)}（归档到项目侧，并注册进 foley_project.json 的 region_csv）")


if __name__ == "__main__":
    main()
