# -*- coding: utf-8 -*-
"""注册并触发一个 REAPER Lua 脚本（RPC 只做触发，干活在 REAPER 进程内）。

用法：
  python run_reaper_script.py --lua 导入视频.lua [--host 127.0.0.1] [--retries 3]

坑（2026-09-09 实测）：reapy RPC 偶发段错误（exit 1 零输出）——本脚本把「直接重跑」
固化进来：外层进程拉起 worker 子进程执行，崩溃自动重试，最多 --retries 次。
要求被触发的 Lua 自身幂等（判重护栏），重跑只补缺的。
"""
import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rfw_common import setup_stdout

WORKER_ENV = "_RUN_REAPER_SCRIPT_WORKER"


def run_once(lua, host):
    """worker：连接 → 注册 → 触发。成功打印 TRIGGER DONE。"""
    import time
    from ipaddress import IPv4Address

    import reapy_boost
    from reapy_boost.tools.network.machines import Host

    reapy_boost.connect(Host(IPv4Address(host)))
    time.sleep(1.5)
    RPR = reapy_boost.reascript_api
    res = RPR.AddRemoveReaScript(True, 0, lua, True)  # True=注册, 0=主 section, 已加载不重复
    cmd = res[0] if isinstance(res, tuple) else res
    if not cmd or int(cmd) == 0:
        print(f"注册失败: {res!r}", flush=True)
        return 1
    RPR.Main_OnCommand(int(cmd), 0)
    time.sleep(0.5)
    print("TRIGGER DONE", flush=True)
    return 0


def main():
    setup_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--lua", required=True)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--retries", type=int, default=3)
    a = ap.parse_args()
    assert os.path.exists(a.lua), f"lua 不存在: {a.lua}"

    if os.environ.get(WORKER_ENV):
        sys.exit(run_once(a.lua, a.host))

    # 外层：段错误（子进程死、无 TRIGGER DONE）自动重试
    for attempt in range(1, a.retries + 1):
        env = dict(os.environ, **{WORKER_ENV: "1"})
        p = subprocess.run([sys.executable, os.path.abspath(__file__),
                            "--lua", a.lua, "--host", a.host],
                           env=env, capture_output=True, text=True, timeout=120)
        out = (p.stdout or "") + (p.stderr or "")
        print(f"--- 第 {attempt} 次 exit={p.returncode} ---")
        print(out.strip() or "(无输出——疑似段错误)")
        if "TRIGGER DONE" in out:
            print("完成。对账以落盘 RPP / 人类核验为准。")
            return
    raise SystemExit(f"重试 {a.retries} 次仍未成功——查 references/pitfalls.md 后人工介入")


if __name__ == "__main__":
    main()
