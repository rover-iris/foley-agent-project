# -*- coding: utf-8 -*-
"""新建剧集音效工程：从模板复制 + 批量建 Region（每集一个，首尾相接）。

plan（离线，无需 REAPER）：
  python new_project.py plan --template 模板.RPP --out 新工程.RPP \
      --videos-dir 各集视频目录          # 或 --durations 92.16,110.08,...
  → 复制工程 + ffprobe 读时长（文件名里的数字=集号）→ 生成 region_plan.csv

apply（在线，REAPER 已打开新工程）：
  python new_project.py apply --project 新工程.RPP --plan region_plan.csv \
      [--videos-dir 目录] [--host 127.0.0.1]
  → 逐集 AddProjectMarker 建 Region → 重新枚举实测 base 打表
  → 可选把每集视频贴到视频轨(track 0)对应区间做参考画面
"""
import argparse
import csv
import os
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rfw_common import (add_project_marker_region, connect_reaper, enum_regions,
                        pointer_ok, setup_stdout)

EP_NUM_RE = re.compile(r"(\d+)")


def probe_duration(video):
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", video],
        capture_output=True, text=True, timeout=60)
    import json
    return float(json.loads(out.stdout)["format"]["duration"])


def collect_episodes(a):
    """→ [(集号, 时长, 视频路径|None)] 按 ep 排序。"""
    eps = {}
    if a.videos_dir:
        for fn in sorted(os.listdir(a.videos_dir)):
            if not fn.lower().endswith((".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm")):
                continue
            m = EP_NUM_RE.search(fn)
            if not m:
                print(f"⚠️ 跳过（文件名无集号数字）: {fn}")
                continue
            ep = int(m.group(1))
            fp = os.path.join(a.videos_dir, fn)
            eps[ep] = (probe_duration(fp), fp)
    if a.durations:
        for i, d in enumerate(a.durations.split(","), 1):
            d = float(d)
            if i not in eps:
                eps[i] = (d, None)
    assert eps, "没有集时长来源（--videos-dir 或 --durations）"
    return [(ep, d, fp) for ep, (d, fp) in sorted(eps.items())]


def write_plan(rows, path):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["集数", "预计base_s", "时长_s", "视频"])
        pos = 0.0
        for ep, dur, fp in rows:
            w.writerow([ep, round(pos, 3), round(dur, 3), fp or ""])
            pos += dur
    print(f"计划已写入 {path}")


def cmd_plan(a):
    setup_stdout()
    assert os.path.exists(a.template), f"模板不存在: {a.template}"
    if os.path.exists(a.out) and not a.force:
        raise SystemExit(f"{a.out} 已存在；确认覆盖加 --force")
    rows = collect_episodes(a)
    shutil.copyfile(a.template, a.out)
    print(f"模板已复制: {a.template} → {a.out}")
    plan_csv = os.path.splitext(a.out)[0] + "_region_plan.csv"
    write_plan(rows, plan_csv)
    print("下一步：用 REAPER 打开新工程，然后跑 apply 建 Region")


def import_video(RPR, p, ep, base, dur, video):
    """把视频 item 贴到视频轨（track 0）对应区间。返回 True/False。"""
    trk = p.tracks[0].id
    src = RPR.PCM_Source_CreateFromFile(video)
    if not pointer_ok(src):
        print(f"⚠️ 集{ep} 视频 source 创建失败: {video}")
        return False
    item = RPR.AddMediaItemToTrack(trk)
    take = RPR.AddTakeToMediaItem(item)
    RPR.SetMediaItemTake_Source(take, src)
    RPR.SetMediaItemInfo_Value(item, "D_POSITION", base)
    RPR.SetMediaItemInfo_Value(item, "D_LENGTH", dur)
    RPR.GetSetMediaItemTakeInfo_String(take, "P_NAME", f"EP{ep:02d}", True)
    ok = pointer_ok(RPR.GetMediaItemTake_Source(take))
    if not ok:
        RPR.DeleteTrackMediaItem(trk, item)
    return ok


def cmd_apply(a):
    setup_stdout()
    assert os.path.exists(a.plan), f"plan 不存在: {a.plan}"
    rows = []
    with open(a.plan, encoding="utf-8-sig") as f:
        for r in list(csv.DictReader(f))[0:]:
            rows.append((int(r["集数"]), float(r["预计base_s"]), float(r["时长_s"]), r["视频"] or None))
    assert rows, "plan 为空"

    rb, RPR, p = connect_reaper(a.host)
    existing = enum_regions(RPR)
    if existing:
        raise SystemExit(f"工程已有 {len(existing)} 个 Region。为防误删历史 cue，本脚本不自动清 Region；"
                         "请人工确认后清空 Region（或另存新工程）再跑。")

    # 建 Region
    for ep, base, dur, _ in rows:
        add_project_marker_region(RPR, base, base + dur, str(ep))
    time.sleep(0.5)

    # 可选：贴参考视频
    if a.videos_dir:
        ok, bad = 0, 0
        for ep, base, dur, _ in rows:
            fp = None
            for fn in os.listdir(a.videos_dir):
                m = EP_NUM_RE.search(fn)
                if m and int(m.group(1)) == ep and fn.lower().endswith(
                        (".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm")):
                    fp = os.path.join(a.videos_dir, fn)
                    break
            if fp and import_video(RPR, p, ep, base, dur, fp):
                ok += 1
            else:
                bad += 1
        print(f"视频参考画面: 成功 {ok}，失败/缺失 {bad}")

    # 实测打表
    measured = enum_regions(RPR)
    print("\n== 实测 Region 基准表 ==")
    print(f"{'集#':>4} {'base(s)':>10} {'end(s)':>10} {'时长(s)':>9}")
    for ep, base, end in measured:
        print(f"{ep if ep is not None else '?':>4} {base:>10.3f} {end:>10.3f} {end - base:>9.3f}")
    print(f"\n共 {len(measured)} 个 Region。")
    print("收尾：保存工程（Main_SaveProjectEx），并用 extract_regions.py 离线复核落 CSV 存档。")


def main():
    setup_stdout()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan", help="离线：复制模板 + 生成 Region 计划")
    p.add_argument("--template", required=True)
    p.add_argument("--out", required=True, help="新工程 .RPP 路径")
    p.add_argument("--videos-dir", help="各集视频目录（文件名数字=集号）")
    p.add_argument("--durations", help="逗号分隔的每集秒数，如 92.16,110.08")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_plan)

    p = sub.add_parser("apply", help="在线：REAPER 内建 Region")
    p.add_argument("--project", help="新工程 .RPP（REAPER 需已打开它）")
    p.add_argument("--plan", required=True, help="plan 生成的 region_plan.csv")
    p.add_argument("--videos-dir", help="可选：贴参考视频到视频轨")
    p.add_argument("--host", default="127.0.0.1")
    p.set_defaults(fn=cmd_apply)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
