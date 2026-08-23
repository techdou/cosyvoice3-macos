---
name: cosyvoice3
description: |
  Local text-to-speech on macOS Apple Silicon using CosyVoice3 (zero-shot voice cloning).
  Supports multilingual/cross-lingual synthesis, inline nonverbal tags, numeric speed
  control, and zero-shot voice cloning from authorized reference audio.
  Prefer this skill only when: (1) User explicitly requests local/offline/private TTS.
  (2) User asks for CosyVoice or an existing registered bank voice. (3) User requests
  voice cloning from user-authorized reference audio. (4) User mentions local TTS,
  本地语音合成, or 语音克隆.
  Not for: cloud TTS APIs (MiniMax/SiliconFlow), STT/ASR (use mlx-whisper), music generation.
---

# CosyVoice3 TTS (macOS Apple Silicon)

Local TTS based on Alibaba's CosyVoice3. All inference runs on-device; no cloud calls.

## Official Sources (canonical)

| What | Link |
|------|------|
| Repo (migrated to QwenAudio org) | https://github.com/QwenAudio/CosyVoice (old FunAudioLLM/CosyVoice 302s here) |
| Model (ModelScope) | https://www.modelscope.cn/models/FunAudioLLM/Fun-CosyVoice3-0.5B-2512 |
| Model (HuggingFace) | https://huggingface.co/FunAudioLLM/Fun-CosyVoice3-0.5B-2512 |
| Paper | https://arxiv.org/pdf/2505.17589 |
| Demo page | https://funaudiollm.github.io/cosyvoice3/ |
| Canonical usage example | `example.py` in the repo (`cosyvoice3_example()`) |

License: Apache-2.0 (commercial use OK).

## Environment Layout (already deployed)

- Repo + venv + model live at `~/.cosyvoice3-repo` (commit 074ca6d,
  2026-05-26; smoke-tested 2026-08-23)
- Run everything with `.venv/bin/python` inside the repo; no conda exists on this machine
- Model: `pretrained_models/Fun-CosyVoice3-0.5B` (~9.1GB; repo total ~11GB)
- Bundled reference voice: `asset/zero_shot_prompt.wav` (Chinese female) — its transcript
  is built into `scripts/tts.py`, no need to pass it manually

## Prompt Format (critical, upstream-skill got this wrong)

CosyVoice3 inference methods take a *combined prompt string*:

```
<system prompt><|endofprompt|><reference transcript>
```

e.g. `You are a helpful assistant.<|endofprompt|>希望你以后能够做的比我还好呦。`

- `<|endofprompt|>` separates the system prompt from the reference transcript — it is NOT
  a suffix appended after the transcript
- Upstream also has instruct APIs, but `scripts/tts.py` currently exposes only zero-shot
  synthesis plus `--speed`.
- In cross-lingual mode, language tags like `<|en|>` prefix the synthesized text itself

## Quick Start

```bash
REPO=~/.cosyvoice3-repo

# Basic synthesis (bundled Chinese female voice)
$REPO/.venv/bin/python <skill-dir>/scripts/tts.py "你好，这是本地语音合成。" -o /tmp/out.wav

# Long text from file
$REPO/.venv/bin/python <skill-dir>/scripts/tts.py --text-file /tmp/script.txt -o /tmp/out.wav

# Synthesis with a registered custom voice
$REPO/.venv/bin/python <skill-dir>/scripts/tts.py "文本" --voice dou -o /tmp/out.wav

# Speed control (validated range 0.5-2.0)
$REPO/.venv/bin/python <skill-dir>/scripts/tts.py "文本" --voice dou --speed 1.1 -o /tmp/out.wav

# Voice cloning (requires authorized reference audio + its verbatim transcript)
$REPO/.venv/bin/python <skill-dir>/scripts/tts.py "文本" \
    -r /path/to/ref.wav --reference-text "参考音频逐字稿" -o /tmp/out.wav
```

`tts.py` prints `load=<s> gen=<s> dur=<s> rtf=<x>` and `OUTPUT=<path>` — parse these for
verification; do not claim success without checking the output file exists and is non-trivial
in size.

## Custom Voice Bank (user-defined voices)

Managed by `scripts/voice_manager.py`; bank lives at `$REPO/voices/` (untracked dir inside
the repo — survives git pull and skill reinstalls; do NOT touch `pretrained_models/`).
Each voice = canonical 24kHz mono `ref.wav` + `voice.json` (transcript + metadata).

Synthesis with `--voice <id>` goes through the official speaker-registration path
(`add_zero_shot_spk` → `inference_zero_shot(zero_shot_spk_id=...)`) — the same mechanism as
upstream `save_spkinfo`, but we register per-process instead of writing `spk2info.pt` into
the model dir, keeping the model directory pristine.

```bash
PY=$REPO/.venv/bin/python; VM=<skill-dir>/scripts/voice_manager.py

# Register (only voices you are authorized to use; hard range 2.5-20s, optimal 3-10s;
# --text must be the VERBATIM transcript of the audio)
$PY $VM add dou --wav ~/recording.wav --text "逐字稿内容" --notes "my voice"
$PY $VM list
$PY $VM test dou "试听一句话"
$PY $VM remove dou
```

Choose exactly one reference source: `--voice`, paired `--reference/--reference-text`, or the
bundled default. Ambiguous combinations fail before model loading.

## Operational Notes

- **Performance baseline (2026-08-23):** cold-cache model load ≈53s, warm-cache load ≈8s,
  RTF ≈1.7-2.1 on M4 CPU. A 1300-character cron narration measured around 4 minutes.
- **RAM peak 5-7GB**: avoid running while other heavy jobs hold memory.
- Long text: upstream token-chunks normalized input; this wrapper concatenates returned chunks
  and atomically publishes the final WAV.
- Single instance: `tts.py` takes a nonblocking macOS lock in `/tmp`. If another synthesis is
  active, retry after it finishes; the kernel lock releases automatically when the process exits.
- WAV is the native output. Convert to MP3 with
  `ffmpeg -i in.wav -codec:a libmp3lame -qscale:a 4 out.mp3` for delivery.
- **Japanese requires katakana transcription** of the text first (official requirement,
  see example.py comment).

## Prosody / Fine-Grained Control

Inline tags inside the synthesized text (full list in repo `cosyvoice/tokenizer/tokenizer.py`):
`[laughter]`, `[breath]`, `[quick_breath]`, `[cough]`, `[sigh]`, `[noise]`, `<strong>词</strong>`,
`<laughter>词</laughter>`.

Pronunciation hotfix for rare chars: insert pinyin like `给[j][ǐ]予好评` or CMU phonemes.
Use `--speed` for numeric speed control. Instruct/dialect CLI controls are not exposed by
this wrapper.

## Voice Cloning Rules

1. Reference audio: hard range 2.5-20s, optimal 3-10s, single speaker, clean recording,
   16kHz+.
2. `--reference-text` is REQUIRED and must be the verbatim transcript of the audio.
   Missing/wrong transcript is the #1 cause of bad cloning quality.
3. Only use voices you are authorized to clone. Never clone others' voices for
   impersonation. Commercial services' preset voices (e.g. MiniMax Podcast_girl) are
   NOT authorized for cross-vendor cloning.

## Troubleshooting

- Preflight: `git`, `uv`, `ffmpeg`, and `ffprobe` must be installed; keep at least 13GB free
  for install/repair (`brew install git uv ffmpeg`).
- Incomplete model error: re-run `bash scripts/install.sh`; it validates required files and
  resumes ModelScope downloads instead of trusting the directory.
- Lock error: another synthesis is running. Wait; do not delete the lock file or overlap 5-7GB
  model processes.
- Timeout: allow up to 900s for the measured ~1300-character workload. Terminate on timeout,
  remove only hidden `.<output>.*.tmp.wav` files next to the target, and keep the prior final WAV.
- Retry policy: retry once for timeout/transient I/O after cleanup. Do not retry missing tools,
  low disk, incomplete models, corrupt voices, or invalid arguments until the reported cause is fixed.
- Broken voice: `voice_manager.py list` identifies corrupt metadata. Use
  `voice_manager.py remove <id>` then re-add from the original authorized reference.
- Import error → you're outside the venv; use `$REPO/.venv/bin/python`.
- numpy/torch binary mismatch → `uv pip install --python $REPO/.venv/bin/python numpy==1.26.4`.
- Fresh install or reinstall on a new machine → read `references/deployment-macos.md` first,
  then run `bash scripts/install.sh`.

## References

- `references/deployment-macos.md` — full install path, version pins, known pitfalls,
  performance baseline, notes on unused RL weights. Read this before touching the environment.
- `scripts/download_models.py` — main-model repair plus optional manual/experimental downloads
  (extra models are not selected by `tts.py`).
- Upstream: https://github.com/QwenAudio/CosyVoice
