# 坑与 FAQ 速查

> 出问题先查这张表。每一条都是实机踩出来的，不是理论。

## REAPER API / reapy

| 现象 | 原因 | 解法 |
|------|------|------|
| reapy 脚本 exit 1、零输出、无报错 | reapy 偶发段错误 | 直接重跑（脚本必须幂等）。诊断用 `runpy` + `faulthandler` 包一层 |
| connect 后第一个调用失败 | 连接未稳定 | connect 后 `time.sleep(1)` |
| start_offset 传了但取窗没生效 | 没写 take 属性 | 必须显式 `SetMediaItemTakeInfo_Value(take,'D_STARTOFFS',off)`（引擎级坑） |
| `Main_SaveProject(0)` 报缺参 | reapy 缺封装 | 用 `Main_SaveProjectEx(0, <rpp路径>, 0)` |
| 保存后退出仍弹「是否保存」 | PROJECT_ISDIRTY 假阳性 | 见下方「保存与关闭」完整流程 |
| `GetTrackUIVolPan` 取 [1] 报错 | 返回四元组 `(retval, track*, vol, pan)` | vol 在 `[2]`，pan 在 `[3]` |
| `GetTrackName` 取返回值报错 | 返回元组 | 轨名在 `[1]` |
| 字符串返回值是空串（不报错） | 缓冲区参数传了 0 | `GetProjectName(proj,"",0)` 这类调用 size 必须传 >0（如 1024）；正确形式是三参 `(proj, buf, sz)`——今天实测 `GetProjectName("",512)` 两参会报 missing p2 |
| `CountProjectMarkers(0)` 单参调用进程秒退（exit 1 零输出，稳定复现非偶发段错误） | 带可选出参的函数缺省出参位，包装层段崩溃（与 GetProjectName 缺参同类；2026-09-09 实测） | 必须三参 `RPR.CountProjectMarkers(0, 0, 0)`；**只有返回元组第 1 位 retval 是真实总数，其余各位是对入参的回显，不能当计数读**（曾因误读回显 0 重复堆了多个 Region） |
| `EnumProjectMarkers2` 一调用进程就秒退（本机 reapy_boost 稳定复现） | 包装层崩溃，该函数禁用 | 用无后缀 `EnumProjectMarkers` **全参形式** `RPR.EnumProjectMarkers(i, 0, 0.0, 0.0, "", 0)`，返回 `[retval, idx回显, isrgn, pos, rgnend, name, markrgnidx]`；但 name 出参不被回写（缓冲区传多大都拿不到）——**Region 名（=集号）在线取不到，权威来源是离线解析 RPP（extract_regions.py）**；删 Region 用 `DeleteProjectMarker(0, idx, True)` 按 idx 直删（返回 0=成功，-1=没找到），从高 idx 往下删 |
| `GetSetMediaItemTakeInfo_String` 读条目名拿到的是指针串 | 原始返回元组下标易错；读取时 setNewValue 必须 False | **条目名核验以落盘 RPP 的 NAME 行为准**；写入用 `(tk,'P_NAME',label,True)`（place 骨架原样）不受影响 |
| extract_regions.py 把 MARKER 内部 idx 当集号 | 批次工程里 idx 恰好=集号所以从未暴露；删建过 Region 的工程 idx 会漂移 | 集号以 Region **名字**为准（place 脚本匹配规则本来就是按 name）；手工归档 CSV 时修正 |
| `GetMediaSourceLength` 报 missing p1 | 包装层传参 bug | 用 `reapy_boost.Source(id).length()`（是**方法**不是属性） |
| connect 无参 / `connect(host=...)` / `connect_to_default_machine()` 报错 | reapy connect 传参坑 | **只用** `connect(Host(IPv4Address(host)))` 这一种；`LOCALHOST` 是字符串常量不能当 Host 传 |
| 循环渲染秒退、文件 0MB | 用了 42230（异步） | 换 41824（同步渲染） |
| 渲染分轨内容全静音 | RENDER_SETTINGS=3 stems 模式 bug | 放弃 stems，用 solo-loop（references/stem-export.md） |
| 某条分轨整条静音（其余正常） | 渲染期间被手动暂停，首遍污染 | **原样重渲即愈**；终验必须逐条 volumedetect |
| 防重跳过导致「新增 0 条」 | 计数器只累计新增 | 用区间对账（数 item）判断成败，别信计数器 |
| item 有位置无波形/无声（空块） | take source 赋值交替性丢失 | 回读验证 + 底层 API 重贴 + 重跑一轮；防重判定必须验 source |
| 看不到波形 | ①源电平低 ②peaks 缓存缺失 ③轨/条目音量或 mute | volumedetect 查电平；Item→Peaks→Build any missing peaks |
| CK 素材层贴上≈无声 | 素材层录音电平保守 | volumedetect 查源电平，item D_VOL 补偿（曾 +12dB） |
| reapy 逐条 RPC 插媒体/建 Region 不稳（段错误率高、无声死） | 本机 RPC 通道脆弱，长流程经不起多次往返（2026-09-09 定稿） | **RPC 只做一次注册+触发，批量操作写进 Lua 在 REAPER 进程内原生跑**：`make_video_driver.py` + `run_reaper_script.py`，见 references/video-import.md |
| 「ReaScript 控制台输出」窗口开着，担心挡控制 | 非模态日志面板，不拦主线程 | 实测不挡 RPC，无需手关；**模态**弹窗（文件框/确认框）才挡，需人工关掉 |

## 连接层 / 环境陷阱（2026-09-09 移植自 Desktop\REAPER-reapy交接_20260909.md，全部实测）

| 现象 | 原因 | 解法 |
|------|------|------|
| connect 不报错但 RPR 全是空壳（AttributeError） | import reapy_boost 时自动 connect 失败（如代理劫持），DisabledDistAPIError 被**静默吞掉** | `connect_reaper()` 已内置断言（hasattr CountTracks）；自写脚本连接后必须调一次真函数验证，失败退出不带病跑 |
| curl/python 到 2308/2309 全挂或 502 | 本机代理劫持对 127.0.0.1 的请求（urllib 会读 HTTP_PROXY！） | `rfw_common.py` 顶部已清代理；自写脚本在 **import reapy_boost 之前**清 HTTP_PROXY/HTTPS_PROXY/ALL_PROXY，设 NO_PROXY=127.0.0.1,localhost |
| Python 3.13 RPC 挂起/进程无声死亡 | reapy_boost 0.10.201 `http.client.parse_headers` 递归解析爆栈 | `rfw_common.py` 顶部已换迭代实现 + setrecursionlimit(20000) |
| 进程在、端口在、HTTP 全超时假死 | REAPER 有**模态弹窗**（保存询问/渲染窗），主线程被堵 | Win32 只读探测（probe_windows.py / detect_reaper.sh，EXIT 2=有弹窗）→ **报告用户人工点击，绝不自动化点弹窗** |
| 分不清 REAPER 开没开 | 进程/端口单一看都会说谎（僵尸假活） | **四重对齐**：进程 + 2308/2309 监听 + HTTP 实测 200 + 窗口标题（原机检测脚本已随旧环境废弃，按四项自行核对） |
| AI 启动的 REAPER「闪退」 | 沙箱 Job Object 回收 bash/PowerShell/cmd start 拉起的进程 | **铁律：AI 绝不自行启动 REAPER**，CLOSED 时请用户手动双击，跑 launch_reaper.sh 握手验收 |
| 触发激活动作返回 200 但不执行 | reaper-kb.ini 损坏（SCR 粘连，本机已修复） | 查 `AppData\Roaming\REAPER\reaper-kb.ini` 行格式，坏备份 `.corrupt-20260831.bak` |
| 2309 配置在但连不上 | reapy server 激活动作没跑（REAPER 重启后） | `curl --noproxy "*" "http://127.0.0.1:2309/_/_RSZuNAdyBEl5JXJYJ5Ax7AyaNgjqka1MsR99VputBI"`（阻塞数十秒属正常） |

## stems 渲染语义修正（2026-09-09 渲染矩阵实验，修正 06 号文档部分结论）

- RENDER_SETTINGS=2（stems）**不是按轨出分轨**——是选中轨混成 1 个文件；=1 是 master+每轨分轨；=3 是按选中轨出分轨。
- ⚠️ 未解现象：显式 `RENDER_STEMS=0` 后渲染全 -91dB 静音，原因未查，重开课题前先复测。
- **solo-loop（逐轨 solo + 41824）仍是默认交付方案**，直到新方案完整复测通过。

## 渲染格式速记

| 需求 | 值 |
|------|-----|
| 48k/24bit wav RENDER_FORMAT | base64 `ZXZhdxgAAAA=`（换格式先 2 秒试条 + ffprobe 验证） |
| 同步渲染命令 | `Main_OnCommand(41824, 0)` |
| 渲染完成判断 | 文件 ≥ 时长×288000B/s×0.98 且连续 2 次读数稳定 |

## 保存与关闭（完整流程）

```python
proj_id = RPR.EnumProjects(-1, "", 1024)[0]           # 当前工程指针（-1=active）
RPR.Main_SaveProjectEx(proj_id, r"<工程完整路径>.RPP", 0)  # 显式传完整路径最稳
RPR.GetSetProjectInfo(0, 'PROJECT_ISDIRTY', 0, True)  # 强制清假阳性 dirty
RPR.Main_OnCommand(40004, 0)                          # File: Quit REAPER，无弹窗
```

- `Main_SaveProject(0)` 在 reapy 里缺参会报错，用 `Main_SaveProjectEx`；`Main_SaveProjectEx` 历史上还有过「只存单轨」的 bug 记录——**保存后必须落盘验证**：RPP 文件 mtime 刷新 + 文件内容里 grep 得到本次新增的东西。
- 保存验收两步：① `IsProjectDirty` 由 1 变 0；② REAPER 窗口标题的 `[modified]` 消失。
- `GetProjectPathEx` 返回的是 `...\工程目录\Media`，工程文件在**上一级**。
- 轮询 tasklist 确认真退出（进程还在 = 弹窗挡住了）。落盘验证别只信 dirty 标志。

## 首次配置 reapy_boost（换机 / 重装时）

- pip 包名 `reapy-boost`（导入名 `reapy_boost`），**必须装进 REAPER ReaScript 指向的同一个 Python 解释器**；`typing_extensions` 依赖一并装（reapy_boost 声明不全）。
- 端口约定：reapy server = 2308，REAPER Web Interface = 2309。Distant API 启用后 REAPER 才可被远程驱动。
- `reapy_boost.config.configure_reaper(resource_path)` 可从外部配置 REAPER，但 **Python 3.13 有兼容坑**：内部 `CaseInsensitiveDict.__contains__` 与 3.13 configparser 不兼容会崩，且 `Config.write()` **先截断再写**——第一次跑可能把 `REAPER.ini` 写空（用它的自动备份 `REAPER.ini.before-reapy_boost.bak` 恢复）。运行前打补丁：

```python
from reapy_boost.config.config import CaseInsensitiveDict
_orig = CaseInsensitiveDict.__contains__
def _safe(self, key):
    return False if not isinstance(key, str) else _orig(self, key)
CaseInsensitiveDict.__contains__ = _safe
from reapy_boost.config import configure_reaper
configure_reaper(resource_path=r"<REAPER资源目录>")
```

- 配置后**必须重启 REAPER** 才生效。

## rpp 文本编辑（绕开 API 的另一条路）

- 🔴 REAPER 开着时不要编辑 .rpp；改前先复制备份。
- `<TRACK` 前有空格，切块用 `re.split(r"\n\s*<TRACK", rpp)`。
- **FXCHAIN 块结束是独立 `>` 行，不是 `</FXCHAIN>`**。按行深度提取：`<` 开头行 depth+1、`>` 行 depth-1，回到 0 结束。
- 往 TRACK 里插入 FXCHAIN：必须 4 空格缩进、紧贴 TRACK 头 `>` 后、无空行——格式不对 REAPER **静默忽略**（FX 不加载、不报错）。
- 发送：无尖括号行 `AUXRECV <源轨索引> ...`，写在**接收轨**块内。
- 带空格的轨名必须加引号（`NAME "音频 01"`）；复用真实工程头部拼接新轨块时，外层 `<REAPER_PROJECT` 本来就是开着的，**别按块平衡给头部补 `>`**——多补一个会把工程块提前闭合，后面所有轨块被静默丢弃（打开不报错但 0 轨）。生成后自检：`<TRACK` 计数 = 预期轨数。

## 沙箱 / 环境类

| 现象 | 解法 |
|------|------|
| pip 装完 reapy_boost 仍 `ModuleNotFoundError: typing_extensions` | reapy_boost 依赖声明不全：`pip install typing_extensions`（实测 2026-09 踩到） |
| python `os.remove` 被安全沙箱拦 | 脚本内不删文件（try 包住忽略），批量清理用外部 shell `rm` |
| DB 报 `no such module: fts5` | 换 Python 版本（官方编译版自带 FTS5） |
| 查 REAPER 进程 | `tasklist` 全量后 grep（禁 `tasklist //FI`——Git Bash 的 MSYS 转换会产生「进程不存在」假阴性）；PowerShell 侧 Get-Process 输出捕获不可靠，以 Bash 侧为准 |
| 2308 拒连 ≠ API 没开 | **Distant API 端口不固定**（2026-09-14 实测某实例在 2309）。连接一律用 `scripts/connect_reaper.py`（自动找进程→netstat 枚举监听端口→逐个实连→回读工程确认；`--launch` 允许自行启动 REAPER——制作人 2026-09-14 解禁，AI 可自启）。别再硬编码端口、别对单一端口反复重试 |

## 工作流类

| 问题 | 解法 |
|------|------|
| 报告里的动作在视频里不存在 | 假动作（报告幻觉）。帧证据核验一票否决，剔掉不贴 |
| 报告时间戳与画面对不上 | 以抽帧实际内容为准修正 |
| 同一音效一集反复出现 | 违反选材规则（同源≤2 + 异窗轮换），见 references/retrieval.md |
| 新集不知道 Region 起点 | `EnumProjectMarkers2` 遍历实测，或 `extract_regions.py` 离线解析；**不要估算** |
| 长条音效拼接露馅 | silencedetect 边界 5+ = oneshot 序列禁止硬拼；找长连续源取窗 |
| 同源多处使用听感重复 | 必须取不同时间窗/切段，或换近义音源 |

## API 行为备忘

- `D_VOL`（轨/item）是**线性值**：1=0dB，2=+6dB；设负数 = 推子 -inf。别做 lin2db 转换。
- bus/folder 轨（ISBUS=1）推子显示 -inf 是正常态。
- API 创建的 item 不自动建 peaks 缓存，波形显示需手动触发重建。
- reapy_boost 的 reascript_api 对部分指针/缓冲参数函数有传参 bug，优先用对象方法（`Source.length()`、`src.filename`）。
- 指针有效性判断：正则取 `0x` 后十六进制判非零，别用 `"0x0" in str(id)`。
- `AddProjectMarker(0, True, pos, end, name, -1)` 建 Region；`EnumProjectMarkers2` 返回 `(retval, isrg, pos, endpos, name, idx)`。
- **`GetTrackName` 直调报 missing p1/p2**；补缓冲区参数后返回值里仍是轨指针而非名字（2026-09-11 第56集实测）——轨名核验走 RPP 离线解析或 reapy 对象方法 `track.name`，别依赖该 API。
- **`GetSourceFilename` 不存在**，正确 API 名为 `GetMediaSourceFileName(src, "", 1024)`（同日实测）。
- **在线枚举 marker 的 reapy_boost 传参陷阱**：`EnumProjectMarkers`/`CountProjectMarkers` 必须带全输出缓冲参数（缺一个就 TypeError，元组解包数也与文档不符）——**Region base 一律用离线 `extract_regions.py` 解析 RPP 或实测后显式传入贴轨脚本**，不要在线裸调枚举 API。
- FTS5 默认分词不切中文连续串：中文素材检索必须靠 desc 里**空格分隔的关键词**；新包入库时逐条写关键词 desc（见 retrieval.md 环境节），否则中文查询假阴性。
- 声音连续性：切镜头 ≠ 切声音，事件音不得在剪辑点戛然而止——延长漫过切点 + `D_FADEOUTLEN` 淡出。
