# 分轨导出（实战定稿方案）

> 需求典型：48000Hz / 24bit WAV / 推子归零 / 只要动作组子轨 / 全时长。
> 结论：REAPER 的 RENDER_SETTINGS "stems" 模式实测**输出内容全静音**（根因未明）。唯一可靠方案 = **逐轨 solo + 渲染 master mix 循环**。工具：`scripts/export_stems.py`。

## 为什么不能走「看起来对」的路（实测结论，别翻案）

| 尝试 | 结果 |
|------|------|
| RENDER_SETTINGS=2（master mix） | 只出一条 Master.wav |
| RENDER_SETTINGS=1（stems+master） | 出分轨但混入不想要的轨 |
| RENDER_SETTINGS=3（纯 stems，各种组合） | **文件正常但内容全静音**——根因未明 |
| 42230 渲染命令 | 异步，循环里秒退，全 0MB |
| ✅ 逐轨 solo + 41824 + 轮询等待 | **可行，定稿** |

master 通道永远可靠；成熟方案（reaper-mcp 等）同款思路。

## 定稿流程

### 1. 备份工程状态
所有轨的 `D_VOL / B_MUTE / I_SOLO` 存 JSON（脚本自动生成带时间戳的备份文件）。

### 2. 全部推子归零
所有轨 `D_VOL=1.0`（**线性值**，1=0dB，不是 dB！）、清 mute/solo。

### 3. 公共渲染设置

```python
RPR.GetSetProjectInfo_String(0, 'RENDER_FORMAT', 'ZXZhdxgAAAA=', True)  # wav 24bit 配置串（已 ffprobe 验证 pcm_s24le）
RPR.GetSetProjectInfo(0, 'RENDER_SETTINGS', 0, True)    # master mix（solo 隔离单轨）
RPR.GetSetProjectInfo(0, 'RENDER_BOUNDSFLAG', 1, True)  # 整个工程时长
RPR.GetSetProjectInfo(0, 'RENDER_SRATE', 48000, True)
RPR.GetSetProjectInfo(0, 'RENDER_CHANNELS', 2, True)
RPR.GetSetProjectInfo(0, 'RENDER_TAILFLAG', 0, True)
RPR.GetSetProjectInfo(0, 'RENDER_DITHER', 0, True)
RPR.GetSetProjectInfo(0, 'RENDER_ADDTOPROJ', 0, True)
RPR.GetSetProjectInfo(0, 'RENDER_NORMALIZE', 0, True)
RPR.GetSetProjectInfo(0, 'RENDER_RESAMPLE', 3, True)
RPR.GetSetProjectInfo_String(0, 'RENDER_FILE', out_dir, True)
RPR.GetSetProjectInfo_String(0, 'RENDER_PATTERN', track_name, True)  # 文件名 = 轨名
```

新格式先跑 2 秒试条 + ffprobe 验证再全量。

### 4. 逐轨循环：solo → 渲染 → 等待 → unsolo

```python
RPR.SetTrackSolo / SetMediaTrackInfo_Value(t.id, 'I_SOLO', 1)
RPR.Main_OnCommand(41824, 0)   # File: Render project, using the most recent render settings
# ⚠️ 41824 同步阻塞到渲染结束；⚠️ 绝不能用 42230（异步，循环里直接掐掉）
```

### 5. 渲染等待与校验（双保险）

- **写满判断**：预期字节 = 工程时长(s) × 48000 × 3字节 × 2声道（24bit 立体声 ≈ 288,000 B/s）。轮询文件大小到 `≥ 预期×0.98` 且**连续 2 次读数稳定**才算完；稳定但没写满 = 短渲染，自动重试（最多 3 次）。
- **电平终验**：每条跑 `ffmpeg -i 文件 -af volumedetect -f null -` 看 max_volume。**全静音文件（≈ -91dB）立即原样重渲即愈**——实测出过一次（渲染期间被手动暂停污染）。

### 6. 恢复现场
从备份 JSON 回写所有轨 D_VOL/B_MUTE/I_SOLO，逐轨 diff 校验恢复完整。

## 运行约束

- 🔴 **渲染全程不许碰 REAPER**。人类手动暂停过一次后，首遍渲染整条静音（文件正常、内容污染）——原样重渲即愈。此坑已写进终验逻辑。
- 沙箱环境 `os.remove` 可能被拦：脚本内删除包裹 try/except 即可（REAPER 会覆盖重写），批量清理旧产物用外部 shell `rm`。
- 渲染 N 轨 × 全时长，单条几分钟，全程约半小时。**耐心轮询文件大小，不要 sleep 硬等**。

## 成品验收标准

1. 格式：ffprobe 确认 `pcm_s24le, 48000 Hz`（逐条）
2. 电平：volumedetect 的 max_volume 均非静音值
3. 数量：= 动作组子轨数（如 15 条）
4. 时长：与工程时长一致（各条相同）

交付前在项目侧进度文档记录：导出目录、轨清单、验收结果。
