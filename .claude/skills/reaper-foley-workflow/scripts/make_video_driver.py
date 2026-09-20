# -*- coding: utf-8 -*-
"""由 region_plan.csv 生成视频导入驱动 Lua（离线，纯文本拼接，不碰 REAPER）。

用法：
  python make_video_driver.py --plan region_plan.csv --rpp 工程.RPP --out 导入视频.lua
之后：python run_reaper_script.py --lua 导入视频.lua  （注册+触发，见 references/video-import.md）

plan 列（utf-8-sig）：集数,预计base_s,时长_s,视频；视频为空则该集只建 Region。
驱动 Lua 幂等：条目按位置判重、Region 按名字判重，可安全重跑。
"""
import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rfw_common import setup_stdout

LUA_TEMPLATE = """\
-- 自动生成 by make_video_driver.py（源: @PLAN@）—— 改参数请改 plan 重新生成
-- 幂等：条目按位置(0.001s)判重跳过；Region 按名字判重跳过；可安全重跑
local RPP = [[@RPP@]]

local EPS = {
@EP_ROWS@
}

local trk = reaper.GetTrack(0, 0)
if not trk then
  reaper.ShowConsoleMsg("track 0 不存在\\n")
  return
end

local region_names = {}
local total = reaper.CountProjectMarkers(0)
for i = 0, total - 1 do
  local _, isrg, _, _, name = reaper.EnumProjectMarkers(i)
  if isrg then region_names[name] = true end
end

local item_pos = {}
for i = 0, reaper.CountTrackMediaItems(trk) - 1 do
  local it = reaper.GetTrackMediaItem(trk, i)
  item_pos[string.format("%.3f", reaper.GetMediaItemInfo_Value(it, "D_POSITION"))] = true
end

local done, skip = 0, 0
for _, e in ipairs(EPS) do
  local poskey = string.format("%.3f", e.base)
  if item_pos[poskey] then
    skip = skip + 1
  elseif e.video ~= "" then
    local src = reaper.PCM_Source_CreateFromFile(e.video)
    if not src then
      reaper.ShowConsoleMsg("source失败: " .. e.video .. "\\n")
    else
      local item = reaper.AddMediaItemToTrack(trk)
      local take = reaper.AddTakeToMediaItem(item)
      reaper.SetMediaItemTake_Source(take, src)
      reaper.SetMediaItemInfo_Value(item, "D_POSITION", e.base)
      reaper.SetMediaItemInfo_Value(item, "D_LENGTH", e.len)
      reaper.GetSetMediaItemTakeInfo_String(take, "P_NAME", e.name, true)
      item_pos[poskey] = true
      done = done + 1
    end
  end
  if not region_names[tostring(e.ep)] then
    reaper.AddProjectMarker(0, true, e.base, e.base + e.len, tostring(e.ep), -1)
  end
end

reaper.Main_SaveProjectEx(0, RPP, 0)
reaper.GetSetProjectInfo(0, "PROJECT_ISDIRTY", 0, true)
reaper.ShowConsoleMsg(string.format("视频导入完成: 新插 %d，跳过 %d（计划 %d 集）\\n", done, skip, #EPS))
"""


def main():
    setup_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True, help="region_plan.csv（集数,预计base_s,时长_s,视频）")
    ap.add_argument("--rpp", required=True, help="目标工程 .RPP 完整路径")
    ap.add_argument("--out", required=True, help="生成的驱动 Lua 路径")
    a = ap.parse_args()

    rows = []
    with open(a.plan, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            ep = int(r["集数"])
            base = float(r["预计base_s"])
            length = float(r["时长_s"])
            video = (r.get("视频") or "").strip()
            name = f"第{ep:03d}集 {os.path.basename(video)}" if video else ""
            rows.append((ep, base, length, video, name))
    assert rows, "plan 为空"

    ep_rows = ",\n".join(
        f"  {{ep={ep}, base={base}, len={length}, video=[[{video}]], name=\"{name}\"}}"
        for ep, base, length, video, name in rows)
    lua = (LUA_TEMPLATE
           .replace("@PLAN@", os.path.basename(a.plan))
           .replace("@RPP@", a.rpp)
           .replace("@EP_ROWS@", ep_rows))

    with open(a.out, "w", encoding="utf-8") as f:
        f.write(lua)
    print(f"已生成 {os.path.abspath(a.out)}（{len(rows)} 集）")
    print(f"下一步：python run_reaper_script.py --lua {a.out}")


if __name__ == "__main__":
    main()
