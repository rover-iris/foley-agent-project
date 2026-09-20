# -*- coding: utf-8 -*-
# 第001集补装视频 v1 — 1.mp4 -> 轨0(Video) @0.0, len=ffprobe实测48.533333s
# 制作人补单 20260910；不算 CUES，不参与三重自检对账（对账仍按 12 条音频）。
# item 写法沿用本单已验证模板：AddMediaItemToTrack+AddTakeToMediaItem+
#   PCM_Source_CreateFromFile+SetMediaItemTake_Source+D_POSITION/D_LENGTH，
#   贴后读回 source filename 防空 item（手册 §3.2/05 防盲区）。
import time, os, sqlite3
from ipaddress import IPv4Address
import reapy_boost
from reapy_boost.tools.network.machines import Host
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)

VID = r"D:\干声视频文件\仿真人-权臣与我组cp\1.mp4"
VLEN = 48.533333
PROJ_PATH = r"D:\reaper工程\权臣CP01_贴轨测试_20260910\权臣CP01_贴轨测试.rpp"
assert os.path.exists(VID), "视频文件不存在"

reapy_boost.connect(Host(IPv4Address("127.0.0.1")))
RPR = reapy_boost.reascript_api
time.sleep(1)
assert hasattr(RPR, "AddMediaItemToTrack"), "reascript_api 未就绪"

trk = RPR.GetTrack(0, 0)
tname_rows = RPR.GetTrackName(trk, "", 512)
tname = next((x for x in tname_rows if isinstance(x, str) and "0x" not in x), "?")
print("目标轨[0] =", tname)

# 防重：轨0 已有同源 item 则跳过
def src_filename_of_take(take):
    s = RPR.GetMediaItemTake_Source(take)
    if not s or int(str(s).split("0x")[1], 16) == 0:
        return None
    rows = RPR.GetMediaSourceFileName(s, "", 1024)
    return next((x for x in rows if isinstance(x, str) and "0x" not in x), None)

skip = False
for i in range(RPR.CountTrackMediaItems(trk)):
    it = RPR.GetTrackMediaItem(trk, i)
    tk = RPR.GetActiveTake(it)
    fn = src_filename_of_take(tk) if tk else None
    if fn and os.path.basename(fn).lower() == "1.mp4":
        print("已存在同源视频 item，防重跳过")
        skip = True
        break

if not skip:
    item_id = RPR.AddMediaItemToTrack(trk)
    take_id = RPR.AddTakeToMediaItem(item_id)
    src_id = RPR.PCM_Source_CreateFromFile(VID)
    if not src_id or int(str(src_id).split("0x")[1], 16) == 0:
        raise RuntimeError("视频 src 创建失败")
    RPR.SetMediaItemTake_Source(take_id, src_id)
    RPR.SetMediaItemInfo_Value(item_id, "D_POSITION", 0.0)
    RPR.SetMediaItemInfo_Value(item_id, "D_LENGTH", VLEN)
    RPR.GetSetMediaItemTakeInfo_String(take_id, "P_NAME", "1.mp4", True)
    time.sleep(0.3)

# 防盲区读回：轨0 item 的 source filename 必须真实挂上
n = RPR.CountTrackMediaItems(trk)
print("轨0 item 数:", n)
ok = False
for i in range(n):
    it = RPR.GetTrackMediaItem(trk, i)
    pos = RPR.GetMediaItemInfo_Value(it, "D_POSITION")
    ln = RPR.GetMediaItemInfo_Value(it, "D_LENGTH")
    tk = RPR.GetActiveTake(it)
    fn = src_filename_of_take(tk) if tk else None
    nm_rows = RPR.GetSetMediaItemTakeInfo_String(tk, "P_NAME", "", False)
    nm = next((x for x in nm_rows if isinstance(x, str) and "0x" not in x), "?")
    print(f"  item[{i}] pos={pos:.6f} len={ln:.6f} name={nm} src={fn}")
    if fn and os.path.basename(fn).lower() == "1.mp4":
        ok = True
print("视频源挂载验证:", "通过" if ok else "!!未通过")

# 重新保存（REAPER 保持打开）
proj_id = RPR.EnumProjects(-1, "", 1024)[0]
RPR.Main_SaveProjectEx(proj_id, PROJ_PATH, 0)
RPR.GetSetProjectInfo(proj_id, 'PROJECT_ISDIRTY', 0, True)
time.sleep(1.0)
txt = open(PROJ_PATH, encoding="utf-8", errors="ignore").read()
print("落盘 mtime:", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(PROJ_PATH))))
print("rpp '<ITEM' 总数:", txt.count("<ITEM"), "(期望 13 = 12音频+1视频)")
print("rpp 含视频路径:", VID in txt)
print("PROJECT_ISDIRTY =", RPR.GetSetProjectInfo(0, 'PROJECT_ISDIRTY', 0, False))
