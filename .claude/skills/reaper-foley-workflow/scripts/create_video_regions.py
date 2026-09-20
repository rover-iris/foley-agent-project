# -*- coding: utf-8 -*-
# create_video_regions_首尾相连.py （20260916 定稿，Python 版）
# 功能：对当前 REAPER 工程的轨 0（Video）上首尾相连的视频条目，逐条创建 Region 选区
#       （对应制作人 Lua 脚本「一键视频整理-首尾相连」的阶段2；阶段1 排列由贴轨入轨脚本完成）
# 用法：视频入轨并保存后运行；幂等可重跑（按位置对账，已存在的 Region 跳过）；建完自动保存工程
# 依赖：reapy-boost（连接姿势见 reaper-remote-ops skill §1.2）
# 实测：3D七宗罪 30 集连映工程 30/30 通过（20260916）；脚本自带的两个坑规避见下方注释
import time, os
from ipaddress import IPv4Address

TRACK_IDX = 0          # 视频所在轨（模板轨序：0 = Video）
REGION_NAME_PREFIX = ""  # Region 名称前缀，可留空（名称 = 前缀 + 两位序号，如 "01"）

for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["NO_PROXY"] = "127.0.0.1,localhost"
import reapy_boost
from reapy_boost.tools.network.machines import Host

reapy_boost.connect(Host(IPv4Address("127.0.0.1")))
RPR = reapy_boost.reascript_api
time.sleep(1)

proj_id = RPR.EnumProjects(-1, "", 512)[0]
proj_path = RPR.GetProjectPathEx(proj_id, "", 1024)[1]
proj_path = proj_path.rsplit("\\Media", 1)[0] + ".rpp"   # ⚠️ GetProjectPathEx 返回 ...\工程目录\Media，工程文件在上一级

# ---- 收集轨 0 视频条目（位置、长度、名称） ----
trk = RPR.GetTrack(0, TRACK_IDX)
n_items = RPR.CountTrackMediaItems(trk)
videos = []
for i in range(n_items):
    it = RPR.GetTrackMediaItem(trk, i)
    pos = RPR.GetMediaItemInfo_Value(it, "D_POSITION")
    ln = RPR.GetMediaItemInfo_Value(it, "D_LENGTH")
    take = RPR.GetActiveTake(it)
    name = RPR.GetSetMediaItemTakeInfo_String(take, "P_NAME", "", False)[1] if take else ""
    videos.append({"pos": pos, "end": pos + ln, "name": name})
print(f"轨{TRACK_IDX} 视频条目: {len(videos)} 条")
if not videos:
    raise SystemExit("轨0 无条目，退出")

# ---- 现有 Region 清点（⚠️ 坑1：CountProjectMarkers 出参在包装层不可靠，一律用逐条枚举对账；
#      ⚠️ 坑2：EnumProjectMarkers 的名称出参会被占位符回显污染，对账只用位置，不比名称） ----
n_all = RPR.CountProjectMarkers(0, 0, 0)[0]   # [0]=总条数可靠，[1][2] 出参不可信
existing = []
for i in range(n_all):
    o = RPR.EnumProjectMarkers(i, 0, 0.0, 0.0, "", 0)
    # 返回形如 [retval, num_markers占位, isrgn, pos, rgnend, name占位, id]，字段序以实测为准
    if len(o) >= 7 and o[2] == 1:
        existing.append((round(float(o[3]), 3), round(float(o[4]), 3)))

def exists(pos, end):
    for p, e in existing:
        if abs(p - pos) < 0.05 and abs(e - end) < 0.05:
            return True
    return False

# ---- 补缺创建 ----
created = 0
for i, v in enumerate(videos, start=1):
    name = f"{REGION_NAME_PREFIX}{i:02d}"
    if exists(v["pos"], v["end"]):
        continue
    r = RPR.AddProjectMarker(0, 1, float(v["pos"]), float(v["end"]), name, -1)
    if isinstance(r, int) and r >= 0:
        created += 1
    else:
        print(f"!! Region {name} 创建失败 @ {v['pos']:.3f}~{v['end']:.3f}")
print(f"新建 Region {created} 个，已有 {len(videos) - created} 个，期望总数 {len(videos)}")

# ---- 读回对账（按位置） ----
n_all2 = RPR.CountProjectMarkers(0, 0, 0)[0]
found = 0
for i in range(n_all2):
    o = RPR.EnumProjectMarkers(i, 0, 0.0, 0.0, "", 0)
    if len(o) >= 7 and o[2] == 1:
        if any(abs(round(float(o[3]), 3) - v["pos"]) < 0.05 and abs(round(float(o[4]), 3) - v["end"]) < 0.05 for v in videos):
            found += 1
print(f"对账: {found}/{len(videos)} " + ("全过 ✓" if found == len(videos) else "!!不足，可重跑本脚本幂等补齐"))

# ---- 保存 ----
if created > 0:
    mt1 = os.path.getmtime(proj_path) if os.path.exists(proj_path) else 0
    RPR.Main_SaveProjectEx(proj_id, proj_path, 0)
    RPR.GetSetProjectInfo(proj_id, "PROJECT_ISDIRTY", 0, True)
    time.sleep(1.5)
    mt2 = os.path.getmtime(proj_path)
    print(f"保存: mtime {'刷新 ✓' if mt2 > mt1 else '!!未刷新!!'}")
else:
    print("无新建，不保存")
