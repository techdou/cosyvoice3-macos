# macOS Apple Silicon 部署参考

> 本目录是本地实测部署经验的沉淀。SKILL.md 只保留命令入口，排坑细节在这里。

## 官方信息（2026-08-23 核实）

- 官方仓库已迁移：`FunAudioLLM/CosyVoice` → **`QwenAudio/CosyVoice`**（旧地址 302 重定向，同一个项目）
- 本地 commit `074ca6d`（2026-05-26）即迁移后的最新 main
- 官方钦定用法参考：仓库根目录 `example.py` 的 `cosyvoice3_example()`
- prompt 格式：`<system prompt><|endofprompt|><参考文本>`——上游 clawhub skill 把
  `<|endofprompt|>` 错误地拼在参考文本末尾，v2 曾沿用，v3 已修正并实测验证
- `llm.rl.pt`（RL 后训练权重，~2GB）随模型包分发但**当前版本代码不加载它**
  （全 repo grep 无引用）。README 评测表中 RL 版 CER 0.81 是官方内部数据，不代表
  当前 repo 代码可直接使用的形态。base 版 CER 1.21 已够用。
- `spk2info.pt` 不在官方包里，frontend 对此有容错（spk 注册功能跳过，不影响 zero_shot）

## 环境概览

- 目标机：Mac mini M4 / 16GB RAM / macOS arm64
- 方案：uv venv（Python 3.10）+ torch 2.3.1 CPU 轮子（MPS 对 CosyVoice3 推理无收益，装 CPU 版即可）
- 模型：Fun-CosyVoice3-0.5B（ModelScope，约 9.1GB，Apache-2.0）
- 磁盘总占用：仓库（含约 9.1GB 模型）≈ 11GB；安装/修复前至少预留 13GB 可用空间
- 语音库参考音频契约：硬拒绝 2.5s 以下或 20s 以上，3-10s 为最佳范围

## 安装（一键）

```bash
bash <skill-dir>/scripts/install.sh
```

脚本幂等：已存在的 venv 会复用；仓库会回到已验证提交；模型只有必需文件全部存在才跳过。支持 `COSYVOICE_REPO` 环境变量覆盖仓库路径（默认 `~/.cosyvoice3-repo`）。
脚本会克隆 `QwenAudio/CosyVoice`、检出测试过的 `074ca6d`，并始终执行
`git submodule update --init --recursive`。如需测试其他上游版本，可临时设置
`COSYVOICE_COMMIT=<commit>`；这属于未验证路径。

模型跳过条件不是目录存在，而是以下必需文件全部存在：
`cosyvoice3.yaml`、`llm.pt`、`flow.pt`、`hift.pt`、`campplus.onnx`、
`speech_tokenizer_v3.onnx`、`CosyVoice-BlankEN/model.safetensors`。
缺失任一文件时重新运行安装脚本会继续 `snapshot_download`。

## 实测坑（2026-08-23，全部踩过）

| # | 坑 | 现象 | 解法 |
|---|----|------|------|
| 1 | requirements.txt 带 CUDA-only 索引 | uv 拒装，报 dependency confusion 提示 | `grep -v '^--extra-index-url' requirements.txt` 后再装 |
| 2 | openai-whisper setup.py 用 pkg_resources | 构建/安装失败 | 先 `setuptools==75.8.0` + `pip`，再 `pip install openai-whisper==20231117 --no-build-isolation` |
| 3 | whisper 拖 numpy 到 2.x | numpy 2.x 与 torch 2.3.1 二进制不兼容 | 最后 `uv pip install numpy==1.26.4` 钉回 |
| 4 | modelscope 缓存 symlink 警告 | `Failed to create symbolic link ... No such file or directory` | 无害，模型已完整落盘，忽略 |

## 性能基准（Mac mini M4 / 16GB，2026-08-23）

- 冷缓存加载模型：约 53s
- 暖缓存加载模型：约 8s
- 合成 RTF：约 1.7-2.1（CPU 推理）
- 内存峰值：约 5-7GB（推理进程存活期间）

> 推论：批处理场景一次进程内做完所有句子，别为每句话重启进程。每天一篇 1500 字口播 ≈ 5 分钟生成，完全可接受。

## 从源码升级/变更

```bash
COSYVOICE_COMMIT=<tested-commit> bash <skill-dir>/scripts/install.sh
```

不要直接 `git pull` 后继续复用旧依赖；把升级当作新的测试矩阵处理。默认安装脚本会回到
已验证的 `074ca6d`。

## 安全审查结论（2026-08-23）

对上游 skill（clawhub: cosyvoice3-macos v1.0.0）逐文件审查：4 个脚本无恶意行为，下载源全部为官方渠道（GitHub QwenAudio / ModelScope / PyTorch 官方 / PyPI），依赖锁定版本。风险点仅硬编码作者路径（本版已修）与 conda 依赖（本版已改 uv）。
