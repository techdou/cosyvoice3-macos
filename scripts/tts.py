#!/usr/bin/env python3
"""
CosyVoice3 local TTS (macOS Apple Silicon) — official prompt format + voice bank.

Prompt format per upstream example.py (cosyvoice3_example):
  system_prompt + '<|endofprompt|>' + reference_transcript

Voice resolution order: --voice <bank-id> > --reference/--reference-text > bundled default.
Bank voices use the official speaker-registration path (add_zero_shot_spk ->
inference_zero_shot(zero_shot_spk_id=...)).

Usage:
  .venv/bin/python tts.py "文本" -o out.wav
  .venv/bin/python tts.py --text-file script.txt -o out.wav
  .venv/bin/python tts.py "文本" --voice my-voice -o out.wav
  .venv/bin/python tts.py "文本" -r ref.wav --reference-text "逐字稿" -o out.wav
"""
import argparse
import errno
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
import time

REPO = os.environ.get(
    "COSYVOICE_REPO",
    os.path.expanduser("~/.cosyvoice3-repo"),
)
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "third_party/Matcha-TTS"))

BANK = os.path.join(REPO, "voices")
VALID_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
BUNDLED_REF = os.path.join(REPO, "asset", "zero_shot_prompt.wav")
BUNDLED_REF_TEXT = "希望你以后能够做的比我还好呦。"
SYSTEM_PROMPT = "You are a helpful assistant."
MODEL_DIR = os.path.join(REPO, "pretrained_models", "Fun-CosyVoice3-0.5B")
REQUIRED_MODEL_FILES = (
    "cosyvoice3.yaml",
    "llm.pt",
    "flow.pt",
    "hift.pt",
    "campplus.onnx",
    "speech_tokenizer_v3.onnx",
    os.path.join("CosyVoice-BlankEN", "model.safetensors"),
)
MIN_SPEED = 0.5
MAX_SPEED = 2.0
MIN_REF_SECONDS = 2.5
MAX_REF_SECONDS = 20.0
OPTIMAL_MIN_REF_SECONDS = 3.0
OPTIMAL_MAX_REF_SECONDS = 10.0


def build_prompt(transcript: str) -> str:
    """官方格式：system prompt 与参考逐字稿之间用 <|endofprompt|> 分隔。"""
    return f"{SYSTEM_PROMPT}<|endofprompt|>{transcript}"


def fail(message: str) -> None:
    sys.exit(f"Error: {message}")


def nonempty_file(path: str) -> bool:
    try:
        return os.path.isfile(path) and os.path.getsize(path) > 0
    except OSError:
        return False


def missing_model_files(model_dir: str):
    return [
        rel for rel in REQUIRED_MODEL_FILES
        if not nonempty_file(os.path.join(model_dir, rel))
    ]


def validate_model_dir(model_dir: str) -> None:
    if not os.path.isdir(model_dir):
        fail(
            f"model directory not found: {model_dir}. "
            "Run scripts/install.sh to download the pinned model."
        )
    missing = missing_model_files(model_dir)
    if missing:
        fail(
            "model is incomplete; missing "
            + ", ".join(missing)
            + ". Re-run scripts/install.sh to resume the download."
        )


def load_bank_voice(voice_id: str):
    if not VALID_ID.match(voice_id):  # block path traversal via --voice
        fail(f"invalid voice id '{voice_id}'")
    vj = os.path.join(BANK, voice_id, "voice.json")
    ref = os.path.join(BANK, voice_id, "ref.wav")
    if not (os.path.isfile(vj) and os.path.isfile(ref)):
        fail(
            f"voice '{voice_id}' is missing ref.wav or voice.json. "
            "Run voice_manager.py list; remove and re-add corrupt entries."
        )
    try:
        with open(vj, encoding="utf-8") as f:
            meta = json.load(f)
        metadata_id = meta["id"]
        transcript = meta["transcript"].strip()
    except json.JSONDecodeError as exc:
        fail(f"voice '{voice_id}' metadata is invalid JSON: {exc}. Remove and re-add it.")
    except KeyError as exc:
        fail(f"voice '{voice_id}' metadata is missing {exc.args[0]}. Remove and re-add it.")
    except FileNotFoundError:
        fail(f"voice '{voice_id}' metadata disappeared. Run voice_manager.py list.")
    except (TypeError, AttributeError):
        fail(f"voice '{voice_id}' metadata has invalid field types. Remove and re-add it.")
    if not transcript:
        fail(f"voice '{voice_id}' has an empty transcript. Remove and re-add it.")
    if metadata_id != voice_id:
        fail(
            f"voice '{voice_id}' metadata id is {metadata_id!r}. "
            "Remove and re-add it."
        )
    return ref, transcript


def read_text_file(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        fail(f"text file not found: {path}")
    except UnicodeDecodeError:
        fail(f"text file must be UTF-8: {path}")
    except OSError as exc:
        fail(f"cannot read text file {path}: {exc}")


def validate_reference_duration(path: str) -> None:
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        dur = float(out)
    except FileNotFoundError:
        fail("ffprobe not found. Install ffmpeg first: brew install ffmpeg")
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        fail(f"ffprobe could not read reference audio {path}: {detail or exc}")
    except ValueError:
        fail(f"ffprobe returned an invalid duration for reference audio {path}")
    if dur < MIN_REF_SECONDS or dur > MAX_REF_SECONDS:
        fail(
            f"reference audio is {dur:.1f}s; hard range is "
            f"{MIN_REF_SECONDS:g}-{MAX_REF_SECONDS:g}s, optimal is "
            f"{OPTIMAL_MIN_REF_SECONDS:g}-{OPTIMAL_MAX_REF_SECONDS:g}s."
        )
    if dur < OPTIMAL_MIN_REF_SECONDS or dur > OPTIMAL_MAX_REF_SECONDS:
        print(
            f"Warning: reference audio is {dur:.1f}s; optimal range is "
            f"{OPTIMAL_MIN_REF_SECONDS:g}-{OPTIMAL_MAX_REF_SECONDS:g}s.",
            file=sys.stderr,
        )


def validate_output_path(path: str) -> str:
    out = os.path.abspath(os.path.expanduser(path))
    if os.path.splitext(out)[1].lower() != ".wav":
        fail("output must be a .wav file")
    parent = os.path.dirname(out) or "."
    if not os.path.isdir(parent):
        fail(f"output directory does not exist: {parent}")
    if os.path.isdir(out):
        fail(f"output path is a directory: {out}")
    if os.path.exists(out) and not os.access(out, os.W_OK):
        fail(f"output file is not writable: {out}")
    try:
        fd, probe = tempfile.mkstemp(prefix=".cosyvoice3_write_", suffix=".tmp", dir=parent)
        os.close(fd)
        os.unlink(probe)
    except OSError as exc:
        fail(f"output directory is not writable: {parent} ({exc})")
    return out


def acquire_lock():
    lock_path = os.path.join(tempfile.gettempdir(), f"cosyvoice3_tts_{os.getuid()}.lock")
    lock_fd = None
    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.ftruncate(lock_fd, 0)
        os.write(lock_fd, str(os.getpid()).encode("ascii"))
        return lock_fd
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EAGAIN):
            holder = "unknown"
            if lock_fd is not None:
                try:
                    os.lseek(lock_fd, 0, os.SEEK_SET)
                    holder = os.read(lock_fd, 32).decode("ascii", errors="replace").strip() or "unknown"
                finally:
                    os.close(lock_fd)
            fail(
                f"another CosyVoice3 synthesis is already running (PID {holder}). "
                "Wait for it to finish; the lock releases automatically."
            )
        if lock_fd is not None:
            os.close(lock_fd)
        fail(f"cannot create synthesis lock {lock_path}: {exc}")


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


def main():
    ap = argparse.ArgumentParser(description="CosyVoice3 local TTS")
    ap.add_argument("text", nargs="?", default="", help="要合成的文本（或改用 --text-file）")
    ap.add_argument("--text-file", help="从文件读长文本（UTF-8），覆盖位置参数")
    ap.add_argument("-o", "--output", help="输出 WAV 路径（默认 /tmp/cosyvoice3_<rand>.wav）")
    ap.add_argument("--voice", help="音色库 ID（voice_manager.py add 注册；不能与 --reference 同用）")
    ap.add_argument("-r", "--reference", help="零样本克隆参考音频（2.5-20s，最佳3-10s，须有授权）")
    ap.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="speech speed multiplier, 0.5-2.0 (default: 1.0)",
    )
    ap.add_argument(
        "--reference-text",
        help="参考音频的逐字稿（纯文本，脚本会自动拼接官方 system prompt）。"
        "使用非自带参考音时必填，否则克隆质量会崩",
    )
    args = ap.parse_args()

    if args.voice and (args.reference or args.reference_text):
        fail("--voice cannot be combined with --reference/--reference-text")
    if args.reference_text and not args.reference:
        fail("--reference-text requires --reference")
    text = args.text
    if args.text_file:
        text = read_text_file(args.text_file)
    text = text.strip()
    if not text:
        fail("empty text (positional or --text-file)")
    if not (MIN_SPEED <= args.speed <= MAX_SPEED):
        fail(f"--speed must be between {MIN_SPEED} and {MAX_SPEED}")
    out = validate_output_path(
        args.output or os.path.join(
            tempfile.gettempdir(), f"cosyvoice3_{os.urandom(4).hex()}.wav"
        )
    )

    bank_mode = False
    if args.voice:
        ref_path, transcript = load_bank_voice(args.voice)
        bank_mode = True
    elif args.reference:
        if not args.reference_text:
            fail("--reference requires paired --reference-text (verbatim reference transcript)")
        ref_path = os.path.abspath(os.path.expanduser(args.reference))
        transcript = args.reference_text.strip()
        if not transcript:
            fail("--reference-text must not be empty")
    else:
        ref_path, transcript = BUNDLED_REF, BUNDLED_REF_TEXT

    if not os.path.isfile(ref_path):
        fail(f"reference audio not found: {ref_path}")
    if not os.access(ref_path, os.R_OK):
        fail(f"reference audio is not readable: {ref_path}")
    validate_reference_duration(ref_path)
    validate_model_dir(MODEL_DIR)

    lock_fd = acquire_lock()
    try:
        try:
            from cosyvoice.cli.cosyvoice import AutoModel
            import torch
            import torchaudio
        except ImportError as exc:
            fail(
                f"cannot import CosyVoice dependencies: {exc}. "
                "Run inside $COSYVOICE_REPO/.venv or re-run scripts/install.sh."
            )

        t0 = time.time()
        try:
            model = AutoModel(model_dir=MODEL_DIR)
        except (FileNotFoundError, KeyError, json.JSONDecodeError, OSError, RuntimeError, ValueError) as exc:
            fail(f"model load failed: {exc}. Re-run scripts/install.sh to repair the model.")
        load_s = time.time() - t0

        t1 = time.time()
        parts = []
        try:
            if bank_mode:
                ok = model.add_zero_shot_spk(build_prompt(transcript), ref_path, args.voice)
                if ok is not True:
                    fail("add_zero_shot_spk failed; re-add the voice or try a different reference")
                gen = model.inference_zero_shot(
                    text, "", "", zero_shot_spk_id=args.voice, stream=False, speed=args.speed
                )
            else:
                gen = model.inference_zero_shot(
                    text, build_prompt(transcript), ref_path, stream=False, speed=args.speed
                )
            for idx, chunk in enumerate(gen, start=1):
                try:
                    speech = chunk["tts_speech"]
                except KeyError:
                    fail(f"synthesis chunk {idx} is missing tts_speech")
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
            if audio.numel() == 0 or audio.shape[-1] == 0:
                fail("synthesis produced empty audio")
            atomic_save_wav(torchaudio, out, audio, model.sample_rate)
        except (RuntimeError, OSError) as exc:
            fail(f"synthesis failed: {exc}. Retry with shorter text or another voice.")
    finally:
        os.close(lock_fd)

    dur = audio.shape[-1] / model.sample_rate
    gen_s = time.time() - t1
    print(f"load={load_s:.1f}s gen={gen_s:.1f}s dur={dur:.1f}s rtf={gen_s / dur:.2f}")
    print(f"OUTPUT={out}")


if __name__ == "__main__":
    main()
