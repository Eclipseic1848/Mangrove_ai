# Phase 4C D5 图像音视频工具链原型报告

> 文档状态：historical-prototype
>
> 2026-08-11：该可抛弃原型源码已从生产源码树移除。本文保留方向评估证据，原型入口不再可执行；
> 当前实现范围只以 [`docs/status/current.md`](../status/current.md) 为准。

- 日期：2026-07-30
- 对应工单：[GitHub #17](https://github.com/Eclipseic1848/Mangrove_platform/issues/17)
- 阶段：`prototype_direction_approved`
- 前置调研：
  [2026-07-30-phase4c-d5-multimedia-toolchain-research.md](../research/2026-07-30-phase4c-d5-multimedia-toolchain-research.md)
- 原型入口：
  `src/services/prototypes/phase4c_media_toolchain/run.ps1`

## 1. 原型问题与边界

本原型只回答：

> 轻量本地分轨链和一个百炼 BYOK 挑战者，能否对同一非私密样本产出带时间、帧和 bbox
> 的独立 Observation，并在不静默覆盖或外发的前提下合并为可复核 Evidence？

原型使用阿里云官方公开的 Paraformer 示例 WAV，并把同一音频与三张确定性中文字幕图合成
9 秒 H.264/AAC 视频。没有读取或外发用户私有样本。百炼调用只有在
`-EnableAlibaba` 显式开关下执行，外发对象仍是官方公开 URL。

原型无数据库、无生产路由、无 Candidate/Delivery，也没有安装依赖到 Mangrove 的
Python 3.13 主环境。`uv` 使用 Python 3.12 隔离环境和固定版本依赖。

## 2. 已验证事实

### 2.1 本机与隔离环境

- 主项目解释器：`E:\python3.13\python.exe`。
- 机器：约 31.5 GiB RAM、Intel Core Ultra 9 285H、Intel Arc 140T 集成 GPU。
- 磁盘空间充足，但 Docker 探测在本轮超时；原型没有改走新的镜像或构建路线。
- 固定原型依赖：
  - PySceneDetect 0.7.1；
  - OpenCV headless 4.13.0.92；
  - PaddleOCR 3.7.0 + ONNX Runtime 1.24.2；
  - faster-whisper 1.1.0；
  - FFmpeg/ffprobe 使用本机 8.1.2。

### 2.2 FFmpeg / ffprobe

同一原型媒体被识别为：

- 时长：9000ms；
- 视频：H.264，1280×720；
- 音频：AAC，16kHz，mono。

最近一次 ffprobe 约 150–184ms。媒体预检可以稳定成为强制首层。

### 2.3 PySceneDetect

`AdaptiveDetector` 对确定性三场景样本准确产出：

| 场景 | 时间区间 | 中点 | 帧号（25fps） |
|---|---:|---:|---:|
| 1 | 0–3000ms | 1500ms | 38 |
| 2 | 3000–6000ms | 4500ms | 112 |
| 3 | 6000–9000ms | 7500ms | 188 |

首次测量约 6.9 秒；依赖缓存后的测量约 2.0 秒。每个关键帧保存 SHA-256，证明
`timestamp_ms + frame_index + image_sha256` 的 locator 形态可行。

这个样本只有硬切场景，不能证明淡入淡出、快速运动、静态长录屏或 VFR 的质量。

### 2.4 百炼 Paraformer BYOK

在显式 `-EnableAlibaba` 下，原型只提交官方公开 WAV URL：

- 模型：`paraformer-v2`；
- 任务约 6.2 秒完成；
- 文本：`Hello world, 来自阿里巴巴达摩院语音实验室。`；
- 时间：0–4080ms；
- 匿名说话人：`SPEAKER_00`；
- Provider 原生用量：`duration=4` 秒；
- Key 未进入状态、输出、命令行或报告。

这证明百炼原生异步接口可以转换为带时间、匿名 speaker、原始任务引用的
`audio_transcript` Observation。它只验证一个单说话人清晰短音频，不能证明噪声、
多人重叠、方言、长媒体或成本上限。

### 2.5 PaddleOCR mobile

第一次运行在 33.5 秒后失败于 PaddleX 的多模型平台连通性预检。按官方支持方式固定 BOS
并跳过多平台预检后：

- `PP-OCRv5_mobile_det_onnx` 成功从官方 BOS 获取；
- `PP-OCRv5_mobile_rec` 在总计 10 分钟门限内没有完成；
- 原型按门限终止，无 OCR Observation；
- 未切换镜像、模型来源或 OCR 工具。

因此当前只能确认 PaddleOCR 代码与检测模型可获取，不能确认这台机器上完整坐标 OCR 可用。

### 2.6 现有 LAN PaddleOCR-VL

项目现有配置指向完整 Pipeline `http://192.168.1.21:18081/layout-parsing`：

- `/openapi.json` 返回 200，健康检查声明完整 Pipeline；
- 对同一公开关键帧实际解析约 7 秒后返回 HTTP 500；
- 其 VLM 子服务 `192.168.1.21:18080/v1` 同时拒绝连接。

所以“服务壳健康”不能作为 OCR 可用证据。该 Pipeline 也不是普通 PaddleOCR mobile 的
等价替代，不能用它覆盖 mobile 失败。

### 2.7 faster-whisper

- 项目 Python 3.13 中已安装 faster-whisper 1.1.0，但没有可用的完整本地模型快照。
- 首次 `small` 下载因 Hugging Face Xet 分片重建失败。
- 禁用 Xet、保持同一 Hugging Face 官方仓库普通 HTTP 重试后，10 分钟内模型文件仍为
  0 字节占位。
- 到门限后终止，无 ASR Observation；未改镜像、仓库或模型尺寸。

因此 faster-whisper 是代码能力候选，不是当前机器“开箱即用”的本地回退。

## 3. 状态模型得到的答案

原型采用独立轨状态：

- `probed`
- `scenes_ready`
- `ocr_ready`
- `local_asr_ready`
- `external_asr_ready`
- `merged`

OCR 或本地 ASR 失败时，其他轨可以继续，但失败仍保留；`merged=succeeded` 只表示关联步骤
执行完成，不能表示 Evidence 已就绪。原型在审查中进一步修正了一个真实逻辑漏洞：
零 Observation 时不再让 `all([])` 把 `evidence_located` 错判为 `true`。

Observation 保留：

- `modality`
- `track_id`
- `[start_ms, end_ms)`
- `frame_index`
- 原图像素 polygon
- `speaker_label`
- extractor/version
- `raw_result_ref`

跨轨只按时间重叠建立 `temporal_overlap`，不会覆盖冲突文本，也不会把 speaker 标签传播给
画面人物。这个数据形态应进入 D6 正式 Evidence 规格。

## 4. 当前结论

### 4.1 已验证默认

- **FFmpeg/ffprobe**：媒体基础层。
- **PySceneDetect Adaptive + 后续固定间隔兜底**：关键帧候选层。
- **百炼 Paraformer BYOK**：当前机器上唯一真实产出带时间和匿名 speaker Evidence 的
  ASR 路线，但仍是外部候选，不具备生产资格。

### 4.2 当前不得作为默认

- PaddleOCR mobile：识别模型未获取完成，未产出 bbox。
- 现有 LAN PaddleOCR-VL：真实调用 HTTP 500。
- faster-whisper：模型获取失败，未产出本地转写。

### 4.3 架构建议

当前最合理的产品路线是：

1. 本地强制执行 ffprobe、音视频分轨、镜头与关键帧定位；
2. OCR 与 ASR 都是可替换 Capability，不阻塞其他轨，但缺轨必须显式显示；
3. 中文 ASR 第一候选使用用户 Key 的百炼原生接口；
4. 本地失败不得自动外发；外发仍需任务修订级确认；
5. 本地模型放入独立 DependencyBundle/模型卷，不在业务运行时联网获取；
6. D6 新增正式 media locator，禁止伪造文档 `page=1`。

## 5. 尚未验证

GitHub #17 的以下完成条件仍未满足：

- PaddleOCR 坐标 OCR 的真实成功输出；
- 本地 faster-whisper 与百炼同样本质量 A/B；
- 多说话人、重叠、噪声、方言、无字幕、损坏输入；
- 30 分钟和 2 小时以上长媒体；
- 关键帧 OCR 与 ASR 的真实跨轨关系；
- Linux/sidecar、取消、并发、资源上限与模型卷；
- 私有样本外发策略和真实成本上限。

因此 #17 不能关闭，也不能表述为 Phase 4C 或整个 Phase 4 完成。

## 6. 可复现命令

本地完整路线：

```powershell
powershell -ExecutionPolicy Bypass -File src/services/prototypes/phase4c_media_toolchain/run.ps1 -Scenario all
```

百炼公开样本路线：

```powershell
powershell -ExecutionPolicy Bypass -File src/services/prototypes/phase4c_media_toolchain/run.ps1 `
  -Scenario all -EnableAlibaba -Steps "probe,alibaba,merge"
```

镜头路线：

```powershell
powershell -ExecutionPolicy Bypass -File src/services/prototypes/phase4c_media_toolchain/run.ps1 `
  -Scenario all -Steps "probe,scenes"
```

运行期 JSON 保存在 `.pytest-tmp/phase4c/`，不进入版本控制。原型生成的人工检查媒体已清理；
依赖与模型缓存属于本机可复用缓存，未删除。

## 7. 用户决策

用户于 2026-07-31 明确确认：

1. Phase 4C 第一版采用“本地轻量媒体底座 + 百炼 BYOK ASR”：
   - 本地固定承担 ffprobe、转码、分轨、镜头检测和关键帧定位；
   - 百炼 Paraformer 作为第一版中文 ASR 外部候选；
   - 外发继续要求任务修订级显式确认，不允许本地失败后自动外发。
2. PaddleOCR 与 faster-whisper 的完整本地模型卷后置到目标服务器条件明确后，通过
   DependencyBundle/隔离 sidecar 重新验证；不继续在当前 Windows/代理环境强行下载。
3. 当前确认只冻结 D5 原型方向，不构成 D6、生产实现、默认入口切换、发布或关闭 #17
   的授权。

## 8. 2026-08-02 收口复核

D5 的结论和用户决策保持不变。本轮没有继续下载模型、调用外部 ASR、修改原型或进入 D6。
当前优先收口覆盖检索、D4、vNext 正式 Delivery 与依赖获取；D6 仍须用户明确选择后才进入。
跨阶段优先级见
[Phase 4 当前问题与优化审计](2026-08-02-phase4-current-issues-audit.md)。
