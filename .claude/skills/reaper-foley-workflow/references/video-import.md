# 视频导入（贴各集视频 + 建 Region）——RPC 只触发，REAPER 内干活

> 定稿背景（2026-09-09 权臣CP 第一集测试实机教训）：逐条 reapy RPC 在实机上段错误率高且无声
> （exit 1 零输出）。唯一稳定的形态：**RPC 只做一次「注册+触发」，批量操作由 Lua 在 REAPER
> 进程内原生执行，对账以落盘 RPP 文本为准**。本流程即此形态的固化。

## 定稿管线（三步）

```
① ffprobe 实测各集时长 → region_plan.csv（离线）
② make_video_driver.py 生成驱动 Lua（离线，纯文本拼接）
③ run_reaper_script.py 注册+触发（唯一一次 RPC 交互；段错误自动重跑）
```

```bash
# ① 计划 CSV（列：集数,预计base_s,时长_s,视频；utf-8-sig）手工或 new_project.py plan 生成
# ② 生成驱动 Lua
python scripts/make_video_driver.py --plan region_plan.csv --rpp 工程.RPP --out 导入视频.lua
# ③ 一次触发全部
python scripts/run_reaper_script.py --lua 导入视频.lua
```

## 驱动 Lua 固化的行为（每集）

| 步骤 | 内容 | 幂等护栏 |
|---|---|---|
| 插条目 | Video 轨(track 0)：D_POSITION=base、D_LENGTH=时长、P_NAME=`第NNN集 <文件名>` | 条目按位置(0.001s 精度)判重，已存在跳过 |
| 建 Region | `AddProjectMarker(0,true,base,base+len,<集号>,-1)`，名字=纯集号 | 按名字判重，已存在跳过 |
| 收尾 | `Main_SaveProjectEx` 落盘 + 清 `PROJECT_ISDIRTY` | 保存幂等 |

脚本可安全重跑；段错误后重跑只补缺的。

## 参数约定（别手估）

- **时长一律 ffprobe 实测值**（不要用 `GetMediaSourceLength` 的容器估算值——两者可差 0.04s；
  批次交付工程用的就是 ffprobe 值）。
- **base = 上一集 Region 终点**（首集=0），来源 `region_bases.csv`（extract_regions.py 离线解析）。
- 命名：条目 `第%03d集 <视频文件名>`；Region 名=纯集号。与批次交付工程逐位一致。

## 验收

- 人类肉眼核验（本流程的验收人）。
- 离线对账：`extract_regions.py --rpp 工程.RPP` 看 Region 数/集号/base；grep RPP 看
  POSITION/LENGTH/NAME/SOURCE VIDEO。

## 实测事实（本机 2026-09-09）

- 触发组合 `AddRemoveReaScript(True,0,<lua路径>,True)` → 拿 cmdID → `Main_OnCommand(cmdID,0)`
  实测一次过；cmdID 每次注册会变，无所谓（checkAlreadyLoadin=True 防重复注册）。
- 「ReaScript 控制台输出」窗口是**非模态**，开着完全不挡 RPC；**模态**弹窗（文件框/确认框）
  才挡，遇到要人工关。
- 视频条目不自动建峰值；画面在 View → Video window（Ctrl+Shift+V）。
- RPC 逐条插视频（`PCM_Source_CreateFromFile` 等）在本机实测不稳定——**不要回退到那条路**。

## ⚠️ 20260916 反面案例：API 直贴 30 集漏建 Region

夜批为省事走了 reapy API 逐条直插（30/30 条目成功），但**没走本流程 → 没建 Region**——制作人标尺上没有逐集选区，验收即投诉。结论：

- 视频+Region 的定稿路线仍是**本流程（Lua 驱动，插条目与建 Region 同批幂等）**；
- 因故走了 API 直贴的，**补建 Region 是必做收尾步骤**：用 `scripts/create_video_regions.py`（20260916 定稿，按轨 0 条目位置/长度逐集建 Region，幂等按位置对账补缺，建完自动保存；包装层坑已内置规避——对账只比位置不比名称）；
- 验收前把「Region 数 == 集数」列为与「条目数 == 集数」同级的检查项。

## 与 new_project.py 的关系

- `new_project.py plan` 仍可用来生成 region_plan.csv；但其 `apply` 走 reapy 逐条 RPC
  （建 Region + 贴视频），在本机不稳定——**视频+Region 一律改走本流程**（参数同源，
  生成器吃同一个 plan CSV）。
