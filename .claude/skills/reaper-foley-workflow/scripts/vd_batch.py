# -*- coding: utf-8 -*-
"""vd_batch.py —— volumedetect 批处理 + 跨批缓存（提速实施案 P0-2）。

用途：把逐窗串行的 ffmpeg volumedetect 取证改为本地并发 + 结果缓存。
ffmpeg 是本地 CPU 活，不占模型路数；缓存 key 含源文件 mtime+size，源变更即失效。

用法（库模式，供 prepare_ep.py 等调用）：
  from vd_batch import vd_batch
  results = vd_batch([{"path": p, "off": 2.0, "len": 1.5}, ...], ffmpeg="ffmpeg")
  # 返回 [{"path","off","len","max_volume","cached"} ...]，顺序与输入一致

用法（CLI，供窗口扫描等即席脚本）：
  python vd_batch.py jobs.json [--out results.json] [--workers 8] [--ffmpeg ffmpeg]
  jobs.json: [{"path": "...", "off": 0, "len": 1.5}, ...]
  末行 RESULT JSON 机器可读：{"windows": N, "hits": H, "misses": M}

缓存：~/.claude/foley_cache/vd/<sha1>.json（key=源路径+mtime+size+off+len）。
回退：调用方传 --serial 或 workers=1 即退化为串行（旧行为）。
"""
import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import subprocess
import sys

VD_LIMIT = -30.0  # 铁律 10：窗口 max_volume 阈值（dB），与 prepare_ep.py 一致
CACHE_DIR = os.path.expanduser(os.path.join("~", ".claude", "foley_cache", "vd"))


def _cache_key(path, off, ln):
    st = os.stat(path)
    raw = "%s|%s|%s|%s|%s" % (os.path.abspath(path), st.st_mtime, st.st_size, off, ln)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _cache_read(path, off, ln):
    try:
        fp = os.path.join(CACHE_DIR, _cache_key(path, off, ln) + ".json")
        with open(fp, "r", encoding="utf-8") as f:
            return json.load(f).get("max_volume")
    except Exception:
        return None


def _cache_write(path, off, ln, mv):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        fp = os.path.join(CACHE_DIR, _cache_key(path, off, ln) + ".json")
        with open(fp, "w", encoding="utf-8") as f:
            json.dump({"path": os.path.abspath(path), "off": off, "len": ln,
                       "max_volume": mv}, f, ensure_ascii=False)
    except Exception:
        pass  # 缓存写失败不阻塞取证


def _run_one(path, off, ln, ffmpeg):
    cmd = [ffmpeg, "-hide_banner", "-ss", str(off), "-t", str(ln),
           "-i", path, "-af", "volumedetect", "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")  # [X-05] 容错
    m = re.search(r"max_volume:\s*(-?[\d.]+)\s*dB", r.stderr or "")
    return float(m.group(1)) if m else None


def vd_batch(jobs, ffmpeg="ffmpeg", workers=8):
    """并发 + 缓存跑 volumedetect。jobs: [{"path","off","len"}...]，返回同序结果列表。"""
    results = [None] * len(jobs)
    todo = []
    for i, j in enumerate(jobs):
        mv = _cache_read(j["path"], j["off"], j["len"])
        if mv is not None:
            results[i] = {"path": j["path"], "off": j["off"], "len": j["len"],
                          "max_volume": mv, "cached": True}
        else:
            todo.append(i)
    if todo:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(workers, len(todo))) as ex:
            futs = {ex.submit(_run_one, jobs[i]["path"], jobs[i]["off"], jobs[i]["len"], ffmpeg): i
                    for i in todo}
            for fut in concurrent.futures.as_completed(futs):
                i = futs[fut]
                mv = fut.result()
                _cache_write(jobs[i]["path"], jobs[i]["off"], jobs[i]["len"], mv)
                results[i] = {"path": jobs[i]["path"], "off": jobs[i]["off"],
                              "len": jobs[i]["len"], "max_volume": mv, "cached": False}
    return results


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("jobs", help="窗口任务 JSON：[{path,off,len}...]")
    ap.add_argument("--out", help="结果 JSON 输出路径（缺省打印到 stdout）")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--ffmpeg", default="ffmpeg")
    a = ap.parse_args()
    with open(a.jobs, "r", encoding="utf-8") as f:
        jobs = json.load(f)
    results = vd_batch(jobs, ffmpeg=a.ffmpeg, workers=a.workers)
    hits = sum(1 for r in results if r["cached"])
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
    else:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    print("RESULT " + json.dumps({"windows": len(results), "hits": hits,
                                  "misses": len(results) - hits}, ensure_ascii=False))


if __name__ == "__main__":
    main()
