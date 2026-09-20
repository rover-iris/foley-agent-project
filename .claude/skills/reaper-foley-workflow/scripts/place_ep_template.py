# -*- coding: utf-8 -*-
"""单集贴轨模板（抄本改 CUES 后运行）。幂等可安全重跑。

使用步骤：
  1. 复制本文件到项目工作区，命名 place_ep{NN}_v1.py；
  2. 填头部：EP / BASE(None=自动实测) / TRACK_RANGE / S 音源字典 / CUES 九元组表；
  3. 跑 --dry-run 先核对（路径解析 + 绝对时间表，不贴）；
  4. 正式运行；跑完看三重自检输出（对账/重叠/越界），全过才算完。

  python place_ep26_v1.py --config foley_project.json
  python place_ep26_v1.py --config foley_project.json --db 库.db --base 1606.99 --dry-run
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rfw_common import (connect_reaper, duration_map, enum_regions, load_config,
                        open_asset_db, pointer_ok, q, setup_stdout)

# ============ 每集必填区 ============
EP = 0            # 集号（Region 名需能匹配，如 int(name)==EP）
BASE = None       # None = 连接 REAPER 后按 EP 实测；或手填已知 base
TRACK_RANGE = (14, 29)   # 动作组子轨索引区间 [起, 止)，贴前必须实测轨序确认
NOTE = "报告已帧证据核验"  # 质检痕迹，写进脚本头

# 音源字典：别名 -> DB 真名（q() 解析真实路径，绝不手写绝对路径）
S_NAMES = {
    # 'FIRE': "CECK FIRE Whoosh Fireball",
    # 'ZHENFA': "法术 阵法 开启 【声羽拾光】",
}

# cue 九元组：(track_idx, t_local, source_key, len, label, fadeout, start_offset, vol, fadein)
# ⚠️ start_offset+len 不得超过源长；同源一集≤2次、邻近异窗轮换（见 references/retrieval.md）
CUES = [
    # (20, 13.2, 'FIRE', 2.0, "符纸自燃爆燃", 0.7, 0, 1.0, None),
]
# ===================================

POS_TOL = 0.15   # 防重：|位置差| < 此值 且 label 相同 → 视为已存在
ROUNDS = 2       # 对账不足自动补贴轮数


def find_base(RPR, ep):
    for e, pos, end in enum_regions(RPR):
        if e == ep:
            return pos, end
    raise SystemExit(f"工程里没找到集 {ep} 的 Region——先建 Region 或用 --base 手填实测值")


def put(RPR, p, ti, pos, path, ln, label, fade, off, vol, fadein):
    """贴一条。返回 True=新增成功 / None=已存在跳过 / str=失败原因。"""
    track = p.tracks[ti]
    # 条目名 = 源文件名（用户按包定位音源，2026-09-09 拍板）；label 仅用于日志对账
    stem = os.path.splitext(os.path.basename(path))[0]
    for it in track.items:
        if abs(it.position - pos) < POS_TOL and it.takes:
            name = ""
            try:
                # 坑：P_NAME 原始返回 [retval, take, 'P_NAME', 名字, setNewValue]，名字在 [3]
                name = RPR.GetSetMediaItemTakeInfo_String(it.takes[0].id, "P_NAME", "", False)[3]
                has_src = pointer_ok(RPR.GetMediaItemTake_Source(it.takes[0].id))
            except Exception:
                has_src = False
            if not has_src:
                RPR.DeleteTrackMediaItem(track.id, it.id)  # 空块删掉重贴
            elif name == stem:
                return None
    item = RPR.AddMediaItemToTrack(track.id)
    take = RPR.AddTakeToMediaItem(item)
    src = RPR.PCM_Source_CreateFromFile(path)
    if not pointer_ok(src):
        return "src失败"
    RPR.SetMediaItemTake_Source(take, src)
    RPR.SetMediaItemInfo_Value(item, "D_POSITION", pos)
    RPR.SetMediaItemInfo_Value(item, "D_LENGTH", ln)
    if off:                                   # 🔴 引擎级坑：不写这行取窗不生效
        RPR.SetMediaItemTakeInfo_Value(take, "D_STARTOFFS", off)
    if fade:
        RPR.SetMediaItemInfo_Value(item, "D_FADEOUTLEN", fade)
    if fadein:
        RPR.SetMediaItemInfo_Value(item, "D_FADEINLEN", fadein)
    # 2026-09-11 制作人口径：音效文件一律用默认音量，不再做任何增益调整。
    # cue 九元组中的 vol 字段保留仅为兼容旧 CUES，落地时强制 1.0。
    RPR.SetMediaItemInfo_Value(item, "D_VOL", 1.0)
    RPR.GetSetMediaItemTakeInfo_String(take, "P_NAME", stem, True)  # 条目名=源文件名，见 put() 头注释
    # 立即回读验证 source 没丢（交替性丢失是已知现象）
    if not pointer_ok(RPR.GetMediaItemTake_Source(take)):
        RPR.DeleteTrackMediaItem(track.id, item)
        return "source丢失"
    return True


def self_check(RPR, p, base, base_end, placed, durs):
    """三重自检。返回违规数。"""
    lo, hi = TRACK_RANGE
    bad = 0
    # 1. 区间对账（别信 placed 计数器）
    items = []
    for ti in range(lo, hi):
        t = p.tracks[ti]
        for it in sorted(t.items, key=lambda x: x.position):
            if base - 0.01 <= it.position < (base_end or it.position + 1):
                items.append((ti, it))
    print(f"\n=== 对账：Region 内 item {len(items)} 条 / CUES {len(CUES)} 条 ===")
    if len(items) != len(CUES):
        print("⚠️ 数量不符！"); bad += 1
    for ti, it in items:
        try:
            fn = os.path.basename(it.takes[0].source.filename)
        except Exception:
            fn = "!!无源空块!!"; bad += 1
        print(f"  [{ti}] {it.position - base:6.2f}s +{it.length:.2f}  {fn[:46]}")
    # 2. 重叠检测
    print("\n=== 重叠检测 ===")
    for ti in range(lo, hi):
        seq = sorted((it for t2, it in items if t2 == ti), key=lambda x: x.position)
        for a, b in zip(seq, seq[1:]):
            if a.position + a.length > b.position + 0.05:
                print(f"⚠️ [{ti}] {a.position:.2f}s 与 {b.position:.2f}s 重叠"); bad += 1
    # 3. 越界审计 off+len ≤ 源长
    print("\n=== 越界审计 ===")
    for ti, it in items:
        try:
            fn = it.takes[0].source.filename
            off = RPR.GetMediaItemTakeInfo_Value(it.takes[0].id, "D_STARTOFFS")
        except Exception:
            continue
        d = durs.get(os.path.basename(fn).lower())
        if d and off + it.length > d + 0.05:
            print(f"⚠️ [{ti}] off={off:.2f}+len={it.length:.2f} > 源长 {d:.2f}  {os.path.basename(fn)[:40]}")
            bad += 1
    print("\n三重自检全过 ✓" if bad == 0 else f"\n⚠️ {bad} 处违规，修复后重跑")
    return bad


def main():
    setup_stdout()
    global BASE, TRACK_RANGE
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", help="foley_project.json")
    ap.add_argument("--db", help="音源库 DB（缺省取 config.paths.asset_db）")
    ap.add_argument("--base", type=float, help="手填实测 base（缺省连 REAPER 实测）")
    ap.add_argument("--tracks", help="如 14:29（缺省取 config.tracks.action_children）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--dry-run", action="store_true", help="只打印解析结果，不贴")
    a = ap.parse_args()

    db_path = a.db or (load_config(a.config)["paths"].get("asset_db") if a.config else "")
    db = open_asset_db(db_path)
    S = {alias: q(db, name) for alias, name in S_NAMES.items()}
    durs = duration_map(db)

    # 解析 cue → 绝对时间表
    rb = RPR = p = None
    base, base_end = a.base, None
    if base is None:  # 坑：第一集 base=0.0 是合法值，不能走真值判断（否则误入 RPC 找 Region）
        rb, RPR, p = connect_reaper(a.host)
        base, base_end = find_base(RPR, EP)
    if a.tracks:
        s, e = a.tracks.split(":")
        TRACK_RANGE = (int(s), int(e))
    elif a.config:
        cfg = load_config(a.config)
        tr = cfg.get("tracks", {}).get("action_children")
        if tr:
            TRACK_RANGE = (int(tr[0]), int(tr[1]))

    print(f"=== EP{EP} 贴轨{'(dry-run)' if a.dry_run else ''}  base={base}  轨{TRACK_RANGE}  {NOTE} ===")
    plan = []
    for cue in CUES:
        ti, t_local, skey, ln, label, fade, off, vol, fi = cue
        plan.append((ti, base + t_local, S[skey], ln, label, fade, off, vol, fi, cue))
        d = durs.get(os.path.basename(S[skey]).lower(), 0)
        flag = "⚠️越界" if d and off + ln > d + 0.05 else ""
        print(f"  [{ti}] @{base + t_local:9.3f}s +{ln}s  {label}  {os.path.basename(S[skey])[:34]} {flag}")
    if a.dry_run:
        return

    if p is None:
        rb, RPR, p = connect_reaper(a.host)

    # 两轮补贴（防重保证第二轮只补缺的）
    todo = plan
    for rnd in range(1, ROUNDS + 1):
        todo2, ok_n = [], 0
        for ti, pos, path, ln, label, fade, off, vol, fi, cue in todo:
            r = put(RPR, p, ti, pos, path, ln, label, fade, off, vol, fi)
            if r is True:
                ok_n += 1
            elif r is not None:
                todo2.append((ti, pos, path, ln, label, fade, off, vol, fi, cue))
        print(f"第{rnd}轮：新增 {ok_n}，待补 {len(todo2)}")
        todo = todo2
        if not todo:
            break
        time.sleep(0.6)
    if todo:
        print(f"❌ 两轮后仍失败 {len(todo)} 条：{[c[9][4] for c in todo]}，人工介入")

    bad = self_check(RPR, p, base, base_end, len(CUES), durs)
    sys.exit(1 if (bad or todo) else 0)


if __name__ == "__main__":
    main()
