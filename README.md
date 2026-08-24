# poly-tts — 多平台多后端语音合成 Skill

一个 Agent Skill：统一 CLI 入口 + 三个可切换的 TTS 后端（本地克隆 × 2、云端 × 1），Windows / macOS / Linux 通吃。

> 仓库背景：本仓库起于 macOS 专用的 cosyvoice3-macos skill，现演进为多平台多后端的 poly-tts，仓库已随之更名为 `techdou/poly-tts`（旧地址自动重定向）。macOS CosyVoice3 路径保留原有实测验证，新增 Windows/Linux 的 Qwen3-TTS 本地后端与阿里云 DashScope 兜底后端。

> **Agent Skill 格式**：AI Agent 读取 `SKILL.md` 后即可自主调用。

## 后端矩阵

| 后端 | 平台 | 硬件 | 声音克隆 | 部署量 |
|------|------|------|----------|--------|
| `qwen3tts` | Windows / Linux | NVIDIA GPU（12GB 即可） | ✅ | venv + torch cu128 + 模型 2.4GB |
| `cosyvoice3` | macOS Apple Silicon（实测）/ Linux | CPU 即可 | ✅ | venv + 模型 9.1GB |
| `dashscope` | 任意 | 无 | ❌（48 预置音色） | 零安装，配 `DASHSCOPE_API_KEY` |

三个后端共享同一个 CLI、同一套音色库（`~/.poly-tts/voices/`）、同一个输出协议
（`OUTPUT=<path>` + load/gen/dur/rtf 计时行）。后端选择：显式 `--backend` >
平台自动路由。两个本地后端依赖冲突（transformers 4.57.3 vs 4.51.3），各自独立
venv，由 `~/.poly-tts/config.json` 登记、调度器自动选用。

## 安装

前置：`uv`、`ffmpeg`。模型默认下载到 `~/.poly-tts/models/`，可用 `--model-dir`
指向已有目录（如 `E:\models\Qwen3-TTS`）。

```bash
# Windows / Linux + NVIDIA GPU（本机有模型直接登记）
python scripts/install.py qwen3tts --model-dir "E:/models/Qwen3-TTS"
# 或让安装器下载模型（HuggingFace / ModelScope 二选一）
python scripts/install.py qwen3tts --download hf

# macOS / Linux（CosyVoice3，走原实测安装路径）
python scripts/install.py cosyvoice3

# 云端兜底（无需安装）
export DASHSCOPE_API_KEY=sk-xxx   # https://bailian.console.aliyun.com

# 体检
python scripts/install.py doctor
python scripts/tts.py backends
```

## 使用

```bash
# 自动路由：win→qwen3tts / mac→cosyvoice3 / 兜底 dashscope
python scripts/tts.py "你好，世界。" -o out.wav

# 云端预置音色
python scripts/tts.py "播报一段新闻" --backend dashscope --voice Cherry -o out.wav

# 注册自己的音色（须有授权参考音频 + 逐字稿；2.5-20s，最佳 3-10s）
python scripts/voice_manager.py add dou --wav rec.wav --text "参考音频逐字稿"
python scripts/tts.py "用我的声音说话" --voice dou -o me.wav

# 零样本一次性克隆
python scripts/tts.py "文本" -r ref.wav --reference-text "逐字稿" -o out.wav
```

详细用法、各后端差异、韵律标签、语速控制见 `SKILL.md`；官方文档调研与选型
依据见 `references/backends.md`。

## 目录结构

```
├── SKILL.md                      # Skill 定义（Agent 入口）
├── scripts/
│   ├── tts.py                    # 统一 CLI（纯标准库调度器 + dashscope 实现）
│   ├── backends/
│   │   ├── run_cosyvoice3.py     # cosyvoice3 后端 worker（跑在它的 venv 里）
│   │   └── run_qwen3tts.py       # qwen3tts 后端 worker（跑在它的 venv 里）
│   ├── install.py                # 跨平台安装器 + doctor
│   ├── install.sh                # macOS/Linux cosyvoice3 原验证路径（install.py 委托它）
│   ├── download_models.py        # 模型下载（清单校验，断点续传，HF/ModelScope）
│   └── voice_manager.py          # 音色库（add/list/remove/test/migrate）
└── references/
    ├── backends.md               # 三后端官方文档调研沉淀
    ├── deployment-macos.md       # macOS 部署细节（版本钉死 + 已踩坑）
    └── deployment-windows.md     # Windows/RTX50 部署实测
```

## 伦理与安全

- 只克隆**获得授权**的参考音频；不克隆他人声音做仿冒，不跨厂商克隆商用预置音色
- 本地后端数据不出机器；dashscope 后端文本会上云（敏感内容选本地）
- `DASHSCOPE_API_KEY` 只从环境变量读取，不写入任何文件

## 许可

- 本 skill 代码：MIT（基于 [lhuaizhong](https://clawhub.ai/user/lhuaizhong) 的原版 cosyvoice3-macos skill 演进）
- 上游模型：CosyVoice Apache-2.0 · Qwen3-TTS Apache-2.0 · DashScope 按阿里云服务条款
