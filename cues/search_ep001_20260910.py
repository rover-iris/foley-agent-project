# -*- coding: utf-8 -*-
# 选材检索：第001集 7 个自动事件候选池（search() 用法见 cues/音源库与资产表检查_20260910.md）
# 用法: python search_ep001_20260910.py [query ...]   无参=跑内置全量清单
import sys, sqlite3, json
sys.path.insert(0, r"C:\Users\Administrator\Desktop\音效工作流交接包_20260907\scripts\pipeline")
DB = r"C:\Users\Administrator\Desktop\音效工作流交接包_20260907\scripts\pipeline\asset_library.db"
from search_assets import search

QUERIES = {
    "码头底": ["dock harbor ambience", "crowd walla market people talking", "water waves lap pier"],
    "船桅":   ["boat mast creak harbor", "wooden ship creak"],
    "啜饮":   ["drinking sip slurp bowl", "gulp swallow liquid"],
    "群笑":   ["crowd laughing group laughter market"],
    "内院底": ["birds chirping garden ambience", "wind leaves rustling quiet park", "birds forest morning"],
    "剪刀":   ["scissors cutting snip fabric", "scissors snips"],
    "叩首扑跪": ["cloth movement fabric rustle", "body fall knee impact ground", "knee hit floor thud", "fall on ground dirt"],
}

def show(tag, hits, durs):
    print(f"\n===== [{tag}] {len(hits)} hits =====")
    for h in hits:
        d = durs.get(h.path.lower(), None)
        print(f"  {h.score:6.2f} r{h.rating} {str(d)[:7]:>7}s | {h.path}")

dur = dict(sqlite3.connect(DB).execute("SELECT path, duration FROM assets").fetchall())
dur_l = {k.lower(): v for k, v in dur.items()}

if len(sys.argv) > 1:
    hits = search(DB, " ".join(sys.argv[1:]), top_k=8, debug=True)
    show(" ".join(sys.argv[1:]), hits, dur_l)
    sys.exit(0)

for tag, qs in QUERIES.items():
    for q in qs:
        try:
            hits = search(DB, q, top_k=8, debug=True)
        except Exception as e:
            print(f"\n===== [{tag}] {q} 检索失败: {e} =====")
            continue
        show(f"{tag} | {q}", hits, dur_l)
