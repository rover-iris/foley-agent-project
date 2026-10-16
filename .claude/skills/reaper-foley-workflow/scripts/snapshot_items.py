# -*- coding: utf-8 -*-
"""snapshot_items.py —— REAPER 全工程条目快照（提速实施案 P1-1 步间快照门）。

连接 REAPER 枚举全部轨 item，输出规范化 hash + 条数 + 明细 JSON。
步间快照门用法：施工各步前后各取一次，由 run_ep_pipeline.py 比对——
步间出现非本步写入的条目差异（幽灵条目，1013测试 轨26 案）即停机。

用法：
  python snapshot_items.py [--host 127.0.0.1] [--out snap.json]
末行 RESULT {"status","count","hash","out"}；exit 0=成功，1=连接失败。
"""
import argparse
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rfw_common import connect_reaper, setup_stdout


def snapshot(p, RPR):
    """返回规范化条目行列表（track|pos|len|pname，排序）。"""
    lines = []
    for ti, t in enumerate(p.tracks):
        for it in t.items:
            tk = RPR.GetActiveTake(it.id)
            name = RPR.GetTakeName(tk) if tk else ""
            lines.append("%d|%.3f|%.3f|%s" % (ti, it.position, it.length, name or ""))
    return sorted(lines)


def digest(lines):
    return hashlib.sha1("\n".join(lines).encode("utf-8")).hexdigest()


def main():
    setup_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--out", help="明细 JSON 输出路径")
    a = ap.parse_args()
    try:
        rb, RPR, p = connect_reaper(a.host)
        lines = snapshot(p, RPR)
    except Exception as e:
        print("RESULT " + json.dumps({"status": "fail", "error": str(e)}, ensure_ascii=False))
        sys.exit(1)
    h = digest(lines)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump({"count": len(lines), "hash": h, "lines": lines}, f,
                      ensure_ascii=False, indent=1)
    print("RESULT " + json.dumps({"status": "pass", "count": len(lines), "hash": h,
                                  "out": a.out or ""}, ensure_ascii=False))


if __name__ == "__main__":
    main()
