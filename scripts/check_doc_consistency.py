# -*- coding: utf-8 -*-
"""check_doc_consistency.py —— 文档一致性扫描（修①根修配套，CLAUDE.md「条款写作标准」挂钩）。

改条款/制度文档后必跑；FAIL 不入库。两关：
  关1 旧名零命中：禁词表（已废除的术语旧称）在活文档范围内零命中。
      范围=工程仓（CLAUDE.md/SOP/.claude/**/scripts/**，排除 __pycache__ 与本脚本自身）
      + skill 三副本。历史档案（12_变更记录.md、pitfalls-archive.md、任务文件夹交付物）不在范围内。
      新增废词：直接加 FORBIDDEN 表（递增不复用旧称原则不变）。
  关2 skill 三副本 sha1 对账：快照 → 工程内 → 全局，逐文件 hash 一致
     （video-action-analysis / reaper-foley-workflow / reaper-remote-ops 三册全量）。

用法：python scripts/check_doc_consistency.py
末行 RESULT JSON；exit 0=全过，1=有违规。
"""
import hashlib
import json
import os
import re
import sys

PROJ = r"C:\Users\Administrator\Desktop\见习音效师工程_claude"
SNAP = r"C:\Users\Administrator\Desktop\音效工作流交接包_20260929\skill快照"
GLOB = os.path.expanduser(r"~\.claude\skills")

# 禁词表：已废除术语旧称（活文档零命中；新增废词追加于下，格式 (词, 废除去向)）
FORBIDDEN = [
    ("三重自检", "出厂自检"),
    ("四关自检", "出厂自检"),
    ("一键四关", "出厂自检"),
]

SCAN_MD_PY = (".md", ".py")
SKIP_DIRS = {"__pycache__", ".git", "node_modules"}
SKIP_FILES = {"check_doc_consistency.py"}


def iter_files(root):
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in SKIP_DIRS]
        for f in fn:
            if f in SKIP_FILES or not f.endswith(SCAN_MD_PY):
                continue
            yield os.path.join(dp, f)


def sha1(p):
    h = hashlib.sha1()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    problems = []

    # 关1：旧名零命中
    roots = [PROJ]
    for skill in ("reaper-foley-workflow", "video-action-analysis", "reaper-remote-ops"):
        roots.append(os.path.join(SNAP, skill))
        roots.append(os.path.join(GLOB, skill))
    for root in roots:
        if not os.path.isdir(root):
            continue
        for fp in iter_files(root):
            try:
                with open(fp, encoding="utf-8", errors="replace") as f:
                    txt = f.read()
            except OSError:
                continue
            for term, dest in FORBIDDEN:
                n = txt.count(term)
                if n:
                    problems.append("旧名命中 %s ×%d → %s（%s）" % (term, n, fp, dest))

    # 关2：skill 三副本 sha1 对账
    for skill in ("reaper-foley-workflow", "video-action-analysis", "reaper-remote-ops"):
        snap_dir = os.path.join(SNAP, skill)
        for dp, dn, fn in os.walk(snap_dir):
            dn[:] = [d for d in dn if d not in SKIP_DIRS]
            for f in fn:
                if f.endswith(".pyc"):
                    continue
                s = os.path.join(dp, f)
                rel = os.path.relpath(s, snap_dir)
                for base, tag in ((os.path.join(PROJ, ".claude", "skills", skill), "工程内"),
                                  (os.path.join(GLOB, skill), "全局")):
                    t = os.path.join(base, rel)
                    if not os.path.exists(t):
                        problems.append("三副本缺失 %s：%s" % (tag, t))
                    elif sha1(s) != sha1(t):
                        problems.append("三副本漂移 %s：%s" % (tag, t))

    for p in problems:
        print("  ❌ " + p)
    print("RESULT " + json.dumps({"status": "fail" if problems else "pass",
                                 "problems": len(problems)}, ensure_ascii=False))
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
