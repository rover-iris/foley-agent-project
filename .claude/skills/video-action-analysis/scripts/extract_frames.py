#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逐秒抽帧 + 生成帧↔秒 manifest（video-action-analysis 第一步）。

背景（2026-09 权臣CP翻车）：s_%03d.jpg 从 1 起编号、秒从 0 起，
s_059 实际是 t=58s，两版报告全按「s_N = 第N秒」引用，cue 点整体 +1s。
本脚本强制产出 manifest.json 作为秒↔帧的唯一权威映射，报告一律以秒为准。

2026-09-11 修订（权臣CP 54-103 集批量实证，15 集主库级错位）：
旧实现用 ffmpeg fps 滤镜就近取帧（round=near），30/24fps 源下帧内容
偏移 +0.4~0.5s 起步，部分视频槽位互换/乱序达数秒级。现改为：
  1. 帧号系精确取帧：select='eq(n,K)'，K=round(t*源帧率)，数学上精确；
  2. showinfo 回读每帧真实 pts_time 写入 manifest（自校验，对不上即 FAIL
     拒出 manifest，不带病交付）；
  3. 补齐尾秒帧（时长非整数时最后一秒也有帧）；
  4. 加密窗口改用 between(t,a,b) 全解过滤，不再用 -ss（-ss 有 GOP 偏早陷阱）。

用法:
  python extract_frames.py <video> <outdir>                  # 1fps 全量（正式报告必须）
  python extract_frames.py <video> <outdir> --fps 3 --start 55 --end 62   # 快速动作段加密
产物:
  outdir/s_%03d.jpg + outdir/manifest.json

2026-09-21 修订（制作人条款）：--scale 默认 600→480。识图目标是人物动作与
明显需配音的事物，不依赖高清细节；帧分辨率直接决定视觉判读的 token 消耗，
480p 足够判读与帧证据核验。需要更细证据帧时临时 --scale 调高。
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


def ffprobe_json(video: Path, args: list) -> dict:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", *args, str(video)],
        capture_output=True, text=True,
    )
    return json.loads(r.stdout or "{}")


def probe_duration(video: Path) -> float:
    info = ffprobe_json(video, ["-show_format"])
    return float(info["format"]["duration"])


def probe_video(video: Path) -> tuple:
    """返回 (源帧率fps: float, 总帧数: int)。总帧数缺失时按时长×帧率估算。"""
    info = ffprobe_json(video, ["-select_streams", "v:0", "-show_streams"])
    st = (info.get("streams") or [{}])[0]
    num, _, den = st.get("r_frame_rate", "25/1").partition("/")
    fps = float(num) / float(den or 1)
    nb = int(st.get("nb_frames") or 0)
    if nb <= 0:
        nb = int(probe_duration(video) * fps)
    return fps, nb


def build_targets(src_fps: float, total: int, dur: float,
                  fps: float, start: float, end: float) -> list:
    """目标时刻列表 t_k → 帧号列表 [(k, n_k, t_k)]。尾秒帧自动纳入。"""
    if fps == 1.0:
        ts = [float(k) for k in range(int(dur - 0.05) + 1)]
    else:
        ts, k = [], 0
        while True:
            t = round(start + k / fps, 6)
            if t >= end - 1e-6 or t > dur:
                break
            ts.append(t)
            k += 1
    out, seen = [], set()
    for t in ts:
        n = round(t * src_fps)
        if n >= total or n in seen:
            continue
        seen.add(n)
        out.append((n, t))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("outdir")
    ap.add_argument("--fps", type=float, default=1.0,
                    help="1=全量逐秒（正式报告必须）；快速动作段用 2~4 加密确认起手帧")
    ap.add_argument("--scale", type=int, default=480,
                    help="输出帧高（宽自适应）；默认 480：识图只需动作与明显事物，低分辨率省视觉 token（20260921）")
    ap.add_argument("--start", type=float, default=0.0, help="窗口起点（秒），加密扫描用")
    ap.add_argument("--end", type=float, default=0.0, help="窗口终点（秒），0=到片尾")
    a = ap.parse_args()

    video, outdir = Path(a.video), Path(a.outdir)
    if not video.exists():
        sys.exit(f"[FAIL] 视频不存在: {video}")
    outdir.mkdir(parents=True, exist_ok=True)

    # 双轮复核时长：瞬时读取失败会表现为两轮不一致（权臣CP第28集曾误报 62.2s，实为 37.0s）
    d1, d2 = probe_duration(video), probe_duration(video)
    if abs(d1 - d2) > 0.2:
        sys.exit(f"[FAIL] 两轮 ffprobe 时长不一致: {d1} vs {d2}，请重试或人工核验")
    dur = round(d1, 3)
    end = a.end if a.end > 0 else dur

    src_fps, total = probe_video(video)
    targets = build_targets(src_fps, total, dur, a.fps, a.start, end)
    if not targets:
        sys.exit("[FAIL] 目标帧列表为空，请检查窗口参数")

    # 帧号系精确取帧：不用 -ss（GOP 偏早陷阱），加密窗口用 between(t) 全解过滤
    sel = "between(t,%s,%s)*(" % (a.start, end) + "+".join(
        f"eq(n,{n})" for n, _ in targets) + ")"
    cmd = ["ffmpeg", "-y", "-v", "info", "-i", str(video),
           "-vf", f"select='{sel}',showinfo,scale=-2:{a.scale}",
           "-fps_mode", "passthrough", "-q:v", "5",
           str(outdir / "s_%03d.jpg")]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        if "fps_mode" in (r.stderr or ""):  # 兼容旧版 ffmpeg
            cmd[cmd.index("-fps_mode")] = "-vsync"
            cmd[cmd.index("passthrough")] = "0"
            r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode:
            sys.exit(f"[FAIL] ffmpeg 抽帧失败: {(r.stderr or '')[-400:]}")

    # 自校验：从 showinfo 回读每帧真实 pts_time，与目标时刻比对
    pts = [float(m) for m in re.findall(r"pts_time:([\d.]+)", r.stderr)]
    files = sorted(outdir.glob("s_*.jpg"))
    if len(files) != len(targets) or len(pts) != len(targets):
        sys.exit(f"[FAIL] 帧数不符：产出 {len(files)} 张 / showinfo {len(pts)} 条 / "
                 f"目标 {len(targets)} 个，拒绝出 manifest（不带病交付）")
    tol = 0.5 / src_fps
    bad = [(f.name, t, p) for f, (_, t), p in zip(files, targets, pts)
           if abs(p - t) > tol]
    if bad:
        sys.exit(f"[FAIL] {len(bad)} 帧时间戳自校验不过（容差 {tol:.3f}s），"
                 f"首例如 {bad[0]}，拒绝出 manifest")

    # 权威映射：sec 用 showinfo 实测 pts_time，不靠推算
    frames = [{"sec": round(p, 3), "file": f.name}
              for f, p in zip(files, pts)]
    manifest = {
        "video": str(video),
        "duration": dur,
        "source_fps": src_fps,
        "fps": a.fps,
        "scale": a.scale,
        "window": [a.start, end],
        "builder": (f"select eq(n) 帧号系精确取帧（2026-09-11 修订版），showinfo pts_time "
                    f"回读自校验 {len(frames)}/{len(frames)} 通过，容差 {tol:.3f}s"),
        "frames": frames,
    }
    (outdir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[OK] {len(frames)} 帧 → {outdir}（时长 {dur:.2f}s，源 {src_fps:g}fps）；"
          f"showinfo 自校验全过；manifest.json 已写入，秒↔帧以此为准"
          f"（s_001 = t={frames[0]['sec']}s）")


if __name__ == "__main__":
    main()
