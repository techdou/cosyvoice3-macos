#!/usr/bin/env python3
"""
CosyVoice3 backend worker — INTERNAL, run by scripts/tts.py inside the
CosyVoice repo venv. Do not call directly.

Input:  one JSON payload on stdin (text/output/ref_path/ref_text/
        register_speaker_id/speed/model_dir/repo)
Output: load=..s gen=..s dur=..s rtf=.. and OUTPUT=<path> on stdout.

Prompt format per upstream example.py (cosyvoice3_example):
  system_prompt + '<|endofprompt|>' + reference_transcript
"""
import json
import os
import sys
import tempfile
import time

REPO = os.environ.get(
    "COSYVOICE_REPO", os.path.expanduser("~/.cosyvoice3-repo")
)
for p in (REPO, os.path.join(REPO, "third_party/Matcha-TTS")):
    if os.path.isdir(p):
        sys.path.insert(0, p)

BUNDLED_REF = os.path.join(REPO, "asset", "zero_shot_prompt.wav")
BUNDLED_REF_TEXT = "希望你以后能够做的比我还好呦。"
SYSTEM_PROMPT = "You are a helpful assistant."


def fail(message: str) -> None:
    sys.exit(f"Error: {message}")


def build_prompt(transcript: str) -> str:
    return f"{SYSTEM_PROMPT}<|endofprompt|>{transcript}"


def atomic_save_wav(torchaudio, out: str, audio, sample_rate: int) -> None:
    parent = os.path.dirname(out) or "."
    fd, tmp = tempfile.mkstemp(prefix=f".{os.path.basename(out)}.", suffix=".tmp.wav", dir=parent)
    os.close(fd)
    try:
        torchaudio.save(tmp, audio, sample_rate)
        if not os.path.isfile(tmp) or os.path.getsize(tmp) <= 44:
            fail("synthesis produced an empty WAV; retry with shorter text or another voice")
        try:
            import soundfile as sf
            info = sf.info(tmp)
            if info.frames <= 0:
                fail("synthesis produced a zero-duration WAV; final output was not replaced")
            if int(info.samplerate) != int(sample_rate):
                fail("temporary WAV sample-rate validation failed; final output was not replaced")
        except (RuntimeError, ValueError) as exc:
            fail(f"temporary WAV validation failed: {exc}; final output was not replaced")
        os.replace(tmp, out)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


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
    spk_id = payload.get("register_speaker_id") or ""
    speed = float(payload.get("speed") or 1.0)
    model_dir = payload.get("model_dir") or os.path.join(
        REPO, "pretrained_models", "Fun-CosyVoice3-0.5B"
    )
    if not ref_path:
        ref_path, ref_text = BUNDLED_REF, BUNDLED_REF_TEXT

    try:
        from cosyvoice.cli.cosyvoice import AutoModel
        import torch
        import torchaudio
    except ImportError as exc:
        fail(
            f"cannot import CosyVoice dependencies: {exc}. "
            "This worker must run with $COSYVOICE_REPO/.venv python (see install)."
        )

    t0 = time.time()
    try:
        model = AutoModel(model_dir=model_dir)
    except (FileNotFoundError, KeyError, json.JSONDecodeError, OSError, RuntimeError, ValueError) as exc:
        fail(f"model load failed: {exc}. Re-run the installer to repair the model.")
    load_s = time.time() - t0

    t1 = time.time()
    parts = []
    try:
        if spk_id:
            ok = model.add_zero_shot_spk(build_prompt(ref_text), ref_path, spk_id)
            if ok is not True:
                fail("add_zero_shot_spk failed; re-add the voice or try a different reference")
            gen = model.inference_zero_shot(
                text, "", "", zero_shot_spk_id=spk_id, stream=False, speed=speed
            )
        else:
            gen = model.inference_zero_shot(
                text, build_prompt(ref_text), ref_path, stream=False, speed=speed
            )
        for idx, chunk in enumerate(gen, start=1):
            speech = chunk.get("tts_speech")
            if not torch.is_tensor(speech):
                fail(f"synthesis chunk {idx} is not an audio tensor")
            if speech.ndim == 1:
                speech = speech.unsqueeze(0)
            if speech.ndim != 2 or speech.shape[-1] == 0:
                fail(f"synthesis chunk {idx} has an invalid audio shape")
            parts.append(speech)
        if not parts:
            fail("synthesis returned no audio")
        audio = torch.cat(parts, dim=-1)
        if audio.numel() == 0:
            fail("synthesis produced empty audio")
        atomic_save_wav(torchaudio, out, audio, model.sample_rate)
    except SystemExit:
        raise
    except (RuntimeError, OSError) as exc:
        fail(f"synthesis failed: {exc}. Retry with shorter text or another voice.")

    dur = audio.shape[-1] / model.sample_rate
    gen_s = time.time() - t1
    print(f"load={load_s:.1f}s gen={gen_s:.1f}s dur={dur:.1f}s rtf={gen_s / dur:.2f}")
    print(f"OUTPUT={out}")


if __name__ == "__main__":
    main()
