# Changelog

## 2.0.0 (2026-08-24)

Skill 更名 `cosyvoice3` → `poly-tts`，从 macOS 专用演进为多平台多后端。

**新增**
- `qwen3tts` 后端：Qwen3-TTS-12Hz-0.6B-Base 本地克隆合成（Windows/Linux + NVIDIA
  GPU）。跨平台安装器 `install.py`（uv venv、torch cu128、模型 HF/ModelScope 双源
  下载、config 注册、doctor 体检）。RTX 50 系（Blackwell sm_120）实测可跑。
- `dashscope` 后端：阿里云 qwen3-tts-flash，零安装兜底；>600 字符自动分段合成并
  拼接；48 预置音色。主 CLI 内实现（纯标准库 urllib）。
- 统一 CLI `scripts/tts.py`：纯标准库调度器，auto 后端路由（平台偏好 + 可用性
  检查）、跨平台单实例锁（msvcrt/fcntl）、统一输出协议。
- 后端无关音色库 `~/.poly-tts/voices/`：所有本地后端共享；`voice_manager.py
  migrate` 从旧 `$COSYVOICE_REPO/voices/` 一键迁移（旧库保留只读兼容）。
- `references/backends.md`：三后端官方文档调研沉淀（来源 URL、版本矩阵、选型依据）。
- `references/deployment-windows.md`：Windows + RTX 5070 Ti 部署实测。

**变更**
- `install.sh` 放开 Linux（CUDA/CPU 按探测选择，社区路径）；尾部注册
  `~/.poly-tts/config.json`；macOS 路径不变。
- `download_models.py` 增加 qwen3-tts 条目与 `--source/--dest` 参数。
- CosyVoice 推理逻辑迁至 `scripts/backends/run_cosyvoice3.py`（内部 worker，
  stdin JSON 协议），校验逻辑与 prompt 格式不变。

**明确不支持**
- Windows 原生 cosyvoice3（上游钉 torch 2.3.1/cu121，无 Blackwell 支持；用 qwen3tts）
- macOS qwen3tts（上游无 MPS 支持，issue #345；用 cosyvoice3）

## 1.0.0 (2026-08-23)

Initial release: cosyvoice3-macos，macOS Apple Silicon 本地 CosyVoice3 合成。
基于 lhuaizhong 的 clawhub 原版 skill 加固重构：uv 替代 conda、官方 prompt
格式修正、可恢复模型下载、音色库、原子输出、单实例锁、前置校验。
