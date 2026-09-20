# -*- coding: utf-8 -*-
# 第001集贴轨 v1 — BASE=0（工程无 region，绝对时间=t_local）
# 测试单口径（制作人拍板 2026-09-10）：跳过所有图片帧核验（不读 jpg、不跑门禁），
#   直接按报告文本贴轨，文本时间=施工时间，不做偏移修正。本条留痕。
# 报告：C:\Users\Administrator\Desktop\权臣CP视频分析报告\第001集_分析报告.md（7 个【自动】事件；
#   【手动】6 项不配、【对白】跳过）
# 模板：place_ep26_v1.py（连接/防重/贴轨/D_STARTOFFS/对账逻辑原样保留）
#   仅改：头部 BASE/S/CUES、DB 路径、审计轨序换本单轨序、P_NAME 写源文件名 stem
#   （2026-09-09 拍板：REAPER 条目名=源 stem，label 仅日志）、新增保存块（reaper-remote-ops §5）。
# 轨序（实测，0 起）：0=Video; 7~12=环境fx 01~06; 14=脚步; 15=脚步(D); 16=衣物摩擦; 17~28=动作fx 03~14
import time, sys, sqlite3, os
from ipaddress import IPv4Address
import reapy_boost
from reapy_boost.tools.network.machines import Host
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
sys.path.insert(0, r"C:/Users/Administrator/Desktop/音效工作流交接包_20260907/scripts/pipeline")

reapy_boost.connect(Host(IPv4Address("127.0.0.1")))
RPR = reapy_boost.reascript_api
p = reapy_boost.Project()
time.sleep(1)
assert hasattr(RPR, "AddMediaItemToTrack"), "reascript_api 未就绪"
db = sqlite3.connect(r"C:/Users/Administrator/Desktop/音效工作流交接包_20260907/scripts/pipeline/asset_library.db")

def q(name):
    r = db.execute("SELECT path FROM assets WHERE name=? LIMIT 1", (name,)).fetchone()
    if r: return r[0]
    r = db.execute("SELECT path FROM assets WHERE name LIKE ? LIMIT 1", (name + '%',)).fetchone()
    if not r: raise RuntimeError(f"库里没有: {name}")
    return r[0]

S = {
    'DOCKA':  q("MLCK LOCATION Marketplace Big 01"),
    'SIP':    q("BT HUMAN Mouth Water Slurp"),
    'LAUGHA': q("MLCK LOCATION Marketplace Small 04"),
    'GARDENA':q("QP02 0220 Wind coniferous consistent light forest ro"),
    'SNIPA':  q("CFCK CLAW Garden Small Cutter Snip Close Open"),
    'SNIPB':  q("CFCK CLAW Small Garden Cutter Snip Close"),
    'CLTHA':  q("CC-CK Movement Cloth 03"),
    'THUDA':  q("CFDS BODY FALL Dirt Simple Small"),
    'THUDB':  q("CFDS BODY FALL Grass Simple Small"),
    'CLTHB':  q("CC-CK Movement Cloth 06"),
    'CLTHC':  q("CC-CK Movement Throw Cloth 02"),
    'THUDC':  q("CFDS BODY FALL Dirt Simple Medium"),
}

PROJ_PATH = r"D:\reaper工程\权臣CP01_贴轨测试_20260910\权臣CP01_贴轨测试.rpp"
BASE = 0.0
USED_TRACKS = [7, 8, 9, 16, 17, 18, 19]
CUES = [
    # (track_idx, t_local, source_key, len, label, fadeout, start_offset, vol, fadein)
    ( 7,  0.0, 'DOCKA',  22.0, "码头环境底:人群嘈杂",  1.5, 30.0, 0.4, None),
    (17,  1.0, 'SIP',     2.0, "捧碗啜饮(碗贴嘴)",    0.4,  2.0, 0.6, None),
    ( 8, 11.0, 'LAUGHA',  2.0, "围观人群群笑",        1.0, 20.0, 0.6, None),
    ( 9, 22.0, 'GARDENA', 26.4, "内院环境底:风林鸟",   2.0, 20.0, 0.4, None),
    (18, 22.4, 'SNIPA',   0.6, "剪刀开合轻响1",       0.15, 0.0, 0.5, None),
    (18, 23.9, 'SNIPB',   0.6, "剪刀轻咔嚓2",         0.15, 3.0, 0.5, None),
    (16, 26.0, 'CLTHA',   1.8, "衣袍窸窣(跪拜起手)",  0.6,  4.0, 0.5, None),
    (19, 26.4, 'THUDA',   0.8, "双膝触地闷响",        0.4,  0.0, 0.6, None),
    (19, 30.2, 'THUDB',   0.7, "额头贴地闷响",        0.4,  0.0, 0.6, None),
    (16, 37.2, 'CLTHB',   1.4, "叩拜余韵极轻衣袍",    0.6,  6.0, 0.3, None),
    (16, 43.5, 'CLTHC',   0.9, "扑跪衣袍急摩擦",      0.4,  2.0, 0.8, None),
    (19, 44.2, 'THUDC',   0.9, "双膝砸地闷响",        0.5,  0.0, 0.8, None),
]

# --- 开工自证：工程名 + 实测轨序（RPR 全出参直调，手册 §7） ---
try:
    buf = RPR.GetProjectName(p.id, "", 512)
    name = next((x for x in buf if isinstance(x, str) and x), "(空)")
    print("工程名:", name)
except Exception as e:
    print("工程名读取失败(不阻塞):", e)
for idx in USED_TRACKS:
    t = p.tracks[idx]
    try:
        tn = RPR.GetTrackName(t.id, "", 512)
        tname = next((x for x in tn if isinstance(x, str) and x), "(空)")
    except Exception as e:
        tname = f"读取失败:{e}"
    print(f"  轨[{idx}] = {tname}")

dur = dict(db.execute("SELECT path, duration FROM assets").fetchall())
placed, failed = [], []

def put(ti, t_local, src, ln, label, fade=None, off=None, vol=None, fadein=None):
    pos = BASE + t_local
    t = p.tracks[ti]
    for it in t.items:
        if abs(it.position - pos) < 0.15:
            try:
                _ = it.takes[0].source.filename
                return None
            except Exception:
                RPR.DeleteTrackMediaItem(t.id, it.id)
    item_id = RPR.AddMediaItemToTrack(t.id)
    take_id = RPR.AddTakeToMediaItem(item_id)
    src_id = RPR.PCM_Source_CreateFromFile(src)
    if not src_id or int(str(src_id).split("0x")[1], 16) == 0:
        return "src失败"
    RPR.SetMediaItemTake_Source(take_id, src_id)
    RPR.SetMediaItemInfo_Value(item_id, "D_POSITION", pos)
    RPR.SetMediaItemInfo_Value(item_id, "D_LENGTH", ln)
    if off is not None: RPR.SetMediaItemTakeInfo_Value(take_id, "D_STARTOFFS", off)
    if fade: RPR.SetMediaItemInfo_Value(item_id, "D_FADEOUTLEN", fade)
    if fadein: RPR.SetMediaItemInfo_Value(item_id, "D_FADEINLEN", fadein)
    if vol: RPR.SetMediaItemInfo_Value(item_id, "D_VOL", vol)
    # 立即验证源
    ok = False
    for tk_i in range(RPR.CountTakes(item_id)):
        tk = RPR.GetTake(item_id, tk_i)
        s = RPR.GetMediaItemTake_Source(tk)
        if s and int(str(s).split("0x")[1], 16) != 0:
            ok = True
    if not ok:
        RPR.DeleteTrackMediaItem(t.id, item_id)
        return "source丢失"
    # REAPER 条目名 = 源文件名 stem（2026-09-09 拍板）；label 仅日志
    stem = os.path.splitext(os.path.basename(src))[0]
    RPR.GetSetMediaItemTakeInfo_String(take_id, "P_NAME", stem, True)
    placed.append((pos, ti, label, ln))
    return True

todo = CUES
for rnd in (1, 2):
    todo2 = []
    for cue in todo:
        ti, t_local, skey, ln, label = cue[0], cue[1], S[cue[2]], cue[3], cue[4]
        fade, off, vol, fi = cue[5], cue[6], cue[7], cue[8]
        r = put(ti, t_local, S[cue[2]], ln, label, fade, off, vol, fi)
        if r is None: continue
        if r is not True: todo2.append(cue)
    todo = todo2
    if not todo:
        break
    time.sleep(0.6)

print(f"\n=== 第001集贴轨 {len(placed)}/{len(CUES)} 条 ===")
if todo: print("失败:", [(c[4],) for c in todo])

# 自检1: 重叠（本单所用轨）
print("\n=== 重叠自检 ===")
bad = 0
for idx in USED_TRACKS:
    t = p.tracks[idx]
    items = sorted(t.items, key=lambda x: x.position)
    for a, b in zip(items, items[1:]):
        if a.position + a.length > b.position + 0.05:
            print(f"⚠️ [{idx}] {a.position:.2f}s vs {b.position:.2f}s"); bad += 1
print("重叠通过 ✓" if bad == 0 else f"{bad} 处重叠")

# 自检2: 区间对账（不信计数器，数 item）
print("\n=== 区间对账 (BASE=0, 0~48.5s) ===")
n = 0
for idx in USED_TRACKS:
    t = p.tracks[idx]
    for it in sorted(t.items, key=lambda x: x.position):
        if BASE - 0.01 <= it.position < BASE + 48.5:
            try:
                fn = os.path.basename(it.takes[0].source.filename)
            except Exception:
                fn = "!!无源!!"
            print(f"  [{idx}] {it.position-BASE:6.2f}s +{it.length:.2f}  {fn[:46]}")
            n += 1
print(f"所用轨共 {n} 条 (期望 {len(CUES)})")
other = 0
total_tracks = RPR.CountTracks(0)
for idx in range(total_tracks):
    if idx in USED_TRACKS: continue
    t = p.tracks[idx]
    for it in t.items:
        if BASE - 0.01 <= it.position < BASE + 48.5:
            other += 1
            print(f"  ⚠️ 其他轨[{idx}] 发现 item @ {it.position:.2f}s")
print(f"其他轨 item 数: {other} (期望 0)")

# 自检3: 全工程 offset 越界审计（本单所用轨）
print("\n=== offset 越界审计 ===")
viol = 0
for idx in USED_TRACKS:
    t = p.tracks[idx]
    for it in t.items:
        try:
            fn = it.takes[0].source.filename
        except Exception:
            print(f"⚠️ [{idx}] {it.position:.2f}s 无源空块"); viol += 1; continue
        key = fn.replace("/", "\\").lower()
        d = None
        for path, du in dur.items():
            if path.lower().endswith(key) or path.lower() == key:
                d = du; break
        if d is None: continue
        off = RPR.GetMediaItemTakeInfo_Value(it.takes[0].id, "D_STARTOFFS")
        if off + it.length > d + 0.05:
            print(f"⚠️ [{idx}] {it.position:.2f}s off={off:.2f}+len={it.length:.2f} > 源长{d:.2f}  {os.path.basename(fn)[:40]}")
            viol += 1
print("offset 全部在界内 ✓" if viol == 0 else f"{viol} 条越界")

# 保存工程（REAPER 保持打开）— reaper-remote-ops §5 定稿写法
print("\n=== 保存工程 ===")
proj_id = RPR.EnumProjects(-1, "", 1024)[0]
RPR.Main_SaveProjectEx(proj_id, PROJ_PATH, 0)
RPR.GetSetProjectInfo(proj_id, 'PROJECT_ISDIRTY', 0, True)
time.sleep(1.0)
try:
    mt = os.path.getmtime(PROJ_PATH)
    print("落盘时间戳:", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mt)))
    txt = open(PROJ_PATH, "r", encoding="utf-8", errors="ignore").read()
    hits = sum(txt.count(os.path.splitext(k)[0]) for k in
               ["CC-CK Movement Cloth 06", "CFDS BODY FALL Dirt Simple Small", "Marketplace Big 01"])
    print(".rpp 内容 grep 命中(抽样源名):", hits)
except Exception as e:
    print("!! 保存落盘验证失败:", e)
db.close()
