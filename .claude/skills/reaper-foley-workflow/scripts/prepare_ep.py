# -*- coding: utf-8 -*-
"""备料一键脚本：数据文件（总监直出 cues_<ep>.py）→ 校验 + 取证 + 预审，不碰 REAPER。

用法：
  python prepare_ep.py --data cues_14-1v2.py --config foley_project.json
  python prepare_ep.py --data cues_14-1v2.py --db 库.db --out 备料报告.json

检查项（任一不过 = FAIL，禁止进实机段）：
  1. 结构：CUES 九元组字段数/类型、S_NAMES 非空、EP 已填；
  2. 路径：S 字典逐条 q() 解析 + 文件存在性；
  3. 越界预审：off+len ≤ DB 源长；
  4. 非零取窗取证：off>0 的条目跑 ffmpeg volumedetect（窗口 max_volume > −30dB 才放行，铁律 10）；
  5. 同源计数：同一 source_key 一集 ≤2 次（retrieval.md 硬约束）。
产物：备料报告 JSON（证据留痕，交班报告引用它，禁手抄数值）。
"""
import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rfw_common import duration_map, load_config, open_asset_db, q, setup_stdout

VD_LIMIT = -30.0     # 铁律 10：窗口 max_volume 阈值（dB）
SAME_SRC_MAX = 2     # 集内同源上限


def load_data(path):
    spec = importlib.util.spec_from_file_location("cues_data", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def volumedetect(path, off, ln, ffmpeg="ffmpeg"):
    """跑窗口电平检测，返回 max_volume（float）或 None（解析失败）。"""
    cmd = [ffmpeg, "-hide_banner", "-ss", str(off), "-t", str(ln),
           "-i", path, "-af", "volumedetect", "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")  # [X-05] 容错
    m = re.search(r"max_volume:\s*(-?[\d.]+)\s*dB", r.stderr or "")
    return float(m.group(1)) if m else None


def main():
    setup_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="总监直出数据文件 cues_<ep>.py")
    ap.add_argument("--config", help="foley_project.json")
    ap.add_argument("--db", help="音源库 DB（缺省取 config.paths.asset_db）")
    ap.add_argument("--out", help="备料报告 JSON 路径（缺省 <data 同目录>/备料报告_<EP>.json）")
    ap.add_argument("--ffmpeg", default="ffmpeg")
    a = ap.parse_args()

    m = load_data(a.data)
    ep = getattr(m, "EP", 0)
    cues = getattr(m, "CUES", [])
    s_names = getattr(m, "S_NAMES", {})
    out = a.out or os.path.join(os.path.dirname(os.path.abspath(a.data)), f"备料报告_{ep}.json")

    problems = []
    # 1. 结构
    if not ep:
        problems.append("EP 未填")
    if not s_names:
        problems.append("S_NAMES 为空")
    if not cues:
        problems.append("CUES 为空")
    for i, c in enumerate(cues):
        if len(c) != 9:
            problems.append(f"CUES[{i}] 不是九元组（{len(c)} 字段）")
            continue
        ti, t_local, skey, ln, label, fade, off, vol, fi = c
        if skey not in s_names:
            problems.append(f"CUES[{i}] {label}：source_key '{skey}' 不在 S_NAMES")
        if ln <= 0:
            problems.append(f"CUES[{i}] {label}：len={ln} 非正")

    report = {"ep": ep, "data": os.path.abspath(a.data), "cues": len(cues),
              "paths": {}, "violations": [], "vd_evidence": [], "same_src": {}}

    if not problems:
        db_path = a.db or (load_config(a.config)["paths"].get("asset_db") if a.config else "")
        db = open_asset_db(db_path)
        # 2. 路径解析 + 存在性
        S = {}
        for alias, name in s_names.items():
            try:
                p = q(db, name)
            except Exception as e:
                problems.append(f"S_NAMES[{alias}] 库里没有: {name}（{e}）")
                continue
            if not os.path.exists(p):
                problems.append(f"S_NAMES[{alias}] 路径不存在: {p}")
            S[alias] = p
            report["paths"][alias] = p
        durs = duration_map(db)

        # 3./4./5. 逐条预审
        for i, c in enumerate(cues):
            ti, t_local, skey, ln, label, fade, off, vol, fi = c
            path = S.get(skey)
            if not path:
                continue
            report["same_src"][skey] = report["same_src"].get(skey, 0) + 1
            d = durs.get(os.path.basename(path).lower(), 0)
            if d and off + ln > d + 0.05:
                problems.append(f"CUES[{i}] {label}：越界 off={off}+len={ln} > 源长 {d:.2f}")
                report["violations"].append({"i": i, "label": label, "kind": "越界"})
            if off and off > 0:  # 非零取窗必须取证（铁律 10）
                mv = volumedetect(path, off, ln, a.ffmpeg)
                ev = {"i": i, "label": label, "off": off, "len": ln,
                      "max_volume": mv, "pass": bool(mv is not None and mv > VD_LIMIT)}
                report["vd_evidence"].append(ev)
                if not ev["pass"]:
                    problems.append(f"CUES[{i}] {label}：vd 证据不达标（max_volume={mv}，要求 >{VD_LIMIT}dB）")
                    report["violations"].append({"i": i, "label": label, "kind": "vd不达标"})
        # 5. 同源计数
        for skey, n in report["same_src"].items():
            if n > SAME_SRC_MAX:
                problems.append(f"同源超限：{skey} 用了 {n} 次（上限 {SAME_SRC_MAX}）")
                report["violations"].append({"key": skey, "kind": "同源超限"})

    report["result"] = "FAIL" if problems else "PASS"
    report["problems"] = problems
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"=== 备料预审 EP{ep}：{len(cues)} 条 cue ===")
    print(f"路径解析 {len(report['paths'])} 条；vd 取证 {len(report['vd_evidence'])} 条；"
          f"同源表 {len(report['same_src'])} 项")
    for p in problems:
        print(f"  ❌ {p}")
    print(f"报告：{out}")
    print("RESULT " + json.dumps({"status": report["result"].lower(), "ep": ep,
                                 "cues": len(cues), "problems": len(problems),
                                 "report": out}, ensure_ascii=False))
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
