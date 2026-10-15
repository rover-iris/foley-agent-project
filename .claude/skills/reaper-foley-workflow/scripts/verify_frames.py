# -*- coding: utf-8 -*-
"""A4 抽帧比对落点脚本化：cue 时间点 → 帧库对应秒帧 ↔ 源视频 eq(n) 重抓帧双判比对。

用法：
  python verify_frames.py --task <任务文件夹> --ep 185
  python verify_frames.py --task <任务文件夹> --ep 185 --ratio 0.3 --focus 3.0,29.5
  python verify_frames.py --data cues_185.py --frames-dir frames\\185 --video 185.mp4 --all

输入约定（--task 模式）：任务文件夹\\frames\\<集号>\\ 的 manifest.json（主库 + 各加密
子目录的 manifest 全部纳入索引）+ 任务文件夹\\cues\\cues_<集号>.py 的 CUES 落点；
源视频路径缺省取主库 manifest 的 video 字段。

判据（任一 MISMATCH / NOFRAME / MISSING / GRAB_FAIL = FAIL）：
  1. 抽验：按 t_local 排序后等距抽取 ceil(N*ratio) 条 cue（默认 ratio=0.2，
     即复核口径 ≥20%），--focus 指定的秒强制纳入（就近 cue 必验），--all 全量；
  2. 帧匹配：帧库全部 manifest（主库 + 加密子目录）中 |sec − t_local| ≤ --match-tol
     （默认 0.5s）的帧全部受验——加密窗口内的帧逐张对号，一条 cue 可牵出多帧；
  3. 重抓：按受验帧标称秒从源视频抓帧，一律 select eq(n) 帧号系精确取帧
     （n = round(sec × 源帧率)，与 extract_frames.py 同一 builder 口径），
     禁止 -ss 直抽（个别秒落点抖动前科）；缩放/编码参数取 manifest 的 scale，与帧库同管线；
  4. 双判：md5 一致 → PASS；md5 不等但 aHash（8x8 灰度均值哈希，PIL 优先、
     无 PIL 走 ffmpeg PGM 管道纯 python 实现）汉明距离 ≤ --ahash-tol（默认 5）
     → PASS_SOFT（防编码参数差异假阳性）；超过 → MISMATCH；
  5. cue 在容差内找不到任何库帧 → NOFRAME（无法验证 = 不过）。

产物：比对报告 JSON（--out 指定，缺省 <data 同目录>/verify_frames_<EP>.json），
末行 RESULT JSON 机器可读；exit 0=全过，1=有违规。
"""
import argparse
import hashlib
import importlib.util
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rfw_common import setup_stdout

AHASH_TOL = 5        # aHash 汉明距离软判阈值（64 bit）
MATCH_TOL = 0.5      # cue 落点↔库帧标称秒匹配容差（秒）
RATIO = 0.2          # 默认抽验比例（复核口径 ≥20%）


def load_data(path):
    spec = importlib.util.spec_from_file_location("cues_data", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def md5_file(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ahash(path, ffmpeg="ffmpeg"):
    """8x8 灰度均值哈希（64 bit int）。PIL 优先；无 PIL 走 ffmpeg PGM 管道。"""
    try:
        from PIL import Image
        img = Image.open(path).convert("L").resize((8, 8), Image.LANCZOS)
        try:
            px = list(img.get_flattened_data())   # Pillow ≥12 新名
        except AttributeError:
            px = list(img.getdata())
    except ImportError:
        r = subprocess.run([ffmpeg, "-v", "error", "-i", path,
                            "-vf", "scale=8:8", "-pix_fmt", "gray",
                            "-f", "image2pipe", "-vcodec", "pgm", "-"],
                           capture_output=True)
        if r.returncode or not r.stdout.startswith(b"P5"):
            raise RuntimeError(f"aHash 回退管道失败: {path}")
        # 解析二进制 PGM：P5\n<w> <h>\n<max>\n<raw>
        head, _, raw = r.stdout.partition(b"\n255\n")
        dims = head.split()[1:] if head.startswith(b"P5") else None
        if not dims or len(raw) != 64:
            raise RuntimeError(f"aHash PGM 解析失败: {path}")
        px = list(raw)
    avg = sum(px) / len(px)
    h = 0
    for v in px:
        h = (h << 1) | (1 if v >= avg else 0)
    return h


def hamming(a, b):
    return bin(a ^ b).count("1")


def load_frame_index(frames_dir):
    """索引帧库全部 manifest（主库 + 含 manifest.json 的子目录）。

    返回 (index, meta)：index = [{sec, file, dir, path}]；meta 取主库 manifest 的
    video/source_fps/scale（重抓参数与建库同管线）。
    """
    manifests = [frames_dir]
    manifests += [os.path.join(frames_dir, d) for d in sorted(os.listdir(frames_dir))
                  if os.path.isdir(os.path.join(frames_dir, d))
                  and os.path.exists(os.path.join(frames_dir, d, "manifest.json"))]
    index, meta = [], {}
    for d in manifests:
        mp = os.path.join(d, "manifest.json")
        if not os.path.exists(mp):
            continue
        with open(mp, encoding="utf-8") as f:
            man = json.load(f)
        if d == frames_dir:
            meta = {"video": man.get("video", ""),
                    "source_fps": float(man.get("source_fps") or 0),
                    "scale": int(man.get("scale") or 480)}
        for fr in man.get("frames", []):
            index.append({"sec": float(fr["sec"]), "file": fr["file"],
                          "dir": os.path.relpath(d, frames_dir),
                          "path": os.path.join(d, fr["file"])})
    if not index:
        raise SystemExit(f"帧库为空或无 manifest.json: {frames_dir}")
    if not meta.get("source_fps"):
        raise SystemExit("主库 manifest 缺 source_fps，无法换算 eq(n) 帧号")
    return index, meta


def grab_frame(video, sec, fps, scale, out_jpg, ffmpeg="ffmpeg"):
    """eq(n) 帧号系精确取帧（禁止 -ss 直抽），缩放/编码与 extract_frames.py 同管线。"""
    n = round(sec * fps)
    cmd = [ffmpeg, "-y", "-v", "error", "-i", video,
           "-vf", f"select='eq(n,{n})',scale=-2:{scale}",
           "-fps_mode", "passthrough", "-frames:v", "1", "-q:v", "5", out_jpg]
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    if r.returncode and "fps_mode" in (r.stderr or ""):  # 兼容旧版 ffmpeg
        cmd[cmd.index("-fps_mode")] = "-vsync"
        cmd[cmd.index("passthrough")] = "0"
        r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    return r.returncode == 0 and os.path.exists(out_jpg)


def pick_sample(cues, ratio, focus):
    """按 t_local 排序等距抽 ceil(N*ratio) 条（索引回原 CUES 序号）+ focus 就近必验。"""
    order = sorted(range(len(cues)), key=lambda i: (cues[i][1], i))
    k = max(1, math.ceil(len(cues) * ratio))
    if k >= len(cues):
        picked = list(order)
    else:
        picked = sorted({order[round(i * (len(order) - 1) / (k - 1))] for i in range(k)}
                        if k > 1 else {order[0]}, key=lambda i: (cues[i][1], i))
    picked_set = set(picked)
    for f in focus:
        near = min(range(len(cues)), key=lambda i: (abs(cues[i][1] - f), i))
        picked_set.add(near)
    return sorted(picked_set, key=lambda i: (cues[i][1], i))


def main():
    setup_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", help="任务文件夹根（缺省推导 frames\\<ep>\\ 与 cues\\cues_<ep>.py）")
    ap.add_argument("--ep", help="集号（--task 模式必填）")
    ap.add_argument("--data", help="cues_<集号>.py（缺省 <task>\\cues\\cues_<ep>.py）")
    ap.add_argument("--frames-dir", help="帧库目录（缺省 <task>\\frames\\<ep>）")
    ap.add_argument("--video", help="源视频（缺省取主库 manifest video 字段）")
    ap.add_argument("--ratio", type=float, default=RATIO, help="抽验比例（默认 0.2）")
    ap.add_argument("--focus", default="", help="重点段秒清单（逗号分隔，就近 cue 强制受验）")
    ap.add_argument("--all", action="store_true", help="全量受验（忽略 --ratio）")
    ap.add_argument("--match-tol", type=float, default=MATCH_TOL, help="cue↔库帧匹配容差秒")
    ap.add_argument("--ahash-tol", type=int, default=AHASH_TOL, help="aHash 软判汉明距离阈值")
    ap.add_argument("--out", help="比对报告 JSON 路径")
    ap.add_argument("--keep-frames", help="保留重抓帧到指定目录（取证用，缺省清理）")
    ap.add_argument("--ffmpeg", default="ffmpeg")
    a = ap.parse_args()

    if a.task:
        if not a.ep:
            raise SystemExit("--task 模式必须给 --ep")
        a.data = a.data or os.path.join(a.task, "cues", f"cues_{a.ep}.py")
        a.frames_dir = a.frames_dir or os.path.join(a.task, "frames", str(a.ep))
    if not a.data or not a.frames_dir:
        raise SystemExit("必须给 --task + --ep，或显式给 --data + --frames-dir")

    m = load_data(a.data)
    ep = str(getattr(m, "EP", "") or a.ep or "")
    cues = getattr(m, "CUES", [])
    if not cues:
        raise SystemExit("CUES 为空")
    out = a.out or os.path.join(os.path.dirname(os.path.abspath(a.data)),
                                f"verify_frames_{ep}.json")
    focus = [float(x) for x in a.focus.split(",") if x.strip()]
    index, meta = load_frame_index(a.frames_dir)
    video = a.video or meta.get("video", "")
    if not video or not os.path.exists(video):
        raise SystemExit(f"源视频不存在: {video or '(未提供且 manifest 无 video 字段)'}")

    sampled = list(range(len(cues))) if a.all else pick_sample(cues, a.ratio, focus)
    tmp = a.keep_frames or tempfile.mkdtemp(prefix="verify_frames_")
    fps, scale = meta["source_fps"], meta["scale"]

    report = {"ep": ep, "video": video, "frames_dir": os.path.abspath(a.frames_dir),
              "ratio": 1.0 if a.all else a.ratio, "focus": focus,
              "match_tol": a.match_tol, "ahash_tol": a.ahash_tol,
              "cues_total": len(cues), "cues_sampled": len(sampled),
              "sample": [], "mismatches": [],
              "pass": 0, "pass_soft": 0, "mismatch": 0, "noframe": 0}
    counts = {"PASS": 0, "PASS_SOFT": 0, "MISMATCH": 0,
              "NOFRAME": 0, "MISSING": 0, "GRAB_FAIL": 0}
    checked = {}  # 库帧 path → 已验结论（跨 cue 去重，同帧不重复抓）

    print(f"=== A4 抽帧比对 EP{ep}：{len(sampled)}/{len(cues)} 条 cue 受验"
          f"（ratio={report['ratio']}，match_tol=±{a.match_tol}s）===")
    for i in sampled:
        t_local = cues[i][1]
        label = cues[i][4]
        hits = [fr for fr in index if abs(fr["sec"] - t_local) <= a.match_tol + 1e-9]
        entry = {"i": i, "label": label, "t_local": t_local, "frames": []}
        if not hits:
            entry["verdict"] = "NOFRAME"
            counts["NOFRAME"] += 1
            report["noframe"] += 1
            report["mismatches"].append({"i": i, "label": label, "t_local": t_local,
                                         "kind": "NOFRAME"})
            print(f"  ❌ CUES[{i}] @{t_local}s {label[:30]}：容差内无库帧")
        for fr in sorted(hits, key=lambda x: x["sec"]):
            if fr["path"] in checked:
                rec = dict(checked[fr["path"]])
            else:
                rec = {"dir": fr["dir"], "file": fr["file"], "sec": fr["sec"]}
                if not os.path.exists(fr["path"]):
                    rec["verdict"] = "MISSING"
                else:
                    gj = os.path.join(tmp, f"grab_{fr['dir'].replace(os.sep, '_')}_{fr['file']}")
                    if not grab_frame(video, fr["sec"], fps, scale, gj, a.ffmpeg):
                        rec["verdict"] = "GRAB_FAIL"
                    else:
                        rec["md5_lib"] = md5_file(fr["path"])
                        rec["md5_grab"] = md5_file(gj)
                        if rec["md5_lib"] == rec["md5_grab"]:
                            rec["verdict"] = "PASS"
                        else:
                            rec["ahash_dist"] = hamming(ahash(fr["path"], a.ffmpeg),
                                                        ahash(gj, a.ffmpeg))
                            rec["verdict"] = ("PASS_SOFT" if rec["ahash_dist"] <= a.ahash_tol
                                              else "MISMATCH")
                checked[fr["path"]] = rec
                counts[rec["verdict"]] += 1
                if rec["verdict"] == "PASS":
                    report["pass"] += 1
                elif rec["verdict"] == "PASS_SOFT":
                    report["pass_soft"] += 1
                else:
                    report["mismatch"] += 1
                    report["mismatches"].append(dict(rec, i=i, label=label,
                                                     t_local=t_local, kind=rec["verdict"]))
                mark = {"PASS": "✓", "PASS_SOFT": "✓(soft)"}.get(rec["verdict"], "❌")
                print(f"  {mark} CUES[{i}] @{t_local}s ↔ {fr['dir']}\\{fr['file']}"
                      f"（标称 {fr['sec']}s）{rec['verdict']}"
                      + (f" aHash距离={rec['ahash_dist']}" if "ahash_dist" in rec else ""))
            entry["frames"].append(rec)
        report["sample"].append(entry)

    bad = counts["MISMATCH"] + counts["NOFRAME"] + counts["MISSING"] + counts["GRAB_FAIL"]
    report["frames_checked"] = len(checked)
    report["result"] = "FAIL" if bad else "PASS"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    if not a.keep_frames:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"受验库帧 {len(checked)} 张：PASS {counts['PASS']} + SOFT {counts['PASS_SOFT']}"
          f" / MISMATCH {counts['MISMATCH']} / NOFRAME {counts['NOFRAME']}"
          f" / MISSING {counts['MISSING']} / GRAB_FAIL {counts['GRAB_FAIL']}")
    print(f"报告：{out}")
    print("RESULT " + json.dumps({"status": report["result"].lower(), "ep": ep,
                                 "cues": len(cues), "sampled": len(sampled),
                                 "frames_checked": len(checked),
                                 "mismatch": counts["MISMATCH"], "noframe": counts["NOFRAME"],
                                 "report": out}, ensure_ascii=False))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
