# -*- coding: utf-8 -*-
"""gen_handover.py —— 交班报告骨架生成器（提速实施案 P0-1）。

数据节（任务与输入/执行步骤/出厂自检/交付物清单）机器填数，来源=落盘 JSON，
禁手抄；判断节（口径自裁/存疑清单/异常记录/疑问）留「【手写待填】」由见习手写。
存疑清单按落盘条目 P_NAME 带【】标注自动铺行（轨号+时段机器填，待裁问题手写）。

用法：
  python gen_handover.py --ep 3 --task-dir <任务文件夹> --rpp <工程.rpp> \
      [--selfcheck-file selfcheck_result.json] [--date 2026-10-16] [--out 交班.md]
  python gen_handover.py --check <交班.md>     # 判断节非空门禁：残留占位符即 FAIL

selfcheck-file：selfcheck_ep.py 末行 RESULT 的 JSON 部分存成文件传入；
缺省时自检节标「【手写待填】」。
门禁（--check）：if 文件含「【手写待填】」→ FAIL（无内容须改写「无」），exit 1。
"""
import argparse
import glob
import importlib.util
import json
import os
import re
import sys

PLACEHOLDER = "【手写待填】"
# 存疑标注词表（[W-08] 拿不准条目命名约定）；品牌类【声羽拾光】等不算存疑
KNOWN_FLAGS = ("可删", "待人删", "存疑", "待裁", "近似", "待人裁", "｜")


def _is_flagged(pname):
    return any(k in (pname or "") for k in KNOWN_FLAGS)


def _vf_status(vj):
    if not vj:
        return PLACEHOLDER
    if vj.get("result"):
        return str(vj["result"]).lower()
    return "pass" if vj.get("mismatch", 1) == 0 and vj.get("noframe", 1) == 0 else "fail"


def _load_cues(path):
    spec = importlib.util.spec_from_file_location("cues_data", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _one(pattern):
    hits = sorted(glob.glob(pattern))
    return hits[-1] if hits else None


def build(args):
    td = os.path.abspath(args.task_dir)
    cues_dir = os.path.join(td, "cues")
    ep = str(args.ep)
    date = args.date or "（日期待填）"

    data_file = _one(os.path.join(cues_dir, f"cues_ep{ep}.py"))
    m = _load_cues(data_file) if data_file else None
    cues_n = len(getattr(m, "CUES", [])) if m else "?"
    snames_n = len(getattr(m, "S_NAMES", {})) if m else "?"

    report_md = _one(os.path.join(td, "reports", f"ep{ep}_逐秒分析报告_*.md"))
    offsets = _one(os.path.join(cues_dir, "offsets", f"ep{ep}.json"))
    region = ""
    if offsets:
        o = json.load(open(offsets, encoding="utf-8"))
        region = f"[{o['BASE']}, {o['END']}]（来源 `cues/offsets/ep{ep}.json` 照抄）"
    prep = _one(os.path.join(cues_dir, f"备料报告_{ep}.json"))
    prep_j = json.load(open(prep, encoding="utf-8")) if prep else {}
    verify = _one(os.path.join(cues_dir, f"verify_frames_{ep}.json"))
    verify_j = json.load(open(verify, encoding="utf-8")) if verify else {}
    items_f = _one(os.path.join(cues_dir, f"ep{ep}_落盘条目.json"))
    items = json.load(open(items_f, encoding="utf-8")) if items_f else []

    sc = PLACEHOLDER
    if args.selfcheck_file and os.path.exists(args.selfcheck_file):
        sc_j = json.load(open(args.selfcheck_file, encoding="utf-8"))
        sc = (f"selfcheck RESULT：`{json.dumps(sc_j, ensure_ascii=False)}`")

    vols = sorted({round(it.get("vol", -1), 3) for it in items}) if items else []
    vol_txt = f"D_VOL dump 实测 {vols}（`cues/ep{ep}_落盘条目.json`）" if vols else PLACEHOLDER
    flagged = [it for it in items if _is_flagged(it.get("pname"))]

    deliverables = []
    for pat in (f"cues_ep{ep}.py", f"ep{ep}_*.md", f"ep{ep}_*.json", f"备料报告_{ep}.json",
                f"verify_frames_{ep}.json", f"dump_ep{ep}_*.py", f"gen_ep{ep}_*.py"):
        deliverables.extend(os.path.basename(p) for p in sorted(glob.glob(os.path.join(cues_dir, pat))))
    deliverables = sorted(set(deliverables))

    L = []
    L.append(f"# ep{ep} 交班报告（{os.path.basename(td)}，{date}，见习音效师）")
    L.append("")
    L.append("> 数据节由 gen_handover.py 机器生成（提速实施案 P0-1）；判断节手写。")
    L.append("")
    L.append("## 1. 任务与输入")
    L.append(f"- 报告：`{report_md or PLACEHOLDER}`")
    L.append(f"- 工程：`{os.path.abspath(args.rpp)}`（Region {ep} = {region or PLACEHOLDER}）")
    L.append(f"- 数据文件：`{data_file or PLACEHOLDER}`（CUES {cues_n} 条 / S_NAMES {snames_n} 项）")
    L.append("")
    L.append("## 2. 执行步骤与结果")
    L.append(f"1. 备料预审：{prep_j.get('result', PLACEHOLDER)}（vd 取证 {len(prep_j.get('vd_evidence', []))} 条；"
             f"同源表 {len(prep_j.get('same_src', {}))} 项；问题 {len(prep_j.get('problems', []))} 条）——`{os.path.basename(prep or '')}`")
    L.append(f"2. 贴轨：落盘 {len(items)} 条（`ep{ep}_落盘条目.json`）")
    L.append(f"3. 帧比对：{_vf_status(verify_j)}（抽样 {verify_j.get('cues_sampled', '?')} 条 / "
             f"受验 {verify_j.get('frames_checked', '?')} 帧 / mismatch {verify_j.get('mismatch', '?')}）")
    L.append(f"4. 异常记录：{PLACEHOLDER}（无则写「无」）")
    L.append("")
    L.append("## 3. 出厂自检结论（附证据）")
    L.append(f"- {sc}")
    L.append(f"- verify_frames：`{os.path.basename(verify or '')}` status={_vf_status(verify_j)}")
    L.append(f"- {vol_txt}")
    L.append("")
    L.append("## 4. 口径自裁留痕（全权委托）")
    L.append(PLACEHOLDER + "（无则写「无」；制作人裁决须注明「非自裁」）")
    L.append("")
    L.append("## 5. 存疑清单（只挂未结项，已照贴+条目名带标注）")
    if flagged:
        L.append("| # | 轨 | 落点s | pname | 待裁问题 |")
        L.append("|---|---|---|---|---|")
        for k, it in enumerate(flagged, 1):
            L.append(f"| {k} | {it['track']} | {it['pos']} | {it.get('pname','')} | {PLACEHOLDER} |")
    else:
        L.append("无（落盘条目无【】标注）。")
    L.append("")
    L.append("## 6. 交付物清单（`" + td + "`）")
    for d in deliverables:
        L.append(f"- `cues\\{d}`")
    L.append("")
    L.append("## 7. 见习生疑问")
    L.append(PLACEHOLDER + "（无则写「无」）")
    L.append("")
    return "\n".join(L)


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--ep")
    ap.add_argument("--task-dir")
    ap.add_argument("--rpp")
    ap.add_argument("--selfcheck-file")
    ap.add_argument("--date")
    ap.add_argument("--out")
    ap.add_argument("--check", help="门禁模式：检查既有交班报告")
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
                                f"ep{a.ep}_交班报告_骨架_{a.date or 'undated'}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(md)
    print("骨架：" + out)
    print("RESULT " + json.dumps({"status": "pass", "out": out}, ensure_ascii=False))


if __name__ == "__main__":
    main()
