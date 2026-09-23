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
| 4 | label | 中文动作描述 | **仅用于运行日志/对账打印，不写入条目名**。条目名一律 = 源文件名 stem（用户按包定位音源，2026-09-09 拍板）；防重判定 = 同位置 + 同源文件名 |
| 5 | fadeout | 出淡（秒） | 0 = 硬切；跨切点事件要延长+淡出（声音连续性） |
| 6 | start_offset | 源内起始偏移（秒） | **🔴 必须显式写 D_STARTOFFS，见下**；**默认 0**——非零取窗必须先出 volumedetect 证据（窗口 max > −30dB），禁止盲选（20260916 制作人条款） |
| 7 | vol | ~~线性增益~~ **已废止（2026-09-11 制作人口径：音效文件一律默认音量，不做任何音量调整）** | 引擎强制写 D_VOL=1.0；旧 CUES 里的 vol 值被忽略 |
| 8 | fadein | 入淡（秒） | None = 不入淡 |

## 🔴 引擎级坑：D_STARTOFFS 必须显式写

`AddMediaItemToTrack + AddTakeToMediaItem + SetMediaItemTake_Source` 走完后 item 从源 0 秒开始播。**start_offset 只传给取窗逻辑而不写 take 属性 = 等于没生效**——历史上有八集 116 条 cue 因此取窗全部失效（off=0）。新代码必须带：

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
RPR.GetSetMediaItemTakeInfo_String(take, 'P_NAME', stem, True)   # stem=源文件名去扩展名；不设 P_NAME 条目在画面里无标签
# 贴完立即回读验证 source 没丢；丢了删 item 返回失败
```

## 空 item / source 交替丢失（批量贴轨已知现象）

take 的 source 赋值可能**交替性丢失**——item 有位置长度但无源（空块）。防御三招：

1. 贴完立即读回 source filename 验证；
2. 失败的用底层 API 重贴（直接传 source id，不经对象包装）；
3. 失败呈交替模式时，**同脚本重跑第二轮即全过**（每轮进程状态重置）。

⚠️ 防重逻辑盲区：跳过判定不能只看「同位置有 item」——要连 source 一起验，**空块必须删掉重贴**（曾发生：首轮 source 丢，次轮被防重挡住永久漏修）。终验 = 全工程扫 source filename 存在性。

## 防重（幂等，必须保留）

贴之前扫目标区间已有 item：**源文件名（P_NAME）相同 且 |位置差| < 0.15s** 视为已存在，跳过。段错误后直接重跑不会贴重。**副作用：计数器会归零失真——判断成败必须靠区间对账，不是脚本打印的「新增 N 条」。**

## 两轮重试

对账数量不足自动再跑一轮（防重保证第二轮只补缺的）。两轮后仍不足报错退出，人工介入。

## 三重自检（全过才算完工）

1. **区间对账**：目标 Region 内 item 数 == CUES 条数（逐条打印 相对时间/长度/源文件名 人工过目）。
2. **重叠检测**：同轨相邻 item 时间窗不得重叠（`a.pos + a.len > b.pos + 0.05` 即违规）。重叠的音效分上下不同轨叠加。
3. **越界审计**：每条 cue `off + len ≤ 源时长`（`scripts/audit_offsets.py`，对 DB 的 duration 全量比对，可续跑；源长按文件名取最大值兜底同名异目录）。超界处理原则：**裁 len，宁可短不可越界**（越界 = 尾部读到别的段/静音）。

## 操作纪律（五条，全部条目出自实机教训）

1. **探针先行**：没把握的 API/格式，先在 2 秒试条上验证再跑全量。
2. **单批小步**：多集/多轨分批跑，每批独立自检，不攒大单（段错误重跑好定位）。
3. **备份先行**：改状态前存 JSON，改文件前复制 .rpp；恢复后 diff 校验。
4. **对账文化**：数量对 item、文件对字节、内容对电平，三个全过才算完成。
5. **留痕**：脚本头部注释写清 base、集数、核验状态，事后可追溯。

## 抄旧 place 模板前必查 vol 行（20260916 事故新增）

`place_ep26_v1.py` 等旧定稿模板的 CUES 九元组里 vol 列是**真值生效**的（`if vol: SetMediaItemInfo_Value(..., "D_VOL", vol)`），与 skill「一律默认音量」条款相抵。抄任何旧模板时必须二选一：**vol 列全改 1.0**，或**删除 vol 赋值行**。20260916 夜批照抄旧模板 vol 列，531 条被调量返工——旧模板的「已定稿」只代表当年口径，不代表现在。

## Base 实测（每集开始）

```python
for i in range(RPR.CountProjectMarkers(0)):
    ok, isrg, pos, endpos, name, idx = RPR.EnumProjectMarkers2(0, i)
    if isrg and name == f"{ep}":        # 按项目 Region 命名规则匹配
        BASE, BASE_END = pos, endpos
```

不要估算。项目侧的 region 基准表 CSV 只是缓存，冲突时以工程实测为准。**派单/脚本里的 BASE 与偏移值必须注明来源基准表文件并照抄，禁止对话手抄**（20260916 夜批手抄 BASE 错 2.375s，两集 69 条返工）。

⚠️ 包装层坑（20260916 实测，详表见 `reaper-remote-ops` skill §7）：`CountProjectMarkers(0,0,0)` 返回的 markers/regions 出参在 reapy_boost 网络通道会误报 0（总数 [0] 可信）；`EnumProjectMarkers` 的**名称出参会原样回显占位串**（读不到真名）——Region/marker 的对账一律逐条枚举、**按位置比对**，删 marker 按返回值末位的 marker id 识别，枚举序号≠id。

## 存档

- place 脚本按集留档（`place_ep{NN}_v1.py`），头部注释写明「报告已帧证据核验」作为质检痕迹。
- 工程保存与静默关闭的完整流程见 references/pitfalls.md。
- **每批做完更新项目侧进度文档**。
