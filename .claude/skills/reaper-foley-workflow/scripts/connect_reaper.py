#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自适应连接 REAPER Distant API（reaper-foley-workflow 开工自检用）。

背景：Distant API 端口并非固定 2308（2026-09-14 实测某实例在 2309），
靠硬编码端口会反复踩坑。本脚本自动完成：
  1. 找 reaper.exe 进程；没运行则（可选）自行启动（制作人 2026-09-14 解禁：AI 可自启 REAPER）；
  2. netstat 枚举该进程监听的 TCP 端口；
  3. 逐个端口 socket 探测 + reapy_boost.connect 实连；
  4. 成功后回读当前工程路径打印确认。

用法:
  python connect_reaper.py                 # 仅连接（REAPER 未运行则报错退出）
  python connect_reaper.py --launch        # REAPER 未运行时自行启动再连
  python connect_reaper.py --launch --reaper-exe "C:/Program Files/REAPER (x64)/reaper.exe" --project <rpp路径>
"""
import argparse
import re
import socket
import subprocess
import sys
import time
from ipaddress import IPv4Address


def find_reaper_pids() -> list:
    r = subprocess.run(["tasklist"], capture_output=True, text=True)
    return sorted({int(m) for m in re.findall(r"reaper\.exe\s+(\d+)", r.stdout or "", re.I)})


def listening_ports(pid: int) -> list:
    r = subprocess.run(["netstat", "-ano"], capture_output=True, text=True)
    ports = set()
    for line in (r.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[-1] == str(pid) and "LISTENING" in line:
            m = re.search(r":(\d+)$", parts[1])
            if m:
                ports.add(int(m.group(1)))
    return sorted(ports)


def try_reapy(port: int):
    import reapy_boost
    from reapy_boost import Host
    h = reapy_boost.Host(IPv4Address("127.0.0.1"), port)
    reapy_boost.connect(h)
    time.sleep(1)
    api = reapy_boost.reascript_api
    res = api.EnumProjects(-1, "", 1024)
    return [x for x in res if isinstance(x, str) and x]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--launch", action="store_true", help="REAPER 未运行时自行启动（制作人已解禁）")
    ap.add_argument("--reaper-exe", default=r"C:\Program Files\REAPER (x64)\reaper.exe")
    ap.add_argument("--project", default="", help="启动时随开的项目 rpp（可选）")
    ap.add_argument("--timeout", type=float, default=120, help="启动后等待端口就绪秒数")
    a = ap.parse_args()

    pids = find_reaper_pids()
    if not pids:
        if not a.launch:
            sys.exit("[FAIL] REAPER 未运行；用 --launch 允许自行启动")
        subprocess.Popen([a.reaper_exe] + ([a.project] if a.project else []),
                         shell=False)
        print(f"[i] 已发起启动 REAPER（{a.reaper_exe}），等待就绪…")
    # 轮询：进程出现 → 端口出现 → 实连成功
    deadline = time.time() + (a.timeout if not pids else 30)
    while time.time() < deadline:
        pids = find_reaper_pids()
        ports = []
        for p in pids:
            ports += listening_ports(p)
        # 优先试常见段，其余端口兜底（不硬编码唯一端口）
        ports = sorted(set(ports), key=lambda x: (not (2300 <= x <= 2400), x))
        for port in ports:
            s = socket.socket()
            s.settimeout(2)
            try:
                s.connect(("127.0.0.1", port))
            except Exception:
                s.close()
                continue
            finally:
                try:
                    s.close()
                except Exception:
                    pass
            try:
                info = try_reapy(port)
                print(f"[OK] 经端口 {port} 连接成功（进程 {pids}）；当前工程: {info}")
                return
            except Exception:
                continue
        time.sleep(3)
    sys.exit("[FAIL] 超时未能连通 Distant API（已枚举进程全部监听端口）")


if __name__ == "__main__":
    main()
