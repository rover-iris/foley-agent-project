"""资产表检索：FTS5 全文 + 中文类别 LIKE + LLM 同义词扩展。

- expand_query(query)：LLM 把查询扩展成英文音效关键词集（sea → sea,ocean,wave,shore,water...）
- search(db, query)：扩展词 → FTS5 全文字段匹配(文件名/描述/类别) + 中文类别 LIKE 加分 → top-k
毫秒级查表，零向量化、零全库扫描。
"""
from __future__ import annotations

import json
import os
import random
import re
import sqlite3
import urllib.request
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

CHAT_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
CHAT_MODEL_DEFAULT = "qwen-turbo"


def _chat_model() -> str:
    """扩展模型可配置（config retrieval.expand_model），默认 qwen-turbo 兜底。"""
    try:
        cfg = json.loads((BASE_DIR / "config.json").read_text(encoding="utf-8"))
        return cfg.get("retrieval", {}).get("expand_model") or CHAT_MODEL_DEFAULT
    except Exception:  # noqa: BLE001
        return CHAT_MODEL_DEFAULT

EXPAND_PROMPT = """你是影视音效库的检索助手。用户会给你一个描述音效的查询词（中文或英文）。
把它扩展成 8-12 个适合检索的英文音效关键词，要求：
1. 含同义词、近义词、上位词，及发声机制相似、画面互换后体感成立的相关动作词（如 开门 → door open/close/slam/lock/latch/handle/creak）；
2. 若动作有明确主体，关键词要带主体限定：如「人走路」→ human walking, boots footsteps（不要扩展出 horse/animal 类词）；「马跑」才用 horse gallop；
3. 用限定短语避免歧义：如「拍手」→ hand clap, applause（不要裸 clap，避免撞 thunderclap）；「雷声」→ thunder rumble, thunderclap；
4. 同时输出应排除的词：**输出能直接出现在资产文件名/描述里的单词或短短语**（如 thunder、horse、window、doorbell；不要输出 car door open 这类宽泛组合——那会误杀所有 door 资产）；主体不符或语义歧义对象（如 人走路排除 horse；拍手排除 thunder）。
5. **复合词连写与分写都要给**（2026-08-28 增）：音效库名字里复合词常分写（Under Water、Wheat Paste），检索词若连写（underwater）会匹配不到——凡复合概念，同时输出连写和分写两种形式（如 underwater + under water）。
只输出 JSON：{"terms": ["word1", "word2", ...], "exclude": ["bad1", "bad2", ...]}"""


def _split_compounds(terms: list[str]) -> list[str]:
    """连写复合词自动补分写变体（LLM 漏给时兜底）：underwater → +under water。
    用常见复合前缀表做启发式拆分，只拆两段都 ≥3 字母的组合。"""
    PREFIXES = ("under", "over", "out", "sub", "super", "inter", "counter", "after", "self")
    out = list(terms)
    for t in terms:
        tl = t.lower().replace("_", "")
        for pre in PREFIXES:
            if tl.startswith(pre) and len(tl) - len(pre) >= 3:
                rest = tl[len(pre):]
                if rest.isalpha():
                    variant = f"{pre} {rest}"
                    if variant not in out:
                        out.append(variant)
                break
    return out


@dataclass
class AssetHit:
    path: str
    name: str
    category: str
    score: float
    cue_offset_sec: float = 0.0
    cue_len_sec: float | None = None
    rating: int = 0


def _api_key() -> str:
    """取 DashScope key：环境变量 DASHSCOPE_API_KEY 优先，其次本目录 config.json 的 retrieval.api_key。
    都取不到时抛异常——expand_query 会捕获并降级为原词拆分，检索仍可用（无同义词扩展）。"""
    env = os.environ.get("DASHSCOPE_API_KEY")
    if env:
        return env
    try:
        cfg = json.loads((BASE_DIR / "config.json").read_text(encoding="utf-8"))
        key = cfg.get("retrieval", {}).get("api_key")
        if key:
            return key
    except Exception:  # noqa: BLE001
        pass
    raise RuntimeError("未配置 DashScope key：设环境变量 DASHSCOPE_API_KEY，或在 config.json 的 retrieval.api_key 填入")


def expand_query(query: str) -> tuple[list[str], list[str]]:
    """LLM 扩展同义词集 + 排除词。失败时降级：原样拆词，无排除。"""
    body = {
        "model": _chat_model(),
        "messages": [
            {"role": "system", "content": EXPAND_PROMPT},
            {"role": "user", "content": query},
        ],
        "temperature": 0.2,
    }
    try:
        req = urllib.request.Request(
            CHAT_URL,
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {_api_key()}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.loads(r.read().decode())
        content = resp["choices"][0]["message"]["content"]
        start, end = content.find("{"), content.rfind("}") + 1
        obj = json.loads(content[start:end])
        terms, exclude = obj.get("terms", []), obj.get("exclude", [])
        # 过滤纯标点/空词，去重，保留中英文
        clean, excl = [], []
        for t in terms:
            t = t.strip()
            if len(t) >= 2 and t not in clean:
                clean.append(t)
        for e in exclude:
            e = e.strip()
            if len(e) >= 2 and e.lower() not in excl:
                excl.append(e.lower())
        return _split_compounds(clean)[:14], excl[:8]
    except Exception:  # noqa: BLE001
        return [q for q in query.split() if len(q) >= 2], []


def _cn_part(query: str) -> list[str]:
    """抽取查询里的中文字段（用于类别 LIKE 加分）。"""
    import re

    return re.findall(r"[\u4e00-\u9fff]+", query)


_TIER_WORDS = ("large", "medium", "small", "hard", "soft", "huge", "aggressive", "fast", "slow")


def series_variants(conn: sqlite3.Connection, name: str) -> list[str]:
    """系列感知（2026-08-28 增）：命中一条 → 带出同系列全部兄弟变体。
    系列基名 = 文件名去掉尾部档位词（Large/Medium/Small/Hard/Soft...）。
    用途：同系列换档/轮换不重样，避免「Impact 挡住 Movement」式变体盲区。"""
    base = name
    changed = True
    while changed:
        changed = False
        for w in _TIER_WORDS:
            if base.lower().endswith(" " + w):
                base = base[: -len(w) - 1]
                changed = True
    rows = conn.execute(
        "SELECT name FROM assets WHERE name LIKE ? AND name != ? GROUP BY name",
        (base + "%", name),
    ).fetchall()
    return [r[0] for r in rows]


def search(db_path: str, query: str, top_k: int = 8, debug: bool = False, scene_theme: str = "通用", genre: str = "") -> list[AssetHit]:
    """检索。scene_theme：'通用'/'写实'/'玄幻'——题材感知过滤（写实排除中国风玄幻系）。
    genre：作品题材（玄幻/魔幻/古风...）——命中 config genre_domain_block 的对域素材降权压末位。"""
    conn = sqlite3.connect(db_path)
    try:
        # 低优先级分类（用户几乎不用，检索降权）
        try:
            _cfg = json.loads((BASE_DIR / "config.json").read_text(encoding="utf-8"))
            low_pri = _cfg["library"].get("low_priority_categories", [])
            action_packs = _cfg["library"].get("action_packs", {})
            domain_tags = _cfg["library"].get("domain_tags", {})
            genre_block = _cfg["library"].get("genre_domain_block", {})
        except Exception:  # noqa: BLE001
            low_pri, action_packs, domain_tags, genre_block = [], {}, {}, {}
        terms, exclude_terms = expand_query(query)
        if debug:
            print(f"[扩展] {query} -> {terms}")
            if exclude_terms:
                print(f"[排除] {exclude_terms}")

        # 两阶段检索（效率优先）：动作→官方包锁定，只在锁定包内匹配
        lock_packs: list[str] = []
        if action_packs:
            q_all = query.lower()  # 触发词只匹配查询词本身（LLM 扩展词易误触发，如「鱼掉落」扩展出 water 撞落水包）
            for trigger, packs in action_packs.items():
                if trigger.lower() in q_all:
                    lock_packs.extend(packs)
            lock_packs = list(dict.fromkeys(lock_packs))
        if debug and lock_packs:
            print(f"[锁定包] {lock_packs}")

        hits: dict[int, dict] = {}

        # 1) FTS5 全文匹配（英文为主）
        # 扩展词拆成单 token，OR 匹配（放宽短语限制，bm25 排序兜底）
        fts_terms = []
        for t in terms:
            for w in t.split():
                if w.isascii() and w.isalpha() and len(w) >= 3:
                    fts_terms.append(w.lower())
        fts_terms = list(dict.fromkeys(fts_terms))[:10]
        # 排除词保护：排除词的 token 若也是检索词（fts_terms）→ 放弃该排除词
        # （防 LLM 自相矛盾：「抛掷」扩展出 throw 又被排除 throw → 全排空）
        if exclude_terms and fts_terms:
            fts_tok_set = set(fts_terms)
            keep = []
            for e in exclude_terms:
                e_toks = [t for t in re.split(r"[\s_\-]+", e.lower()) if len(t) >= 3]
                if e_toks and any(t in fts_tok_set for t in e_toks):
                    continue  # 排除词与检索词冲突 → 放弃
                keep.append(e)
            exclude_terms = keep
        if fts_terms:
            match_sql = " OR ".join(f'"{t}"' for t in fts_terms)
            try:
                if lock_packs:
                    # 锁定包内检索：official 模糊匹配（候选从几万 → 几百）
                    like_sql = " OR ".join("official LIKE ?" for _ in lock_packs)
                    like_pats = [f"%{p}%" for p in lock_packs]
                    rows = conn.execute(
                        "SELECT rowid, bm25(assets_fts) AS b FROM assets_fts"
                        " WHERE assets_fts MATCH ? AND rowid IN"
                        " (SELECT id FROM assets WHERE " + like_sql + ")"
                        " ORDER BY b LIMIT 500",
                        (match_sql, *like_pats),
                    ).fetchall()
                else:
                    # 全库兜底
                    rows = conn.execute(
                        "SELECT rowid, bm25(assets_fts) AS b FROM assets_fts"
                        " WHERE assets_fts MATCH ? ORDER BY b LIMIT ?",
                        (match_sql, 500),
                    ).fetchall()
                for rowid, b in rows:
                    hits[rowid] = {"score": -b, "rank": len(hits)}  # bm25 越小越好
                # 兜底：锁包内搜不到（如「蹲下」crouch 类词包内无资产）→ 退回全库，宁可全库不空手
                if not rows and lock_packs:
                    try:
                        rows = conn.execute(
                            "SELECT rowid, bm25(assets_fts) AS b FROM assets_fts"
                            " WHERE assets_fts MATCH ? ORDER BY b LIMIT ?",
                            (match_sql, 500),
                        ).fetchall()
                        for rowid, b in rows:
                            hits[rowid] = {"score": -b, "rank": len(hits)}
                    except sqlite3.OperationalError:
                        pass
            except sqlite3.OperationalError:
                pass  # FTS 语法错误时忽略，走 LIKE

        # 2) 中文类别 LIKE 加分
        cn_words = _cn_part(query)
        if cn_words:
            like_rows = conn.execute(
                "SELECT id FROM assets WHERE "
                + " OR ".join("category LIKE ?" for _ in cn_words),
                [f"%{w}%" for w in cn_words],
            ).fetchall()
            for (aid,) in like_rows:
                if aid in hits:
                    hits[aid]["score"] += 0.5
                else:
                    hits[aid] = {"score": 0.5, "rank": len(hits)}

        # 3) 组装结果
        # 分类先导（用户洞察：文件夹分类应高优先级，不能在全库文件里裸找）：
        # 查询中文词命中 category + 扩展英文词命中 official → 该分类候选决定性加分（合并）
        cat_hits: set[int] = set()
        try:
            q_cn = _cn_part(query)
            for w in q_cn:
                if len(w) >= 2:
                    # 分类 LIKE + name LIKE（2026-08-28 增：纯中文名资产 FTS 撞不上英文扩展词，
                    # 中文词直接 LIKE name 才能命中——如「御剑」→「法术 御剑飞行 甩飞灵剑」）
                    for (aid2,) in conn.execute(
                        "SELECT id FROM assets WHERE category LIKE ? OR name LIKE ?", (f"%{w}%", f"%{w}%")
                    ).fetchall():
                        cat_hits.add(aid2)
            for t in terms:
                for w in t.lower().split():
                    if len(w) >= 4:
                        for (aid2,) in conn.execute(
                            "SELECT id FROM assets WHERE official LIKE ?", (f"%{w}%",)
                        ).fetchall():
                            cat_hits.add(aid2)
        except Exception:  # noqa: BLE001
            cat_hits = set()

        results = []
        for aid, info in hits.items():
            row = conn.execute(
                "SELECT path, name, category, n_cues, rating, theme, official FROM assets WHERE id=?", (aid,)
            ).fetchone()
            if not row:
                continue
            path, name, category, n_cues, rating, theme, official = row
            # 低优先级分类降权（用户几乎不用，压到结果末尾）
            score = info["score"]
            if category in low_pri:
                score -= 8
            # 分类先导加分：查询词命中该资产所属分类（category/official）→ +8 决定性
            if aid in cat_hits:
                score += 8
            # 锁定包加分：动作映射的官方包内候选再 +8（正确包应压过仅分类名撞词的）
            if lock_packs:
                off_l = (official or "").lower()
                if any(p.lower() in off_l for p in lock_packs):
                    score += 8
            # 素材层排除：目录规则（Construction Kit / Source Audio / *_ck）+ 文件名前缀规则
            path_l = path.lower().replace("\\", "/")
            name_l = name.lower()
            # 题材感知（scene_theme）：写实场景排除中国风玄幻系；玄幻场景加权
            if scene_theme == "写实" and theme == "中国风":
                continue
            if scene_theme == "玄幻" and theme == "中国风":
                score += 0.5
            # 题材域降权（2026-09-23 试行）：genre 命中的对域素材压到候选末位（降权非排除，宁可末位不空手）
            if genre and genre in genre_block:
                for _dom in genre_block[genre]:
                    _tags = domain_tags.get(_dom, {})
                    if official in _tags.get("officials", []) or category in _tags.get("categories", []):
                        score -= 8
                        if debug:
                            print(f"  [域降权] {name[:40]} <- {official or category}（{genre}避{_dom}域）")
                        break
            # 排除词过滤：短语（含空格）→ 子串匹配；单词 → 前缀互匹
            # 防误杀：'car door open' 是短语→子串匹配，不会因含 door 而毙掉整个门分类
            if exclude_terms:
                hit = False
                toks = None
                for e in exclude_terms:
                    e_l = e.lower()
                    if any(sep in e for sep in (" ", "_", "-")):
                        if e_l in name_l or e_l in path_l:
                            hit = True
                            break
                    else:
                        if toks is None:
                            name_toks = set(re.split(r"[\s_\-]+", name_l))
                            path_toks = set(re.split(r"[\s_\-]+", path_l))
                            toks = name_toks | path_toks
                        if len(e_l) >= 3:
                            for t in toks:
                                if (e_l.startswith(t) and len(t) >= 4) or (t.startswith(e_l) and len(e_l) >= 4):
                                    hit = True
                                    break
                        if hit:
                            break
                if hit:
                    continue
            # 素材层处理（2026-08-27 用户修正）：CK 不是不能用，只是优先级不高 → 降权保留在候选池
            # （Designed 没有对应质感时如 Movement Cloth，素材层顶上）；Source Audio 维持剔除
            path_l = path.lower().replace("\\", "/")
            name_l = name.lower()
            if "construction kit" in path_l:
                score -= 2
            elif "source audio" in path_l:
                continue
            segs = path_l.split("/")
            if any(s.endswith("_ck") for s in segs[:-1]):
                score -= 2
            stem = name.strip()
            # 文件名前缀：CK=素材层排除；DS/DK=Designed 加分（词边界防误伤 Background 类）
            fn_ck = bool(
                re.search(r"(?:^|[\s_-])(CK)(?:$|[\s_])", stem, re.I)
                or re.match(r"^[A-Za-z]{2}(CK)(?:[\s_]|$)", stem)
            )
            fn_ds = bool(
                re.search(r"(?:^|[\s_-])(DS|DK)(?:$|[\s_])", stem, re.I)
                or re.match(r"^[A-Za-z]{2}(DS|DK)(?:[\s_]|$)", stem)
            )
            if fn_ck:
                score -= 2
            # Designed 成品加分（目录 + 文件名前缀，DS/DK 均计）+ rating 加权（5 星 +2.5）
            if "designed" in path_l or any(s.endswith(("_ds", "_dk")) for s in segs[:-1]) or fn_ds:
                score += 0.5
            if rating:
                # 评分加语义门槛：名字与检索词有交集才全额（避免 5 星 Punch 在拍手/衣物查询里
                # 靠 +7.5 压过语义正确的 Slap/衣物音效）；不相关仅微加 0.3 系数保留常用倾向
                name_toks_s = set(re.split(r"[\s_\-]+", name_l))
                if fts_terms and (name_toks_s & set(fts_terms)):
                    score += rating * 1.5
                else:
                    score += rating * 0.3
            cue = None
            if n_cues:
                cue = conn.execute(
                    "SELECT offset_sec, len_sec, label FROM cues WHERE asset_id=? LIMIT 1",
                    (aid,),
                ).fetchone()
            results.append(
                AssetHit(
                    path=path,
                    name=name,
                    category=category,
                    score=score,
                    cue_offset_sec=cue[0] if cue else 0.0,
                    cue_len_sec=cue[1] if cue else None,
                    rating=rating or 0,
                )
            )
        results.sort(key=lambda h: h.score, reverse=True)
        # 多 5 星随机（用户定稿）：5 星候选 ≥2 时整体前置 + 内部随机——
        # 前置要求语义相关：5 星名字 token 与检索词有交集（拳击→Punch 前置；
        # 拍手→Punch 不相关不前置，Slap 才能上位，避免 5 星拳击到处顶）
        if fts_terms:
            fts_tok = set(fts_terms)
            fives = [
                h for h in results
                if h.rating == 5 and (set(re.split(r"[\s_\-]+", h.name.lower())) & fts_tok)
            ]
            if len(fives) >= 2:
                random.shuffle(fives)
                seen = {id(h) for h in fives}
                results = fives + [h for h in results if id(h) not in seen]
        return results[:top_k]
    finally:
        conn.close()


if __name__ == "__main__":
    import sys

    # 资产表按布局回退（CLI 直跑找库）：本包 pipeline\ 平铺 → NAS _索引\ 分发
    # （NAS 版 db 与引擎同目录）→ 工程侧 scripts\ 旁的 assets\ → 原机 index\ 子目录
    candidates = [
        BASE_DIR / "asset_library.db",
        BASE_DIR / "asset_library_nas.db",
        BASE_DIR.parent / "assets" / "asset_library_nas.db",
        BASE_DIR / "index" / "asset_library.db",
    ]
    _db = next((p for p in candidates if p.exists()), candidates[0])
    db = str(_db)
    for q in sys.argv[1:] or ["sea"]:
        print(f"== 查询: {q} ==")
        for h in search(db, q, top_k=5, debug=True):
            print(f"  {h.score:.2f} | {h.name[:44]} | {h.category}")
