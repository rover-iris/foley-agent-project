# -*- coding: utf-8 -*-
"""NAS 版资产表生成器（2026-09-20 制作人指令：通用版，方便音效库不在本地的用户）。

流程：读本地 asset_library.db → 按 nas_path_map.json 规则改写 path →
线程池逐条 os.path.exists 验证（判 MISS 前重试一次，防网络抖动误杀）→
未命中按文件名在规则指定的 NAS 子树兜底 → 仍未命中剔除并进存疑清单 →
同 schema 新库（保留原 id 与 cues 关联，FTS5 rebuild）→ 复制三处副本 + sha1 →
映射报告落 cues/资产表NAS版映射报告_<日期>.md。

对 NAS 只做读与最后一次性写副本（_索引\\），不改音效文件本身。

用法：
  python make_nas_asset_db.py --dry-run 500   # 抽样校验规则命中率（不建库不复制）
  python make_nas_asset_db.py                 # 全量生成
  python make_nas_asset_db.py --workers 16    # 调并发
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import shutil
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
MAP_PATH = SCRIPT_DIR / "nas_path_map.json"
DRY_SEED = 20260920


def load_map() -> dict:
    cfg = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    lit = [r for r in cfg["rules"] if r["type"] == "literal"]
    rx = [r for r in cfg["rules"] if r["type"] == "re"]
    lit.sort(key=lambda r: len(r["match"]), reverse=True)  # 长前缀优先
    for r in rx:
        r["_re"] = re.compile(r["pattern"])
    cfg["_literals"], cfg["_regexes"] = lit, rx
    return cfg


def map_rel(rel: str, cfg: dict):
    """本地相对路径 → (NAS 相对路径, 规则名)。无规则命中按原样直拼并标 no-rule。"""
    for r in cfg["_literals"]:
        if rel.startswith(r["match"]):
            return r["replace"] + rel[len(r["match"]):], r["name"]
    for r in cfg["_regexes"]:
        new, n = r["_re"].subn(r["replace"], rel, count=1)
        if n:
            return new, r["name"]
    return rel, "no-rule"


def exists_robust(full: str) -> bool:
    if os.path.exists(full):
        return True
    if full.startswith("\\\\"):
        try:  # 长路径保险（>260 字符）
            return os.path.exists("\\\\?\\UNC" + full[1:])
        except OSError:
            return False
    return False


def verify_all(items, workers: int) -> set:
    """items: [(id, full)] → 命中 id 集合。线程池一轮 + 未命中串行重试一轮。"""
    hits = set()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for (aid, full), ok in zip(items, ex.map(lambda t: exists_robust(t[1]), items)):
            if ok:
                hits.add(aid)
    for aid, full in items:
        if aid not in hits:
            time.sleep(0.05)
            if exists_robust(full):
                hits.add(aid)
    return hits


_INDEX_CACHE: dict[str, dict[str, list[str]]] = {}


def _norm_name(filename: str) -> str:
    """去【中文注释】后缀 + 空格折叠 + 小写。NAS 翻译版常给目录/文件名追加【...】，
    剥掉后与本地原名对齐（2026-09-20 Matter Mayhem 实测）。"""
    stem, dot, ext = filename.rpartition(".")
    if dot:
        stem = re.sub(r"【[^】]*】", "", stem)
        return re.sub(r"\s+", " ", stem).strip().lower() + "." + ext.lower()
    return re.sub(r"\s+", " ", filename).strip().lower()


def fname_index(root_full: str) -> dict[str, list[str]]:
    """懒构建 NAS 子树 文件名→完整路径 索引（原始键 + 去注释归一化键），跨规则共享缓存。"""
    if root_full not in _INDEX_CACHE:
        idx: dict[str, list[str]] = {}
        for dirpath, _dirs, files in os.walk(root_full):
            for f in files:
                idx.setdefault(f.lower(), []).append(os.path.join(dirpath, f))
                nk = _norm_name(f)
                if nk != f.lower():
                    idx.setdefault(nk, []).append(os.path.join(dirpath, f))
        _INDEX_CACHE[root_full] = idx
    return _INDEX_CACHE[root_full]


def sha1_of(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", type=int, metavar="N", default=0, help="随机抽样 N 条校验命中率，不建库不复制")
    ap.add_argument("--workers", type=int, default=10)
    args = ap.parse_args()

    cfg = load_map()
    src = cfg["source_db"]
    nas_root = cfg["nas_root"]
    prefix = cfg["local_prefix"]

    db = sqlite3.connect(src)  # 只 SELECT，不写源库
    rows = db.execute("SELECT id, path FROM assets").fetchall()
    db.close()
    print(f"源库：{src}（{len(rows)} 条）")

    if args.dry_run and args.dry_run < len(rows):
        random.seed(DRY_SEED)
        rows = random.sample(rows, args.dry_run)
        print(f"DRY-RUN 抽样 {len(rows)} 条（seed={DRY_SEED}）")

    t0 = time.time()
    mapped = []  # (id, orig, nas_rel, nas_full, rule)
    for aid, p in rows:
        rel = p[len(prefix):] if p.startswith(prefix) else p
        nas_rel, rule = map_rel(rel, cfg)
        mapped.append((aid, p, nas_rel, os.path.join(nas_root, nas_rel), rule))

    print(f"[1/5] 映射完成，开始存在性验证（workers={args.workers}）...")
    hits = verify_all([(m[0], m[3]) for m in mapped], args.workers)
    print(f"[2/5] 直验完成：{len(hits)}/{len(mapped)}，耗时 {time.time() - t0:.0f}s")

    # 兜底：按文件名（原始→去注释归一化）在规则指定子树定位；
    # 去重：同一 NAS 路径只允许一个本地条目占用（Stereo/Mono 同名等场景，防 UNIQUE 冲突）
    fallback: dict[int, str] = {}
    used = {m[3] for m in mapped if m[0] in hits}
    rule_by_name = {r["name"]: r for r in cfg["rules"]}
    misses = [m for m in mapped if m[0] not in hits]
    for aid, orig, _nas_rel, _nas_full, rule in misses:
        fn = os.path.basename(orig).lower()
        nfn = _norm_name(os.path.basename(orig))
        for rt in rule_by_name.get(rule, {}).get("fallback_roots", []):
            idx = fname_index(os.path.join(nas_root, rt))
            cands = idx.get(fn) or idx.get(nfn) or []
            for c in sorted(cands, key=len):
                if c not in used:
                    fallback[aid] = c
                    used.add(c)
                    break
            if aid in fallback:
                break
    final_miss = [(m[1], m[4]) for m in misses if m[0] not in fallback]
    print(f"[3/5] 文件名兜底：新增 {len(fallback)}，最终未命中 {len(final_miss)}")

    # 分规则统计
    stat: dict[str, dict] = {}
    for aid, orig, nas_rel, nas_full, rule in mapped:
        s = stat.setdefault(rule, {"total": 0, "direct": 0, "fallback": 0, "miss": 0})
        s["total"] += 1
        if aid in hits:
            s["direct"] += 1
        elif aid in fallback:
            s["fallback"] += 1
        else:
            s["miss"] += 1
    print(f"{'规则':<20}{'条数':>8}{'直验':>8}{'兜底':>6}{'未命中':>6}")
    for k, v in sorted(stat.items(), key=lambda kv: -kv[1]["total"]):
        print(f"{k:<20}{v['total']:>8}{v['direct']:>8}{v['fallback']:>6}{v['miss']:>6}")

    if args.dry_run:
        for orig, rule in final_miss[:30]:
            print(f"  MISS [{rule}] {orig}")
        rate = (len(mapped) - len(final_miss)) / len(mapped) * 100
        print(f"DRY-RUN 命中率 {rate:.2f}%（门槛 ≥99% 再跑全量）")
        return 0

    # ===== 建库：同 schema，保留 id 与 cues 关联，FTS rebuild =====
    out_dir = PROJECT_ROOT / "assets"
    out_dir.mkdir(exist_ok=True)
    out_db = out_dir / cfg["db_filename"]
    if out_db.exists():
        out_db.unlink()
    dest = sqlite3.connect(out_db)
    dest.execute("ATTACH DATABASE ? AS src", (src,))
    for name in ("assets", "cues"):
        dest.execute(dest.execute(
            "SELECT sql FROM src.sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()[0])
    kept = [m[0] for m in mapped if m[0] in hits or m[0] in fallback]
    dest.execute("CREATE TEMP TABLE kept(id INTEGER PRIMARY KEY)")
    dest.executemany("INSERT INTO kept(id) VALUES(?)", [(i,) for i in kept])
    dest.execute("INSERT INTO main.assets SELECT * FROM src.assets WHERE id IN (SELECT id FROM kept)")
    dest.executemany("UPDATE main.assets SET path=? WHERE id=?", [
        (fallback.get(aid) or os.path.join(nas_root, nas_rel), aid)
        for aid, _orig, nas_rel, _full, _rule in mapped if aid in hits or aid in fallback
    ])
    dest.execute("INSERT INTO main.cues SELECT * FROM src.cues WHERE asset_id IN (SELECT id FROM kept)")
    dest.execute(dest.execute(
        "SELECT sql FROM src.sqlite_master WHERE type='table' AND name='assets_fts'"
    ).fetchone()[0])
    dest.execute("INSERT INTO assets_fts(assets_fts) VALUES('rebuild')")
    dest.commit()
    integrity = dest.execute("PRAGMA integrity_check").fetchone()[0]
    n_assets = dest.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
    n_cues = dest.execute("SELECT COUNT(*) FROM cues").fetchone()[0]
    sample = dest.execute("SELECT name FROM assets WHERE length(name) > 8 LIMIT 1").fetchone()[0]
    tok = re.findall(r"[A-Za-z]{4,}", sample)
    fmatch = dest.execute(
        "SELECT COUNT(*) FROM assets_fts WHERE assets_fts MATCH ?", ((tok[0] if tok else "impact").lower(),)
    ).fetchone()[0]
    print(f"[4/5] 新库：assets={n_assets} cues={n_cues} integrity={integrity} FTS自测('{tok[0] if tok else 'impact'}')={fmatch}")
    dest.execute("DETACH DATABASE src")
    dest.close()

    # ===== 三处副本 + sha1 =====
    copies = {"工程 assets": (out_db, sha1_of(out_db))}
    for label, d in (("NAS _索引", Path(cfg["nas_index_dir"])), ("交接包 pipeline", Path(cfg["pipeline_dir"]))):
        try:
            d.mkdir(parents=True, exist_ok=True)
            target = d / cfg["db_filename"]
            shutil.copyfile(out_db, target)
            copies[label] = (target, sha1_of(target))
        except OSError as e:
            print(f"[5/5] ⚠️ 副本写入失败（{label}）：{e} —— 降级跳过，其余副本保留")
    print(f"[5/5] 副本 {len(copies)}/3 处：")
    for label, (p, s) in copies.items():
        print(f"  {label}: {p} sha1={s[:12]}…")

    # ===== 映射报告落盘 =====
    date = time.strftime("%Y%m%d")
    fb_rows = sorted((m[1], fallback[m[0]]) for m in mapped if m[0] in fallback)
    lines = [
        "# 资产表 NAS 版映射报告",
        "",
        f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 源库：`{src}`（{len(rows)} 条，前缀 `{prefix}`）",
        f"- NAS 根：`{nas_root}`",
        f"- 结果：直验命中 {len(hits)}，文件名兜底命中 {len(fallback)}，剔除 {len(final_miss)}（命中率 {(len(mapped) - len(final_miss)) / len(mapped) * 100:.2f}%）",
        "",
        "## 分规则统计",
        "",
        "| 规则 | 条数 | 直验 | 兜底 | 未命中 |",
        "|---|---|---|---|---|",
    ]
    for k, v in sorted(stat.items(), key=lambda kv: -kv[1]["total"]):
        lines.append(f"| {k} | {v['total']} | {v['direct']} | {v['fallback']} | {v['miss']} |")
    lines += ["", "## 文件名兜底映射对照（人工抽查用）", "", "| 本地原路径 | NAS 实际定位 |", "|---|---|"]
    for o, f in fb_rows:
        lines.append(f"| {o} | {f} |")
    lines += ["", "## 未命中存疑清单（已从 NAS 版剔除；补传 NAS 后重跑生成器即可回填）", "",
              "| 本地原路径 | 适用规则 |", "|---|---|"]
    for o, r in final_miss:
        lines.append(f"| {o} | {r} |")
    lines += ["", "## 副本与校验", ""]
    for label, (p, s) in copies.items():
        lines.append(f"- {label}：`{p}` sha1=`{s}`")
    lines += [
        "",
        f"- 新库自检：integrity={integrity}，assets={n_assets}，cues={n_cues}，FTS MATCH 自测命中 {fmatch} 条",
        "- 重跑：调整 `scripts/nas_path_map.json` 后执行 `python scripts/make_nas_asset_db.py`",
    ]
    report = PROJECT_ROOT / "cues" / f"资产表NAS版映射报告_{date}.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    print(f"报告已落盘：{report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
