# -*- coding: utf-8 -*-
# NAS _索引 检索引擎副本同步（2026-09-23 制作人授权）
import os, shutil, sys

NAS = r"\\192.168.9.251\音效2\资源库\_索引"
SRC = r"C:\Users\Administrator\Desktop\音效工作流交接包_20260923\scripts\pipeline"

if not os.path.isdir(NAS):
    print("NAS 不可达:", NAS); sys.exit(1)

print("_索引 现有文件:")
for f in sorted(os.listdir(NAS)):
    print("  ", f)

# 1) 检索引擎副本
dst = os.path.join(NAS, "search_assets.py")
shutil.copy2(os.path.join(SRC, "search_assets.py"), dst)
print("search_assets.py 已同步 ->", dst)

# 2) config.json：genre 域过滤依赖它（domain_tags/genre_domain_block）；若 _索引 已有 config 则合并更新，没有则复制
cfg_src = os.path.join(SRC, "config.json")
cfg_dst = os.path.join(NAS, "config.json")
import json
new_cfg = json.load(open(cfg_src, encoding="utf-8"))
if os.path.exists(cfg_dst):
    old_cfg = json.load(open(cfg_dst, encoding="utf-8"))
    lib = old_cfg.setdefault("library", {})
    lib["domain_tags"] = new_cfg["library"]["domain_tags"]
    lib["genre_domain_block"] = new_cfg["library"]["genre_domain_block"]
    json.dump(old_cfg, open(cfg_dst, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("config.json 已存在，合并 domain_tags/genre_domain_block")
else:
    shutil.copy2(cfg_src, cfg_dst)
    print("config.json 不存在，已整份复制")

# 3) 验证：从 NAS 副本直读并跑 genre 过滤
sys.path.insert(0, NAS)
import importlib, search_assets
importlib.reload(search_assets)
db = os.path.join(NAS, "asset_library_nas.db")
hits = search_assets.search(db, "force field loop 能量罩", top_k=3, genre="玄幻")
print("NAS 副本 genre=玄幻 实测 top3:")
for h in hits:
    print(f"   {h.score:6.2f} | {h.name[:44]} | {h.category[:24]}")
