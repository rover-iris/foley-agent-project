---
name: reaper-remote-ops
description: 用外部 Python（reapy_boost）远程驱动 REAPER 的完整操作手册：连接配置、插入媒体/贴 cue、轨道状态读写、分轨渲染、保存与静默关闭、rpp 文本编辑、API bug 速查。全部条目在 REAPER 7.52 + Windows 实测。触发词：REAPER、reapy、reapy_boost、Distant API、贴轨、渲染导出、DAW 自动化。
agent_created: true
---

# reaper-remote-ops — 外部 Python 驱动 REAPER

> 目标读者：需要用脚本控制 REAPER 的 AI 助手。实测环境：REAPER 7.52 / Windows / Python 3.10+ / reapy-boost 0.10.201。
> 本册是「怎么操作 REAPER」层的**唯一权威**；业务流程（贴轨 SOP、选材）见 reaper-foley-workflow，foley 工作流域的坑见其 references/pitfalls.md。

> **维护规则（改本册前必读）**：
> 1. 坑速查表条目编号 R-xx 递增、**不复用**（删条目留空号），跨文件引用写作 `[R-xx]`；
> 2. 条目=「症状 → if/动作」，分支指令式、判据量化，**禁案情叙述与日期戳**——历史归交接包 `12_变更记录.md`；
> 3. 正文 §1~§6 是操作定式，§7 只做速查+指针（正文已有的内容 §7 引用而非复述）；
> 4. 改后三副本同步（交接包 skill快照 → 工程内 .claude/skills → 全局 ~/.claude/skills）+ sha1 校验。

---

## 0. 核心心智模型

1. **不模拟按键**：一切走 ReaScript API（reapy_boost Distant API 通道），永不依赖窗口焦点。
2. **先怀疑包装层**：reapy_boost 对部分函数封装有缺参/传参 bug；报错先换 `reapy_boost.reascript_api`（RPR）直调原始函数。
3. **脚本必须幂等可重跑**：偶发段错误（exit 1 零输出）是常态，重跑是标准恢复手段，脚本内自带防重。
4. **不信计数，只信对账**：成败永远用区间对账（数 item/文件/字节），不用返回计数。
5. **动手前备份现场**：改状态前存 JSON，改 .rpp 前复制文件。

---

## 1. 配置与连接

### 1.1 一次性配置（换机/重装）

**首选自动装机**：`connect_reaper.py --setup`（foley skill scripts 目录）——幂等 S0~S4：装 reapy_boost → configure_reaper 写配置 → 生成 `__startup.lua` 自启桥 → 拉起 REAPER 全程自检。手动路径仅当自动失败时走：

- pip 包名 `reapy-boost`（导入名 `reapy_boost`），**必须装进 REAPER ReaScript 指向的同一个 Python 解释器**；`typing_extensions` 一并装（依赖声明不全）。
- REAPER 侧三要素：启用 Python ReaScript、Web Interface、activate_reapy_server 注册进 Actions（`reapy_boost.config.configure_reaper()` 一次办齐，配置后**必须重启 REAPER**）。
- if Python 3.13 → configure_reaper 会崩（`CaseInsensitiveDict.__contains__` 与 3.13 configparser 不兼容）且 `Config.write()` 先截断再写可能写空 reaper.ini（备份 `.before-reapy_boost.bak` 可恢复）——运行前打补丁：

```python
from reapy_boost.config.config import CaseInsensitiveDict
_orig = CaseInsensitiveDict.__contains__
def _safe(self, key):
    return False if not isinstance(key, str) else _orig(self, key)
CaseInsensitiveDict.__contains__ = _safe
from reapy_boost.config import configure_reaper
configure_reaper(resource_path=r"C:\Users\<你>\AppData\Roaming\REAPER")
```

### 1.2 连接（每次开工）

**唯一入口**：`connect_reaper.py`（自诊断自愈 L0~L3，末行 RESULT JSON 机器可读；`--launch` 允许自启 REAPER）。脚本内连接定式：

```python
import time
from ipaddress import IPv4Address
import reapy_boost
from reapy_boost.tools.network.machines import Host

reapy_boost.connect(Host(IPv4Address("127.0.0.1")))   # 不显式传端口：自动走 Web 口发现再握手服务端
RPR = reapy_boost.reascript_api                        # 原始 API，绕开包装层 bug
proj = reapy_boost.Project()
time.sleep(1)                                          # connect 后必须等，立刻调用偶发失败
```

- **端口禁手抄**：三级权威源 = EXTSTATE 实读 > reaper.ini csurf 实配 > 库默认常量（`reapy_boost.config`）。`connect()` 无参 / `connect(host=...)` / `connect_to_default_machine()` 均有传参坑，只用 `Host(IPv4Address(...))` 一种；`LOCALHOST` 是字符串常量不能当 Host 传。
- 连接后必须空壳断言（`hasattr(RPR, "CountTracks")`）——import 时自动 connect 失败会被静默吞掉（见 [R-27]）。

---

## 2. 轨道与工程状态读写

### 2.1 遍历轨、读推子

```python
n = RPR.CountTracks(0)
for i in range(n):
    trk = RPR.GetTrack(0, i)
    retval, _, vol, pan = RPR.GetTrackUIVolPan(trk)   # 四元组：vol 在 [2]，pan 在 [3]
```

⚠️ `GetTrackName` 直调报 missing p1/p2，补缓冲区后返回值仍是轨指针非名字——**轨名核验走 RPP 离线解析或对象方法 `track.name`**，不用该 API。

### 2.2 备份/恢复现场（改任何状态前必做）

```python
state = []
for i in range(RPR.CountTracks(0)):
    trk = RPR.GetTrack(0, i)
    state.append({
        "track_idx": i,
        "vol":  RPR.GetMediaTrackInfo_Value(trk, "D_VOL"),
        "mute": RPR.GetMediaTrackInfo_Value(trk, "B_MUTE"),
        "solo": RPR.GetMediaTrackInfo_Value(trk, "I_SOLO"),
    })
json.dump(state, open("fader_backup.json", "w", encoding="utf-8"), ensure_ascii=False)
# 恢复后逐轨 diff 校验，确认全部写回
```

### 2.3 🔴 D_VOL 是线性值不是 dB

0=静音、1=0dB（unity）、2=+6dB。**禁 lin2db 换算后传入**——负数把推子打成 -inf。「推子归零」= 写 `D_VOL=1.0`，不是 0。

### 2.4 独奏/静音

```python
RPR.SetTrackSolo(trk, 1)     # 1=独奏，0=取消
RPR.SetMediaTrackInfo_Value(trk, "B_MUTE", 1)
```

---

## 3. 插入媒体 / 贴 cue

### 3.1 坐标系

Region 分集工程：`绝对时间 = Region起点 + 集内相对时间`。Region 起点离线解析 RPP（extract_regions.py）或实测传入，**禁估算**；Region 名（=集号）在线取不到，名权威=离线 RPP 解析（见 [R-13]）。

### 3.2 item 必须手动建 take

`AddMediaItemToTrack` 创建的 item **没有 take**，必须手动补：

```python
src_id = RPR.PCM_Source_CreateFromFile(path)      # 中文路径 OK
item   = RPR.AddMediaItemToTrack(trk)
take   = RPR.AddTakeToMediaItem(item)             # ← 关键，item 不自带 take
RPR.SetMediaItemTake_Source(take, src_id)
RPR.SetMediaItemInfo_Value(item, 'D_POSITION', abs_pos)
RPR.SetMediaItemInfo_Value(item, 'D_LENGTH', cue_len)
RPR.SetMediaItemTakeInfo_Value(take, 'D_STARTOFFS', off)   # ← 见 3.3
RPR.SetMediaItemInfo_Value(item, 'D_VOL', vol)             # 线性值，见 2.3
RPR.SetMediaItemInfo_Value(item, 'D_FADEOUTLEN', fadeout)
RPR.SetMediaItemInfo_Value(item, 'D_FADEINLEN', fadein)    # 不入淡可不设
RPR.GetSetMediaItemTakeInfo_String(take, 'P_NAME', stem, True)  # 写条目名；不设则界面 item 无标签
```

读侧注意：`GetSetMediaItemTakeInfo_String` 读取时 setNewValue 必须 False，且原始返回元组含指针串易错——**条目名核验以落盘 RPP 的 NAME 行为准**。

### 3.3 🔴 start_offset 必须显式写 D_STARTOFFS

只把 offset 传进自己的取窗逻辑而不写 take 属性 = **完全没生效**（item 从源 0 秒起播）。每条必须执行 3.2 里的 `SetMediaItemTakeInfo_Value(take,'D_STARTOFFS',off)`。

### 3.4 源文件长度与越界

- ❌ `RPR.GetMediaSourceLength(id)` 包装层传参 bug（missing p1）；✅ `reapy_boost.Source(src_id).length()`（**方法**非属性）。
- 读源文件路径：`GetMediaSourceFileName(src, "", 1024)`（`GetSourceFilename` 这个名字不存在）。
- 越界审计：每条校验 `off + len ≤ 源长`，超出裁 len（宁短勿越界）。

### 3.5 指针有效性判断

正则 `0x([0-9A-Fa-f]+)` 取地址判非零。**禁用 `"0x0" in str(id)`**——合法指针也含 `0x0`，误判。

### 3.6 防重（幂等）

贴前扫目标区间已有 item：`P_NAME 相同 且 |位置差| < 0.15s` → 已存在跳过。副作用：新增计数显示 0——成败靠区间对账不靠计数（心智模型 4）。

---

## 4. 渲染与分轨导出

### 4.1 结论

stems 渲染模式（RENDER_SETTINGS=3）**文件正常但内容全静音**（根因未明）。唯一可靠方案：**逐轨 solo + 渲染 master mix** 循环。

| 路径 | 结果 |
|------|------|
| RENDER_SETTINGS=2 | 只出一条混音，非分轨 |
| RENDER_SETTINGS=1/3 | 文件有但全静音 |
| `Main_OnCommand(42230)` | 异步命令，循环里被掐，全 0MB |
| ✅ `Main_OnCommand(41824)` | 同步渲染，阻塞到结束 |

⚠️ 未解现象：显式 `RENDER_STEMS=0` 后渲染全静音，原因未查——重开渲染方案课题前必须先复测，复测通过前维持 solo-loop。

### 4.2 渲染设置

```python
RPR.GetSetProjectInfo(0, 'RENDER_FORMAT', 'ZXZhdxgAAAA=', True)  # wav 48k/24bit（base64 配置串）
RPR.GetSetProjectInfo(0, 'RENDER_FILE',    out_dir,  True)
RPR.GetSetProjectInfo(0, 'RENDER_PATTERN', track_name, True)     # 输出文件名
RPR.GetSetProjectInfo(0, 'RENDER_BOUNDSFLAG', '1', True)         # 整工程时长
RPR.GetSetProjectInfo(0, 'RENDER_SRATE', '48000', True)
```

换格式先探针：2 秒试条渲一次，ffprobe 验证（pcm_s24le/48000）后再跑全量。

### 4.3 逐轨循环

```python
for trk in 目标轨:
    RPR.SetTrackSolo(trk, 1)
    RPR.Main_OnCommand(41824, 0)          # 同步渲染
    RPR.SetTrackSolo(trk, 0)
    # 等待+校验见 4.4
```

### 4.4 完成判断与终验（双保险缺一不可）

1. **写满判断**：轮询文件大小 ≥ 理论下限（48k/24bit ≈ 16.5MB/分钟）且**连续 2 次读数稳定**。
2. **电平终验**：逐条 `ffmpeg -i 文件 -af volumedetect -f null -`；`max_volume ≈ -91dB`（数字静音）→ 原样重渲该条即愈。

### 4.5 渲染纪律

- 🔴 **渲染全程不许碰 REAPER**：不暂停、不切工程、不点窗口（手动暂停会污染当遍渲染：文件正常但静音）。
- 恢复推子用备份 JSON 回写 + diff 校验。

---

## 5. 保存与静默关闭

```python
proj_id = RPR.EnumProjects(-1, "", 1024)[0]             # 当前工程指针（-1=active）
RPR.Main_SaveProjectEx(proj_id, r"工程完整路径.RPP", 0)   # 显式完整路径，API 直存无窗口
RPR.Main_OnCommand(40004, 0)                            # File: Quit REAPER
```

定式（每条都是踩坑结论）：

1. **保存只走 API**：交互式保存命令（40022 另存为系）必弹二级窗、可能改写工程路径，**禁用**；40021（原地保存）为候选兜底，实测确认无弹窗前禁用。
2. **保存验收双证**：文件 mtime 刷新 + .rpp 内容 grep/重解析比对到本次改动。**dirty 标志不作判据**——7.52 假阳性：落盘成功后 IsProjectDirty 可恒 1、窗口[已修改]不消失，程序化清零无效；退出弹「是否保存」即此假阳性，人工处置。
3. `Main_SaveProject(0)` 缺参报错，用 `Main_SaveProjectEx`。
4. 缓冲区参数必须 >0：`GetProjectName(proj,"",0)` 传 0 返回空串不报错，传 1024。
5. `GetProjectPathEx` 返回 `...\工程目录\Media`，工程文件在**上一级**。
6. 退出确认：轮询 tasklist 进程真消失（进程还在=弹窗挡住了）。

---

## 6. rpp 文本编辑（绕开 API 的另一条路）

🔴 REAPER 开着时禁编辑 .rpp；改前先复制备份。

- 切轨块：`re.split(r"\n\s*<TRACK", rpp)`（`<TRACK` 前有空格）。
- 块提取按行深度：`<` 开头行 depth+1、独立 `>` 行 depth-1，回 0 结束——**FXCHAIN 结束是独立 `>` 行，不是 `</FXCHAIN>`**。
- TRACK 内插 FXCHAIN：4 空格缩进、紧贴 TRACK 头 `>` 后、无空行——格式错 REAPER **静默忽略**（FX 不加载不报错）。
- `ISBUS 1 1` = folder/bus 轨（推子 -inf 正常）；发送行 `AUXRECV <源轨索引> ...` 写在**接收轨**块内。
- 复用真实工程头部拼新轨块时，外层 `<REAPER_PROJECT` 本来就开着，**别按块平衡补 `>`**——多补会提前闭合工程块，后续轨块全被静默丢弃（打开 0 轨不报错）。生成后自检：`<TRACK` 计数=预期轨数。
- region 两行式：起点行带名+R 标志、终点行 `"" 1` 名空——离线解析器按两行式按 idx 成对取（起点取名、终点取 end），按老单行格式解析 0 命中。
- `NAME` 行对无空格值不写引号：`NAME 衣物摩擦` 与 `NAME "动作fx 02"` 并存——解析正则必须兼容无引号写法，否则跳过该行误取下一行带引号的值。
- SOURCE 是多行块（`<SOURCE WAVE>` 属性行…`>`），按行流式状态机读，禁假设单行形态。
- 资产 DB 的 name 字段是已去扩展名的 stem，**禁再 `splitext`**——`xxx 2.0` 的 `.0` 会被误剥；DB name 全串原样比对。

---

## 7. 坑速查表

> 症状 → 动作。正文 §1~§6 已有定式的不复述，给指针。

| 编号 | 现象 | 动作 |
|------|------|------|
| R-01 | 脚本 exit 1 零输出无报错 | 偶发段错误：直接重跑（脚本须幂等）；诊断用 runpy+faulthandler 包一层 |
| R-02 | connect 后第一个调用失败 | connect 后 `time.sleep(1)`（§1.2） |
| R-03 | 包装层报 missing p1/缺参 | 换 `reapy_boost.reascript_api`（RPR）直调原始函数 |
| R-04 | 取窗没生效（全从 0 秒放） | 忘写 `D_STARTOFFS` → §3.3 |
| R-05 | 推子打成 -inf | D_VOL 传了 dB/负值；线性值归零=1.0 → §2.3 |
| R-06 | `GetTrackUIVolPan` 取 [1] 报错 | 四元组，vol[2] pan[3] → §2.1 |
| R-07 | 渲染循环全 0MB | 用了异步 42230 → 换同步 41824（§4.1） |
| R-08 | 分轨文件正常但内容静音 | stems bug → solo-loop（§4.1）；渲染期被碰过 → 原样重渲；终验逐条 volumedetect（§4.4） |
| R-09 | 退出弹「是否保存」/dirty 恒 1 | 7.52 假阳性：验收=mtime+内容双证，dirty 不作判据；40022 系禁用 → §5 |
| R-10 | 字符串返回值空串不报错 | 缓冲区参数传了 0 → 传 1024 |
| R-11 | `GetMediaSourceLength` 报错 | 包装层 bug → `Source(id).length()`（§3.4） |
| R-12 | `os.remove` 被沙箱拦 | 脚本内不删文件；批量清理用外部 shell `rm` |
| R-13 | 枚举/计数 marker 出参位不可信 | 字符串/索引出参位网络通道**不回写**：只读数值可信位 r[0]/r[2]/r[3]/r[4]，禁 `int()` 转换空串；Region 名权威=离线 RPP 解析，在线对账只比位置；删 marker 按 `AddProjectMarker` 返回的 **id**（枚举序号≠id）；`EnumProjectMarkers2` 包装层崩溃禁用，用无后缀全参形式 `EnumProjectMarkers(i,0,0.0,0.0,"",0)`；`CountProjectMarkers(0,0,0)` 必须三参 |
| R-14 | 建删 Region/marker | 建：`AddProjectMarker(0,1,pos,end,name,-1)` 返回新 id；删：`DeleteProjectMarkerByIndex` 前先用 id 核对目标；改完 `Main_SaveProjectEx` 落盘 + .rpp grep 验证 |
| R-15 | 解包出参 TypeError | 字符串出参可能返回 list 非 tuple——解包/索引前先确认容器类型 |
| R-16 | 反复弹「脚本执行错误」裸窗（无脚本名无行号） | =reapy 服务没在跑。if 频发 → 查 reaper.ini `pythonlibpath64` 是否指第三方应用管理的 Python（**禁指 vendor Python**：应用更新会端掉 site-packages）→ 改指系统 Python + configure_reaper 重配（改前关 REAPER，退出会回写 ini）；排查先 netstat 看服务端端口（库默认 2308，权威源见 §1.2）是否 LISTENING |
| R-17 | import/connect 时 RecursionError | 服务没跑的客户端症状（Web 口通但 server 未起，激活重试递归爆栈）——不是脚本 bug，禁盲目重试；先起服务再连（connect_reaper.py L2 自愈） |
| R-18 | 显式指定端口连不上 | connect 不显式传端口：Host 默认走 Web 发现口读 EXTSTATE 再握手服务端；显式直连 Web 口不通（它是发现口不是 API 通道） → §1.2 |
| R-19 | activate 注册了但 Actions 搜不到 / Web 触发静默无效 | REAPER 不加载 kb.ini 注册：Actions → New action → Load ReaScript 手动 Load 一次（弹「任务控制」说明已在跑）；重启后由 `Scripts\__startup.lua` 自启，失效则回退手动 Load；动作 ID 从 kb.ini 现场解析（connect_reaper.py 内置），**禁手抄** |
| R-20 | 想用 `reaper.exe -nonewinst <脚本.py>` | **永久禁用**（.py 被当工程文件打开）。if 服务没起 → connect_reaper.py 自愈；if REAPER 未运行 → `--launch` |
| R-21 | 连接挂起/超时，想杀进程重来 | **禁杀挂起的客户端进程**：服务端 accept 回握手撞死客户端抛 WinError 10053，无兜 → 崩服+模态弹窗堵主线程，杀一个=崩一次。if 挂起 → 等自然超时；if 服务已崩（netstat 无 2308 LISTENING）→ connect_reaper.py 自愈重启。注：服务端 activate_reapy_server.py 已打补丁 v2（run_main_loop 捕获 Exception，兜住 10053 + 非协议字节 JSONDecodeError 两条崩服链；site-packages 机器级，备份 `.bak-20261012`，pip 重装 reapy_boost 须补回） |
| R-22 | 施工期另一会话在线枚举卡顿 | 未定论观察项：疑争用单线程服务。if 贴轨施工进行中 → 其他会话禁在线枚举，改离线 rpp 解析 |
| R-23 | 连接异常/端口拿不准 | 一律先跑 `connect_reaper.py` 自诊断（初次装机/换机 `--setup`），读 RESULT JSON 按 level 分支处置；**禁让用户手动试错、禁凭记忆写端口**（端口三级权威源见 §1.2） |
| R-24 | 自写连接器把服务端打死 | 旧版逐端口探针 HTTP 打 2308 裸协议口 → 服务端 json.loads 抛 JSONDecodeError 崩服（补丁 v2 已兜，见 R-21 注）。**禁自写端口探针**，连接只走 §1.2 定式 |
| R-25 | 进程在、端口在、HTTP 全超时假死 | REAPER 有**模态弹窗**堵主线程 → 只读探测窗口 + 报告人工点击，**绝不自动点弹窗**；「ReaScript 控制台输出」是非模态日志面板，不挡，无需关 |
| R-26 | 触发激活动作返回 200 但不执行 | reaper-kb.ini 损坏（SCR 行粘连）→ 检查 `%APPDATA%\REAPER\reaper-kb.ini` 行格式 |
| R-27 | connect 不报错但 RPR 全空壳（AttributeError） | import 时自动 connect 失败被静默吞 → 连接后空壳断言（§1.2，rfw_common 内置）；自写脚本断言失败即退出，不带病跑 |
| R-28 | curl/python 访问 2308/2309 全挂或 502 | 系统代理劫持 127.0.0.1 请求 → import reapy_boost **之前**清 HTTP_PROXY/HTTPS_PROXY/ALL_PROXY、设 NO_PROXY=127.0.0.1,localhost（connect_reaper.py 已内置；urllib 调用走无代理 opener） |
| R-29 | Python 3.13 下 RPC 挂起/进程无声死 | reapy_boost 0.10.201 `http.client.parse_headers` 递归爆栈 → rfw_common 顶部已换迭代实现 + setrecursionlimit(20000) |
| R-30 | 逐条 RPC 长流程不稳（段错误率高） | RPC 只做一次注册+触发，批量操作写进 Lua 在 REAPER 进程内跑：`make_video_driver.py` + `run_reaper_script.py`（见 foley skill references/video-import.md） |
| R-31 | 查 REAPER 进程假阴性 | `tasklist` 全量输出后 grep（**禁 `tasklist //FI`**：Git Bash MSYS 转换致「进程不存在」假阴性）；PowerShell Get-Process 输出捕获不可靠，以 Bash 侧为准 |

---

## 8. 工作方式总结（操作纪律）

1. **探针先行**：没把握的 API/格式先在 2 秒试条上验证，再跑全量。
2. **单批小步**：多集/多轨分批跑，每批独立自检，不攒大单。
3. **备份先行**：改状态前存 JSON，改文件前复制 .rpp，恢复后 diff 校验。
4. **对账文化**：数量对 item、文件对字节、内容对电平，三个全过才算完成。
5. **留痕**：脚本头注释写清 base、集数、核验状态，事后可追溯。
