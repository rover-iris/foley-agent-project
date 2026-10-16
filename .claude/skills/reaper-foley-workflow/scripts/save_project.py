# -*- coding: utf-8 -*-
"""save_project.py —— 保存工程定式（reaper-remote-ops §5/[R-09]，提速实施案 P1-1 配套）。

Main_SaveProjectEx 显式路径保存；验收 = mtime 新于调用时刻 + 文件头含 <REAPER_PROJECT 双证。
禁另存类交互命令；dirty 假阳性不作判据。

用法：python save_project.py --rpp <工程.rpp> [--host 127.0.0.1]
末行 RESULT {"status","rpp","mtime"}；exit 0=保存双证通过，1=失败。
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rfw_common import connect_reaper, setup_stdout


def main():
    setup_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--rpp", required=True)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    rpp = os.path.abspath(a.rpp)
    t0 = time.time()
    try:
        rb, RPR, p = connect_reaper(a.host)
        RPR.Main_SaveProjectEx(0, rpp, 0)
    except Exception as e:
        print("RESULT " + json.dumps({"status": "fail", "error": str(e)}, ensure_ascii=False))
        sys.exit(1)
    time.sleep(1.0)
    ok_mtime = os.path.exists(rpp) and os.path.getmtime(rpp) >= t0 - 1
    ok_head = False
    if os.path.exists(rpp):
        with open(rpp, "rb") as f:
            ok_head = b"<REAPER_PROJECT" in f.read(4096)
    ok = ok_mtime and ok_head
    print("RESULT " + json.dumps({"status": "pass" if ok else "fail", "rpp": rpp,
                                  "mtime_ok": ok_mtime, "head_ok": ok_head,
                                  "mtime": os.path.getmtime(rpp) if os.path.exists(rpp) else 0},
                                 ensure_ascii=False))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
