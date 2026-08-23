# 后端参考：官方文档调研与选型矩阵

> 检索日期 2026-08-24，来源全部为官方渠道。不确定处已标注。

## 选型矩阵

| 后端 | 平台 | 硬件 | 克隆 | 模型/规格 | 成本 |
|------|------|------|------|-----------|------|
| `qwen3tts` | Windows / Linux | NVIDIA GPU（12GB 显存即可） | ✅ 3s 参考音频 | Qwen3-TTS-12Hz-0.6B-Base（0.9B 参数，bf16 权重 ~1.7GB） | 本地推理 |
| `cosyvoice3` | macOS arm64（实测）/ Linux（社区路径） | CPU 即可（M4 RTF 1.7-2.1） | ✅ 3-10s 最佳 | Fun-CosyVoice3-0.5B（~9.1GB） | 本地推理 |
| `dashscope` | 任意 | 无 | ❌ 仅预置音色（克隆走 voice enrollment，本 skill 未封装） | qwen3-tts-flash 云 API | 0.8 元/万字符，免费 1 万字符/90 天 |

## 为什么 Windows 上没有 cosyvoice3

- 上游钉死 `torch==2.3.1 / cu121`，无 sm_120（Blackwell，RTX 50 系）轮子；新版 torch 又不满足其 requirements。
- 上游无官方 Windows 安装路径（issues #979/#1046 open）。文本前端 pynini 痛点已被 wetext 替代，但 Matcha-TTS 子模块 + sox 仍需处理，性价比不如直接用 qwen3tts。

## 为什么两个本地后端不能共用一个 venv

- CosyVoice 钉 `transformers==4.51.3`；qwen-tts 钉 `transformers==4.57.3`。冲突。
- 因此 `~/.poly-tts/config.json` 里每个后端有自己的 `venv_python`，调度器按后端选择解释器。

## qwen3tts 后端（QwenLM/Qwen3-TTS）

- 官方仓库：https://github.com/QwenLM/Qwen3-TTS
- pip 包：`qwen-tts`（依赖钉 `transformers==4.57.3`、`accelerate==1.12.0`，Python≥3.9，README 推荐 3.12）
- 模型：https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-Base（Apache-2.0）
- 加载：`Qwen3TTSModel.from_pretrained(<本地目录或HF id>, device_map="cuda:0", dtype=torch.bfloat16)`——官方明确支持本地目录路径
- 推理：`generate_voice_clone(text, language, ref_audio, ref_text)`；官方示例 ref_audio 为 URL，本 skill 传本地路径（soundfile/librosa 加载，实测可行）
- 输出：24kHz（源码 `output_sample_rate=24000`）
- flash-attn：官方措辞 "recommend"，可选，仅 fp16/bf16 可用；Windows 不装
- GPU：官方全部示例 `device_map="cuda:0"`；CPU 未被官方支持（理论可跑，慢，属实验路径——installer 会在无 GPU 时给出警告）
- 语言：Chinese / English / Japanese / Korean / German / French / Russian / Portuguese / Spanish / Italian（`language` 参数）
- 模型家族：0.6B-Base（克隆，本 skill 用）、1.7B-Base（克隆+微调）、1.7B-CustomVoice（9 预置音色+instruct 控制）、1.7B-VoiceDesign（文字描述设计音色）；API 对应 `generate_voice_clone` / `generate_custom_voice` / `generate_voice_design`
- pysox 依赖：12Hz tokenizer 路径实测不需要 SoX 二进制（onnx 推理），缺失时仅提示

## cosyvoice3 后端（QwenAudio/CosyVoice）

- 官方仓库：https://github.com/QwenAudio/CosyVoice（FunAudioLLM/CosyVoice 迁移后的地址，旧链接 302）
- 模型：https://www.modelscope.cn/models/FunAudioLLM/Fun-CosyVoice3-0.5B-2512（Apache-2.0）
- 入口：`from cosyvoice.cli.cosyvoice import AutoModel`（example.py 的 `cosyvoice3_example()` 为钦定用法）
- prompt 格式（关键）：`<system prompt><|endofprompt|><参考逐字稿>`——`<|endofprompt|>` 是分隔符不是后缀
- 已验证 commit：`074ca6d`（2026-05-26）
- 韵律标签：`[laughter]` `[breath]` `[quick_breath]` `[cough]` `[sigh]` `<strong>词</strong>` 等（tokenizer.py）
- 日语需先转片假名（官方要求）

## dashscope 后端（阿里云百炼）

- 文档：https://help.aliyun.com/zh/model-studio/qwen-tts-api
- 端点：`POST https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation`
- 认证：`Authorization: Bearer $DASHSCOPE_API_KEY`（北京/新加坡地域 key 不通用）
- body：`{"model":"qwen3-tts-flash","input":{"text":...,"voice":"Cherry","language_type":"Chinese"}}`
- 响应：`output.audio.url`（wav，24h 有效）
- 限制：单请求 ≤600 字符（本 skill 自动分段+拼接）、RPM 180
- **没有 OpenAI 兼容的 `/v1/audio/speech` 端点**——别用 OpenAI SDK 的 audio.speech 指向 compatible-mode
- 48 预置音色，常用：Cherry 芊悦（女）、Serena、Ethan（男）、Chelsie、Momo；方言 10 个（Sunny 四川话、Rocky 粤语等）。全列表：https://help.aliyun.com/zh/model-studio/qwen-tts-voice-list
- 无数值 speed/pitch 参数；语速控制走自然语言 instruct（仅 instruct 变体模型）
- 旧版 `qwen-tts` 按_token_计费且官方标注引导迁移，本 skill 不用
- 价格：https://help.aliyun.com/zh/model-studio/model-pricing

## 安全底线

- 只克隆**获得授权**的参考音频；不克隆他人声音做仿冒；不跨厂商克隆商用预置音色
- DASHSCOPE_API_KEY 只从环境变量读，绝不写入 config.json / 代码 / 日志
- 本地后端数据不出机器；dashscope 后端文本会上云（敏感文本选本地后端）
