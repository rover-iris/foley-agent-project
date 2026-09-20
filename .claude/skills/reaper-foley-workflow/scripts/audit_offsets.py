# -*- coding: utf-8 -*-
"""全工程 off+len 越界审计 + 空块扫描（可续跑：重复运行安全）。

逐条检查动作组子轨上的 item：start_offset + length 是否超过源文件时长
（超过 = 尾部读到了别的段/静音），并顺带扫描无源空块。

用法：
  python audit_offsets.py --config foley_project.json
  python audit_offsets.py --db 库.db --tracks 14:29 [--ep 7 --base 123.4]
  python audit_offsets.py --db 库.db --all-tracks
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rfw_common import (connect_reaper, duration_map, enum_regions, load_config,
                        open_asset_db, setup_stdout, track_range)


def main():
    setup_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--config")
    ap.add_argument("--db", help="音源库 DB（缺省取 config.paths.asset_db）")
    ap.add_argument("--tracks", help="如 14:29；缺省取 config；--all-tracks 时忽略")
    ap.add_argument("--all-tracks", action="store_true")
    ap.add_argument("--ep", type=int, help="只审某集 Region 区间")
    ap.add_argument("--base", type=float, help="配合 --ep 手填 base")
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()

    cfg = load_config(a.config) if a.config else None
    db_path = a.db or (cfg and cfg["paths"].get("asset_db")) or ""
    db = open_asset_db(db_path)
    durs = duration_map(db)
    db.close()

    rb, RPR, p = connect_reaper(a.host)
    if a.all_tracks:
        lo, hi = 0, len(p.tracks)
    elif a.tracks:
        lo, hi = (int(x) for x in a.tracks.split(":"))
    else:
        lo, hi = track_range(cfg)

    lo_s = hi_s = None
    if a.ep:
        for e, pos, end in enum_regions(RPR):
            if e == a.ep:
                lo_s, hi_s = pos - 0.01, end
                break
        if lo_s is None and a.base:
            lo_s, hi_s = a.base - 0.01, None

    print(f"=== 越界审计：轨 {lo}~{hi - 1}"
          f"{'，区间 ' + str((lo_s, hi_s)) if lo_s is not None else '，全时间线'} ===")
    viol = empty = checked = 0
    for ti in range(lo, hi):
        t = p.tracks[ti]
        for it in t.items:
            if lo_s is not None and not (lo_s <= it.position < (hi_s or it.position + 1)):
                continue
            try:
                fn = it.takes[0].source.filename
            except Exception:
                print(f"⚠️ 空块 [{ti}] @ {it.position:.2f}s（无源，删掉重贴）")
                empty += 1
                continue
            off = RPR.GetMediaItemTakeInfo_Value(it.takes[0].id, "D_STARTOFFS")
            d = durs.get(os.path.basename(fn).lower())
            checked += 1
            if d and off + it.length > d + 0.05:
                print(f"⚠️ 越界 [{ti}] @ {it.position:.2f}s off={off:.2f}+len={it.length:.2f}"
                      f" > 源长 {d:.2f}  {os.path.basename(fn)[:44]}")
                viol += 1
    print(f"\n检查 {checked} 条：越界 {viol}，空块 {empty}")
    print("全部在界内 ✓" if viol == 0 and empty == 0 else "⚠️ 存在问题，逐条修复后重跑本脚本复核")
    sys.exit(1 if (viol or empty) else 0)


if __name__ == "__main__":
    main()
