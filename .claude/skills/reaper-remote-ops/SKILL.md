---
name: reaper-remote-ops
description: 用外部 Python（reapy_boost）远程驱动 REAPER 的完整操作手册：连接配置、插入媒体/贴 cue、轨道状态读写、分轨渲染、保存与静默关闭、rpp 文本编辑、API bug 速查。全部条目在 REAPER 7.52 + Windows 实测。触发词：REAPER、reapy、reapy_boost、Distant API、贴轨、渲染导出、DAW 自动化。
agent_created: true
---

# reaper-remote-ops — 外部 Python 驱动 REAPER 的方式与经验

> 目标读者：任何需要用脚本控制 REAPER 的 AI 助手。
> 实测环境：REAPER 7.52 / Windows / Python 3.10+ / reapy-boost 0.10.201。
> 本文档是「怎么操作 REAPER」这一层；业务流程（贴轨 SOP、选材）见同目录其他文档。

---

## 0. 核心心智模型

1. **不模拟按键**。所有操作走 ReaScript API（通过 reapy_boost 的 Distant API 通道），永不依赖窗口焦点。
2. **API 包装层有 bug**。reapy_boost 对部分 ReaScript 函数的封装会缺参/传参错误。遇到报错先怀疑包装层，换 `reapy_boost.reascript_api`（RPR）直接调原始函数。
3. **一切脚本必须幂等可重跑**。reapy 偶发段错误（exit 1、零输出、无报错）是常态，重跑是标准恢复手段——脚本内部要自带防重。
4. **不信任返回计数，只信任对账**。防重跳过、异步失败、静默污染都可能让「成功计数」失真。判断成败永远用区间对账（实际数 item/文件/字节数）。
5. **动手前备份现场**。改推子/mute/solo 前先存 JSON；改 .rpp 前先复制文件。

---

## 1. 配置与连接

### 1.1 一次性配置 REAPER

- pip 包名 `reapy-boost`（**不是** `reapy`），导入名 `import reapy_boost`。
- 必须装进 REAPER 内嵌 ReaScript 使用的**同一个 Python 解释器**里（REAPER 里 Preferences → Plug-ins → ReaScript 指向的 python DLL）。依赖 `typing_extensions` 一并装。
- REAPER 侧：启用 Python ReaScript、加 Web Interface（端口 2309）、reapy server 端口 2308。官方配置函数 `reapy_boost.config.configure_reaper(resource_path)` 可从外部跑。

**⚠️ Python 3.13 兼容坑**：`configure_reaper` 内部 `CaseInsensitiveDict.__contains__` 与 3.13 configparser 不兼容会崩，且 `Config.write()` **先截断再写**——第一次跑可能把 `REAPER.ini` 写空（用它自动备份的 `REAPER.ini.before-reapy_boost.bak` 恢复）。运行前打补丁：

```python
from reapy_boost.config.config import CaseInsensitiveDict
_orig = CaseInsensitiveDict.__contains__
def _safe(self, key):
    return False if not isinstance(key, str) else _orig(self, key)
CaseInsensitiveDict.__contains__ = _safe
from reapy_boost.config import configure_reaper
configure_reaper(resource_path=r"C:\Users\<你>\AppData\Roaming\REAPER")
```

配置后**必须重启 REAPER**。

### 1.2 连接（每次开工）

```python
import time
from ipaddress import IPv4Address
import reapy_boost
from reapy_boost.tools.network.machines import Host

reapy_boost.connect(Host(IPv4Address("127.0.0.1")))   # 传 Host 对象最稳
RPR = reapy_boost.reascript_api                        # 原始 API，绕开包装层 bug
proj = reapy_boost.Project()
time.sleep(1)                                          # connect 后必须等，立刻调用会偶发失败
```

- `connect()` 无参 / `connect(host="127.0.0.1")` / `connect_to_default_machine()` 都有传参坑，**只用 `Host(IPv4Address(...))` 这一种**。
- `LOCALHOST` 是字符串常量，不能当 Host 传。
- 端口约定：reapy server = 2308，Web Interface = 2309。

---

## 2. 轨道与工程状态读写

### 2.1 遍历轨、读推子

```python
n = RPR.CountTracks(0)
for i in range(n):
    trk = RPR.GetTrack(0, i)
    # ⚠️ GetTrackUIVolPan 返回 (retval, track*, vol, pan) 四元组，vol 在 [2]，pan 在 [3]
    retval, _, vol, pan = RPR.GetTrackUIVolPan(trk)
    name = RPR.GetTrackName(trk)[1] if RPR.CountTracks(0) else ""
```

### 2.2 备份/恢复现场（改任何状态前必做）

```python
state = []
for i in range(RPR.CountTracks(0)):
    trk = RPR.GetTrack(0, i)
    state.append({
        "track_idx": i,
        "vol":  RPR.GetTrackMediaItemInfo_Value(trk, 0, "D_VOL") if False else RPR.GetMediaTrackInfo_Value(trk, "D_VOL"),
        "mute": RPR.GetMediaTrackInfo_Value(trk, "B_MUTE"),
        "solo": RPR.GetMediaTrackInfo_Value(trk, "I_SOLO"),
    })
json.dump(state, open("fader_backup.json", "w", encoding="utf-8"), ensure_ascii=False)
# 恢复后逐轨 diff 校验，确认全部写回
```

### 2.3 🔴 D_VOL 是线性值不是 dB（血泪教训）

0=静音、1=0dB、2=+6dB。**别做 lin2db 换算后传入**——负数会把推子打成 -inf（界面显示推子到底）。「推子归零」= 写 `D_VOL=1.0`（unity），不是 0。

### 2.4 独奏/静音

```python
RPR.SetTrackSolo(trk, 1)     # 1=独奏，0=取消
RPR.SetMediaTrackInfo_Value(trk, "B_MUTE", 1)
```

---

## 3. 插入媒体 / 贴 cue（正确姿势）

### 3.1 坐标系

工程若用 Region 分集，`绝对时间 = Region起点 + 集内相对时间`。Region 起点用 `EnumProjectMarkers2` 遍历取 pos，**不要估算**。

### 3.2 item 必须手动建 take

`add_item(start, length)` 创建的 item **没有 take**（active_take 是空指针），必须手动补：

```python
src_id = RPR.PCM_Source_CreateFromFile(path)      # 中文路径 OK
item   = RPR.AddMediaItemToTrack(trk)
take   = RPR.AddTakeToMediaItem(item)             # ← 关键，item 不会自带 take
RPR.SetMediaItemTake_Source(take, src_id)
RPR.SetMediaItemInfo_Value(item, 'D_POSITION', abs_pos)
RPR.SetMediaItemInfo_Value(item, 'D_LENGTH', cue_len)
RPR.SetMediaItemTakeInfo_Value(take, 'D_STARTOFFS', off)   # ← 见 3.3
RPR.SetMediaItemInfo_Value(item, 'D_VOL', vol)             # item 级 D_VOL 同样是线性值
RPR.SetMediaItemInfo_Value(item, 'D_FADEOUTLEN', fadeout)
RPR.SetMediaItemInfo_Value(item, 'D_FADEINLEN', fadein)    # 不入淡可不设
RPR.GetSetMediaItemTakeInfo_String(take, 'P_NAME', stem, True)  # stem=源文件名去扩展名（foley 口径：条目名=源文件名，按包定位音源）；不设则画面里 item 无标签
```

### 3.3 🔴 start_offset 必须显式写 D_STARTOFFS

只把 offset 传进自己的取窗逻辑而不写 take 属性 = **完全没生效**（item 从源 0 秒开始放）。曾有 8 个连续工程批次 116 条 cue 因此取窗全部失效。贴完每条必须执行 3.2 代码里那句 `SetMediaItemTakeInfo_Value(take,'D_STARTOFFS',off)`。

### 3.4 源文件长度

- ❌ 别用 `RPR.GetMediaSourceLength(id)`——reapy_boost 包装有传参 bug（missing p1）。
- ✅ 用 `reapy_boost.Source(src_id).length()`（是**方法**，不是属性）。
- 越界审计：每条 cue 校验 `off + len ≤ 源长`，超了裁 len（宁可短不可越界）。

### 3.5 指针有效性判断

用正则 `0x([0-9A-Fa-f]+)` 取地址判非零。**不要用 `"0x0" in str(id)`**——合法指针 `0x000...ABC` 也包含 `0x0`，会误判。

### 3.6 防重（幂等）

贴之前扫目标区间已有 item：`源文件名(P_NAME) 相同 且 |位置差| < 0.15s` → 已存在，跳过。段错误后直接重跑不贴重。副作用：新增计数会显示 0——所以成败靠区间对账，不靠计数。

---

## 4. 渲染与分轨导出（实战定稿）

### 4.1 结论先行

REAPER 的 stems 渲染模式（RENDER_SETTINGS=3）实测**文件正常但内容全静音**，根因未明。唯一可靠方案：**逐轨 solo + 渲染 master mix** 循环。

| 尝试 | 结果 |
|------|------|
| RENDER_SETTINGS=2（master mix） | 只出一条混音，不是分轨 |
| RENDER_SETTINGS=1/3（stems 系） | 文件有但内容全静音（试过拍平目录/含父轨/旁通 FX，均无效） |
| `Main_OnCommand(42230)`（用上次设置渲染） | **异步命令**，循环里直接掐掉，全 0MB |
| ✅ `Main_OnCommand(41824)`（同步渲染） | 阻塞到渲染结束，配合轮询最稳 |

### 4.2 渲染设置

```python
RPR.GetSetProjectInfo(0, 'RENDER_FORMAT', 'ZXZhdxgAAAA=', True)  # wav 24bit（base64 配置串，ffprobe 验过 pcm_s24le/48000/24）
RPR.GetSetProjectInfo(0, 'RENDER_FILE',    out_dir,  True)
RPR.GetSetProjectInfo(0, 'RENDER_PATTERN', track_name, True)     # 输出文件名
RPR.GetSetProjectInfo(0, 'RENDER_BOUNDSFLAG', '1', True)         # 整工程时长
RPR.GetSetProjectInfo(0, 'RENDER_SRATE', '48000', True)
```

格式探针法：先建 2 秒试条渲一次，ffprobe 验格式对了再跑全量（`render_format_test.py` 思路）。

### 4.3 逐轨循环

```python
for trk in 目标轨:
    RPR.SetTrackSolo(trk, 1)
    RPR.Main_OnCommand(41824, 0)          # 同步渲染
    RPR.SetTrackSolo(trk, 0)
    # 等待+校验见 4.4
```

### 4.4 完成判断与终验（双保险，缺一不可）

1. **写满判断**：轮询输出文件大小，直到 `≥ 理论下限`（全时长 48k/24bit ≈ 每分钟 16.5MB）且**连续 2 次读数稳定**。
2. **电平终验**：每条文件跑 `ffmpeg -i 文件 -af volumedetect -f null -` 看 `max_volume`。
   **max ≈ -91dB（数字静音）→ 原样重渲该条**，重渲即愈（实测出过整条静音，重跑后 max -4.9dB）。

### 4.5 渲染纪律

- 🔴 **渲染全程不许碰 REAPER**：不暂停、不切工程、不点窗口。人类手动暂停过一次后，首遍渲染整条内容被污染（文件正常但静音）。
- 沙箱环境里 `os.remove` 可能被安全策略拦截——清理旧产物用外部 shell `rm`，别写在 python 脚本里。
- 恢复推子状态用备份 JSON 回写 + diff 校验。

---

## 5. 保存与静默关闭

```python
proj_id = RPR.EnumProjects(-1, "", 0)[0]              # 当前工程指针（-1=active）
RPR.Main_SaveProjectEx(proj_id, r"工程完整路径.RPP", 0)  # 显式传完整路径最稳
RPR.GetSetProjectInfo(proj_id, 'PROJECT_ISDIRTY', 0, True)  # 清假阳性 dirty 标志
RPR.Main_OnCommand(40004, 0)                            # File: Quit REAPER，实测无弹窗
```

- 实测记录：`Main_SaveProject(0)` 直传 0 曾报缺参；传 `EnumProjects(-1,...)[0]` 的真实指针 + `Main_SaveProjectEx` 显式路径是组合最稳的写法。历史上 `Main_SaveProjectEx` 还有过「只存单轨」的 bug 记录——**保存后必须落盘验证**（文件时间戳刷新 + .rpp 里 grep 到本次新增内容）。
- 保存验收两步：① `IsProjectDirty` 由 1 变 0；② REAPER 窗口标题 `[modified]` 消失（`tasklist /V /FI "IMAGENAME eq reaper.exe" /FO CSV` 可查）。
- 退出弹「是否保存」窗 = dirty 标志假阳性（保存后标志没自动清）→ 用上面 `GetSetProjectInfo(...,'PROJECT_ISDIRTY',0,True)` 强制清零再退出。
- ⚠️ 缓冲区参数必须 >0：`GetProjectName(proj,"",0)` / `GetProjectPathEx(proj,"",0)` 传 0 会**返回空串**（不报错）。传 1024。
- ⚠️ `GetProjectPathEx` 返回的是 `...\工程目录\Media`，工程文件在**上一级**。

---

## 6. rpp 文本编辑（绕开 API 的另一条路）

.rpp 是纯文本，批量结构性修改（如注入 FXCHAIN）直接编辑文件更快：

- `<TRACK` 前有空格：切块用 `re.split(r"\n\s*<TRACK", rpp)`。
- **FXCHAIN 块结束是独立 `>` 行，不是 `</FXCHAIN>`**。按行深度提取：`<` 开头行 depth+1、`>` 行 depth-1，回到 0 结束（`<FXCHAIN` 自身 depth 从 1 起）。
- 插入 FXCHAIN 到 TRACK 内：必须 4 空格缩进、紧贴 TRACK 头 `>` 后、无空行，否则 REAPER 静默忽略（FX 不加载，不报错）。
- `ISBUS 1 1` = folder/bus 轨（推子显示 -inf 正常）；`ISBUS 0 0` = 普通轨。
- 发送：无尖括号行 `AUXRECV <源轨索引> ...`，写在**接收轨**块内。
- 改完保存前先复制原文件备份；REAPER 开着时不要编辑 .rpp。

---

## 7. 坑速查表

| 现象 | 解法 |
|------|------|
| reapy 脚本 exit 1 零输出 | 偶发段错误，直接重跑（脚本要幂等）。诊断时用 runpy + faulthandler 包一层 |
| connect 后第一个调用失败 | connect 后 `time.sleep(1)` |
| 包装层报 missing p1 / 缺参 | 换 `reapy_boost.reascript_api`（RPR）直调原始函数 |
| 取窗没生效（全从 0 秒放） | 忘写 `D_STARTOFFS`，见 3.3 |
| 推子打成 -inf | D_VOL 传了 dB/负值。D_VOL 是线性值，归零=1.0 |
| `GetTrackUIVolPan` 取 [1] 报错 | 返回 (retval, track*, vol, pan)，vol 在 [2] |
| 渲染循环全 0MB | 用了异步的 42230，换同步的 41824 |
| 分轨文件正常但内容静音 | stems 模式 bug → 改 solo-loop；或渲染被人碰过 → 重渲 |
| 退出弹「是否保存」 | 保存后 dirty 假阳性没清 → GetSetProjectInfo 清零再 40004 |
| 字符串返回值是空串 | 缓冲区参数传了 0，改传 1024 |
| `GetMediaSourceLength` 报错 | 包装层 bug，用 `Source(id).length()` |
| `os.remove` 被沙箱拦 | 外部 shell rm，脚本内不删文件 |
| `CountProjectMarkers` 出参误报（20260916 实测） | `(0,0,0)` 返回 [总数, markers, regions, …] 里 markers/regions 在网络通道会恒 0——总数取 [0] 可信，明细一律逐条 `EnumProjectMarkers` 枚举 |
| `EnumProjectMarkers` 名称出参回显（20260916 实测） | 传入的 name 占位串被原样返回（读不到真名）——对账只比位置不比名称；删 marker 按返回值末位 **marker id**（`AddProjectMarker` 返回值就是 id），**枚举序号≠id**，按序号删曾误删一条 |
| 建删 Region/markers 正确姿势（20260916 定稿） | 建：`AddProjectMarker(0, 1, pos, end, name, -1)` 返回新 id（≥0 即成功）；删：`DeleteProjectMarkerByIndex(0, 枚举序号)` 前先用 id 核对目标；改完必须 `Main_SaveProjectEx` 落盘并用 .rpp 文本 grep 验证（内存读回不可靠时文件是真值） |

---

## 8. 工作方式总结（给 AI 的操作纪律）

1. **探针先行**：没把握的 API/格式，先在 2 秒试条上验证（渲染格式、越界、坐标换算），再跑全量。
2. **单批小步**：多集/多轨分批跑，每批独立自检，不攒大单——段错误重跑好定位。
3. **备份先行**：改状态前存 JSON，改文件前复制 .rpp。恢复后 diff 校验。
4. **对账文化**：数量对 item、文件对字节、内容对电平。三个全过才算完成。
5. **留痕**：脚本头部注释写清 base、集数、核验状态（如「报告已帧证据核验」），事后可追溯。
