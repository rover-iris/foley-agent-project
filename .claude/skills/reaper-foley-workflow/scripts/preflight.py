# -*- coding: utf-8 -*-
"""开工自检：环境依赖 → 音源库 DB → REAPER 进程 → reapy 连接 → 工程概况。

用法：
  python preflight.py --config foley_project.json
  python preflight.py --db <asset_library.db> [--host 127.0.0.1] [--port 2308] [--skip-connect]
"""
import argparse
import os
import shutil
import sqlite3
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rfw_common import connect_reaper, enum_regions, load_config, setup_stdout

RESULTS = []


def check(name, fn):
    try:
        detail = fn()
        RESULTS.append((name, True, detail))
    except Exception as e:  # noqa: BLE001
        RESULTS.append((name, False, f"{type(e).__name__}: {e}"))


def check_python():
    v = sys.version_info
    assert v >= (3, 10), f"需要 3.10+，当前 {v.major}.{v.minor}"
    return f"{v.major}.{v.minor}.{v.micro}"


def check_reapy():
    import reapy_boost  # noqa: F401

    return "reapy_boost 可导入"


def check_ffmpeg():
    for tool in ("ffmpeg", "ffprobe"):
        assert shutil.which(tool), f"{tool} 不在 PATH"
    return "ffmpeg/ffprobe 在 PATH"


def make_db_check(db):
    def _db():
        assert db and os.path.exists(db), f"DB 不存在: {db}"
        conn = sqlite3.connect(db)
        try:
            n = conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
            conn.execute("SELECT COUNT(*) FROM assets_fts LIMIT 1")  # FTS5 可用性
            d = conn.execute("SELECT MAX(duration) FROM assets").fetchone()[0]
            return f"{n:,} 条资产，FTS5 正常，最长源 {d:.1f}s"
        finally:
            conn.close()

    return _db


def check_reaper_proc():
    out = subprocess.run(["tasklist"], capture_output=True, text=True, timeout=30).stdout.lower()
    assert "reaper" in out, "REAPER 进程未运行（先启动 REAPER 并开启 Distant API）"
    return "REAPER 进程在运行"


def make_connect_check(host):
    def _conn():
        rb, RPR, p = connect_reaper(host)
        # 坑：GetProjectName 必须三参 (proj, buf, sz)，2 参形式段崩溃
        name = RPR.GetProjectName(0, "", 1024)[1]
        regions = enum_regions(RPR)
        return f"工程 {name!r}，{len(p.tracks)} 轨，{len(regions)} 个 Region"

    return _conn


def main():
    setup_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", help="foley_project.json")
    ap.add_argument("--db", help="音源库 DB 路径（缺省取 config.paths.asset_db）")
    ap.add_argument("--host", default=None)
    ap.add_argument("--skip-connect", action="store_true", help="只查环境不连 REAPER")
    a = ap.parse_args()

    cfg = None
    if a.config:
        cfg = load_config(a.config)
    db = a.db or (cfg and cfg["paths"].get("asset_db")) or ""
    host = a.host or (cfg and cfg["reaper"]["host"]) or "127.0.0.1"

    check("Python ≥3.10", check_python)
    check("reapy_boost", check_reapy)
    check("ffmpeg/ffprobe", check_ffmpeg)
    check("音源库 DB", make_db_check(db))
    if not a.skip_connect:
        check("REAPER 进程", check_reaper_proc)
        check("reapy 连接", make_connect_check(host))

    print("== 开工自检 ==")
    fail = 0
    for name, ok, detail in RESULTS:
        fail += 0 if ok else 1
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    print("\n全部通过，可以开工 ✓" if fail == 0 else f"\n⚠️ {fail} 项未过，先解决再开工")
    if fail and "reapy_boost" in [n for n, ok, _ in RESULTS if not ok]:
        print("提示：pip install reapy_boost")
    if fail and any("REAPER 进程" == n for n, ok, _ in RESULTS if not ok):
        print("提示：启动 REAPER，Preferences → Control/OSC/web → Add → Distant API（默认端口 2308）")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
