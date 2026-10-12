#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""REAPER Distant API 自诊断连接（reaper-foley-workflow 开工自检唯一入口）。

2026-10-12 v3 自诊断自愈版（制作人条款：不许让用户替我们测试）：
  端口不猜、不探、不手抄——全部从权威来源现场取：
    · Web 口/服务端口号默认值 → reapy_boost.config（库源码即权威，换版本自动跟随）
    · 运行时实际服务端口号     → Web 口 EXTSTATE（activate 脚本自报）
    · activate 动作 ID        → %APPDATA%\\REAPER\\reaper-kb.ini 现场解析
  分级检查，能自愈的自愈，不能自愈的报分支指令；严禁事项写死：
  不杀进程 / 不对端口发非协议字节（20261012 源码实锤：HTTP 打到 2308 裸 socket
  服务端 → JSONDecodeError 崩服弹窗）/ 检测到弹窗只报告不点击。

分级（if→动作）：
  L0 reaper.exe 在跑？      → 否：--launch 则拉起并等待；否则 FAIL
  L1 Web 口 HTTP 通？        → 候选=库常量 + reaper.ini csurf 实配（只读解析，换机/异版
    自适应——Web 口是每台机器自己的 REAPER 配置，禁凭记忆假定）；全不通：FAIL
  L2 EXTSTATE 读到服务端口号？→ 否：自愈——Web 口触发 activate 动作，轮询重读（≤10s）
  L3 rfw_common.connect_reaper() 定式连接 + 空壳断言 → PASS
  附加（只读）：枚举顶层窗口发现「ReaScript 错误」弹窗 → 报告（人工点击，纪律不变）

用法:
  python connect_reaper.py                 # 自诊断+自愈+连接（REAPER 未运行则 FAIL）
  python connect_reaper.py --launch        # REAPER 未运行时自行启动再连
  python connect_reaper.py --setup         # 初次装机引导（S0~S4 幂等）+ 全程自检
  python connect_reaper.py --no-heal       # 只诊断不自愈
退出码：0=PASS，1=FAIL（最后一行恒为 RESULT 开头的 JSON，供 agent 机器读取）

--setup 初次装机引导（2026-10-12 制作人批准；幂等，可反复跑）：
  S0 REAPER 本体存在？      → 否：FAIL 报安装指引
  S1 reapy_boost 可 import？→ 否：pip install（报版本）
  S2 Web 口+activate 注册齐？→ 缺且 REAPER 在跑：FAIL 让先关 REAPER（运行中改 ini 会被
    退出回写覆盖，1007 坑）；缺且已关：configure_reaper 写 ini/kb.ini 并复核
  S3 Scripts\\__startup.lua 在？→ 否：按本机实配动作 ID 生成自启桥（ID 不手抄）
  S4 拉起 REAPER → L0~L3 全程自检，PASS 才算装机完成
"""
import argparse
import ctypes
import json as _json
import os
import re
import subprocess
import sys
import time
from urllib import request as _req
from urllib.error import URLError

# 2026-10-12 补丁：本机系统代理会劫持指向 127.0.0.1 的 urllib 链路致握手挂死——
# 必须在 import reapy_boost 之前清掉；且所有 HTTP 调用走无代理 opener（双保险）
for _proxy_var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                   "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_proxy_var, None)
os.environ["NO_PROXY"] = "127.0.0.1,localhost"
_OPENER = _req.build_opener(_req.ProxyHandler({}))

# 端口权威来源：库源码（禁手抄数字——2308=裸协议服务端、2309=Web 发现口，20261012 config.py 实锤）
from reapy_boost.config import REAPY_SERVER_PORT, WEB_INTERFACE_PORT  # noqa: E402

# __startup.lua 自启桥模板（20261008 固化机制；动作 ID 占位，生成时填本机 kb.ini 实配值）
_STARTUP_LUA = """-- reapy_boost 服务自启（connect_reaper.py --setup 生成；机制同 2026-10-08 固化版）
-- REAPER 启动时自动执行本脚本（Scripts/__startup.lua 原生机制），
-- 通过 NamedCommandLookup 触发已注册的 activate_reapy_server.py。
-- 韧性设计：lua 无外部依赖；动作缺失/失效时 NamedCommandLookup 返回 0，静默跳过防弹窗。
local function start_reapy_server()
  local cmd = reaper.NamedCommandLookup("{action_id}")
  if cmd and cmd ~= 0 then
    reaper.Main_OnCommand(cmd, 0)
  end
end
-- 延迟一个调度周期再触发，避开启动期工程加载
reaper.defer(start_reapy_server)
"""


def _http_get(url: str, timeout: float = 2.0) -> str:
    return _OPENER.open(url, timeout=timeout).read().decode("utf-8", "replace")


def find_reaper_pids() -> list:
    r = subprocess.run(["tasklist"], capture_output=True, text=True)
    return sorted({int(m) for m in re.findall(r"reaper\.exe\s+(\d+)", r.stdout or "", re.I)})


def web_ports_from_ini() -> list:
    """从本机 reaper.ini 的 csurf_N=HTTP 行解析 Web 口实配端口（只读；换机/异版自适应）。"""
    ini = os.path.join(os.environ.get("APPDATA", ""), "REAPER", "reaper.ini")
    ports = []
    try:
        with open(ini, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                m = re.match(r"csurf_\d+=HTTP\s+\S+\s+(\d+)", line.strip())
                if m:
                    ports.append(int(m.group(1)))
    except OSError:
        pass
    return ports


def read_server_port(web_port: int) -> int:
    """L2：经 Web 口读 EXTSTATE 服务端口号。未起服务/未开通俗口一律抛异常。"""
    body = _http_get(f"http://127.0.0.1:{web_port}/_/GET/EXTSTATE/reapy_boost/server_port", timeout=1.5)
    value = body.split("\t")[-1].strip()
    if not value:
        raise RuntimeError("EXTSTATE server_port 为空（reapy 服务未起）")
    return int(_json.loads(value))


def find_activate_action_id() -> str:
    """从 reaper-kb.ini 现场解析 activate_reapy_server.py 的动作 ID（禁手抄）。"""
    kb = os.path.join(os.environ.get("APPDATA", ""), "REAPER", "reaper-kb.ini")
    with open(kb, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if "activate_reapy_server.py" in line:
                m = re.search(r"\b(RS[0-9A-Za-z]{40})\b", line)
                if m:
                    return "_" + m.group(1)
    raise RuntimeError("reaper-kb.ini 里找不到 activate_reapy_server.py 注册（需 REAPER 内手动 Load 一次该脚本，见坑册 activate 行）")


def _kb_has_activate() -> bool:
    try:
        find_activate_action_id()
        return True
    except Exception:
        return False


def setup_mode(a):
    """初次装机引导 S0~S4（幂等）。每级失败给分支指令，不留给用户试错。"""
    print("[setup] 初次装机引导开始（幂等，可反复跑）")
    # S0 REAPER 本体
    if not os.path.exists(a.reaper_exe):
        _verdict(False, "S0", f"未找到 REAPER（{a.reaper_exe}）——先安装 REAPER，或用 --reaper-exe 指定实际路径")
    print(f"[OK] S0 REAPER 已装（{a.reaper_exe}）")
    # S1 reapy_boost
    try:
        import reapy_boost  # noqa: F401
        from importlib.metadata import version as _ver
        print(f"[OK] S1 reapy_boost 已装（{_ver('reapy_boost')}）")
    except ImportError:
        print(f"[i] S1 reapy_boost 未装 → pip install（解释器 {sys.executable}）")
        r = subprocess.run([sys.executable, "-m", "pip", "install", "reapy_boost"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            _verdict(False, "S1", f"pip 安装失败：{(r.stderr or '')[-300:]}")
        print("[OK] S1 reapy_boost 安装完成")
    # S2 REAPER 配置（Web 口 + activate 注册）——REAPER 运行中禁写 ini（退出回写覆盖，1007 坑）
    if web_ports_from_ini() and _kb_has_activate():
        print("[OK] S2 REAPER 配置已就位（幂等跳过）")
    else:
        if find_reaper_pids():
            _verdict(False, "S2", "需写 REAPER 配置但 REAPER 正在运行——关闭 REAPER 后重跑 --setup")
        from reapy_boost.config import configure_reaper
        try:
            configure_reaper(detect_portable_install=False)
        except Exception as e:
            _verdict(False, "S2", f"configure_reaper 失败（{type(e).__name__}: {e}）")
        if not (web_ports_from_ini() and _kb_has_activate()):
            _verdict(False, "S2", "configure_reaper 跑完但 reaper.ini/kb.ini 复核未过")
        print("[OK] S2 REAPER 配置已写入并复核（Web 口 + activate 注册）")
    # S3 __startup.lua 自启桥（动作 ID 取本机 kb.ini 实配，禁手抄）
    scripts_dir = os.path.join(os.environ.get("APPDATA", ""), "REAPER", "Scripts")
    lua_path = os.path.join(scripts_dir, "__startup.lua")
    if os.path.exists(lua_path):
        print("[OK] S3 __startup.lua 已存在（幂等跳过）")
    else:
        os.makedirs(scripts_dir, exist_ok=True)
        with open(lua_path, "w", encoding="utf-8") as f:
            f.write(_STARTUP_LUA.format(action_id=find_activate_action_id()))
        print(f"[OK] S3 已生成 __startup.lua 自启桥（动作 ID 取本机实配）")
    print("[setup] 装机引导完成 → 拉起 REAPER 走 L0~L3 全程自检")


def detect_error_dialog() -> list:
    """只读枚举顶层窗口：发现 ReaScript 报错弹窗只报告标题，永不点击。"""
    titles = []
    if os.name != "nt":
        return titles
    buf = ctypes.create_unicode_buffer(512)
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def _cb(hwnd, _lp):
        if ctypes.windll.user32.IsWindowVisible(hwnd):
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, 512)
            t = buf.value
            if "ReaScript" in t and ("错误" in t or "error" in t.lower()):
                titles.append(t)
        return True

    ctypes.windll.user32.EnumWindows(WNDENUMPROC(_cb), 0)
    return titles


def try_reapy(web_port: int):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    if web_port == WEB_INTERFACE_PORT:
        from rfw_common import connect_reaper as _connect
        _reapy, RPR, _proj = _connect()
    else:
        # Web 口非库默认（换机/异版实配）：同一定式语义，显式传实配发现口
        import reapy_boost
        from ipaddress import IPv4Address
        from reapy_boost.tools.network.machines import Host
        reapy_boost.connect(Host(IPv4Address("127.0.0.1"), web_port))
        RPR = reapy_boost.reascript_api
        assert hasattr(RPR, "CountTracks"), "空壳断言失败：连接未生效"
    res = RPR.EnumProjects(-1, "", 1024)
    return [x for x in res if isinstance(x, str) and x]


def _verdict(ok: bool, level: str, detail: str):
    print(("RESULT " + _json.dumps(
        {"status": "pass" if ok else "fail", "level": level, "detail": detail},
        ensure_ascii=False)))
    sys.exit(0 if ok else 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--launch", action="store_true", help="REAPER 未运行时自行启动（制作人已解禁）")
    ap.add_argument("--reaper-exe", default=r"C:\Program Files\REAPER (x64)\reaper.exe")
    ap.add_argument("--project", default="", help="启动时随开的项目 rpp（可选）")
    ap.add_argument("--timeout", type=float, default=120, help="启动后等待就绪秒数")
    ap.add_argument("--no-heal", action="store_true", help="只诊断，不触发 activate 自愈")
    ap.add_argument("--setup", action="store_true", help="初次装机引导（幂等 S0~S4）+ 全程自检")
    a = ap.parse_args()

    if a.setup:
        setup_mode(a)
        a.launch = True  # S4：装机后拉起 REAPER 走全程自检

    print(f"[i] 库默认端口参考值：服务端={REAPY_SERVER_PORT} Web发现口={WEB_INTERFACE_PORT}（取自 reapy_boost.config；实配以 EXTSTATE/reaper.ini 现场读数为准）")

    # L0：进程
    pids = find_reaper_pids()
    if not pids:
        if not a.launch:
            _verdict(False, "L0", "REAPER 未运行；加 --launch 允许自行启动")
        subprocess.Popen([a.reaper_exe] + ([a.project] if a.project else []), shell=False)
        print(f"[i] 已发起启动 REAPER（{a.reaper_exe}），等待就绪…")
        deadline = time.time() + a.timeout
        while time.time() < deadline and not find_reaper_pids():
            time.sleep(2)
        pids = find_reaper_pids()
        if not pids:
            _verdict(False, "L0", "REAPER 启动超时")
    print(f"[OK] L0 进程在跑 {pids}")

    dlgs = detect_error_dialog()
    if dlgs:
        print(f"[!] 检测到 ReaScript 报错弹窗（只报告不点击）：{dlgs}——需人工关闭后再继续")

    # L1：Web 口——候选=库常量优先 + reaper.ini csurf 实配（只读解析，禁探测乱发字节）
    web_port = None
    candidates = [WEB_INTERFACE_PORT] + [p for p in web_ports_from_ini() if p != WEB_INTERFACE_PORT]
    for p in candidates:
        try:
            _http_get(f"http://127.0.0.1:{p}/", timeout=1.5)
            web_port = p
            break
        except Exception:
            continue
    if web_port is None:
        _verdict(False, "L1", f"Web 口不通（候选 {candidates}）——REAPER 里开 Web 接口，或跑 configure_reaper 配置")
    print(f"[OK] L1 Web 口 {web_port} 通" + ("" if web_port == WEB_INTERFACE_PORT
          else f"（非库默认 {WEB_INTERFACE_PORT}，取自 reaper.ini 实配）"))

    # L2：EXTSTATE 服务端口号（缺则自愈触发 activate）
    server_port = None
    try:
        server_port = read_server_port(web_port)
    except Exception as e:
        if a.no_heal:
            _verdict(False, "L2", f"读不到服务端口号（{e}）；--no-heal 未自愈")
        print(f"[i] L2 服务未起（{e}）→ 自愈：Web 口触发 activate")
        try:
            action_id = find_activate_action_id()
            _http_get(f"http://127.0.0.1:{web_port}/_/{action_id}", timeout=2)
        except Exception as e2:
            _verdict(False, "L2", f"activate 触发失败（{e2}）——按坑册 activate 行人工 Load 一次脚本")
        deadline = time.time() + 10
        while time.time() < deadline and server_port is None:
            time.sleep(1.5)
            try:
                server_port = read_server_port(web_port)
            except Exception:
                pass
        if server_port is None:
            _verdict(False, "L2", "activate 已触发但 10s 内服务未就绪——人工看 REAPER 是否有弹窗")
    print(f"[OK] L2 服务端口号={server_port}（EXTSTATE 实读；库默认参考值 {REAPY_SERVER_PORT}）")

    # L3：定式连接
    try:
        info = try_reapy(web_port)
    except Exception as e:
        _verdict(False, "L3", f"定式连接失败（{type(e).__name__}: {e}）")
    print(f"[OK] L3 连接成功；当前工程: {info}")
    _verdict(True, "L3", f"工程={info}")


if __name__ == "__main__":
    main()
