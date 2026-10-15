# 坑与 FAQ 归档（foley 工作流域 · 已固化坑）

> 本册为记录层：已转化为脚本门禁或条款的坑存档备查，**不入 agent 入场阅读**；查案/回溯时才翻。
> 现役坑见 `references/pitfalls.md`（分层规则见其维护规则第 5 条：归档坑复发且未固化 → 提回现役册）。
> 编号沿用现役册 W-xx（工作流）/ X-xx（音源检索），递增不复用；条目保持「症状 → if/动作」分支指令式。

## 工作流类

| 编号 | 现象 | 动作 | 固化去向 |
|------|------|------|------|
| W-01 | 报告里的动作在视频里不存在 | 假动作（报告幻觉）：帧证据核验一票否决，剔掉不贴；疑似假动作上报制作人，**任何人禁改判** | CLAUDE.md 纪律（上报+禁改判）+ video-action-analysis 帧证据核验条款 |
| W-02 | 报告时间戳与画面对不上 | 以抽帧实际内容为准修正 | verify_frames.py 帧比对门禁（place-and-audit.md「验收包」节） |
| W-03 | 同一音效一集反复出现 | 违反选材规则 → retrieval.md（同源 ≤2 + 异窗轮换） | retrieval.md 选材规则 + prepare_ep.py 同源计数预审 |
| W-04 | 新集不知道 Region 起点 | `extract_regions.py` 离线解析 RPP 或实测显式传入，**禁估算**；跨派单数值引用落盘文件，禁手抄 | CLAUDE.md 数值纪律条款 + SOP「派活模式」数值禁止手抄条款 |
| W-05 | 长条音效拼接露馅 | silencedetect 边界 ≥5 = oneshot 序列禁止硬拼；找长连续源取窗 | retrieval.md「贴长条前查源结构」节 |
| W-06 | 同源多处使用听感重复 | 必须取不同时间窗/切段，或换近义音源 | retrieval.md 选材规则（异窗轮换） |
| W-07 | audit_offsets.py 参数口径 | `--ep` 未命中 Region 且未传 `--base` → 报错退出（禁静默退化为全时间线审计）；`--base` 可单独传定审计起点，配合 `--ep` 时作未命中兜底 | audit_offsets.py 脚本行为（报错退出由脚本自执行） |
| W-08 | place 防重判读漏判重复 | 条目名两种形态并存：默认 = 源文件名 stem；拿不准条目 = 「label ｜ stem」。防重比对两种都认（place 模板已内置兼容） | place_ep_template.py 防重比对内置 + retrieval.md 条目命名节 |
| W-09 | 事件音在剪辑点戛然而止 | 切镜头 ≠ 切声音：延长漫过切点 + `D_FADEOUTLEN` 淡出 | 未固化 · 归档备查（复发时提回现役册） |

## 音源 / 检索类

| 编号 | 现象 | 动作 | 固化去向 |
|------|------|------|------|
| X-01 | 中文素材检索假阴性 | FTS5 默认分词不切中文连续串：desc 必须空格分隔关键词，新包入库逐条写 desc | retrieval.md 检索管线节（desc 书写规则） |
| X-02 | 素材贴上≈无声（保守电平源） | volumedetect 查源电平；**D_VOL 不动**（音画条款，禁调量补偿）：电平过低 → 换源或报存疑清单 | 铁律 8 音量一律不动 + retrieval.md 换源口径 |
| X-03 | item 有位置无波形/无声（空块） | take source 赋值丢失：回读验证 + 重贴；防重判定必须验 source 不验位置 | place_ep_template.py 防重验 source + 回读验证内置 |
| X-04 | 贴完看不到波形 | ①volumedetect 查源电平 → ②peaks 缓存（place 模板已内置 `build_peaks()` 自动建峰，手动「峰值→构建峰值」仅兜底）→ ③查轨/条目 mute | place_ep_template.py 内置 build_peaks() 自动建峰 |
| X-05 | subprocess 读 ffmpeg 输出抛 UnicodeDecodeError / 判读误判 | ffmpeg stderr 含非 UTF-8 字节：`subprocess.run(..., text=True, errors="replace")` 容错后再解析 | 工具脚本统一 errors="replace" 容错（脚本内置） |
| X-06 | DB 报 `no such module: fts5` | Python 换官方编译版（自带 FTS5） | reaper-remote-ops §1.1 装机定式 |
| X-07 | python 访问 NAS 反斜杠 UNC 失败 | 本机实测反斜杠 UNC（`\\192.168...`）exists/open 静默 False，**正斜杠**（`//192.168.../...`）正常——脚本里 NAS 路径一律正斜杠 | CLAUDE.md + SOP ④ 条款（NAS 一律脚本文件执行 + 正斜杠，禁 `python -c` 内联） |
