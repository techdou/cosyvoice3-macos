# Windows 部署参考（qwen3tts 后端）

> 实测环境：Windows 11 · RTX 5070 Ti Laptop 12GB（Blackwell, sm_120）·
> uv 0.8.22 · 部署日期 2026-08-24。

## 为什么 Windows 走 qwen3tts 而不是 CosyVoice

- CosyVoice 上游钉 `torch==2.3.1/cu121`：无 sm_120 轮子，RTX 50 系直接不可用；
  且上游无官方 Windows 路径（issues #979/#1046 仍 open）。
- Qwen3-TTS：纯 pip 包（`qwen-tts` 钉 `transformers==4.57.3`）、支持本地目录
  加载模型、0.6B 权重仅 ~1.7GB、官方对 Windows 无已知阻塞。

## 安装（一键）

```powershell
# 模型已在 E:\models\Qwen3-TTS（Qwen3-TTS-12Hz-0.6B-Base，HF 格式，2.4GB）
python scripts/install.py qwen3tts --venv-dir "E:\poly-tts\venvs\qwen3tts" --model-dir "E:\models\Qwen3-TTS"
# 无模型时自动下载：加 --download hf（或 --download ms 走 ModelScope）
```

要点：
- **torch 必须装 cu128 轮子**（`torch 2.11.0+cu128` 实测）——RTX 50 系 Blackwell
  的 sm_120 只有 cu128 索引里有。installer 检测到 nvidia-smi 自动选。
- venv 建议放非系统盘（torch cu128 全套 ~3GB+）。
- Python 3.12 venv（uv 自动管理，不依赖系统 Python 版本）。

## 实测数据（2026-08-24）

| 指标 | 数值 |
|------|------|
| 模型加载（冷/暖） | ~5.5s / ~5.3s |
| 推理 RTF | 首次 6.6（含 CUDA 预热），稳态 ~5.9 |
| 显存占用 | ~4GB（bf16，0.9B 参数） |
| 输出 | 24kHz / mono / 16bit WAV |

> RTF 偏慢的主因：**flash-attn 在 Windows 装不了官方轮子**，qwen-tts 走
> 手动 PyTorch attention 路径（每次运行都打 Warning，无害）。1 分钟音频 ≈ 6 分钟
> 生成——口播/讲义场景可用；要实时性选 dashscope 后端。

## 已知坑

| # | 现象 | 结论 |
|---|------|------|
| 1 | `pysox` import 时打 "SoX could not be found!" 大警告 | 无害。12Hz tokenizer 走 onnx，不需要 SoX 二进制；stderr 照常转发不影响退出码 |
| 2 | soundfile 保存临时文件报 "unable to get format from file extension" | 临时文件必须保留 `.wav` 后缀（`.part.wav`），worker 已内置 |
| 3 | flash-attn 提示每次出现 | 同 1，Windows 无官方轮子，忽略 |
| 4 | 大模型进程并发 | 调度器已持 msvcrt 单实例锁，第二个请求立即报错而非爆显存 |

## 路径约定（本机实测布局）

```
C:\Users\<user>\.poly-tts\config.json      # 后端注册表（installer 写入）
C:\Users\<user>\.poly-tts\assets\           # 官方 demo 参考音频缓存（首次自动下载）
C:\Users\<user>\.poly-tts\voices\           # 共享音色库
E:\poly-tts\venvs\qwen3tts\                 # venv（torch cu128 ~3GB，放数据盘）
E:\models\Qwen3-TTS\                        # 模型（2.4GB）
```

`POLY_TTS_HOME` 环境变量可整体挪 `~/.poly-tts`；config 可手编（venv/model_dir 路径）。
