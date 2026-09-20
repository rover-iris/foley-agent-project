# -*- coding: utf-8 -*-
"""项目配置管理：为具体项目生成/查看 foley_project.json。

本 skill 不存任何具体工程路径；每个项目的路径事实都收敛在这份配置里。

用法：
  python project_config.py init --name 剧名 --project-rpp 工程.RPP --asset-db 库.db \
      [--template-rpp 模板.RPP] [--reports-dir 目录] [--export-dir 目录] \
      [--work-dir 目录] [--tracks 14:29] [--region-csv bases.csv] \
      [--host 127.0.0.1] [--port 2308] [--out foley_project.json]
  python project_config.py show [--out foley_project.json]
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rfw_common import CONFIG_NAME, CONFIG_TEMPLATE, setup_stdout


def cmd_init(a):
    cfg = json.loads(json.dumps(CONFIG_TEMPLATE))  # deep copy
    cfg["project"] = a.name
    cfg["reaper"]["host"] = a.host
    cfg["reaper"]["port"] = a.port
    p = cfg["paths"]
    p["project_rpp"] = a.project_rpp or ""
    p["template_rpp"] = a.template_rpp or ""
    p["asset_db"] = a.asset_db or ""
    p["reports_dir"] = a.reports_dir or ""
    p["export_dir"] = a.export_dir or ""
    p["work_dir"] = a.work_dir or ""
    if a.tracks:
        s, e = a.tracks.split(":")
        cfg["tracks"]["action_children"] = [int(s), int(e)]
    cfg["region_csv"] = a.region_csv or ""
    if os.path.exists(a.out) and not a.force:
        raise SystemExit(f"{a.out} 已存在；确认覆盖请加 --force")
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print(f"已生成 {a.out}")
    cmd_show(argparse.Namespace(out=a.out))


def cmd_show(a):
    if not os.path.exists(a.out):
        raise SystemExit(f"{a.out} 不存在；先用 init 生成")
    cfg = load_cfg(a.out)
    print(f"== 项目配置: {a.out} ==")
    print(json.dumps(cfg, ensure_ascii=False, indent=2))
    print("\n== 存在性检查 ==")
    checks = [
        ("project_rpp", cfg["paths"].get("project_rpp")),
        ("template_rpp", cfg["paths"].get("template_rpp")),
        ("asset_db", cfg["paths"].get("asset_db")),
        ("reports_dir", cfg["paths"].get("reports_dir")),
        ("export_dir", cfg["paths"].get("export_dir")),
        ("region_csv", cfg.get("region_csv")),
    ]
    bad = 0
    for k, v in checks:
        if not v:
            print(f"  [未配置] {k}")
            continue
        ok = os.path.exists(v)
        bad += 0 if ok else 1
        print(f"  [{'OK ' if ok else '缺失'}] {k}: {v}")
    if bad:
        print(f"\n⚠️ {bad} 项路径缺失，开工前先解决")
    else:
        print("\n路径检查全部通过")


def load_cfg(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main():
    setup_stdout()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="生成项目配置")
    p.add_argument("--name", required=True)
    p.add_argument("--project-rpp")
    p.add_argument("--template-rpp")
    p.add_argument("--asset-db")
    p.add_argument("--reports-dir")
    p.add_argument("--export-dir")
    p.add_argument("--work-dir")
    p.add_argument("--tracks", help="动作组子轨区间，如 14:29")
    p.add_argument("--region-csv")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=2308)
    p.add_argument("--out", default=CONFIG_NAME)
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("show", help="查看配置与路径存在性")
    p.add_argument("--out", default=CONFIG_NAME)
    p.set_defaults(fn=cmd_show)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
