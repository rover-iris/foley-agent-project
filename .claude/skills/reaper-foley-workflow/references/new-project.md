# 新建工程 / 老工程加集（Region 分集）

> 适用两个场景：A. 给一部新剧从模板新建音效工程；B. 现有工程追加新集（Region 已预留或需新造）。
> 所有路径来自 `foley_project.json` / 用户输入，本手册不含任何具体机器路径。

## 前提事实：模板与工程结构

- 模板是一份 50 轨左右的 .RPP（纯文本），分组典型为：视频轨 + 对话组（室内/心声/室外/怪兽）+ 环境fx组(01~06) + 动作fx组（脚步/脚步(D)/衣物摩擦/动作fx 03~14）+ 过渡fx组(01~06) + BGM组(01~04) + 混响组 + 编组/效果/总输出。
- **轨序即轨道索引**（从 0 数）。贴轨、导出都按索引定位，所以任何项目动手前先枚举一遍轨名并存档：

```python
for i, t in enumerate(p.tracks):
    print(i, t.name)   # 记进 foley_project.json 的 tracks 字段
```

- 动作组子轨区间（如「14:29」表示索引 14~28 共 15 条）是贴轨与分轨导出的核心参数，写进项目配置。

## Region 分集约定

- 整剧一条时间线，每集一个 Region：**Region 起点 = 该集 0 秒**，Region 长度 = 该集时长，Region 名 = 集号（如 `3`）。
- Region 依次首尾相接（上一集终点 = 下一集起点），全剧总时长 = 各集时长之和。
- 贴轨坐标一律用「Region 内相对时间」，换算 `绝对时间 = base + t_local`，base 用实测，绝不手加。

## 场景 A：新建剧集工程（通用流程）

### A1. 准备模板与集时长

1. 复制模板 RPP 为新工程（不要在模板原件上动）：
   ```bash
   python scripts/new_project.py plan --template <模板.RPP> --out <新工程.RPP> \
       --videos-dir <各集视频目录>        # 或 --durations 92.16,110.08,... 手给秒数
   ```
   脚本会：复制工程文件 → 用 ffprobe 逐个读视频时长（文件名中的数字即集号）→ 生成 `region_plan.csv`（集号/预计base/时长）。
2. 人工过目 plan（各集时长是否合理、顺序对不对）。

### A2. 建 Region（需 REAPER 运行且已打开新工程）

```bash
python scripts/new_project.py apply --project <新工程.RPP> --plan region_plan.csv \
    [--videos-dir <视频目录>]   # 可选：把每集视频贴到视频轨对应区间做参考画面
```

脚本用 `RPR.AddProjectMarker(0, True, pos, end, str(ep), -1)` 逐集建 Region，建完重新枚举**实测** base 打表。API 要点：

```python
RPR.AddProjectMarker(0, True, pos, regend, name, -1)  # isrg=True, wantidx=-1 追加
ok, isrg, pos, endpos, name, idx = RPR.EnumProjectMarkers2(0, i)  # 枚举实测
```

### A3. 落盘与存档

1. 建完 Region 后**立即用基准确表存档**（两种方式任选）：
   - 离线解析：`python scripts/extract_regions.py --rpp <工程.RPP> --out region_bases.csv`（无需 REAPER，直接解析 RPP 文本 MARKER 行）
   - 在线枚举：reapy 遍历 markers 打表
2. CSV 归档到**项目侧**，并把它注册进 `foley_project.json`（`region_csv` 字段）。
3. 保存工程：`Main_SaveProjectEx(0, <rpp路径>, 0)`（坑见 pitfalls.md）。
4. 提交命名建议：`{剧名}_全{N}集_音效工程.RPP`；即使只做部分集，也建议按总集数预留 Region（后续加集零成本）。

## 场景 B：老工程加集

1. Region 已预留（起点已有、还没贴）→ 只需 `extract_regions.py` 或在线枚举拿到该集 base，直接进每集 SOP。
2. Region 未预留 → 先拿到新集时长（ffprobe），在时间线末尾追加 Region（起点 = 当前工程末尾或上一 Region 终点），再走 A3 存档。
3. **加集不改既有 Region**：Region 一旦有 cue，起点绝不能再动（会整体位移历史 cue）。

## 直接文本改 RPP 的备选方案（REAPER 不便启动时）

RPP 是纯文本，Region 在其中形如（成对出现）：

```
  MARKER 1 0 1 1 0 1 R {GUID} 0      ← Region 1 起点 @0s，名字"1"，带 R 标志
  MARKER 1 92.16 "" 1                ← Region 1 终点 @92.16s
  MARKER 2 92.16 2 1 0 1 R {GUID} 0  ← Region 2 起点
```

`scripts/extract_regions.py` 就是按此格式解析的。手工文本编辑：改完必须用 REAPER 打开验证无解析错误，**改前备份**。

## 新工程自检清单

- [ ] 轨序枚举结果与模板一致，动作组子轨区间已记入项目配置
- [ ] Region 数 = 集数，首尾相接无缝隙无重叠
- [ ] 全剧集基准表 CSV 已归档项目侧并注册进 foley_project.json
- [ ] 抽一集 base 与 plan 对比一致
- [ ] 工程已保存（RPP mtime 已更新）
