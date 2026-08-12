# Phase 4C D5 图像、音频、视频工具链调研

- 日期：2026-07-30
- 对应工单：[GitHub #17 — Phase 4C 图像音视频工具链赛马](https://github.com/Eclipseic1848/Mangrove_platform/issues/17)
- 阶段：`research_complete_pending_user_decision`
- 范围：只做高信任第一方来源调研与本地只读核对；未安装依赖、未写原型、未上传私有样本、未调用付费服务
- 后续边界：本报告不构成 `prototype` 或生产实施授权

## 1. 结论先行

### 1.1 推荐进入 D5 原型赛马的最小组合

以下是**尚未用统一真实语料验证的推荐组合**，不是生产定论。

| 层 | 默认候选 | 受控回退 / 挑战者 | 选择理由 |
|---|---|---|---|
| 媒体探测、解封装、转码 | FFmpeg/ffprobe 8.1.x，固定构建与参数白名单 | 无第二套基础设施；失败关闭 | 成熟、跨容器/编码、可输出流/帧/字幕信息；本机已有 8.1.2 |
| 镜头切分 | PySceneDetect `AdaptiveDetector` | FFmpeg `select=gt(scene,...)` 作为轻量基线；固定间隔兜底 | 自适应阈值可降低快速运动误切；但必须用真实视频调参 |
| 关键帧 OCR | PaddleOCR PP-OCRv5 server 检测+识别，保留多边形框 | PP-OCRv5 mobile；已有 Qwen-VL 仅作语义复核 | Paddle 原生返回坐标，中文能力与部署资料完整；VLM 自由文本不能替代框证据 |
| 中文 ASR | FunASR `paraformer-zh` + FSMN-VAD + 标点 + 时间戳 | 已安装 faster-whisper；WhisperX 作为时间对齐挑战者 | Paraformer 是中文专项候选；faster-whisper 现成、跨语种、支持词时间戳 |
| 说话人分离 | pyannote `community-1` 本地 sidecar | FunASR CAM++/多说话人链；外部专业 API | pyannote 有公开中文会议集 DER；但依赖重、模型下载和遥测需治理 |
| 本地视觉语义 | 已有 Qwen-VL / OpenAI-compatible 服务，只消费已选关键帧 | 不直接上传整段视频 | 控制显存、上下文和证据粒度；模型回答只能生成派生候选 |
| 中文外部 API | 阿里云百炼 Paraformer/Fun-ASR 录音文件识别 | Azure Speech Batch；OpenAI 转写/分离端点 | 阿里原生给句/词时间戳及 speaker；Azure 长批次和企业治理较完整 |

建议的原则不是“一条黑盒多媒体模型吃完整视频”，而是：

```text
不可变媒体
  → ffprobe 预检
  → 分离字幕轨 / 音轨 / 视频轨
  → 各轨独立抽取带位置的 Observation
  → 只按时间区间建立关联
  → 独立 Verifier 重开原媒体与证据
  → Candidate，而非直接 Delivery
```

### 1.2 明确不推荐

1. 不把当前 Qwen 视频 `summary` 当作原始证据；它没有帧时间和 bbox。
2. 不把固定 300 秒切片当作镜头切分；`-c copy` 还可能受关键帧边界影响。
3. 不把“OpenAI-compatible `/audio/transcriptions`”理解为统一支持词时间戳或说话人；
   兼容端点的字段与模型能力必须逐 Provider 冻结。
4. 不用全文 base64 传递长视频；它放大内存、请求体和失败重试成本。
5. 不在主 Python 环境直接安装 PaddleOCR/FunASR/pyannote/WhisperX 的全部依赖；
   应采用固定模型卷的 sidecar。
6. 不做实时流、DRM 绕过、声纹身份识别；它们不在 #17 范围。

## 2. 事实分类

### 2.1 本地已验证事实

以下结论来自 2026-07-30 对当前工作区、解释器和命令行的只读核对：

- `FFmpeg` 与 `ffprobe` 均为 `8.1.2-full_build-www.gyan.dev`，Windows full build。
- `yt-dlp==2026.7.4`、`imageio-ffmpeg==0.6.0`、`faster-whisper==1.1.0` 已安装。
- FunASR、pyannote.audio、PySceneDetect 未安装。
- `src/services/video_evidence.py` 当前：
  - 下载 VTT 后删除时间戳，只保留去重文本；
  - 远程 ASR 只读取响应顶层 `text`；
  - 本地 faster-whisper 虽迭代 `segments`，但只拼接 `segment.text`；
  - Qwen 视频能力只读取 `summary`；
  - 最终证据只有 `type + text`，没有媒体时间、帧号、bbox、speaker 或原始响应引用。
- 现有视频证据离线测试 9 项、Paddle 客户端测试 7 项通过；这只验证现有行为，不代表
  时间轴、说话人或关键帧 OCR 已实现。
- 当前 `EvidenceRef.page` 必填且 `ge=1`；媒体位置只能临时塞入 `location`，因此 D6
  仍需正式媒体 Evidence 契约，不能把文档页码伪造为媒体页。

### 2.2 基于代码的推断

- 当前 `_subtitle_text` 主动丢弃 VTT cue 时间，之后无法可靠还原字幕时间轴。
- 当前远程 ASR 即使 Provider 返回 `segments/words/speaker` 也会静默丢弃。
- 当前按时长 `-c copy` 切片可能不精确落在请求时间点；适合承载模型请求，不适合作为
  精确证据定位。
- `summary` 是派生描述，不是“画面文字原文”；把它与字幕/ASR 拼成一段文本后，
  下游无法判断冲突来自哪一轨。
- 当前全视频 base64 路径会同时占用原始字节、base64 字符串和 JSON 请求内存，长视频风险高。

### 2.3 尚未验证的建议

- “FunASR 是中文默认、faster-whisper 是回退”必须经同一中文真实集的 CER/WER、时间戳误差、
  热词、方言、噪声和长音频 A/B 后才能确认。
- “PySceneDetect AdaptiveDetector 是默认”必须经课程录屏、访谈、短视频、屏幕演示、快速运动
  和淡入淡出样本验证。
- PaddleOCR、pyannote、本地 Qwen 的速度、显存和并发阈值尚未在目标服务器测量。
- 任何外部 API 的中文效果、成本、限流、地域和数据保留仍需用户批准后以非私密样本验证。

## 3. 媒体基础设施

### 3.1 FFmpeg / ffprobe

**官方事实**

- FFmpeg 官方把 `ffmpeg` 定义为媒体转换工具；`ffprobe` 可用 `-show_format`、
  `-show_streams`、`-show_frames`、`-show_packets` 和 JSON writer 输出容器、流、帧与包信息
  （[ffprobe 官方文档](https://www.ffmpeg.org/ffprobe-all.html)）。
- FFmpeg 官方只发布源码，但其下载页列出 Linux 包、静态构建和两个 Windows 构建入口；
  FFmpeg 本身也支持 MinGW-w64 原生 Windows 构建
  （[下载页](https://ffmpeg.org/download.html)，[平台说明](https://www.ffmpeg.org/platform.html)）。
- 官方安全页持续记录解码器相关 CVE，说明不可信媒体不能在无限权宿主进程内直接处理
  （[FFmpeg Security](https://ffmpeg.org/security.html)）。

**建议**

1. 把 ffprobe 作为所有媒体的强制预检：
   - 格式、duration、start_time、bit_rate；
   - 视频 codec、宽高、像素格式、帧率、rotation；
   - 音频 codec、sample_rate、channels、channel_layout；
   - 字幕 codec、language、default/forced disposition；
   - 读取错误和流数量。
2. 使用参数数组调用，禁止 shell 拼接；输入只允许任务工作区内的已登记路径。
3. 设置进程超时、最大输出、CPU/内存/临时盘限额；禁用输入协议白名单之外的网络协议。
4. 规范化音频可生成 16 kHz、16-bit PCM mono **派生副本**，但原音轨、声道和哈希必须保留。
5. 对双声道客服录音先分别处理声道；不要先混成 mono 再做 speaker diarization。

**典型失败模式**

- 损坏容器、错误 duration、可变帧率、旋转元数据、加密/DRM、超大分辨率、字幕编码、
  恶意构造的解码器输入、硬件解码器差异。
- `stream copy` 切片的起点受关键帧影响；精确证据帧应按时间 seek 后解码或重新编码。

### 3.2 抽帧与镜头切分

**PySceneDetect 官方事实**

- `ContentDetector` 用 HSV 帧差找切点；`ThresholdDetector` 针对淡入淡出；
  `AdaptiveDetector` 在 ContentDetector 分数上使用滚动均值，可缓解快速镜头运动误报
  （[官方 detector 文档](https://www.scenedetect.com/docs/latest/api/detectors.html)）。
- PySceneDetect 0.7 提供 CLI、Python API、图片保存和 FFmpeg 分割命令；无 GUI 的服务器可安装
  `scenedetect-headless`，分割仍依赖 FFmpeg 或 mkvmerge
  （[官方文档](https://www.scenedetect.com/docs/latest/)）。

**建议的关键帧策略**

- 第一层：保留字幕/ASR 句子起止附近的帧。
- 第二层：每个检测镜头取中点帧，并在镜头过长时按最大间隔补帧。
- 第三层：全片低频固定间隔兜底，防止静态录屏只有一个“镜头”。
- 第四层：用感知哈希去重相邻近似帧；不能只按 JPEG 文件哈希。
- 每张帧保存 `timestamp_ms + frame_index + source_stream + image_sha256`。
- OCR 后只有文字框变化时才保留新证据，降低长视频帧爆炸。

FFmpeg 自带 scene 表达式可作为无额外 Python 依赖的基线
（[FFmpeg filter 官方文档](https://ffmpeg.org/ffmpeg-filters.html)），但阈值不是跨场景通用常数。

## 4. 图片与关键帧 OCR

### 4.1 PaddleOCR

**官方事实**

- PaddleOCR 的 PP-OCRv5 server 模型面向高准确率，mobile 模型面向速度；官方 OCR 文档列出
  `PP-OCRv5_server_rec` 的中文平均准确率、CPU/GPU 延迟和模型大小，但不同评测集的数值
  不能直接横比
  （[OCR pipeline 官方文档](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/OCR.en.md)）。
- PaddleOCR 3.2 官方发布说明包括 Linux/Windows C++ 部署、CUDA 12、高稳定服务化部署、
  HTTP 调用、细粒度 benchmark 和单字符坐标
  （[官方仓库](https://github.com/PaddlePaddle/PaddleOCR)）。
- 官方 FAQ 支持模型手工下载、本地路径和下载源配置，适合离线固定模型卷
  （[官方 FAQ](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/FAQ.en.md)）。

**建议**

- 默认候选：独立 PaddleOCR sidecar，server 检测+识别；GPU不足时 mobile。
- 保存原始四点 polygon；统一 Evidence 可另生成 `normalized_1000` bbox，但不能覆盖原响应。
- 先做文字检测再识别，不让 VLM 自由生成坐标。
- 视频小字需保留源帧分辨率；不要为了吞吐先统一缩到 480p。
- 针对字幕条可做受控 ROI，但全帧 OCR 仍作为召回兜底。

### 4.2 Qwen-VL / OpenAI-compatible 视觉模型

Qwen 官方视频示例支持本地路径、URL、帧列表，以及通过 `fps/num_frames/total_pixels`
限制采样与视觉 token；官方同时建议按显存限制每帧与总像素
（[Qwen3-VL 官方仓库](https://github.com/QwenLM/Qwen3-VL)）。

这类模型适合：

- 对 OCR 框做语义分类；
- 理解图表、界面状态或连续步骤；
- 对候选冲突给出需复核原因。

不适合：

- 单独充当逐字 OCR 权威来源；
- 用一个自由文本 summary 代替帧级证据；
- 让模型声称的 bbox 覆盖确定性 OCR 原坐标。

“OpenAI-compatible”只表示传输形态近似；视频输入类型、帧采样扩展和坐标输出都不是统一协议，
必须按 Provider/模型建立能力目录。

## 5. ASR 与时间戳

### 5.1 faster-whisper

**官方事实**

- faster-whisper 基于 CTranslate2；官方示例支持 segment 时间戳、`word_timestamps=True`、
  Silero VAD 和批量转写
  （[SYSTRAN 官方仓库](https://github.com/SYSTRAN/faster-whisper)）。
- 官方安装说明指出音频解码通过 PyAV 完成，通常无需系统 FFmpeg；GPU 当前依赖 CUDA 12 与
  cuDNN 9，旧 CUDA/cuDNN 组合需锁旧依赖版本。

**评价**

- 优点：项目已安装；多语种；CPU int8/GPU；词时间戳；Windows/Linux 路径相对简单。
- 风险：通用 Whisper 不是中文专项最优的既定事实；VAD、幻觉、长音频切段和专有名词仍需 A/B。
- 定位：原型中的现成基线与通用语言回退，不应因“已安装”自动成为生产默认。

### 5.2 FunASR / Paraformer

**官方事实**

- FunASR 官方仓库提供 ASR、VAD、标点、时间戳、speaker verification/diarization 和多说话人
  能力；`paraformer-zh` 标注为 60,000 小时普通话、带时间戳，另有 FSMN-VAD、CT-Punc、
  时间戳预测和 CAM++ 组件
  （[ModelScope FunASR 官方仓库](https://github.com/modelscope/FunASR)）。
- 官方也提供离线文件转写服务与 Docker 路线；这比把整套 Torch/模型依赖塞进主环境更符合
  Mangrove 隔离边界。

**评价**

- 优点：中文专项、热词/VAD/标点/时间戳组件齐全、适合服务化。
- 风险：模型与运行时组合多，版本/模型源/线程/GPU资源要冻结；官方支持列表不等于本项目
  中文噪声集必胜。
- 定位：中文默认的首要挑战者；在真实 A/B 前仍只是推荐。

### 5.3 WhisperX

WhisperX 官方仓库用 faster-whisper 批处理、wav2vec2 forced alignment 提供词时间戳，并可接
pyannote 做 speaker 标签；官方也明确 alignment 模型与语言相关，缺少默认语言模型时需自行
寻找并验证
（[WhisperX 官方仓库](https://github.com/m-bain/whisperX)）。

因此它适合做“时间对齐+说话人合并”的挑战者，但不宜直接成为首个默认：

- 依赖栈比 faster-whisper/FunASR 更重；
- 中文 alignment 模型与标点切句需专项验证；
- 它会与 Mangrove 自己的证据合并职责重叠。

## 6. 说话人分离

### 6.1 pyannote.audio

**官方事实**

- pyannote.audio `community-1` 可本地运行，输出 speaker turn；官方仓库公开了 AISHELL-4、
  AliMeeting 等中文会议集 DER，并同时提供付费 `precision-2`
  （[pyannote.audio 官方仓库](https://github.com/pyannote/pyannote-audio)）。
- 官方模型需要 Hugging Face token/接受模型条款；pipeline 默认 CPU，需显式迁移到 GPU。
- 官方说明其可选 telemetry 会记录模型来源、文件时长和 speaker 数参数，并提供
  `PYANNOTE_METRICS_ENABLED=0` 关闭方式。

**建议**

- 原型本地 diarization 基线用 `community-1`，sidecar 默认关闭 telemetry、固定模型快照。
- speaker 只记录 `SPEAKER_00/01`，不得推断真实身份。
- 用户已知人数时传 min/max 或固定 speaker count；未知时保留聚类不确定性。
- 重叠说话要允许同一时间区间出现多个 speaker observation，不能 first-wins。

### 6.2 FunASR CAM++ 与专业 API

FunASR CAM++ 可作为中文轻量 challenger，但官方“支持 diarization”不足以证明完整链路质量；
必须与 pyannote 在同一 AliMeeting/真实授权样本结构上比较。

专业 API 的 speaker ID 同样只是一次任务内的聚类标签。除非用户明确提供参考声纹并批准用途，
不得跨任务关联或命名真实人员。

## 7. 外部 API 候选

### 7.1 OpenAI 原生音频 API

**官方事实**

- OpenAI 文件转写单文件上限 25 MB；更大文件需压缩或切块，并提示不要从句中切断
  （[官方 File transcription](https://developers.openai.com/api/docs/guides/speech-to-text)）。
- `whisper-1 + verbose_json + timestamp_granularities=["word"]` 才提供词/段时间戳；
  `timestamp_granularities` 不是所有转写模型的通用能力。
- `gpt-4o-transcribe-diarize` 使用 `diarized_json` 返回 `speaker/start/end`；30 秒以上需
  `chunking_strategy=auto` 或 VAD 配置；最多可提供四个短参考音频映射已知 speaker。
- speaker diarization 不支持 Realtime transcription session。

**结论**

- 可作为用户自带 Key 的外部候选，但必须区分“普通转写”“词时间戳”“说话人分离”三个
  capability，不能只保存一个 `/audio/transcriptions` 开关。
- 25 MB 限制意味着 Mangrove 必须先做本地音频提取与语义边界切块。

### 7.2 阿里云百炼 Paraformer / Fun-ASR

**官方事实**

- 录音文件识别可返回句级和词级毫秒时间戳；Paraformer/Fun-ASR 支持单声道 speaker
  diarization，并返回 `speaker_id`；speaker count 是提示而非保证
  （[百炼官方指南](https://help.aliyun.com/en/model-studio/non-realtime-speech-recognition-user-guide)）。
- diarization 建议音频不超过 2 小时；多声道不支持 diarization，但 API 可以指定识别声道
  （[Paraformer HTTP API](https://help.aliyun.com/en/model-studio/paraformer-recorded-speech-recognition-restful-api)）。
- Qwen3-ASR 通过 OpenAI-compatible 接口时不返回时间戳；要时间戳需使用异步 Filetrans
  原生接口。这直接证明“OpenAI-compatible”不能替代能力探测。

**评价**

- 中文、方言、热词、词时间戳和 speaker 是强候选。
- 异步任务、OSS/可访问 URL、排队、地域、数据保留和费用需要独立 Provider Adapter。

### 7.3 Azure Speech

**官方事实**

- Azure Batch Transcription 支持异步批量、word/display word 时间戳、speaker diarization、
  多声道和 TTL；3 人以上可提供 min/max speaker，单文件 diarization 上限 240 分钟
  （[创建批量转写](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/batch-transcription-create)）。
- 结果包含 source、channel、offset/duration、words/displayWords 和 speaker
  （[结果结构](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/batch-transcription-get)）。
- 批处理输入/输出位于客户指定存储，TTL 可配置；传输加密
  （[数据、隐私与安全](https://learn.microsoft.com/en-us/azure/ai-foundry/responsible-ai/speech-service/speech-to-text/data-privacy-security)）。

**评价**

- 长媒体、企业存储、批量和治理能力强；但需要 Azure 资源、对象存储和异步状态机。
- 官方 P90/队列描述表明批量作业不能承诺交互式低延迟
  （[Batch overview](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/batch-transcription)）。

### 7.4 腾讯云与听悟

- 腾讯云录音文件识别官方说明支持最长 5 小时文件、中文/英语及多方言混合模型
  （[腾讯云产品功能](https://cloud.tencent.com/document/product/1093/35682)）。
- 通义听悟支持中英粤日等、最多 24 小时实时记录及可选 speaker diarization
  （[听悟 API](https://help.aliyun.com/en/tingwu/interface-and-implementation)）。

它们可留在后续专业 API 赛马，但 D5 第一轮不应同时接入所有供应商。优先比较：

1. 本地方案；
2. 中文原生阿里百炼；
3. 一个跨区域企业方案 Azure；
4. 已有模型连接体系可承载的 OpenAI 原生接口。

## 8. 证据合并契约建议

### 8.1 不做“文本拼接”，做时间轴关联

建议 D6 新增媒体 locator，而不是复用 `page=1`：

```json
{
  "artifact_id": "sha256-bound-artifact",
  "content_unit_id": "media-unit-id",
  "modality": "subtitle|audio_transcript|frame_ocr|visual_candidate",
  "track_id": "subtitle:0|audio:1|video:0",
  "start_ms": 12340,
  "end_ms": 15620,
  "frame_index": 371,
  "region": {
    "polygon": [[92, 812], [1160, 812], [1160, 1002], [92, 1002]],
    "coordinate_space": "source_pixels",
    "source_width": 1280,
    "source_height": 720
  },
  "speaker_label": "SPEAKER_01",
  "quote": "可复核原文",
  "quote_sha256": "...",
  "extractor": "paddleocr|faster-whisper|funasr|pyannote",
  "extractor_version": "...",
  "raw_result_ref": "immutable-sidecar-json",
  "confidence": 0.91
}
```

### 8.2 合并不变量

1. 字幕、ASR、帧 OCR、视觉描述分别保存，不互相覆盖。
2. 只通过 `[start_ms, end_ms)` 重叠建立 `related_to`；重叠不是“内容相同”的证明。
3. 字幕与 ASR 文本冲突时保留两者，不能选较长者或让 LLM 静默裁决。
4. speaker 标签附着在音频区间，不能自动传播到同时间画面中的人物。
5. OCR bbox 必须指向保存的关键帧；关键帧必须能由原媒体哈希+时间重新解码验证。
6. 模型总结、主题、步骤是 `DerivedResult`，其证据引用回多个原始 observation。
7. 任一非空来源观察必须有位置与原始结果引用；没有时间/框的模型自由文本只能
   `review_required`。
8. Verifier 重新打开原媒体和 sidecar，不复用 Agent 的内存对象。

### 8.3 长媒体策略

- ffprobe 先拒绝未知时长、超限流数、超限像素率或解码失败。
- 字幕轨直接解析 cue；音频按 VAD/静音边界切段，保留 0.5–2 秒上下文重叠；
  不从词或句中硬切。
- 视觉按镜头+字幕/ASR 事件采样，并设全片最大关键帧预算。
- 每段独立幂等键：`artifact_sha256 + track + start_ms + end_ms + extractor_version`。
- 分段失败可局部重试；缺段必须进入覆盖率门，不能用其他轨“补成成功”。

## 9. 资源、平台、隔离与安全

### 9.1 Windows / Linux

| 候选 | Windows 开发 | Linux/容器 | 建议 |
|---|---|---|---|
| FFmpeg/ffprobe | 官方下载页指向 Windows 构建；本机已可用 | 发行版包/静态构建成熟 | 宿主可开发，生产固定容器版本 |
| PySceneDetect | 官方提供 Windows build/headless Python 包 | headless 适合服务器 | 与 FFmpeg 同 sidecar |
| PaddleOCR | 官方声明 C++ Linux/Windows 能力 | Docker/服务化更成熟 | 不进主环境，独立服务 |
| faster-whisper | Python/CTranslate2 可用；GPU 依赖需精确匹配 | CPU/GPU 均可 | 原型基线 |
| FunASR | 不把“Python 可安装”推断为 Windows 已验证 | 官方 Docker/服务路线更合适 | Linux sidecar |
| pyannote/WhisperX | Torch/CUDA 依赖重 | Linux GPU sidecar 更可控 | 与主环境隔离 |

### 9.2 强制安全边界

- 不可信媒体解析运行在无 Docker Socket、无宿主敏感挂载、默认无网络的任务容器。
- 原文件只读挂载，输出写候选目录；禁止解析器直接发布 Delivery。
- FFmpeg 协议、demuxer、decoder、像素率、流数、时长、临时盘、CPU、内存和墙钟时间设限。
- 模型及权重在不挂载用户来源的依赖获取阶段下载，校验哈希后进入只读模型卷。
- 外部 API 必须复用 ModelConnection/Grant/Relay 的用户隔离；每次任务冻结 Provider、
  模型、能力、外发媒体范围和用户确认。
- 本地失败不得静默外发；外部失败不得静默换另一 Provider。
- 原始音视频、参考声纹、转写和关键帧都可能含敏感信息，必须按 owner/revision 隔离并进入
  生命周期策略。
- pyannote telemetry 默认显式关闭；任何后续开启都需新的用户确认。

## 10. 原型阶段建议评测矩阵

这只是 D5 `prototype` 的建议输入，尚未执行。

### 10.1 样本维度

- 中文普通话、粤语/方言、中英混说；
- 单人、双人、3–5 人、重叠说话；
- 清晰录音、远场、回声、音乐、低信噪比；
- 有人工字幕、自动字幕、无字幕、字幕与语音冲突；
- 课程录屏、会议、访谈、竖屏短视频、快速运动、静态界面；
- 5 分钟、30 分钟、2 小时以上；
- VFR、旋转、多个音轨/字幕轨、损坏/截断、无声视频。

不得用一个演示视频宣布生产可用；真实私有样本必须在用户另行批准后才能外发。

### 10.2 指标

| 领域 | 指标 |
|---|---|
| 探测/转码 | 成功率、错误分类、时长/流信息准确率、峰值内存、实时因子 |
| 镜头 | cut precision/recall、淡变召回、快速运动误报、关键帧覆盖/重复率 |
| OCR | 中文 CER、框 IoU、字幕条召回、帧级 evidence 覆盖、每帧耗时 |
| ASR | CER/WER、专名召回、时间戳 MAE/P95、空白/幻觉率、长音频漏段 |
| Diarization | DER、speaker count error、重叠说话漏检、speaker-attributed CER |
| 全链 | 每项非空 observation 证据覆盖率 100%、跨轨误合并 0、跨用户引用 0 |
| 工程 | 安装/冷启动、CPU/GPU/RAM/显存、Windows/Linux、取消/超时/局部重试 |

### 10.3 推荐赛马顺序

1. FFmpeg/ffprobe 预检与可复现媒体规范化。
2. FFmpeg scene baseline vs PySceneDetect Adaptive。
3. PaddleOCR mobile/server 与现有 Qwen 复核。
4. faster-whisper vs FunASR Paraformer 中文 ASR。
5. pyannote vs FunASR speaker 链。
6. 本地胜出者 vs 用户批准的阿里百炼/Azure/OpenAI 外部 API。
7. 最后做分轨 Evidence 合并；不能先写一个大一统黑盒。

## 11. 推荐默认、回退与淘汰候选

### 11.1 推荐默认候选

- `FFmpeg/ffprobe`：唯一媒体基础层。
- `PySceneDetect Adaptive + 固定间隔兜底`：镜头和静态录屏兼顾。
- `PaddleOCR PP-OCRv5 server`：关键帧坐标 OCR。
- `FunASR Paraformer`：中文 ASR 默认挑战者。
- `pyannote community-1`：speaker diarization 基线。
- `Qwen-VL`：已选关键帧的语义候选/复核，不作为 OCR 权威。

### 11.2 受控回退

- PaddleOCR server 资源不足 → mobile，不静默改 VLM。
- FunASR 不支持语言/失败 → faster-whisper；是否回退由任务冻结策略决定。
- pyannote 不可用 → 返回“未分离 speaker”，不伪造单说话人。
- 镜头检测无切点 → 固定间隔+字幕/ASR 事件帧。
- 外部 API 只在用户明确选择且批准媒体外发后使用。

### 11.3 淘汰或仅保留历史兼容

- 仅按固定时长 `-c copy` 切片后生成整段 summary。
- VTT 去时间戳后只存全文。
- ASR 只读顶层 `text`。
- 全视频 base64 上传作为长媒体默认。
- 模型自由文本 bbox 作为权威坐标。
- 所有外部 `/audio/transcriptions` 共享一个所谓“OpenAI-compatible”解析器。
- 本地失败后自动外发，或 Provider 失败后自动换供应商。

## 12. 必须由用户确认的决策

进入 `prototype` 前至少需要用户逐项确认：

1. 是否接受以 **FunASR Paraformer** 作为中文默认挑战者，以 faster-whisper 为基线/回退。
2. 是否允许原型下载 PaddleOCR、FunASR、pyannote 模型，并采用 Linux/Docker sidecar；
   模型体积、GPU/CPU 预算和存储上限是多少。
3. 是否接受 pyannote Hugging Face 模型条款；telemetry 是否保持强制关闭。
4. 第一轮外部 API 只选择哪一个：阿里百炼、Azure Speech 或 OpenAI；是否暂不接外部 API。
5. 哪些非私密/已授权真实样本可发送给外部服务；默认答案应为“不允许”。
6. 可接受的单任务最长媒体时长、最大文件、关键帧上限和处理时限。
7. speaker 只做任务内 A/B/C 匿名标签，是否明确禁止跨任务声纹关联。
8. D6 是否批准新增正式媒体 Evidence locator，停止用文档 `page` 字段承载时间轴。

## 13. 本阶段退出结论

高信任官方来源调研已覆盖 #17 要求的探测/转码、镜头与抽帧、坐标 OCR、中文 ASR 与时间戳、
说话人分离、分轨证据合并、本地开源、OpenAI-compatible 与专业 API。

当前最重要的架构结论是：**成熟工具应复用，但能力必须拆轨并落到统一 Evidence；模型 summary
不能替代时间和坐标证据。**

本报告完成后应停在用户决策门。未取得确认前，不安装上述依赖、不下载模型、不创建原型、
不调用外部服务，也不进入生产实现。
