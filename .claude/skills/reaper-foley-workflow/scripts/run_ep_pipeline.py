# -*- coding: utf-8 -*-
"""run_ep_pipeline.py —— 施工链一键总控（提速实施案 P1-1）。

编号化执行路径（每步落可验收产物，fail-stop，人从断点续）：
  ①备份 rpp → ②connect_reaper --launch → ③prepare_ep 预审 → ④place --dry-run
  → ⑤机器 diff 门（dry-run 绝对时间表 vs 数据文件：轨号/位置/长度逐条对账，差异>0 即停）
  → ⑥步间快照（施工前）→ ⑦正式贴轨 → ⑧步间快照门（终态完整性=CUES 全在场 + 新增无幽灵，
      幽灵条目即停——1013测试 轨26 案；幂等重跑不误拦）→ ⑨selfcheck_ep 出厂自检
  → ⑩verify_frames 帧比对（frames 集目录存在时强制，缺失记 skipped）→ ⑪保存（save_project 双证）
  → ⑫gen_handover / gen_acceptance 出骨架（判断节留手写，--check 归见习）。

用法：
  python run_ep_pipeline.py --data cues_ep3.py --ep 3 --task-dir <任务文件夹> \
      --rpp <工程.rpp> [--db 库.db | --config foley_project.json] [--date 2026-10-16]
BASE/END 读 <任务文件夹>/cues/offsets/ep<ep>.json（数值纪律：禁手抄）。
回退：总控整体异常 → 回退 SOP 逐步手工链（现役脚本未改）。
末行 RESULT {"status","ep","step",...}；exit 0=全通，1=fail-stop。
"""
import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def run(cmd, label):
    """跑子脚本，返回 (exit_code, stdout)。stdout 实时回显并留档。"""
    print(f"\n----- {label} -----")
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (p.stdout or "") + (("\n[stderr]\n" + p.stderr) if p.stderr and p.returncode else "")
    print(out[-4000:] if len(out) > 4000 else out)
    return p.returncode, out


def stop(step, reason, ep):
    print(f"\n❌ STOP@{step}：{reason}")
    print("现场保留；人从断点续（不从头重跑）。")
    print("RESULT " + json.dumps({"status": "fail", "ep": ep, "step": step,
                                 "reason": reason}, ensure_ascii=False))
    sys.exit(1)


def load_data(path):
    spec = importlib.util.spec_from_file_location("cues_data", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def parse_dryrun(stdout):
    """解析 place dry-run 输出行为 (ti, abs_pos, ln)。"""
    rows = []
    for line in stdout.splitlines():
        mm = re.match(r"^\s+\[(\d+)\]\s+@\s*([\d.]+)s\s+\+([\d.]+)s\s\s", line)
        if mm:
            rows.append((int(mm.group(1)), float(mm.group(2)), float(mm.group(3))))
    return rows


def parse_snap(path):
    """读快照 JSON → {(ti, pos, len)} 集合（pos/len 保留两位以便容差对账）。"""
    d = json.load(open(path, encoding="utf-8"))
    out = []
    for ln in d["lines"]:
        ti, pos, le, _name = ln.split("|", 3)
        out.append((int(ti), round(float(pos), 2), round(float(le), 2)))
    return out


def fuzzy_diff(expected, actual, tol=0.01):
    """expected/actual: [(ti,pos,len)]；按轨号分组贪心匹配（位置/长度容差 tol）。
    返回 (missing, unexpected)。"""
    missing, pool = [], list(actual)
    for e in expected:
        hit = None
        for j, a2 in enumerate(pool):
            if a2[0] == e[0] and abs(a2[1] - e[1]) <= tol and abs(a2[2] - e[2]) <= tol:
                hit = j
                break
        if hit is None:
            missing.append(e)
        else:
            pool.pop(hit)
    return missing, pool


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--ep", required=True)
    ap.add_argument("--task-dir", required=True)
    ap.add_argument("--rpp", required=True)
    ap.add_argument("--db")
    ap.add_argument("--config")
    ap.add_argument("--date", default=time.strftime("%Y-%m-%d"))
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()

    ep = str(a.ep)
    td = os.path.abspath(a.task_dir)
    rpp = os.path.abspath(a.rpp)
    data = os.path.abspath(a.data)
    cues_dir = os.path.join(td, "cues")
    py = sys.executable

    def S(name):
        return os.path.join(SCRIPT_DIR, name)

    m = load_data(data)
    cues = getattr(m, "CUES", [])
    track_range = tuple(getattr(m, "TRACK_RANGE"))
    # config 缺省推导：<任务文件夹>/foley_project.json 存在即采用（SOP 例不需手传）
    if not a.config:
        cand = os.path.join(td, "foley_project.json")
        if os.path.exists(cand):
            a.config = cand
    if not a.db and not a.config:
        stop("○参数", "既无 --db 也无 --config，且 <任务文件夹>/foley_project.json 不存在", ep)
    off_f = os.path.join(cues_dir, "offsets", f"ep{ep}.json")
    if not os.path.exists(off_f):
        stop("①读offsets", f"缺 {off_f}（数值纪律：BASE 禁手抄）", ep)
    oj = json.load(open(off_f, encoding="utf-8"))
    base, base_end = oj["BASE"], oj["END"]

    # ① 备份
    if os.path.exists(rpp):
        bak = os.path.join(os.path.dirname(rpp),
                           f"{os.path.splitext(os.path.basename(rpp))[0]}_备份_ep{ep}施工前_{a.date.replace('-','')}.rpp")
        if not os.path.exists(bak):
            shutil.copy2(rpp, bak)
        print(f"①备份：{bak}")
    else:
        stop("①备份", f"工程不存在：{rpp}", ep)

    # ② 连接
    rc, _ = run([py, S("connect_reaper.py"), "--launch", "--project", rpp], "②connect_reaper")
    if rc:
        stop("②连接", "connect_reaper FAIL", ep)

    # ③ 备料预审
    prep_out = os.path.join(cues_dir, f"备料报告_{ep}.json")
    cmd = [py, S("prepare_ep.py"), "--data", data, "--out", prep_out]
    if a.db:
        cmd += ["--db", a.db]
    if a.config:
        cmd += ["--config", a.config]
    rc, _ = run(cmd, "③prepare_ep 预审")
    if rc:
        stop("③预审", f"备料预审 FAIL（{prep_out}）", ep)

    # ④ dry-run
    cmd = [py, S("place_ep_template.py"), "--data", data, "--base", str(base), "--dry-run"]
    if a.db:
        cmd += ["--db", a.db]
    if a.config:
        cmd += ["--config", a.config]
    rc, dry = run(cmd, "④place --dry-run")
    if rc:
        stop("④dry-run", "dry-run 执行失败", ep)

    # ⑤ 机器 diff 门
    plan = parse_dryrun(dry)
    expected = [(c[0], round(base + c[1], 3), round(c[3], 3)) for c in cues]
    if len(plan) != len(expected):
        stop("⑤diff门", f"dry-run 行数 {len(plan)} != CUES {len(expected)}", ep)
    diffs = [f"轨{ti} @{pos} +{ln} vs CUES 轨{eti} @{epos} +{eln}"
             for (ti, pos, ln), (eti, epos, eln) in zip(plan, expected)
             if ti != eti or abs(pos - epos) > 0.005 or abs(ln - eln) > 0.005]
    if diffs:
        stop("⑤diff门", "dry-run 时间表与数据文件不符：\n" + "\n".join(diffs[:10]), ep)
    print(f"⑤diff门：{len(plan)} 行对账零差异 ✓")

    # ⑥ 施工前快照
    snap_pre = os.path.join(cues_dir, f"snap_ep{ep}_pre.json")
    rc, _ = run([py, S("snapshot_items.py"), "--host", a.host, "--out", snap_pre], "⑥施工前快照")
    if rc:
        stop("⑥快照", "施工前快照失败", ep)

    # ⑦ 正式贴轨
    cmd = [py, S("place_ep_template.py"), "--data", data, "--base", str(base)]
    if a.db:
        cmd += ["--db", a.db]
    if a.config:
        cmd += ["--config", a.config]
    rc, _ = run(cmd, "⑦正式贴轨")
    if rc:
        stop("⑦贴轨", "place_ep_template FAIL", ep)

    # ⑧ 步间快照门（幽灵条目对账）
    snap_post = os.path.join(cues_dir, f"snap_ep{ep}_post.json")
    rc, _ = run([py, S("snapshot_items.py"), "--host", a.host, "--out", snap_post], "⑧施工后快照")
    if rc:
        stop("⑧快照", "施工后快照失败", ep)
    pre, post = parse_snap(snap_pre), parse_snap(snap_post)
    _m, added_pool = fuzzy_diff(pre, post)          # added = post - pre
    expected_new = [(c[0], round(base + c[1], 2), round(c[3], 2)) for c in cues]
    # 终态完整性：CUES 必须全在 post（幂等重跑 added=0 也不误拦）
    missing, _ = fuzzy_diff(expected_new, post)
    # 幽灵检测：新增条目超出 CUES 的部分（重跑 added=0 → 天然 0）
    _missing_in_delta, ghosts = fuzzy_diff(expected_new, added_pool)
    if missing or ghosts:
        stop("⑧快照门", f"终态对账不符：CUES 缺 {len(missing)} / 新增幽灵 {len(ghosts)}"
                        + (f"；幽灵示例 {ghosts[:3]}" if ghosts else ""), ep)
    print(f"⑧快照门：终态 {len(expected_new)} 条全在场 ✓（新增幽灵 0）")

    # ⑨ selfcheck 出厂自检
    sc_file = os.path.join(cues_dir, f"selfcheck_result_{ep}.json")
    cmd = [py, S("selfcheck_ep.py"), "--base", str(base), "--base-end", str(base_end),
           "--tracks", f"{track_range[0]}:{track_range[1]}", "--expect", str(len(cues))]
    if a.db:
        cmd += ["--db", a.db]
    if a.config:
        cmd += ["--config", a.config]
    rc, sc_out = run(cmd, "⑨selfcheck_ep 出厂自检")
    mm = re.search(r"^RESULT (\{.*\})\s*$", sc_out, re.M)
    if mm:
        with open(sc_file, "w", encoding="utf-8") as f:
            f.write(mm.group(1))
    if rc or not mm:
        stop("⑨自检", "selfcheck FAIL 或 RESULT 缺失", ep)

    # ⑩ verify_frames 帧比对（frames\<ep> 存在时强制；缺失记 skipped——验收包第三节依赖该产物）
    vf_json = os.path.join(cues_dir, f"verify_frames_{ep}.json")
    vf_status = "skipped"
    if os.path.isdir(os.path.join(td, "frames", ep)):
        rc, _ = run([py, S("verify_frames.py"), "--task", td, "--ep", ep,
                     "--out", vf_json], "⑩verify_frames 帧比对")
        if rc:
            stop("⑩帧比对", f"verify_frames FAIL（{vf_json}）", ep)
        vf_status = "pass"
    else:
        print(f"⑩帧比对：frames\\{ep} 不存在，skipped（验收包第三节将标待填）")

    # ⑪ 保存（双证）
    rc, _ = run([py, S("save_project.py"), "--rpp", rpp, "--host", a.host], "⑪保存工程")
    if rc:
        stop("⑪保存", "保存双证未过", ep)

    # ⑫ 骨架
    for gen in ("gen_handover.py", "gen_acceptance.py"):
        rc, _ = run([py, S(gen), "--ep", ep, "--task-dir", td, "--rpp", rpp,
                     "--selfcheck-file", sc_file, "--date", a.date], f"⑫{gen}")
        if rc:
            stop("⑫骨架", f"{gen} 失败", ep)

    print("\n✅ 全链通过。")
    print("RESULT " + json.dumps({"status": "pass", "ep": ep, "cues": len(cues),
                                 "snap_pre": snap_pre, "snap_post": snap_post,
                                 "selfcheck": sc_file, "verify_frames": vf_status},
                                ensure_ascii=False))


if __name__ == "__main__":
    main()
