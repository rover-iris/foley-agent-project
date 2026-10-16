# -*- coding: utf-8 -*-
"""一键自检：区间对账 + 空块扫描 + 同轨重叠 + 越界审计 + 父轨清查，一次跑完出总报告。

用法：
  python selfcheck_ep.py --ep 15v2 --rpp 工程.rpp --tracks 7:31 --expect 40 --config foley_project.json
  python selfcheck_ep.py --base 1606.99 --base-end 1750.0 --tracks 14:29 --expect 45 --db 库.db

注意：Region 名（集号）在线取不到（[R-13] 出参不回写），--ep 解析一律离线读 RPP（extract_regions.parse_rpp）。

出厂自检（任何一关不过 = FAIL）：
  1. 区间对账：Region 内 item 数 == --expect（不信任何计数器）+ 空块扫描（无 take/无 source）；
  2. 同轨重叠：相邻 item 时间窗不重叠（0.05s 容差）；
  3. 越界审计：每条 off+len ≤ DB 源长（basename 匹配 duration_map）；
  4. 父轨清查：编组轨（I_FOLDERDEPTH==1）item 数必须 = 0（铁律 17）。
末行 RESULT JSON 机器可读；exit 0=全过，1=有违规。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rfw_common import (connect_reaper, duration_map, load_config,
                        open_asset_db, pointer_ok, setup_stdout)

OVLAP_TOL = 0.05


def main():
    setup_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", help="foley_project.json")
    ap.add_argument("--db", help="音源库 DB")
    ap.add_argument("--ep", help="集号/Region 名（离线解析 RPP 查基准；需 --rpp 或 config paths.project_rpp）")
    ap.add_argument("--rpp", help="工程 .RPP 路径（--ep 离线解析用）")
    ap.add_argument("--base", type=float, help="实测 base（跳过 Region 查询）")
    ap.add_argument("--base-end", type=float)
    ap.add_argument("--tracks", required=True, help="动作组子轨区间，如 14:29")
    ap.add_argument("--expect", type=int, required=True, help="CUES 条数（对账基准）")
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()

    lo, hi = (int(x) for x in a.tracks.split(":"))
    db_path = a.db or (load_config(a.config)["paths"].get("asset_db") if a.config else "")
    durs = duration_map(open_asset_db(db_path))

    rb, RPR, p = connect_reaper(a.host)
    if a.base is not None:
        base, base_end = a.base, a.base_end
    elif a.ep is not None:
        from extract_regions import parse_rpp
        rpp = a.rpp or (load_config(a.config)["paths"].get("project_rpp") if a.config else "")
        if not rpp:
            print("❌ --ep 需要 --rpp 或 config 的 paths.project_rpp（离线解析）")
            sys.exit(1)
        hit = [r for r in parse_rpp(rpp)
               if str(a.ep) in (str(r.get("名称", "")), str(r["集数"]))]
        if not hit:
            print(f"❌ RPP 里没找到 Region「{a.ep}」")
            sys.exit(1)
        base, base_end = hit[0]["base_s"], hit[0]["end_s"]
    else:
        print("❌ --ep 与 --base 必须给一个")
        sys.exit(1)

    bad = []
    # 关 1：区间对账 + 空块扫描
    items = []
    for ti in range(lo, hi):
        for it in sorted(p.tracks[ti].items, key=lambda x: x.position):
            if base - 0.01 <= it.position < (base_end or it.position + 1):
                items.append((ti, it))
    print(f"=== 关1 对账：Region [{base:.3f}~{base_end}] 内 item {len(items)} / 预期 {a.expect} ===")
    if len(items) != a.expect:
        bad.append(f"对账不符：{len(items)} != {a.expect}")
    for ti, it in items:
        tk = RPR.GetActiveTake(it.id)
        src = tk and RPR.GetMediaItemTake_Source(tk)
        if not (tk and pointer_ok(src)):
            bad.append(f"空块：[{ti}] @{it.position:.2f}s")

    # 关 2：同轨重叠
    print("=== 关2 同轨重叠 ===")
    for ti in range(lo, hi):
        seq = [it for t2, it in items if t2 == ti]
        for x, y in zip(seq, seq[1:]):
            if x.position + x.length > y.position + OVLAP_TOL:
                bad.append(f"重叠：[{ti}] {x.position:.2f}s 与 {y.position:.2f}s")

    # 关 3：越界审计
    print("=== 关3 越界审计 ===")
    for ti, it in items:
        tk = RPR.GetActiveTake(it.id)
        if not tk:
            continue
        try:
            fn = it.takes[0].source.filename
            off = RPR.GetMediaItemTakeInfo_Value(tk, "D_STARTOFFS")
        except Exception:
            continue
        d = durs.get(os.path.basename(fn).lower())
        if d and off + it.length > d + OVLAP_TOL:
            bad.append(f"越界：[{ti}] off={off:.2f}+len={it.length:.2f} > 源长 {d:.2f} {os.path.basename(fn)[:40]}")

    # 关 4：父轨清查（铁律 17）
    print("=== 关4 父轨清查 ===")
    for ti, t in enumerate(p.tracks):
        if int(RPR.GetMediaTrackInfo_Value(t.id, "I_FOLDERDEPTH")) == 1 and len(t.items) > 0:
            bad.append(f"编组轨 [{ti}] 带 {len(t.items)} 个 item（必须=0）")

    print()
    for b in bad:
        print(f"  ❌ {b}")
    status = "fail" if bad else "pass"
    print("RESULT " + json.dumps({"status": status, "base": base, "base_end": base_end,
                                 "items": len(items), "expect": a.expect,
                                 "violations": len(bad)}, ensure_ascii=False))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
