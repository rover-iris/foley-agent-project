#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逐秒动作分析报告 · 出厂门禁（video-action-analysis 第三步）。

对照《音效分析工作要求》硬性条款做机器检查，任何 FAIL = 不许交付、不许开下一集。
背景（2026-09 权臣CP翻车）：54/54 集多秒合并行、白羽箭动作链被改写、
环境底衬凑统计数，而当时的 QC 只查文件覆盖/标签规范，内容级红线零拦截。

用法:
  python check_report.py --report 第26集.md --duration 65.2 [--frames frames/26] \
                         [--jsonl 第26集.jsonl] [--ban-names 周理成,戚允安]
  python check_report.py --dir <报告目录> --durations durations.txt [--frames-root <帧根目录>]
durations.txt 每行: "<集号> <秒>"（如 "26 65.2"，集号/文件名/秒以空白分隔）
"""
import argparse
import json
import math
import re
import subprocess
import sys
from pathlib import Path

ENV_RE = re.compile(r"底衬|留环境|环境底")
DISCRETE_RE = re.compile(r"起手|落点|收尾|impact|movement|whoosh|命中|入水|落地")
FRAME_RE = re.compile(r"[sS]_(\d+)")


def parse_time_cell(cell):
    """返回 (kind, start, end)。kind: OK / RANGE(36-45) / SPAN(18（至21）) / SKIP。"""
    c = cell.replace("**", "").strip()
    m = re.match(r"^(\d+(?:\.\d+)?)(.*)$", c)
    if not m:
        return "SKIP", None, None
    start, rest = float(m.group(1)), m.group(2)
    if re.match(r"\s*[-~—–]", rest):
        return "RANGE", start, None
    m2 = re.search(r"[（(]\s*至|到\s*(\d+(?:\.\d+)?)", rest)
    if m2 and m2.group(1):
        end = float(m2.group(1))
        return ("SPAN" if end > start + 0.999 else "OK"), start, end
    return "OK", start, None


def timeline_rows(text):
    """取「逐秒时间轴」章节的表行：[(时间格, 画面格, 声音格, 类型格)]。"""
    sec = re.split(r"^#{1,6}.*(?:逐秒时间轴|时间轴).*$", text, maxsplit=1, flags=re.M)
    body = sec[1] if len(sec) > 1 else text
    rows = []
    for line in body.splitlines():
        if not line.strip().startswith("|"):
            if rows:
                break  # 表已结束
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4 or set(cells[0]) <= {"-", ":", " "}:
            continue
        kind, start, end = parse_time_cell(cells[0])
        if kind == "SKIP":
            continue
        rows.append((kind, start, end, cells))
    return rows


def is_env_row(cells):
    sound, typ = cells[2], cells[3]
    return bool(ENV_RE.search(sound)) and not bool(DISCRETE_RE.search(sound + typ))


def check_one(name, text, dur, frames_dir=None, jsonl_path=None, ban_names=()):
    fails, warns = [], []
    need = math.ceil(dur - 0.05)
    rows = timeline_rows(text)
    ok_rows = [r for r in rows if r[0] == "OK"]

    # 1. 行数下限
    if len(ok_rows) < need:
        fails.append(f"行数不足: 单秒行 {len(ok_rows)} < ceil(时长)={need}")
    # 2. 合并行 / 跨秒括注
    for kind, start, end, cells in rows:
        if kind == "RANGE":
            fails.append(f"多秒合并行 {cells[0].replace(chr(42), '')!r}（硬性禁令）")
        elif kind == "SPAN":
            fails.append(f"括注跨秒行 {cells[0]!r}（=变体合并行）")
        elif start is not None and start >= dur:
            fails.append(f"时间越界: {cells[0]!r} ≥ 时长 {dur}")
    # 3. 秒覆盖空洞
    covered = {int(r[1]) for r in ok_rows}
    missing = [s for s in range(need) if s not in covered]
    if missing:
        fails.append(f"秒覆盖空洞 {len(missing)} 个: {missing[:10]}{'...' if len(missing) > 10 else ''}")
    # 4. 章节齐全
    if not re.search(r"#+\s*.*声音事件统计", text):
        fails.append("缺「声音事件统计」章节")
    if not re.search(r"#+\s*.*待.*拍板|拍板", text):
        fails.append("缺「待用户拍板」章节")
    # 5. 统计数 == 表内行数（环境行除外）
    auto_rows = [r for r in rows if "【自动】" in r[3][3] and not is_env_row(r[3])]
    m = re.search(r"【自动】\s*(\d+)\s*个", text)
    if m:
        declared, actual = int(m.group(1)), len(auto_rows)
        # 统计按事件链计数（11-13s 取信封=1 事件 3 行表）合法，declared < actual 仅提示；
        # declared > actual = 表外凑数/环境计入（权臣CP V2 用蝶类环境凑到 12 个），硬性 FAIL。
        if declared > actual:
            fails.append(f"统计【自动】{declared} 个 > 表内自动事件行 {actual} 个（环境底衬/表外凑数）")
        elif declared < actual:
            warns.append(f"统计【自动】{declared} 个 < 表内自动事件行 {actual} 个（按事件链聚合，请逐条可对应）")
    else:
        fails.append("统计段无「【自动】N 个」口径")
    # 6. 自动行质量：帧证据 + 声音列
    no_evidence = no_sound = 0
    for _, _, _, cells in auto_rows:
        if not FRAME_RE.search(cells[1] + cells[2]):
            no_evidence += 1
        if cells[2].strip("；;— ") in ("", "—", "无配"):
            no_sound += 1
    if no_evidence:
        warns.append(f"{no_evidence} 条【自动】行未引用帧证据（s_NNN），请核对是否逐帧确认")
    if no_sound:
        fails.append(f"{no_sound} 条【自动】行声音事件列为空")
    # 7. 帧文件存在性
    if frames_dir:
        manifest = {}
        mf = frames_dir / "manifest.json"
        if mf.exists():
            manifest = {f["file"]: f["sec"] for f in json.loads(mf.read_text(encoding="utf-8"))["frames"]}
        for fn in sorted({f"s_{int(x):03d}.jpg" for x in FRAME_RE.findall(text)}):
            if not (frames_dir / fn).exists():
                fails.append(f"引用帧不存在: {fn}")
            elif manifest and fn in manifest:
                pass  # 有 manifest 时，报告时间应以 manifest sec 为准（人工复核项）
    # 8. 判读落盘 jsonl 行数
    if jsonl_path and Path(jsonl_path).exists():
        n = sum(1 for ln in Path(jsonl_path).read_text(encoding="utf-8").splitlines() if ln.strip())
        if n < need:
            fails.append(f"per_second.jsonl 仅 {n} 行 < {need}：判读未逐秒落盘")
    # 9. 人名黑名单
    for nm in ban_names:
        if nm and nm in text:
            fails.append(f"出现人名「{nm}」（应用外观特征称呼）")
    return fails, warns, len(ok_rows), need


def get_duration(ep, args):
    if args.duration:
        return float(args.duration)
    if args.durations:
        for ln in Path(args.durations).read_text(encoding="utf-8").splitlines():
            parts = ln.split()
            if len(parts) >= 2 and parts[0].lstrip("0") == str(ep).lstrip("0"):
                return float(parts[-1])
    if args.videos:
        v = Path(args.videos) / f"{ep}.mp4"
        if v.exists():
            r = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json",
                                "-show_format", str(v)], capture_output=True, text=True)
            return float(json.loads(r.stdout)["format"]["duration"])
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report"); ap.add_argument("--dir")
    ap.add_argument("--duration", type=float)
    ap.add_argument("--durations"); ap.add_argument("--videos")
    ap.add_argument("--frames"); ap.add_argument("--frames-root")
    ap.add_argument("--jsonl")
    ap.add_argument("--ban-names", default="")
    a = ap.parse_args()
    ban = [x for x in a.ban_names.split(",") if x]

    jobs = []
    if a.dir:
        root = Path(a.dir)
        for f in sorted(root.glob("第*集*.md")):
            ep = int(re.search(r"第(\d+)集", f.name).group(1))
            fr = Path(a.frames_root) / str(ep) if a.frames_root else None
            jl = f.with_suffix(".jsonl")
            jobs.append((f.name, f.read_text(encoding="utf-8"), get_duration(ep, a), fr, jl))
    else:
        if not (a.report and a.duration):
            sys.exit("用法: --report X.md --duration N 或 --dir <目录> --durations/--videos")
        fr = Path(a.frames) if a.frames else None
        jobs.append((Path(a.report).name, Path(a.report).read_text(encoding="utf-8"), a.duration, fr, a.jsonl))

    total_fail = 0
    for name, text, dur, fr, jl in jobs:
        if dur is None:
            print(f"[SKIP] {name}: 无时长数据")
            continue
        fails, warns, rows, need = check_one(name, text, dur, fr, jl, ban)
        total_fail += len(fails)
        status = "PASS" if not fails else "FAIL"
        print(f"[{status}] {name}（{rows}/{need} 行）")
        for x in fails:
            print(f"   FAIL: {x}")
        for x in warns:
            print(f"   warn: {x}")
    sys.exit(1 if total_fail else 0)


if __name__ == "__main__":
    main()
