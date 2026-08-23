#!/usr/bin/env python3
"""
Qwen3-TTS backend worker — INTERNAL, run by scripts/tts.py inside the
qwen3tts venv. Do not call directly.

Model: Qwen3-TTS-12Hz-0.6B-Base (voice cloning). Loaded from a local
directory (HF format) written by install.py / download_models.py.

Input:  one JSON payload on stdin (text/output/ref_path/ref_text/
        language/model_dir/device)
Output: load=..s gen=..s dur=..s rtf=.. and OUTPUT=<path> on stdout.
"""
import json
import os
import sys
import time
import urllib.request

POLY_HOME = os.environ.get("POLY_TTS_HOME", os.path.expanduser("~/.poly-tts"))
DEMO_REF_URL = (
    "https://qianwen-res.oss-cn-beijing.aliyuncs.com/Qwen3-TTS-Repo/clone.wav"
)
DEMO_REF_TEXT = (
    "Okay. Yeah. I resent you. I love you. I respect you. "
    "But you know what? You blew it! And thanks to you."
)


def fail(message: str) -> None:
    sys.exit(f"Error: {message}")


def bundled_demo_ref() -> str:
    """Download the official demo reference once, cache under ~/.poly-tts/assets."""
    cache = os.path.join(POLY_HOME, "assets", "qwen_demo_ref.wav")
    if os.path.isfile(cache) and os.path.getsize(cache) > 44:
        return cache
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    tmp = cache + ".part"
    try:
        with urllib.request.urlopen(DEMO_REF_URL, timeout=120) as r:
            blob = r.read()
    except Exception as exc:  # noqa: BLE001 - surface one actionable message
        fail(
            f"no --voice/--reference given and the bundled demo reference could not be "
            f"downloaded ({exc}). Pass -r ref.wav --reference-text \"...\" instead."
        )
    if len(blob) <= 44:
        fail("bundled demo reference download is empty; pass -r/--reference-text instead")
    with open(tmp, "wb") as f:
        f.write(blob)
    os.replace(tmp, cache)
    return cache


def atomic_save(out: str, samples, sample_rate: int) -> None:
    import soundfile as sf
    # keep the .wav suffix: soundfile infers the container from the extension
    tmp = out + ".part.wav"
    sf.write(tmp, samples, sample_rate)
    if not os.path.isfile(tmp) or os.path.getsize(tmp) <= 44:
        fail("synthesis produced an empty WAV; retry with shorter text or another voice")
    os.replace(tmp, out)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        fail(f"bad payload on stdin: {exc}")

    text = (payload.get("text") or "").strip()
    if not text:
        fail("empty text")
    out = payload["output"]
    ref_path = payload.get("ref_path") or ""
    ref_text = payload.get("ref_text") or ""
    language = payload.get("language") or "Chinese"
    model_dir = payload.get("model_dir") or ""
    device = payload.get("device") or "auto"

    if not model_dir or not os.path.isdir(model_dir):
        fail(f"model_dir not found: {model_dir!r}. Run install.py qwen3tts first.")
    if not ref_path:
        ref_path, ref_text = bundled_demo_ref(), DEMO_REF_TEXT
    if not ref_text:
        fail("ref_text is required when ref_path is given")

    try:
        import torch
        from qwen_tts import Qwen3TTSModel
    except ImportError as exc:
        fail(f"cannot import qwen-tts dependencies: {exc}. Re-run install.py qwen3tts.")

    if device in ("", "auto"):
        device = "cuda:0" if torch.cuda.is_available() else "cpu"
    dtype = torch.bfloat16 if device.startswith("cuda") else torch.float32

    t0 = time.time()
    try:
        model = Qwen3TTSModel.from_pretrained(model_dir, device_map=device, dtype=dtype)
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        fail(f"model load failed: {exc}. Check model_dir contents (model.safetensors + speech_tokenizer/).")
    load_s = time.time() - t0

    t1 = time.time()
    try:
        wavs, sr = model.generate_voice_clone(
            text=text,
            language=language,
            ref_audio=ref_path,
            ref_text=ref_text,
        )
    except TypeError as exc:
        fail(
            f"generate_voice_clone rejected arguments ({exc}); your qwen-tts version "
            "may differ from the pinned one. Re-run install.py qwen3tts."
        )
    except (RuntimeError, OSError, ValueError) as exc:
        fail(f"synthesis failed: {exc}. Retry with shorter text or another reference.")
    gen_s = time.time() - t1
    if not wavs:
        fail("synthesis returned no audio")

    atomic_save(out, wavs[0], sr)
    dur = len(wavs[0]) / float(sr)
    print(f"load={load_s:.1f}s gen={gen_s:.1f}s dur={dur:.1f}s rtf={gen_s / max(dur, 0.01):.2f}")
    print(f"OUTPUT={out}")


if __name__ == "__main__":
    main()
