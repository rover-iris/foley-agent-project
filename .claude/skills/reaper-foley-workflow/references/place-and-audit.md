# 贴轨脚本规范与三重自检

> 模板：`scripts/place_ep_template.py`。cue 数据格式是定稿，别改结构。

## cue 九元组

```python
(track_idx, t_local, source_key, len, label, fadeout, start_offset, vol, fadein)
```

| # | 字段 | 含义 | 要点 |
|---|------|------|------|
| 0 | track_idx | 目标轨索引 | 工程轨序从 0 数，贴前**必须实测确认轨序**（枚举轨名） |
| 1 | t_local | Region 内相对秒 | 贴时换算 `绝对 = BASE + t_local`，不要手工加 |
| 2 | source_key | S 字典别名 | S 字典用 `q()` 从 DB 取真实路径，不手写绝对路径 |
| 3 | len | 取窗长度（秒） | 不得超过源长，且 `start_offset + len ≤ 源长` |
| 4 | label | 中文动作描述 | 条目名 P_NAME 默认 = 源文件名 stem；仅拿不准条目（label 含待人删/待裁等待标注）写「label ｜ stem」（retrieval.md 条目命名节）；防重兼容两种写法（[W-08]，模板已内置） |
| 5 | fadeout | 出淡（秒） | 0 = 硬切；跨切点事件要延长+淡出（声音连续性） |
| 6 | start_offset | 源内起始偏移（秒） | **🔴 必须显式写 D_STARTOFFS，见下**；**默认 0**——非零取窗必须先出 volumedetect 证据（窗口 max > −30dB），禁止盲选（[铁律 10]） |
| 7 | vol | ~~线性增益~~ **已废止（制作人口径：音效文件一律默认音量，不做任何音量调整）** | 引擎强制写 D_VOL=1.0；旧 CUES 里的 vol 值被忽略 |
| 8 | fadein | 入淡（秒） | None = 不入淡 |

## 轨组→轨映射

九元组 track_idx 取值纪律：目标轨必须是实体音频轨（ISBUS=0），编组轨禁落 item（铁律 17）。
1. 轨组解析成轨索引时逐条校验 ISBUS——组名同名轨即编组轨，禁止按名命中；
2. 禁止盲取组区间首轨；
3. 组内多条 cue 按「同轨时间重叠才分层」依次填子轨，首尾相切不算重叠；
4. 施工前轨序对账加验 ISBUS 列——只对名序不验轨性质，拦不住贴错层。

## 🔴 引擎级坑：D_STARTOFFS 必须显式写

`AddMediaItemToTrack + AddTakeToMediaItem + SetMediaItemTake_Source` 走完后 item 从源 0 秒开始播。**start_offset 只传给取窗逻辑而不写 take 属性 = 等于没生效**。新代码必须带：

```python
if off:
    RPR.SetMediaItemTakeInfo_Value(take_id, 'D_STARTOFFS', off)
```

任何贴轨引擎自查一条：off 参数是否真的落到了 take 上（终验读回 `D_STARTOFFS` 与源长比对）。

## 贴轨核心骨架

```python
pos = BASE + t_local
item = RPR.AddMediaItemToTrack(track_id)
take = RPR.AddTakeToMediaItem(item)
src = RPR.PCM_Source_CreateFromFile(path)
if not src or 指针为0: return "src失败"          # 指针判断：取 0x 后十六进制 != 0
RPR.SetMediaItemTake_Source(take, src)
RPR.SetMediaItemInfo_Value(item, 'D_POSITION', pos)
RPR.SetMediaItemInfo_Value(item, 'D_LENGTH', ln)
RPR.SetMediaItemTakeInfo_Value(take, 'D_STARTOFFS', off)   # ← 上面说的坑
RPR.SetMediaItemInfo_Value(item, 'D_VOL', 1.0)  # 2026-09-11 制作人口径：一律默认音量
RPR.SetMediaItemInfo_Value(item, 'D_FADEOUTLEN', fadeout)
if fadein: RPR.SetMediaItemInfo_Value(item, 'D_FADEINLEN', fadein)
pname = f"{label} | {stem}" if label else stem   # 2026-10-07 拍板：label（含【待人删】等标注）必须与源名合并落地 P_NAME，只写源名 = 界面无标注（1007测试 返工事故）
RPR.GetSetMediaItemTakeInfo_String(take, 'P_NAME', pname, True)   # 不设 P_NAME 条目在画面里无标签
# 贴完立即回读验证 source 没丢；丢了删 item 返回失败
```

## 空 item / source 交替丢失（批量贴轨已知现象）

take 的 source 赋值可能**交替性丢失**——item 有位置长度但无源（空块）。防御三招：

1. 贴完立即读回 source filename 验证；
2. 失败的用底层 API 重贴（直接传 source id，不经对象包装）；
3. 失败呈交替模式时，**同脚本重跑第二轮即全过**（每轮进程状态重置）。

⚠️ 防重逻辑盲区：跳过判定不能只看「同位置有 item」——要连 source 一起验，**空块必须删掉重贴**（曾发生：首轮 source 丢，次轮被防重挡住永久漏修）。终验 = 全工程扫 source filename 存在性。

## 建峰（2026-10-07 固化，贴完必跑）

API 创建的 item **不自动建 peaks 缓存**，不建则界面无波形（历任批次靠制作人手动「峰值→构建峰值」，1007测试 实测 62/99 条缺峰）。place 模板末尾已内置 `build_peaks()`：官方 `PCM_Source_BuildPeaks(src, mode)` 三段式（REAPER 6.35+）——mode 0 启动（返回 0=已有峰→跳过，天然幂等）→ mode 1 批量推进至归 0 → 归 0 立即 mode 2 收尾（提前调 mode2 会显示残缺峰）→ `UpdateArrange()`。脚本重跑不会重复建峰。

## 防重（幂等，必须保留）

贴之前扫目标区间已有 item：**源文件名相同（P_NAME 中 ` | ` 后的部分）且 |位置差| < 0.15s** 视为已存在，跳过。段错误后直接重跑不会贴重。**副作用：计数器会归零失真——判断成败必须靠区间对账，不是脚本打印的「新增 N 条」。**

## 两轮重试

对账数量不足自动再跑一轮（防重保证第二轮只补缺的）。两轮后仍不足报错退出，人工介入。

## 三重自检（全过才算完工）

1. **区间对账**：目标 Region 内 item 数 == CUES 条数（逐条打印 相对时间/长度/源文件名 人工过目）。
2. **重叠检测**：同轨相邻 item 时间窗不得重叠（`a.pos + a.len > b.pos + 0.05` 即违规）。重叠的音效分上下不同轨叠加。
3. **越界审计**：每条 cue `off + len ≤ 源时长`（`scripts/audit_offsets.py`，对 DB 的 duration 全量比对，可续跑；源长按文件名取最大值兜底同名异目录）。超界处理原则：**裁 len，宁可短不可越界**（越界 = 尾部读到别的段/静音）。
4. **父轨清查**：全部 ISBUS=1 轨 item 数 = 0（实机 + 落盘解析双通道）。

## 操作纪律（五条，全部条目出自实机教训）

1. **探针先行**：没把握的 API/格式，先在 2 秒试条上验证再跑全量。
2. **单批小步**：多集/多轨分批跑，每批独立自检，不攒大单（段错误重跑好定位）。
3. **备份先行**：改状态前存 JSON，改文件前复制 .rpp；恢复后 diff 校验。
4. **对账文化**：数量对 item、文件对字节、内容对电平，三个全过才算完成。
5. **留痕**：脚本头部注释写清 base、集数、核验状态，事后可追溯。

## 抄旧 place 模板前必查 vol 行（20260916 事故新增）

`place_ep26_v1.py` 等旧定稿模板的 CUES 九元组里 vol 列是**真值生效**的（`if vol: SetMediaItemInfo_Value(..., "D_VOL", vol)`），与 skill「一律默认音量」条款相抵。抄任何旧模板时必须二选一：**vol 列全改 1.0**，或**删除 vol 赋值行**。20260916 夜批照抄旧模板 vol 列，531 条被调量返工——旧模板的「已定稿」只代表当年口径，不代表现在。

## rfw_common 导入一律双路径兜底（20260928 实测）

skill 安装副本位置因机而异（工程内 `.claude/skills/` 与全局 `~/.claude/skills/` 不保证同时存在），place / 复核 / 搭建类脚本开头统一写：

```python
_SKILL_PATHS = [
    r"<全局副本>\reaper-foley-workflow\scripts",
    r"<工程内副本>\reaper-foley-workflow\scripts",
]
for _p in _SKILL_PATHS:
    if os.path.isdir(_p):
        sys.path.insert(0, _p)
```

禁止单点硬编码一个绝对路径；两处都在时全局优先、工程内兜底，任何一处在即可跑。

## Base 实测（每集开始）

base 一律**离线解析**（在线枚举出参不可信，[R-13]）：

```bash
python scripts/extract_regions.py --rpp <工程.RPP> --out region_bases.csv   # Region 名=集号，起点/终点全在其中
```

不要估算。项目侧的基准表 CSV 与工程落盘 RPP 冲突时，以**工程落盘 RPP 离线解析**为准。**派单/脚本里的 BASE 与偏移值必须注明来源基准表文件并照抄，禁止对话手抄**（案情见 12 号）。

⚠️ 包装层坑（详表见 `reaper-remote-ops` skill §7 [R-13]）：枚举/计数出参在 reapy_boost 网络通道不回写——Region/marker 的对账一律**按位置比对**，删 marker 按 marker id 识别（枚举序号≠id）。

## 存档

- place 脚本按集留档（`place_ep{NN}_v1.py`），头部注释写明「报告已帧证据核验」作为质检痕迹。
- 工程保存与静默关闭的完整流程见 `reaper-remote-ops` SKILL §5（坑见 [R-09]）。
- **每批做完更新项目侧进度文档**。

## 直通模式（默认路径，制作人 2026-10-12 批准）

目标：消灭见习手工转写——派单数据一次写对，机器直通到底。

1. **总监直出数据文件** `cues_<集号>.py`（纯数据，与派单文档同目录）：
   ```python
   EP = 3                       # 集号
   BASE = 1606.99               # 引用基准表文件照抄，禁手抄
   TRACK_RANGE = (14, 29)       # 以实测轨序为准
   NOTE = "报告已帧证据核验"
   S_NAMES = {'FIRE': "CECK FIRE Whoosh Fireball", ...}   # 别名 -> DB 真名
   CUES = [(20, 13.2, 'FIRE', 2.0, "符纸自燃爆燃", 0.7, 0, 1.0, None), ...]  # 九元组
   ```
2. **备料预审**（不碰 REAPER）：`python prepare_ep.py --data cues_<集号>.py --config foley_project.json`——结构/路径解析/越界预审/非零取窗 vd 取证/同源计数，FAIL 即打回总监；PASS 出 `备料报告_<EP>.json`（vd 证据留痕，交班引用它）。
3. **贴轨**：`python place_ep_template.py --data cues_<集号>.py --config foley_project.json [--dry-run 先核对]`——模板照旧内置防重/建峰/自检。
4. **一键自检**：`python selfcheck_ep.py --ep <集号> --tracks 14:29 --expect <条数> --config foley_project.json`——对账+空块+重叠+越界+父轨清查四关一次跑完，末行 RESULT JSON。

旧路径（抄模板手填必填区）仍可用，仅当总监未直出数据文件时回退。

## 验收包（见习交班必产，A4 复核单一入口）

见习交班除三件套外必产单一验收包文件 `<集号>_验收包_<日期>.md`（与三件套同目录）。验收包是 A4 复核的唯一阅读对象——A4 读验收包 + 机器门禁结果 + 只抽查异常项，不回翻报告/派单/三件套。

### 验收包模板（五节齐全；缺一节 = 缺包）

1. **对账表**：CUES 逐条 ↔ 落盘 rpp 条目（轨号/绝对落点/时长/源文件/sofs，容差 0.01s），全命中或逐行列差异；
2. **四关自检输出**：`selfcheck_ep.py` 末行 RESULT JSON 原文内联 + 四关逐项违规计数（对账/重叠/越界/父轨，各 = 0）；
3. **帧比对输出**：`verify_frames.py` 末行 RESULT JSON 原文内联 + MISMATCH/NOFRAME 清单（有则逐条列，无则写 0）；
4. **vd 证据**：非零取窗逐条 max_volume 数值内联，并注明来源文件路径（备料报告/vd 结果 JSON，禁手抄）；
5. **存疑清单**：待人删/待人裁条目逐条内联（label / 轨号 / 绝对时段 / 待裁问题）。

### 帧比对门禁（verify_frames.py，交班前必跑）

`python verify_frames.py --task <任务文件夹> --ep <集号>`——抽验比例默认 ≥20%（--ratio），重点段经 --focus 秒清单强制全覆盖，--all 全量；取帧一律 eq(n) 帧号系精确取帧（禁止 -ss 直抽），md5 + aHash 双判（判据详表见脚本头注释）。RESULT = fail（MISMATCH/NOFRAME/MISSING/GRAB_FAIL 任一 >0）时未闭环禁止交班。

### A4 复核口径（分支穷尽）

1. 若验收包缺节或缺文件 → 打回补包，不回翻旧档替代；
2. 若任一机器门禁 RESULT = fail（selfcheck_ep / verify_frames）→ 打回，按报告内 mismatches/violations 清单逐项整改；
3. 若门禁全 pass → 只抽查异常项：存疑清单逐条 + 对账表差异行 + PASS_SOFT 帧，逐项亲验；
4. 异常项亲验全部成立 → 出「通过」；任一不成立 → 打回（问题清单）。
免检口径单不做图片抽查，免检项在验收包内注明并按留痕口径复核。
