# -*- coding: utf-8 -*-
"""reaper-foley-workflow 脚本公共层：stdout、项目配置、REAPER 连接、音源库 DB。

本工具集不内置任何机器特定路径；一切路径来自 --config 的 foley_project.json 或命令行参数。
"""
import json
import os
import sqlite3
import sys
import time

# ===== 连接层加固（2026-09-09 实测，详见 Desktop\REAPER-reapy交接_20260909.md）=====
# 坑1：本机代理 HTTP_PROXY 会劫持 urllib 对 127.0.0.1:2308/2309 的请求——
#      必须在 import reapy_boost 之前清掉（machines 在 import 时就自动 connect）
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_k, None)
os.environ["NO_PROXY"] = "127.0.0.1,localhost"
os.environ["no_proxy"] = "127.0.0.1,localhost"

# 坑2：Python 3.13 + reapy_boost 0.10.201 的 http.client.parse_headers 递归解析会爆栈，换迭代实现
sys.setrecursionlimit(20000)
import http.client
def _parse_headers_iter(fp, _cls=http.client.HTTPMessage):
    msg = _cls()
    while True:
        line = fp.readline(65536)
        if not line or line in (b"\r\n", b"\n"):
            break
        if b":" in line:
            k, v = line.split(b":", 1)
            try:
                msg[k.decode("latin1").strip()] = v.decode("latin1").strip()
            except Exception:
                pass
    return msg
http.client.parse_headers = _parse_headers_iter

CONFIG_NAME = "foley_project.json"

CONFIG_TEMPLATE = {
    "project": "",
    "reaper": {"host": "127.0.0.1", "port": 2308},
    "paths": {
        "project_rpp": "",
        "template_rpp": "",
        "asset_db": "",
        "reports_dir": "",
        "export_dir": "",
        "work_dir": "",
    },
    "library": {"path_prefix": "", "note": "音源实体根目录前缀；与 DB path 前缀一致则可直接用"},
    "tracks": {"action_children": [14, 29], "note": "动作组子轨索引区间 [起, 止)，以实测轨序为准",
               "layout_note": ""},
    "region_csv": "",
}


def setup_stdout():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def load_config(path):
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    missing = [k for k in ("reaper", "paths") if k not in cfg]
    if missing:
        raise SystemExit(f"配置缺少字段: {missing}（用 project_config.py init 生成）")
    return cfg


def cfg_path(cfg, key):
    return cfg.get("paths", {}).get(key) or ""


def track_range(cfg, override=None):
    spec = override or cfg.get("tracks", {}).get("action_children")
    if isinstance(spec, str):
        a, b = spec.split(":")
        spec = [int(a), int(b)]
    if not spec or len(spec) != 2:
        raise SystemExit("动作组子轨区间未配置（foley_project.json tracks.action_children 或 --tracks 起:止）")
    return int(spec[0]), int(spec[1])


def connect_reaper(host="127.0.0.1", port=None):
    """连接本机 REAPER，返回 (reapy_boost, RPR, project)。已含连接层加固与空壳断言。

    坑（2026-09-09 实测）：connect 不抛异常 ≠ 连上了——import 时自动 connect 失败会被
    静默吞掉（DisabledDistAPIError 只发 warning），reascript_api 是空壳。连接后必须
    断言真函数存在，空壳就报错退出，绝不带病运行。
    """
    from ipaddress import IPv4Address

    import reapy_boost
    from reapy_boost.tools.network.machines import Host

    reapy_boost.connect(Host(IPv4Address(host)))
    RPR = reapy_boost.reascript_api
    if not hasattr(RPR, "CountTracks"):
        raise SystemExit("reapy_boost reascript_api 是空壳（连接静默失败）——"
                         "查 references/pitfalls.md「连接层/环境陷阱」：REAPER 是否运行且无模态弹窗、"
                         "代理是否清理、2309 HTTP 是否 200")
    project = reapy_boost.Project()
    time.sleep(1)  # 连接后立刻调用会偶发失败
    return reapy_boost, RPR, project


def pointer_ok(obj):
    """reapy 指针有效性：取 0x 后十六进制判非零（不能用 '0x0' in str，合法指针也含 0x0）。"""
    import re

    if not obj:
        return False
    m = re.search(r"0x([0-9A-Fa-f]+)", str(obj))
    return bool(m) and int(m.group(1), 16) != 0


def open_asset_db(path):
    if not path:
        raise SystemExit("音源库 DB 路径未提供（--db 或 --config 的 paths.asset_db）")
    return sqlite3.connect(path)


def q(db, name):
    """按名取音源真实路径：精确名优先，前缀兜底，取不到报错（绝不硬编码绝对路径）。"""
    r = db.execute("SELECT path FROM assets WHERE name=? LIMIT 1", (name,)).fetchone()
    if r:
        return r[0]
    r = db.execute("SELECT path FROM assets WHERE name LIKE ? LIMIT 1", (name + "%",)).fetchone()
    if not r:
        raise RuntimeError(f"库里没有: {name}")
    return r[0]


def duration_map(db):
    """filename(lower) -> 最大时长（同名异目录取最大兜底）。"""
    out = {}
    for path, d in db.execute("SELECT path, duration FROM assets"):
        fn = path.replace("\\", "/").rsplit("/", 1)[-1].lower()
        out[fn] = max(out.get(fn, 0.0), d or 0.0)
    return out


def add_project_marker_region(RPR, pos, end, name):
    """建 Region（isrg=True，wantidx=-1 追加），返回 marker id。"""
    return RPR.AddProjectMarker(0, True, pos, end, str(name), -1)


def enum_regions(RPR):
    """实测枚举全部 Region：[(集号或None, base, endpos)]。

    坑（2026-09-09 本机 reapy_boost 实测）：
    - CountProjectMarkers 单参会段崩溃；必须三参 (0,0,0)，返回元组只有 retval 是真总数；
    - EnumProjectMarkers2 必崩，禁用；
    - EnumProjectMarkers 全参形式可用，但 name 出参不被回写（拿不到 Region 名=集号），
      在线只能返回集号 None；集号的权威来源是离线解析 RPP（extract_regions.py）。
    """
    out = []
    total = RPR.CountProjectMarkers(0, 0, 0)[0]
    for i in range(total):
        r = RPR.EnumProjectMarkers(i, 0, 0.0, 0.0, "", 0)
        # 原始返回: [retval, idx回显, isrgn, pos, rgnend, name(不回写), markrgnidx]
        if r[0] and r[2]:
            out.append((None, r[3], r[4]))
    out.sort(key=lambda x: x[1])
    return out
