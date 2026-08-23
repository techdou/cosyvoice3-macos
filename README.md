# CosyVoice3 TTS Skill (macOS Apple Silicon)

Local text-to-speech agent skill wrapping Alibaba's [CosyVoice3](https://github.com/QwenAudio/CosyVoice) for macOS Apple Silicon — multilingual synthesis, zero-shot voice cloning, fully on-device inference.

> **Agent Skill 格式**：本仓库是一个 OpenClaw / ClawHub 兼容的 Agent Skill，AI Agent 读取 `SKILL.md` 后即可自主调用。

## ✨ 特性

- 🗣️ **多语言合成** — 中/英/跨语言，inline 非语言标签（笑声等）
- 🎭 **零样本音色克隆** — 一段 3-10s 授权参考音频 + 逐字稿即可注册专属音色
- 🚀 **语速控制** — `--speed 0.5~2.0`
- 🔒 **单实例锁** — fcntl 锁防止多进程并发加载模型导致内存爆掉
- ⚛️ **原子输出** — 临时文件 + 校验 + 原子替换，不会留半截音频
- 📦 **可恢复安装** — 模型文件清单校验，断点续传；锁定已验证 commit `074ca6d`（`COSYVOICE_COMMIT` 可覆盖）
- 🛡️ **前置校验** — 参数/路径/音色元数据在模型加载前快速失败，报错自带修复提示

## 📁 目录结构

```
├── SKILL.md                      # Skill 定义（Agent 入口，先读这个）
├── scripts/
│   ├── install.sh                # 一键安装：依赖预检 + clone + venv + 模型下载
│   ├── download_models.py        # 模型下载（清单校验，可续传）
│   ├── tts.py                    # 合成 CLI
│   └── voice_manager.py          # 音色库管理（add/list/remove）
└── references/
    └── deployment-macos.md       # macOS 部署细节与故障排查
```

## 🚀 快速开始

### 安装（新机器）

```bash
bash scripts/install.sh
```

预检 `git/uv/ffmpeg/ffprobe` 与磁盘空间（模型 ~9.1GB，repo 共 ~11GB），然后部署到 `~/.cosyvoice3-repo`（`COSYVOICE_REPO` 可覆盖）。

### 合成

```bash
REPO=~/.cosyvoice3-repo
PY=$REPO/.venv/bin/python

# 基础合成（内置中文女声音色）
$PY scripts/tts.py "你好，世界。" -o /tmp/hello.wav

# 语速控制
$PY scripts/tts.py "语速快一点" --speed 1.5 -o /tmp/fast.wav

# 长文本（从文件读，上游自动分块）
$PY scripts/tts.py --text-file script.txt --voice my-voice -o out.wav
```

### 注册克隆音色

```bash
# 仅使用你有权使用的音频；硬限 2.5-20s，最佳 3-10s
$PY scripts/voice_manager.py add my-voice --wav ~/ref.wav --text "参考音频逐字稿"
$PY scripts/voice_manager.py list
$PY scripts/tts.py "用我的音色说话" --voice my-voice -o me.wav
```

## ⏱️ 性能实测（M4 / 16GB RAM / CPU 推理）

| 指标 | 数值 |
|------|------|
| 冷启动（模型加载） | ~53s |
| 热启动 | ~8s |
| RTF | 1.7~2.1 |
| 1300 字端到端 | ~4min |
| 内存峰值 | 5-7GB |

## ⚠️ 伦理与安全

- 只克隆**获得授权**的参考音频；不要克隆他人声音
- 本 skill 的合成不经过第三方 API，数据不出机器

## 📄 许可

- 本 skill 代码：MIT（基于 [lhuaizhong](https://clawhub.ai/user/lhuaizhong) 的原版 skill 大幅加固重构）
- 上游 CosyVoice：Apache-2.0
- 模型 Fun-CosyVoice3-0.5B：遵循其发布许可
