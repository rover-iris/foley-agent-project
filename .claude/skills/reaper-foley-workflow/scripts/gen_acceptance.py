# -*- coding: utf-8 -*-
"""gen_acceptance.py —— 验收包骨架生成器（提速实施案 P0-1）。

五节模板（对账表/出厂自检/帧比对/vd 与判型证据/存疑清单）数据节机器填数，
存疑清单与首节注记留「【手写待填】」。判据量化门禁同 gen_handover.py --check。

用法：
  python gen_acceptance.py --ep 3 --task-dir <任务文件夹> --rpp <工程.rpp> \
      [--selfcheck-file r.json] [--judge-file 判型证据.json] [--date 2026-10-16] [--out 验收包.md]
  python gen_acceptance.py --check <验收包.md>
"""
import argparse
import glob
import importlib.util
import json
import os
import sys

from gen_handover import PLACEHOLDER, _is_flagged, _load_cues, _one, _vf_status


def build(args):
    td = os.path.abspath(args.task_dir)
    cues_dir = os.path.join(td, "cues")
    ep = str(args.ep)
    date = args.date or "（日期待填）"

    data_file = _one(os.path.join(cues_dir, f"cues_ep{ep}.py"))
    m = _load_cues(data_file) if data_file else None
    cues_n = len(getattr(m, "CUES", [])) if m else "?"

    offsets = _one(os.path.join(cues_dir, "offsets", f"ep{ep}.json"))
    region = ""
    if offsets:
        o = json.load(open(offsets, encoding="utf-8"))
        region = f"[{o['BASE']}, {o['END']}]（来源 `cues/offsets/ep{ep}.json`）"
    recon = _one(os.path.join(cues_dir, f"ep{ep}_对账表.md"))
    recon_n = None
    if recon:
        txt = open(recon, encoding="utf-8").read()
        recon_n = txt.count("| ✓ |")
    prep = _one(os.path.join(cues_dir, f"备料报告_{ep}.json"))
    prep_j = json.load(open(prep, encoding="utf-8")) if prep else {}
    verify = _one(os.path.join(cues_dir, f"verify_frames_{ep}.json"))
    verify_j = json.load(open(verify, encoding="utf-8")) if verify else {}
    items_f = _one(os.path.join(cues_dir, f"ep{ep}_落盘条目.json"))
    items = json.load(open(items_f, encoding="utf-8")) if items_f else []
    flagged = [it for it in items if _is_flagged(it.get("pname"))]

    sc = PLACEHOLDER
    if args.selfcheck_file and os.path.exists(args.selfcheck_file):
        sc = "`" + json.dumps(json.load(open(args.selfcheck_file, encoding="utf-8")), ensure_ascii=False) + "`"

    L = []
    L.append(f"# ep{ep} 验收包（{os.path.basename(td)}，{date}）——A4 复核单一入口")
    L.append("")
    L.append("> 数据节由 gen_acceptance.py 机器生成（提速实施案 P0-1）。")
    L.append(f"工程：`{os.path.abspath(args.rpp)}`；Region {ep} = {region or PLACEHOLDER}；CUES {cues_n} 条（落点为 Region 相对秒）。")
    L.append(f"注记：{PLACEHOLDER}（裁决/异常/免检口径等，无则写「无」）")
    L.append("")
    L.append("## 一、对账表（CUES ↔ 落盘 rpp 条目，容差 0.01s）")
    if recon:
        L.append(f"全文 `{os.path.relpath(recon, td)}`；命中 {recon_n} 行。结论：{PLACEHOLDER}（照对账表结论行抄）")
    else:
        L.append(PLACEHOLDER + "（对账表缺失 → 打回补包）")
    L.append("")
    L.append("## 二、出厂自检输出（selfcheck_ep.py）")
    L.append(f"selfcheck RESULT：{sc}")
    L.append("")
    L.append("## 三、帧比对输出（verify_frames.py）")
    L.append(f"RESULT：`{json.dumps(verify_j, ensure_ascii=False) if verify_j else PLACEHOLDER}`")
    L.append("")
    L.append("## 四、vd 与判型证据")
    vd = prep_j.get("vd_evidence", [])
    if vd:
        L.append("| i | label | off | len | max_volume | cached | pass |")
        L.append("|---|---|---|---|---|---|---|")
        for e in vd:
            L.append(f"| {e['i']} | {e['label']} | {e['off']} | {e['len']} | {e['max_volume']} | {e.get('cached', '')} | {'✓' if e['pass'] else '✗'} |")
    else:
        L.append("本集无非零取窗条目（vd 不适用）。")
    if args.judge_file and os.path.exists(args.judge_file):
        L.append(f"\n判型证据（铁律 15 末款）：`{args.judge_file}` 已附，音头数逐源见文件。")
    L.append("")
    L.append("## 五、存疑清单（只挂未结项，全部已照贴、条目名带标注）")
    if flagged:
        L.append("| # | 轨 | 时段s | label | 待裁问题 |")
        L.append("|---|---|---|---|---|")
        for k, it in enumerate(flagged, 1):
            L.append(f"| {k} | {it['track']} | {it['pos']}~{round(it['pos']+it['len'],1)} | {it.get('pname','')} | {PLACEHOLDER} |")
    else:
        L.append("无。")
    L.append("")
    return "\n".join(L)


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--ep")
    ap.add_argument("--task-dir")
    ap.add_argument("--rpp")
    ap.add_argument("--selfcheck-file")
    ap.add_argument("--judge-file")
    ap.add_argument("--date")
    ap.add_argument("--out")
    ap.add_argument("--check")
    a = ap.parse_args()

    if a.check:
        txt = open(a.check, encoding="utf-8").read()
        left = txt.count(PLACEHOLDER)
        ok = left == 0
        print("RESULT " + json.dumps({"status": "pass" if ok else "fail",
                                      "file": a.check, "placeholders_left": left},
                                     ensure_ascii=False))
        sys.exit(0 if ok else 1)

    md = build(a)
    out = a.out or os.path.join(os.path.abspath(a.task_dir), "cues",
                                f"ep{a.ep}_验收包_骨架_{a.date or 'undated'}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(md)
    print("骨架：" + out)
    print("RESULT " + json.dumps({"status": "pass", "out": out}, ensure_ascii=False))


if __name__ == "__main__":
    main()
