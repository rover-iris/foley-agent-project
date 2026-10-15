# -*- coding: utf-8 -*-
"""BGM 产物三项机器门禁（配乐线方案 §4，全离线 ffmpeg/ffprobe 活）。

用法：
  python verify_bgm.py --bgm exports\\配乐\\185_v2m.mp3 --video 185.mp4
  python verify_bgm.py --bgm x.mp3 --video y.mp4 --out cues\\verify_bgm_185.json

门禁（判据量化，阈值均可命令行覆盖）：
  1. 带通频谱：-af highpass=f=2000 + volumedetect，取 mean_volume（>2kHz 频段
     全程 RMS）；< --hf-thresh（默认 −55dB）判翻车 = FAIL（唯一 FAIL 项，
     对应方案 §3 第 3 条「原路线重抽一次并留痕」）；
  2. 时长对齐：ffprobe 产物时长 vs ffprobe 视频时长，|差| > --dur-tol
     （默认 1.0s）→ 标注（WARN，贴轨按画面点裁切收齐）；
  3. 静音/削波：silencedetect（noise 默认 −50dB、最短段 --silence-min-dur
     默认 0.5s）静音段总长占全曲 > --silence-ratio（默认 0.30）→ 标注；
     全带 volumedetect max_volume ≥ --clip-db（默认 0.0dB，满幅）→ 标注削波。
     标注一律进「待制作人听」清单，不判 FAIL。

产物：门禁报告 JSON（--out 指定，缺省 <bgm 同目录>/verify_bgm_<stem>.json），
末行 RESULT JSON 机器可读；exit 0=PASS（含标注），1=FAIL（翻车或测量失败——
无法验证 = 不过）。
"""
import argparse
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rfw_common import setup_stdout

HF_FREQ = 2000       # 门禁 1 高通截止（Hz）
HF_THRESH = -55.0    # 门禁 1 翻车阈值（dB，mean_volume 低于此值判翻车）
DUR_TOL = 1.0        # 门禁 2 时长容差（秒）
SIL_NOISE = -50.0    # 门禁 3 静音判定门限（dB）
SIL_MINDUR = 0.5     # 门禁 3 最短静音段（秒）
SIL_RATIO = 0.30     # 门禁 3 静音占比阈值
CLIP_DB = 0.0        # 门禁 3 削波判定（max_volume 满幅 dB）


def ffprobe_duration(path, ffprobe="ffprobe"):
    """ffprobe format.duration，返回 float 秒或 None（解析失败）。"""
    r = subprocess.run([ffprobe, "-v", "error", "-print_format", "json",
                        "-show_format", path], capture_output=True, text=True,
                       errors="replace")
    if r.returncode:
        return None
    try:
        return float(json.loads(r.stdout)["format"]["duration"])
    except (ValueError, KeyError, TypeError):
        return None


def volumedetect(path, ffmpeg="ffmpeg", pre_af=""):
    """volumedetect 全曲电平，返回 (mean_volume, max_volume) dB 或 (None, None)。

    pre_af 前置滤镜（如 highpass=f=2000），与 volumedetect 串成一条 -af 链。
    """
    af = (pre_af + "," if pre_af else "") + "volumedetect"
    r = subprocess.run([ffmpeg, "-hide_banner", "-i", path,
                        "-af", af, "-f", "null", "-"],
                       capture_output=True, text=True, errors="replace")
    err = r.stderr or ""
    mm = re.search(r"mean_volume:\s*(-?[\d.]+)\s*dB", err)
    mx = re.search(r"max_volume:\s*(-?[\d.]+)\s*dB", err)
    return (float(mm.group(1)) if mm else None,
            float(mx.group(1)) if mx else None)


def silencedetect(path, total, noise, min_dur, ffmpeg="ffmpeg"):
    """silencedetect 全曲静音段，返回 (静音段列表, 静音总时长秒)。

    曲尾开口静音（有 start 无 end）按总长截齐。
    """
    r = subprocess.run([ffmpeg, "-hide_banner", "-i", path,
                        "-af", f"silencedetect=noise={noise}dB:d={min_dur}",
                        "-f", "null", "-"],
                       capture_output=True, text=True, errors="replace")
    segs, open_start = [], None
    for line in (r.stderr or "").splitlines():
        m = re.search(r"silence_start:\s*([\d.]+)", line)
        if m:
            open_start = float(m.group(1))
            continue
        m = re.search(r"silence_end:\s*([\d.]+)\s*\|\s*silence_duration:\s*([\d.]+)",
                      line)
        if m and open_start is not None:
            segs.append({"start": open_start, "end": float(m.group(1)),
                         "dur": float(m.group(2))})
            open_start = None
    if open_start is not None and total:
        segs.append({"start": open_start, "end": total,
                     "dur": max(0.0, total - open_start)})
    return segs, sum(s["dur"] for s in segs)


def main():
    setup_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--bgm", required=True, help="BGM 产物音频（mp3/wav）")
    ap.add_argument("--video", required=True, help="对照视频（时长基准）")
    ap.add_argument("--out", help="门禁报告 JSON 路径（缺省 <bgm 同目录>/verify_bgm_<stem>.json）")
    ap.add_argument("--hf-thresh", type=float, default=HF_THRESH, help="门禁1 翻车阈值 dB")
    ap.add_argument("--dur-tol", type=float, default=DUR_TOL, help="门禁2 时长容差秒")
    ap.add_argument("--silence-noise", type=float, default=SIL_NOISE, help="门禁3 静音门限 dB")
    ap.add_argument("--silence-min-dur", type=float, default=SIL_MINDUR, help="门禁3 最短静音段秒")
    ap.add_argument("--silence-ratio", type=float, default=SIL_RATIO, help="门禁3 静音占比阈值")
    ap.add_argument("--clip-db", type=float, default=CLIP_DB, help="门禁3 削波判定 dB")
    ap.add_argument("--ffmpeg", default="ffmpeg")
    ap.add_argument("--ffprobe", default="ffprobe")
    a = ap.parse_args()

    for p, tag in ((a.bgm, "BGM 产物"), (a.video, "对照视频")):
        if not os.path.exists(p):
            raise SystemExit(f"{tag}不存在: {p}")
    out = a.out or os.path.join(os.path.dirname(os.path.abspath(a.bgm)),
                                f"verify_bgm_{os.path.splitext(os.path.basename(a.bgm))[0]}.json")

    warnings, errors = [], []
    report = {"bgm": os.path.abspath(a.bgm), "video": os.path.abspath(a.video),
              "thresholds": {"hf_freq": HF_FREQ, "hf_thresh_db": a.hf_thresh,
                             "dur_tol_s": a.dur_tol,
                             "silence_noise_db": a.silence_noise,
                             "silence_min_dur_s": a.silence_min_dur,
                             "silence_ratio": a.silence_ratio,
                             "clip_db": a.clip_db},
              "gates": {}, "warnings": warnings, "errors": errors}

    print(f"=== BGM 三项门禁：{os.path.basename(a.bgm)} ↔ {os.path.basename(a.video)} ===")

    # 门禁 1：带通频谱 >2kHz RMS
    hf_mean, _ = volumedetect(a.bgm, a.ffmpeg, pre_af=f"highpass=f={HF_FREQ}")
    g1 = {"filter": f"highpass=f={HF_FREQ}", "rms_db": hf_mean,
          "threshold_db": a.hf_thresh}
    if hf_mean is None:
        g1["verdict"] = "FAIL"
        errors.append("门禁1 测量失败（volumedetect 无输出）")
    elif hf_mean < a.hf_thresh:
        g1["verdict"] = "FAIL"   # 翻车：>2kHz 频段整体哑掉，判生成失败
    else:
        g1["verdict"] = "PASS"
    report["gates"]["band_spectrum"] = g1
    print(f"  ① 带通频谱 >{HF_FREQ}Hz RMS = "
          f"{hf_mean if hf_mean is not None else '测量失败'} dB"
          f"（阈值 {a.hf_thresh} dB）→ {g1['verdict']}")

    # 门禁 2：时长对齐
    bgm_dur = ffprobe_duration(a.bgm, a.ffprobe)
    vid_dur = ffprobe_duration(a.video, a.ffprobe)
    diff = abs(bgm_dur - vid_dur) if (bgm_dur is not None and vid_dur is not None) else None
    g2 = {"bgm_s": bgm_dur, "video_s": vid_dur, "diff_s": diff, "tol_s": a.dur_tol}
    if diff is None:
        g2["verdict"] = "WARN"
        errors.append("门禁2 时长测量失败（ffprobe 无 duration）")
    elif diff > a.dur_tol:
        g2["verdict"] = "WARN"
        warnings.append(f"时长越界：产物 {bgm_dur:.2f}s vs 视频 {vid_dur:.2f}s"
                        f"（差 {diff:.2f}s > ±{a.dur_tol}s），贴轨按画面点裁切收齐")
    else:
        g2["verdict"] = "PASS"
    report["gates"]["duration_align"] = g2
    print(f"  ② 时长：产物 {bgm_dur}s vs 视频 {vid_dur}s"
          f"（差 {diff if diff is None else round(diff, 3)}s，容差 ±{a.dur_tol}s）→ {g2['verdict']}")

    # 门禁 3：静音占比 + 削波
    _, full_max = volumedetect(a.bgm, a.ffmpeg)
    segs, sil_total = silencedetect(a.bgm, bgm_dur, a.silence_noise,
                                    a.silence_min_dur, a.ffmpeg)
    ratio = (sil_total / bgm_dur) if bgm_dur else None
    g3 = {"silence_segments": segs, "silence_total_s": round(sil_total, 3),
          "silence_ratio": (round(ratio, 4) if ratio is not None else None),
          "ratio_thresh": a.silence_ratio, "max_volume_db": full_max,
          "clip_thresh_db": a.clip_db}
    if ratio is None:
        g3["silence_verdict"] = "WARN"
        errors.append("门禁3 静音占比无法计算（缺产物时长）")
    elif ratio > a.silence_ratio:
        g3["silence_verdict"] = "WARN"
        warnings.append(f"全曲静音段 {ratio:.1%} > {a.silence_ratio:.0%}，进待制作人听")
    else:
        g3["silence_verdict"] = "PASS"
    if full_max is None:
        g3["clip_verdict"] = "WARN"
        errors.append("门禁3 削波测量失败（volumedetect 无输出）")
    elif full_max >= a.clip_db:
        g3["clip_verdict"] = "WARN"
        warnings.append(f"检出削波：max_volume {full_max} dB ≥ {a.clip_db} dB，进待制作人听")
    else:
        g3["clip_verdict"] = "PASS"
    report["gates"]["silence_clip"] = g3
    print(f"  ③ 静音 {len(segs)} 段共 {sil_total:.2f}s"
          f"（占比 {f'{ratio:.1%}' if ratio is not None else '未知'}，"
          f"阈值 {a.silence_ratio:.0%}）→ {g3['silence_verdict']}；"
          f"max_volume {full_max} dB（削波线 {a.clip_db} dB）→ {g3['clip_verdict']}")

    # 汇总：仅门禁 1 判 FAIL；测量失败（无法验证）同样 FAIL
    fail = g1["verdict"] == "FAIL" or bool(errors)
    report["result"] = "FAIL" if fail else "PASS"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)

    print(f"标注 {len(warnings)} 条 / 测量异常 {len(errors)} 条；报告：{out}")
    print("RESULT " + json.dumps({"status": report["result"].lower(),
                                 "band_rms_db": hf_mean,
                                 "dur_diff_s": diff,
                                 "silence_ratio": (round(ratio, 4) if ratio is not None else None),
                                 "max_volume_db": full_max,
                                 "warnings": len(warnings),
                                 "report": out}, ensure_ascii=False))
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
