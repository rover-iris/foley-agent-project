# -*- coding: utf-8 -*-
"""分轨导出（定稿方案：逐轨 solo + 41824 渲染 master mix 循环）。

流程：备份推子状态 → 全部归零 → 逐轨 solo 渲染（写满+稳定判定，短了自动重试）
     → 恢复现场 + diff 校验 → 可选 ffmpeg volumedetect 电平终验（静音 = 渲染期被碰过，原样重渲即愈）。

用法：
  python export_stems.py --config foley_project.json --out <导出目录>
  python export_stems.py --db x.db --tracks 14:29 --out 目录 [--verify] [--host 127.0.0.1]

🔴 渲染全程不许碰 REAPER（手动暂停会污染首遍渲染：文件正常但整条静音）。
"""
import argparse
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rfw_common import connect_reaper, load_config, setup_stdout, track_range

WAV24_FORMAT_B64 = "ZXZhdxgAAAA="  # 48k/24bit wav 配置串（换格式先 2s 试条 + ffprobe 验证）
BYTES_PER_SEC = 48000 * 3 * 2      # 24bit 立体声 ≈ 288000 B/s
ATTEMPTS = 3


def fader_states(RPR, p):
    states = []
    for i, t in enumerate(p.tracks):
        r = RPR.GetTrackUIVolPan(t.id, 0.0, 0.0)
        states.append({"i": i, "vol": float(r[2]),
                       "mute": RPR.GetMediaTrackInfo_Value(t.id, "B_MUTE"),
                       "solo": RPR.GetMediaTrackInfo_Value(t.id, "I_SOLO")})
    return states


def restore(RPR, p, states):
    diff = 0
    for s in states:
        t = p.tracks[s["i"]]
        RPR.SetMediaTrackInfo_Value(t.id, "D_VOL", s["vol"])
        RPR.SetMediaTrackInfo_Value(t.id, "B_MUTE", s["mute"])
        RPR.SetMediaTrackInfo_Value(t.id, "I_SOLO", s["solo"])
        RPR.SetMediaTrackInfo_Value(t.id, "I_SELECTED", 0)
    for s in states:
        r = RPR.GetTrackUIVolPan(p.tracks[s["i"]].id, 0.0, 0.0)
        if abs(float(r[2]) - s["vol"]) > 0.0005:
            diff += 1
    return diff


def render_and_wait(RPR, fp, expect, timeout=600):
    """触发渲染并轮询文件写满+稳定。返回 (final_size, ok)。"""
    RPR.Main_OnCommand(41824, 0)  # 同步渲染（42230 是异步，循环里会秒退）
    stable, last, t0 = 0, -1, time.time()
    while time.time() - t0 < timeout:
        time.sleep(4)
        sz = os.path.getsize(fp) if os.path.exists(fp) else 0
        if sz >= expect:
            return sz, True
        if sz == last and sz > 0:
            stable += 1
            if stable >= 2:
                return sz, False  # 稳定但没写满 → 短渲染
        else:
            stable = 0
        last = sz
    return (os.path.getsize(fp) if os.path.exists(fp) else 0), False


def max_volume(fp):
    r = subprocess.run(["ffmpeg", "-i", fp, "-af", "volumedetect", "-f", "null", "-"],
                       capture_output=True, text=True, timeout=600)
    for line in r.stderr.splitlines():
        if "max_volume" in line:
            return float(line.split("max_volume:")[1].replace("dB", "").strip())
    return None


def main():
    setup_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--config")
    ap.add_argument("--db", help="仅用于提示；导出不依赖 DB")
    ap.add_argument("--out", required=True, help="导出目录")
    ap.add_argument("--tracks", help="如 14:29；缺省取 config.tracks.action_children")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--verify", action="store_true", help="导完逐条 volumedetect 终验（需 ffmpeg）")
    ap.add_argument("--rerender-silent", action="store_true", help="终验发现静音时自动原样重渲一轮")
    a = ap.parse_args()

    cfg = load_config(a.config) if a.config else None
    lo, hi = track_range(cfg, a.tracks)
    os.makedirs(a.out, exist_ok=True)
    rb, RPR, p = connect_reaper(a.host)

    # 1. 备份 + 2. 归零
    backup_fp = os.path.join(a.out, f"fader_backup_{time.strftime('%Y%m%d_%H%M%S')}.json")
    states = fader_states(RPR, p)
    with open(backup_fp, "w", encoding="utf-8") as f:
        json.dump(states, f, ensure_ascii=False, indent=1)
    print(f"推子状态已备份 → {backup_fp}（{len(states)} 轨）")
    for t in p.tracks:
        RPR.SetMediaTrackInfo_Value(t.id, "D_VOL", 1.0)
        RPR.SetMediaTrackInfo_Value(t.id, "B_MUTE", 0)
        RPR.SetMediaTrackInfo_Value(t.id, "I_SOLO", 0)

    # 3. 公共渲染配置
    proj_len = RPR.GetProjectLength(0)
    expect = proj_len * BYTES_PER_SEC * 0.98
    RPR.GetSetProjectInfo_String(0, "RENDER_FORMAT", WAV24_FORMAT_B64, True)
    RPR.GetSetProjectInfo(0, "RENDER_SETTINGS", 0, True)   # master mix；stems 模式实测输出全静音，勿用
    RPR.GetSetProjectInfo(0, "RENDER_BOUNDSFLAG", 1, True)
    RPR.GetSetProjectInfo(0, "RENDER_SRATE", 48000, True)
    RPR.GetSetProjectInfo(0, "RENDER_CHANNELS", 2, True)
    RPR.GetSetProjectInfo(0, "RENDER_TAILFLAG", 0, True)
    RPR.GetSetProjectInfo(0, "RENDER_DITHER", 0, True)
    RPR.GetSetProjectInfo(0, "RENDER_ADDTOPROJ", 0, True)
    RPR.GetSetProjectInfo(0, "RENDER_NORMALIZE", 0, True)
    RPR.GetSetProjectInfo(0, "RENDER_RESAMPLE", 3, True)
    RPR.GetSetProjectInfo_String(0, "RENDER_FILE", a.out, True)
    print(f"工程时长 {proj_len:.2f}s，预期单条 ≥ {expect/1e6:.0f}MB")

    # 4/5. 逐轨 solo 渲染循环
    results = []
    for i in range(lo, hi):
        t = p.tracks[i]
        try:
            name = t.name
        except Exception:
            name = f"track{i}"
        fp = os.path.join(a.out, name + ".wav")
        log, ok, sz = [], False, 0
        for attempt in range(1, ATTEMPTS + 1):
            for j, tt in enumerate(p.tracks):
                RPR.SetMediaTrackInfo_Value(tt.id, "I_SOLO", 1 if j == i else 0)
            RPR.GetSetProjectInfo_String(0, "RENDER_PATTERN", name, True)
            try:
                os.remove(fp)
            except Exception:
                pass  # 沙箱可能拦删除；REAPER 会覆盖重写
            time.sleep(0.5)
            sz, ok = render_and_wait(RPR, fp, expect)
            log.append(f"#{attempt}={sz/1e6:.0f}MB{'✓' if ok else '✗'}")
            if ok:
                break
            time.sleep(1.5)
        RPR.SetMediaTrackInfo_Value(t.id, "I_SOLO", 0)
        results.append((i, name, fp, ok, "|".join(log)))
        print(f"[{i}] {name}: {'✓' if ok else '✗'} {sz/1e6:.0f}MB  ({'|'.join(log)})", flush=True)

    # 6. 恢复现场
    diff = restore(RPR, p, states)
    ok_n = sum(1 for r in results if r[3])
    print(f"\n渲染完成 {ok_n}/{hi - lo} 轨；推子恢复差异 {diff}")

    # 电平终验
    if a.verify:
        print("\n== volumedetect 终验 ==")
        silent = []
        for i, name, fp, ok, _ in results:
            if not ok:
                continue
            mv = max_volume(fp)
            status = "✓" if (mv is not None and mv > -60) else "⚠️静音"
            if status != "✓":
                silent.append((i, name, fp))
            print(f"  [{i}] {name}: max {mv}dB {status}", flush=True)
        if silent and a.rerender_silent:
            print(f"\n原样重渲 {len(silent)} 条静音轨（重渲即愈）...")
            for i, name, fp in silent:
                for attempt in range(1, ATTEMPTS + 1):
                    for j, tt in enumerate(p.tracks):
                        RPR.SetMediaTrackInfo_Value(tt.id, "I_SOLO", 1 if j == i else 0)
                    RPR.GetSetProjectInfo_String(0, "RENDER_PATTERN", name, True)
                    sz, ok = render_and_wait(RPR, fp, expect)
                    if ok:
                        break
                RPR.SetMediaTrackInfo_Value(p.tracks[i].id, "I_SOLO", 0)
                mv = max_volume(fp)
                print(f"  [{i}] {name}: 重渲后 max {mv}dB", flush=True)
            restore(RPR, p, states)
        elif silent:
            print("⚠️ 存在静音轨：渲染期间可能被碰过 → 原样重跑本脚本（加 --rerender-silent）即愈")

    failed = [r[1] for r in results if not r[3]]
    print("\n验收：ffprobe 验 pcm_s24le/48000 + 数量 + 时长一致（见 references/stem-export.md）")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
