---
name: reaper-foley-workflow
description: AI 驱动 REAPER 影视音效工作流（foley pipeline）通用方法论与工具集。覆盖：从逐秒动作分析报告出发的每集贴轨 SOP（帧证据核验→音源库检索选材→reapy_boost 自动贴轨→三重自检）、从模板新建/扩展剧集工程（Region 分集）、48k/24bit 分轨导出、以及 REAPER/reapy 全部实机踩坑速查。Use whenever 用户提到 REAPER 音效、贴轨、自动贴音效、foley、音效工程、分轨导出/stem、音源库检索、给视频按时间轴配音效、新建音效工程、Region 基准、reapy/reapy_boost —— 即使只说「贴一下音效」「导分轨」「建个音效工程」也要触发。
---

# Reaper Foley Workflow — AI 驱动影视音效工作流

**一句话**：上游是「逐秒动作分析报告」（带帧证据），下游是贴好 cue 的 REAPER 工程 + 48k/24bit 分轨 WAV。AI 全自动驱动 REAPER 完成中间的检索、贴轨、自检、导出。

**本 skill 是通用的**：不内置任何具体工程的路径。每个具体项目的事实（工程位置、音源库位置、轨序、进度）存放在**项目侧**——`foley_project.json` 项目配置 + 项目自己的交接文档。开工第一步永远是建立/读取项目配置（见下）。

## 🔴 铁律（违反任何一条都是事故）

1. **NAS / 网络盘绝对只读**——任何情况下不写入、不修改、不删除。
2. **音源实体文件只读**——只索引、只取路径，不移动不重命名。
3. **外部报告不可直接信任**——贴轨前必须抽帧对照核验动作真实存在（假动作防御），一票否决。
4. **改工程前必须备份**——.RPP 是纯文本，改坏前先复制；推子/mute/solo 状态先存 JSON。
5. **每批贴轨必须三重自检全过**（区间对账 + 重叠检测 + off+len 越界审计），不过不进下一步。
6. **渲染期间不许碰 REAPER**——手动暂停/切工程会污染渲染内容（文件正常但整条静音）。
7. **脚本必须幂等可安全重跑**——reapy 偶发段错误（exit 1 零输出）是已知现象，重跑靠防重机制保证不贴重。
8. **音量一律不动（20260916 制作人条款）**——D_VOL 恒为 1.0，任何文档/派单出现「vol 0.3~0.5 / 0.5~0.8」经验值一律按**已废止**处理并上报，不得照抄执行。20260916 夜批旧口径复发：抄旧 place 模板把 vol 列真值写进 D_VOL，531 条被调量返工。
9. **脚步声一律不配（20260916 制作人条款）**——脚步节奏无法与画面对齐；脚步/怪物足步/爬行步一律归【手动】人工，AI 不选材不贴轨。
10. **start_offset 默认 0，盲选无证据不取窗（20260916 制作人条款）**——源头部起播是第一优先；非零取窗必须先出 ffmpeg volumedetect 证据（窗口内 max_volume > −30dB）。>10s 杂集/compilation 类素材（如 creature_foley_ck 40s 集锦，内部静音占比高）**默认不作候选**，确需使用须全窗有声证据并存疑清单放行。实测：盲选窗口 3/4 为数字静音（−90dB）。
11. **口径冲突即停即问；数值禁止手抄**——见习/总监上报口径冲突时值班必须暂停请示制作人，「按单继续」不是值班可裁决事项；跨派单的 BASE/集偏移/区间只准引用项目侧落盘基准表（region_bases.csv / offsets.json），从对话手抄数字一律禁止（20260916 手抄 BASE 错 2.375s，69 条返工）。
12. **环境底衬跟随场景，切换用双轨重叠法（2026-09-22 制作人条款）**——环境底衬按上游报告的「全片环境判断/场景定场」分段铺设；遇环境底色过渡（转场、白天→黑夜、室内→室外等），底衬必须随场景同步切换。施工一律用**双轨重叠法**，不做交叉淡化（crossfade）自动化：前段底衬与后段底衬分放**两条环境轨**，过渡点处前条尾部与后条头部**留出重叠区**，前条尾部做 item 淡出、后条头部做 item 淡入；重叠长度与淡形的细调**留给人工**，AI 只把两段底衬与首尾淡形贴到位。淡入淡出一律走 item fade（D_FADEINLEN/D_FADEOUTLEN），D_VOL 恒 1.0 不动（铁律 8 优先）。
13. **巨物档位硬校验（2026-09-22 制作人条款）**——主体为巨物/巨兽/巨型构造（巨魔、山、巨型建筑等）的 cue，选材一律 **Large/Huge 档位**（力度档位=体型×幅度），禁止拿轻质感素材充数（布料 whoosh、轻摩擦、小件碰撞）——贴出来「巨大」感尽失即返工（20260922 测试2 开场案：巨魔动作被配成布料 whoosh）。场景级破坏（山体倒塌、建筑崩毁）按报告标注的 impact 配齐倒塌/碎石/尘烟层次，不得只配肇事动作漏掉受击环境。
14. **魔法/玄幻特效类音效 AI 可选材贴轨（2026-09-22 制作人条款）**——报告标【手动】的玄幻特效（黑雾/金光/结界/雷电/传送门/剑鸣等），按特效关键词检索音源库选材贴轨，不再默认等人工；【手动】标注仅提示「非物理声源」。库内确无可检素材的（UI 弹窗、转场字卡等）仍归人工，写入存疑清单上报，禁止拿不相干物理素材充数。

## 开工三件事（每次会话按序做）

1. **定位/建立项目配置**。找项目目录下的 `foley_project.json`；没有就用 `scripts/project_config.py init` 生成（路径从用户、交接文档或既有目录结构中确认，**不要猜**）：
   ```bash
   python scripts/project_config.py init --name <剧名> \
     --project-rpp <主工程.RPP> --asset-db <asset_library.db> \
     --template-rpp <模板.RPP> --reports-dir <报告目录> --export-dir <导出目录> \
     --tracks <动作组子轨起:止>     # 如 14:29，以实测轨序为准
   ```
2. **环境自检**（REAPER 是否运行、reapy_boost、ffmpeg、DB 完好、连接通）：
   ```bash
   python scripts/preflight.py --config foley_project.json
   ```
   能打印出工程名和 Region 数 = 通道正常。REAPER 未运行时**可由 AI 自行启动**（制作人 2026-09-14 解禁，不再是禁止事项）；连接一律用自适应脚本（Distant API 端口非固定 2308，实测为准）：
   ```bash
   python scripts/connect_reaper.py --launch [--project <rpp>]   # 自启动+自动发现端口+实连+回读工程确认
   ```
3. **读项目交接文档/进度**（如交接包、`交接文档_*.md`、`CONTINUE.md`），确认做到哪了、有什么挂账，再排本次计划。

## 每集标准 SOP（细节读对应分册）

```
上游报告 → ①帧证据核验 → ②实测Region基准 → ③检索选材 → ④写place脚本
        → ⑤执行贴轨 → ⑥三重自检 → ⑦存档+更新进度
```

| 步 | 要点 | 详见 |
|---|------|------|
| ① | 每条 cue 抽帧亲眼确认动作存在；时间戳与画面不符以帧为准 | SKILL 快照 `video-action-analysis`（上游格式） |
| ② | base 用脚本实测（`EnumProjectMarkers2` 或离线解析 RPP），**绝不估算**；`绝对时间 = base + t_local` | references/place-and-audit.md |
| ③ | 音源库 SQLite+FTS5 检索；同源≤2次/异窗轮换/锁包优先；星级不硬卡（弱/轻类 cue 不做星级与 Designed 加权）；力度/质感对位硬校验 | references/retrieval.md |
| ④ | 抄 `scripts/place_ep_template.py`，填 CUES 九元组表 | references/place-and-audit.md |
| ⑤ | 幂等防重，段错误直接重跑；**成败不看计数器看对账** | references/place-and-audit.md |
| ⑥ | 区间对账 + 重叠检测 + off+len 越界审计（`scripts/audit_offsets.py`） | references/place-and-audit.md |
| ⑦ | place 脚本按集留档；工程保存+静默关闭见坑册；**更新项目进度文档** | references/pitfalls.md |

多集连做：逐集跑完逐集自检，**不要攒一起跑**（段错误重跑时无法定位是哪集）。

## 任务路由

| 任务 | 用什么 |
|------|--------|
| 新建一个剧的音效工程 / 老工程加新集（Region） | references/new-project.md + `scripts/new_project.py` + `scripts/extract_regions.py` |
| 各集视频导入（贴视频+建Region，支持整批） | references/video-import.md + `scripts/make_video_driver.py` + `scripts/run_reaper_script.py`（RPC 只触发、Lua 内干活，实机定稿路线） |
| 补建逐集 Region（视频已 API 直贴的补救） | references/video-import.md 末节 + `scripts/create_video_regions.py`（幂等按位置对账） |
| 给某集贴轨 | 上表 SOP + `scripts/place_ep_template.py` |
| 检索音源 / 选材 | references/retrieval.md（`q()` 模式 / search_assets.py） |
| 越界审计 | `scripts/audit_offsets.py` |
| 分轨导出 48k/24bit | references/stem-export.md + `scripts/export_stems.py` |
| 任何报错/异常 | references/pitfalls.md 先查表，再动手 |

## 工程结构约定（本工作流的通用假设）

- 整部剧**一条时间线**，每集一个 **Region**，Region 起点即该集 0 秒；所有 cue 用 Region 内相对时间，贴时换算绝对时间。
- 轨道分组由模板决定（典型：视频轨 + 对话组 + 环境fx组 + 动作fx组 + 过渡fx组 + BGM组 + 混响组 + 总输出）。**轨序必须实测**（贴轨前枚举一遍轨名），把动作组子轨区间记进 `foley_project.json` 的 `tracks`。
- 新项目先用 `scripts/extract_regions.py` 离线解析 RPP 得到全剧集基准表（无需启动 REAPER），归档到**项目侧**。

## 环境要求

| 组件 | 说明 |
|------|------|
| REAPER | 需开启 Distant API（默认端口 2308），reapy 走此通道 |
| Python 3.10+ | `pip install reapy_boost`（**必须 reapy_boost，不用原版 reapy**，原版长任务随机断连） |
| ffmpeg | PATH 可用即可，导出终验用 ffprobe + volumedetect |
| 音源库 DB | SQLite + FTS5（`assets` 表 + `assets_fts`），由 pipeline 的 `build_asset_db.py` 扫描音源根目录构建 |

连接口诀：`reapy_boost.connect(Host(IPv4Address(host)))` → 拿 `reascript_api` → `Project()` → **`time.sleep(1)` 再调第一发**。

## 选材核心原则（压缩版，全文见 references/retrieval.md）

- **每条 cue 定稿前过「选材终审六问」**：事件/声源存在/档位/质感域/空间/结构六面对位，任何一问拿不准进存疑清单不硬贴（2026-09-23 起试行）。
- AI 不需要懂剧情，只需要懂动作：只抽「动作事件」，丢弃主体语义。
- 集内同源 ≤2 次、邻近 cue 异窗轮换；星级不硬卡（5★ 内部随机仅适用于中档以上 impact；弱/轻类不做星级与 Designed 加权）。
- 两阶段检索：动作→官方包锁定→包内找（候选从几万降到几百）。
- Boom 库用英文检索匹配，不翻译使用；中文题材库直接中文检索。
- 一个视觉事件 = 多个声学瞬间（起手 + 收尾声都要有）；画面在「动」配 movement，画面在「撞」配 impact。
- 贴长条前必查源结构：静音边界 5+ = oneshot 序列，禁止拉长硬拼。
- **力度/质感对位三问**（20260916 金属撞击事故沉淀）：力度词同档（轻/弱 vs Massive/Hard 同现即判存疑）、素材层对位（轻互动声 foley 库优先于电影冲击库）、声源存在性；冲突条目进存疑音色清单，放行后才贴。

## 项目事实存放原则

本 skill 与具体项目解耦。接手任何项目：

1. 具体路径 → `foley_project.json`（`scripts/project_config.py` 管理）
2. 集-Region 基准表 → 项目侧 CSV（`scripts/extract_regions.py` 生成）
3. 进度与挂账 → 项目侧交接文档（做完一批必须更新）
4. 历史决策 → 项目侧交接文档「关键决策记录」，**别翻案除非有新证据**

## 与上游 skill 的关系

- `video-action-analysis`（独立 skill）：视频 → 逐秒动作分析报告的生成规范。本工作流消费它的产出；报告格式或字段有疑问时读它。
