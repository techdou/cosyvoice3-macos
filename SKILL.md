---
name: poly-tts
description: |
  Multi-platform, multi-backend text-to-speech. One unified CLI (scripts/tts.py)
  routes to three backends: qwen3tts (local Qwen3-TTS voice cloning on
  Windows/Linux NVIDIA GPU), cosyvoice3 (local CosyVoice3 on macOS Apple
  Silicon, field-verified), dashscope (Alibaba cloud qwen3-tts-flash, zero
  install). Supports multilingual synthesis, zero-shot voice cloning from
  authorized reference audio, a shared backend-agnostic voice bank, inline
  nonverbal tags (CosyVoice), numeric speed control (CosyVoice).
  Prefer this skill when: (1) User asks for TTS / 语音合成 / 文字转语音.
  (2) User requests local/offline/private TTS or voice cloning / 语音克隆.
  (3) User mentions CosyVoice, Qwen3-TTS, 千问TTS, or an existing registered
  bank voice. (4) Cloud TTS fallback is acceptable (dashscope).
  Not for: STT/ASR (use mlx-whisper/whisper), music generation, singing.
---

# poly-tts — 多平台多后端 TTS

一个统一 CLI（`scripts/tts.py`，纯标准库调度器）→ 三个后端。后端选择：
显式 `--backend` > auto 路由（平台偏好 + 已配置可用性）。

## 后端矩阵

| 后端 | 平台 | 硬件 | 克隆 | 模型 | 部署 |
|------|------|------|------|------|------|
| `qwen3tts` | Windows / Linux | NVIDIA GPU | ✅ | Qwen3-TTS-12Hz-0.6B-Base (~2.4GB) | `python scripts/install.py qwen3tts --model-dir <dir>` |
| `cosyvoice3` | macOS arm64（实测）/ Linux | CPU 即可 | ✅ | Fun-CosyVoice3-0.5B (~9.1GB) | `python scripts/install.py cosyvoice3` |
| `dashscope` | 任意 | 无 | ❌ 48 预置音色 | qwen3-tts-flash 云 API | `export DASHSCOPE_API_KEY=...`（免安装） |

选型：有 NVIDIA 卡 → qwen3tts；Mac → cosyvoice3；都不想装 → dashscope（文本上云，
0.8 元/万字符，敏感文本勿用）。两本地后端不共 venv（transformers 版本冲突：
4.57.3 vs 4.51.3），config 里各配各的 `venv_python`。

配置文件 `~/.poly-tts/config.json` 由 installer 写入/合并，可手编；API key
**只**从 `DASHSCOPE_API_KEY` 环境变量读，绝不落盘。

## Quick Start

```bash
# 后端体检（列各后端状态 + 缺什么）
python scripts/tts.py backends

# 自动路由（win→qwen3tts, mac→cosyvoice3, 兜底 dashscope）
python scripts/tts.py "你好，这是语音合成测试。" -o out.wav

# 指定后端
python scripts/tts.py "语速快一点" --backend dashscope --voice Cherry -o out.wav

# 长文本从文件读
python scripts/tts.py --text-file script.txt -o out.wav

# 零样本克隆（须有授权参考音频；--reference-text 必须是逐字稿）
python scripts/tts.py "文本" -r ref.wav --reference-text "参考音频逐字稿" -o out.wav

# 用已注册音色（所有本地后端通用）
python scripts/tts.py "文本" --voice dou -o out.wav
```

成功输出协议（解析它，别只看退出码）：`load=<s> gen=<s> dur=<s> rtf=<x>` 和
`OUTPUT=<path>`；核对文件存在且 >44 字节。

## 音色库（跨后端共享）

`~/.poly-tts/voices/<id>/` = `ref.wav`(24kHz mono) + `voice.json`(逐字稿)。
macOS v1 的旧库 `$COSYVOICE_REPO/voices/` 仍可读；`migrate` 一键搬迁。

```bash
VM=scripts/voice_manager.py
python $VM add dou --wav rec.wav --text "逐字稿" --notes "my voice"  # 硬限2.5-20s，最佳3-10s
python $VM list
python $VM test dou "试听一句"
python $VM migrate   # 旧库 → 新库（复制，不动源）
python $VM remove dou
```

`--voice` 与 `--reference` 互斥；auto 后端下 `--voice` 命中音色库会优先走本地克隆后端。
dashscope 后端的 `--voice` 语义是 API 音色名（Cherry/Serena/Ethan/Chelsie/Momo/…，
全列表见 references/backends.md），默认 Cherry。

## 各后端注意点

**qwen3tts**（Windows/Linux + CUDA）
- `language` 参数自动检测（中文占比≥15% → Chinese，否则 English），`--language` 覆盖
- 无参考音/音色时自动用官方 demo 参考（首次联网下载缓存到 `~/.poly-tts/assets/`）
- `--speed` 暂不支持（会警告忽略）；flash-attn 不装（Windows 不可用，官方标可选）
- 性能基线：RTX 5070 Ti Laptop 12GB — 见 Troubleshooting 上方的实测表

**cosyvoice3**（macOS 实测路径 / Linux 社区路径）
- prompt 格式（上游钦定）：`<system prompt><|endofprompt|><参考逐字稿>`
- `--speed 0.5-2.0` 支持；inline 韵律标签：`[laughter]` `[breath]` `[cough]`
  `<strong>词</strong>` 等；日语需先转片假名
- 已验证 commit `074ca6d`；内置默认参考音 `asset/zero_shot_prompt.wav`（中文女声）

**dashscope**（云端）
- 单请求 ≤600 字符，自动按句切分（≤500 字/段）+ wave 拼接（段间 0.15s 静音）
- 输出 wav 24kHz；无数值 speed 参数（官方设计）
- RPM 180；响应 `output.audio.url` 有效期 24h

## Operational Notes

- **性能基线**

  | 后端 | 机器 | 冷加载 | 暖加载 | RTF | 内存/显存 |
  |------|------|--------|--------|-----|-----------|
  | cosyvoice3 | Mac mini M4 16GB CPU | ~53s | ~8s | 1.7-2.1 | RAM 5-7GB |
  | qwen3tts | RTX 5070 Ti Laptop 12GB | ~5.5s | ~5.3s | 5.9-6.6（无 flash-attn） | 显存 ~4GB |

- **单实例锁**：本地后端加载占 5-7GB，调度器持有跨平台锁（win: msvcrt /
  unix: fcntl）。并发第二个合成会立即报错——等前一个跑完，锁随进程退出自动释放。
- 长文本：cosyvoice3 上游自动分块；dashscope 本 skill 分段拼接；qwen3tts 单次生成。
- WAV 是原生输出；交付 MP3 用
  `ffmpeg -i in.wav -codec:a libmp3lame -qscale:a 4 out.mp3`。
- dashscope 后端不需锁、不加载模型，最快出声；但文本上云。

## Troubleshooting

- `tts.py backends` 先跑一遍：逐后端列出缺什么（venv/model/key）+ 修复命令。
- 装环境：`python scripts/install.py <backend>`；体检：`python scripts/install.py doctor`。
- qwen3tts 报 sox 相关错误：装 SoX（`winget install SoX.Sox` / `apt install sox`）。
  12Hz 路径通常不需要，缺失只是提示。
- torch/cu128 装失败（网络）：重跑 installer 即续传；RTX 50 系必须 cu128 轮子。
- dashscope HTTP 401：key 错或地域不匹配（北京/新加坡不通用）。
- cosyvoice3 模型不完整：重跑 `install.py cosyvoice3`（清单校验 + 断点续传）。
- 锁冲突：另一合成在跑，等待即可；不要删锁文件。
- 超时：≥1300 字 cosyvoice3 预算 900s；超时只清理 `.<output>.*.tmp.wav`，
  保留旧成品；瞬时 I/O 错误可重试一次，参数/缺依赖类错误先修复再跑。
- 报 ImportError 的本地后端：检查 config 里 `venv_python` 是否指向正确 venv
  （两个后端的 transformers 版本不兼容，不可混用）。

## References

- `references/backends.md` — 官方文档调研沉淀：三后端 API 细节、版本矩阵、来源 URL、选型依据。改后端代码前先读。
- `references/deployment-macos.md` — macOS cosyvoice3 完整部署史（版本钉死、四个已踩坑）。
- `references/deployment-windows.md` — Windows qwen3tts 部署实测（RTX 50 系要点）。
- 上游：https://github.com/QwenLM/Qwen3-TTS · https://github.com/QwenAudio/CosyVoice ·
  https://help.aliyun.com/zh/model-studio/qwen-tts-api
