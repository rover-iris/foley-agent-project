# -*- coding: utf-8 -*-
# 探针：第001集测试工程状态实测（轨序/BASE/条目数/dirty），改工程前状态快照
# 依据 reaper-foley-workflow pitfalls：清代理→connect(Host(IPv4))→sleep(1)→真函数验证
# 出参坑：GetProjectName(proj,"",1024)名字在[2]；CountProjectMarkers(0,0,0)三参、真实数在[0]
import os, json, time
for k in ("HTTP_PROXY","HTTPS_PROXY","ALL_PROXY","http_proxy","https_proxy","all_proxy"):
    os.environ.pop(k, None)
os.environ["NO_PROXY"] = "127.0.0.1,localhost"
from ipaddress import IPv4Address
import reapy_boost
from reapy_boost.tools.network.machines import Host

reapy_boost.connect(Host(IPv4Address("127.0.0.1")))
RPR = reapy_boost.reascript_api
time.sleep(1)

n_tr = RPR.CountTracks(0)
print("轨道数:", n_tr)
assert n_tr == 50, f"轨数与交接不符: {n_tr}"

mk = RPR.CountProjectMarkers(0, 0, 0)
mk_total = mk[0] if isinstance(mk, (list, tuple)) else mk
print("marker/region 总数:", mk_total, "=> BASE =", 0 if mk_total == 0 else "非0需实测!")

pn = RPR.GetProjectName(0, "", 1024)
print("工程名:", pn[2] if len(pn) > 2 else pn)
pp = RPR.GetProjectPathEx(0, "", 1024)
print("工程媒体路径:", pp[1] if len(pp) > 1 else pp)

dirty = RPR.GetSetProjectInfo(0, "PROJECT_ISDIRTY", 0, False)
print("PROJECT_ISDIRTY(改前):", dirty)

# 轨序 + 状态快照（改前存 JSON，纪律：改状态前备份）
state = []
for i in range(n_tr):
    tid = RPR.GetTrack(0, i)
    tn = RPR.GetTrackName(tid, "", 512)
    name = tn[1] if isinstance(tn, (list, tuple)) and len(tn) > 1 else str(tn)
    vp = RPR.GetTrackUIVolPan(tid, 0.0, 0.0)
    vol = vp[2] if len(vp) > 2 else None
    pan = vp[3] if len(vp) > 3 else None
    mute = RPR.GetMediaTrackInfo_Value(tid, "B_MUTE")
    solo = RPR.GetMediaTrackInfo_Value(tid, "I_SOLO")
    n_items = RPR.CountTrackMediaItems(tid)
    isbus = RPR.GetMediaTrackInfo_Value(tid, "I_FOLDERDEPTH")
    state.append({"idx": i, "name": name, "vol": vol, "pan": pan,
                  "mute": int(mute), "solo": int(solo),
                  "items": n_items, "folderdepth": isbus})
    print(f"[{i:2d}] {name:<14} items={n_items:<3d} vol={vol:.2f} mute={int(mute)} solo={int(solo)}")

with open(r"C:\Users\Administrator\Desktop\见习音效师工程\cues\track_state_ep001_20260910.json", "w", encoding="utf-8") as f:
    json.dump(state, f, ensure_ascii=False, indent=1)
print("状态快照已存 cues/track_state_ep001_20260910.json")

# 目标轨现有条目清点（应为 0；非 0 则防重逻辑按对账兜底）
targets = list(range(7, 13)) + list(range(14, 29))
occupied = {i: state[i]["items"] for i in targets if state[i]["items"] > 0}
print("目标轨(7-12,14-28)已有条目:", occupied if occupied else "全部为空 ✓")
print("探针通过 ✓")
