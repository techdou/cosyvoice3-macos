#!/usr/bin/env python3
"""
poly-tts unified CLI — one entry, multiple TTS backends.

Backends:
  qwen3tts    local Qwen3-TTS (GPU, Windows/Linux) — voice cloning via qwen-tts package
  cosyvoice3  local CosyVoice3 (macOS CPU / Linux CUDA) — voice cloning, voice bank speakers
  dashscope   Alibaba DashScope cloud API (qwen3-tts-flash) — zero-install fallback

This dispatcher is stdlib-only. Heavy backends run in their own venv via
scripts/backends/run_cosyvoice3.py and scripts/backends/run_qwen3tts.py
(internal protocol: one JSON payload on stdin, OUTPUT=<path> on stdout).

Config: ~/.poly-tts/config.json (written by install.py / install.sh; hand-editable).
API keys are NEVER stored in config — dashscope reads DASHSCOPE_API_KEY from env.

Usage:
  python tts.py "文本" -o out.wav
  python tts.py "文本" --backend qwen3tts --voice dou -o out.wav
  python tts.py "文本" --backend dashscope --voice Cherry -o out.wav
  python tts.py backends    # show backend availability
"""
import argparse
import errno
import getpass
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import wave

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

POLY_HOME = os.environ.get("POLY_TTS_HOME", os.path.expanduser("~/.poly-tts"))
CONFIG_PATH = os.path.join(POLY_HOME, "config.json")
BANK = os.path.join(POLY_HOME, "voices")
LEGACY_BANK = os.path.join(
    os.environ.get("COSYVOICE_REPO", os.path.expanduser("~/.cosyvoice3-repo")), "voices"
)
VALID_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
MIN_SPEED = 0.5
MAX_SPEED = 2.0
MIN_REF_SECONDS = 2.5
MAX_REF_SECONDS = 20.0

# 平台偏好：auto 路由时按此顺序取第一个可用的后端
PLATFORM_PREFERENCE = {
    "win32": ["qwen3tts", "dashscope"],
    "darwin": ["cosyvoice3", "dashscope"],
    "linux": ["qwen3tts", "cosyvoice3", "dashscope"],
}


def fail(message: str) -> None:
    sys.exit(f"Error: {message}")


# ---------------------------------------------------------------- config ----

def load_config() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        if not isinstance(cfg, dict):
            raise ValueError("config root must be an object")
        return cfg
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, ValueError, OSError) as exc:
        fail(f"cannot read config {CONFIG_PATH}: {exc}. Fix or delete it, then re-run install.")


def backend_config(cfg: dict, name: str) -> dict:
    entry = cfg.get("backends", {}).get(name)
    if not isinstance(entry, dict):
        return {}
    return entry


def nonempty_file(path) -> bool:
    try:
        return os.path.isfile(path) and os.path.getsize(path) > 0
    except OSError:
        return False


def check_backend_ready(cfg: dict, name: str) -> tuple[bool, str]:
    """Return (ready, missing_what)."""
    if name == "dashscope":
        if os.environ.get("DASHSCOPE_API_KEY"):
            return True, ""
        return False, "DASHSCOPE_API_KEY is not set — export it (https://bailian.console.aliyun.com → API-KEY); no install needed"
    entry = backend_config(cfg, name)
    if not entry:
        return False, f"backend '{name}' not configured — run: python scripts/install.py {name}"
    venv_py = entry.get("venv_python", "")
    if not (os.path.isfile(venv_py)):
        return False, f"venv python missing: {venv_py} — re-run install.py {name}"
    model_dir = entry.get("model_dir", "")
    if not os.path.isdir(model_dir):
        return False, f"model dir missing: {model_dir} — run install.py {name} (or download_models.py)"
    if name == "cosyvoice3":
        required = (
            "cosyvoice3.yaml", "llm.pt", "flow.pt", "hift.pt",
            "campplus.onnx", "speech_tokenizer_v3.onnx",
            os.path.join("CosyVoice-BlankEN", "model.safetensors"),
        )
        missing = [r for r in required if not nonempty_file(os.path.join(model_dir, r))]
        if missing:
            return False, "model incomplete, missing " + ", ".join(missing)
    else:
        required = ("model.safetensors", "config.json", "preprocessor_config.json")
        missing = [r for r in required if not nonempty_file(os.path.join(model_dir, r))]
        tok = os.path.join(model_dir, "speech_tokenizer", "model.safetensors")
        if not nonempty_file(tok):
            missing.append(os.path.join("speech_tokenizer", "model.safetensors"))
        if missing:
            return False, "model incomplete, missing " + ", ".join(missing)
    return True, ""


def pick_backend(cfg: dict, requested: str) -> str:
    if requested != "auto":
        if requested not in ("qwen3tts", "cosyvoice3", "dashscope"):
            fail(f"unknown backend '{requested}' (qwen3tts | cosyvoice3 | dashscope | auto)")
        ok, why = check_backend_ready(cfg, requested)
        if not ok:
            fail(why)
        return requested
    for name in PLATFORM_PREFERENCE.get(sys.platform, ["dashscope"]):
        ok, _ = check_backend_ready(cfg, name)
        if ok:
            return name
    problems = []
    for name in ("qwen3tts", "cosyvoice3", "dashscope"):
        ok, why = check_backend_ready(cfg, name)
        if not ok:
            problems.append(f"  {name}: {why}")
    fail("no usable backend. Per-backend status:\n" + "\n".join(problems))


# ------------------------------------------------------------------ voice ----

def load_bank_voice(voice_id: str):
    """Resolve a bank voice to (ref.wav path, transcript). Checks new bank first,
    then the legacy macOS ~/.cosyvoice3-repo/voices bank."""
    if not VALID_ID.match(voice_id):
        fail(f"invalid voice id '{voice_id}'")
    for bank_dir in (BANK, LEGACY_BANK):
        if not os.path.isdir(bank_dir):
            continue
        vj = os.path.join(bank_dir, voice_id, "voice.json")
        ref = os.path.join(bank_dir, voice_id, "ref.wav")
        if os.path.isfile(vj) and os.path.isfile(ref):
            try:
                with open(vj, encoding="utf-8") as f:
                    meta = json.load(f)
                transcript = meta["transcript"].strip()
            except (json.JSONDecodeError, KeyError, TypeError, AttributeError, OSError) as exc:
                fail(f"voice '{voice_id}' metadata unreadable ({exc}); remove and re-add it")
            if not transcript:
                fail(f"voice '{voice_id}' has an empty transcript; remove and re-add it")
            return ref, transcript, bank_dir
    fail(
        f"voice '{voice_id}' not found. Checked {BANK}"
        + (f" and {LEGACY_BANK}" if os.path.isdir(LEGACY_BANK) else "")
        + ". Register with: voice_manager.py add"
    )


# ----------------------------------------------------------- input checks ----

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


def validate_output_path(path: str) -> str:
    out = os.path.abspath(os.path.expanduser(path))
    if os.path.splitext(out)[1].lower() != ".wav":
        fail("output must be a .wav file")
    parent = os.path.dirname(out) or "."
    if not os.path.isdir(parent):
        fail(f"output directory does not exist: {parent}")
    if os.path.isdir(out):
        fail(f"output path is a directory: {out}")
    return out


def probe_duration(path: str) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        return float(out)
    except FileNotFoundError:
        fail("ffprobe not found. Install ffmpeg (winget install Gyan.FFmpeg / brew install ffmpeg / apt install ffmpeg)")
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        fail(f"ffprobe could not read reference audio {path}: {detail or exc}")
    except ValueError:
        fail(f"ffprobe returned an invalid duration for reference audio {path}")


# ------------------------------------------------------------------- lock ----

def acquire_lock():
    """Cross-platform single-instance lock (protects 5-7GB local model loads)."""
    user = (getpass.getuser() or "default").replace("\\", "_").replace("/", "_")
    lock_path = os.path.join(tempfile.gettempdir(), f"poly_tts_{user}.lock")
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
    except OSError as exc:
        fail(f"cannot create synthesis lock {lock_path}: {exc}")
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        fail(
            "another poly-tts synthesis is already running. Wait for it to finish; "
            "the lock releases automatically when that process exits."
        )
    try:
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}".encode("ascii"))
    except OSError:
        pass  # pid inside the lock file is diagnostic only
    return fd


# ------------------------------------------------------- local backends ------

def run_local_backend(backend: str, payload: dict) -> None:
    cfg = load_config()
    entry = backend_config(cfg, backend)
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "backends", f"run_{backend}.py")
    if not os.path.isfile(script):
        fail(f"backend script missing: {script}")
    cmd = [entry["venv_python"], script]
    env = dict(os.environ)
    if backend == "cosyvoice3":
        env.setdefault("COSYVOICE_REPO", entry.get("repo", ""))
    try:
        proc = subprocess.run(
            cmd, input=json.dumps(payload), capture_output=True, text=True,
            encoding="utf-8", errors="replace", env=env, timeout=3600,
        )
    except FileNotFoundError:
        fail(f"venv python not found: {entry['venv_python']}. Re-run install.py {backend}.")
    except subprocess.TimeoutExpired:
        fail("backend timed out after 3600s. Retry with shorter text.")
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        sys.stderr.write(detail + "\n")
        fail(f"{backend} backend failed with exit code {proc.returncode}")
    out_line = [ln for ln in proc.stdout.splitlines() if ln.startswith("OUTPUT=")]
    if not out_line:
        fail(f"{backend} backend produced no OUTPUT= line; raw stdout:\n{proc.stdout}")
    sys.stdout.write(proc.stdout)
    if proc.stderr.strip():
        sys.stderr.write(proc.stderr)


# ------------------------------------------------------ dashscope backend ----

DASHSCOPE_ENDPOINT = (
    "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
)
DASHSCOPE_MODEL = "qwen3-tts-flash"
DASHSCOPE_MAX_CHARS = 600   # per-request hard limit (official docs)
DASHSCOPE_SEGMENT_CHARS = 500
DASHSCOPE_SEGMENT_PAUSE_S = 0.15


def detect_language(text: str) -> str:
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    return "Chinese" if cjk and cjk / max(len(text.strip()), 1) >= 0.15 else "English"


def split_text(text: str, limit: int) -> list:
    """Split into <=limit-char segments on sentence boundaries."""
    if len(text) <= limit:
        return [text]
    parts, buf = [], ""
    for chunk in re.split(r"(?<=[。！？!?；;\n.])", text):
        if not chunk:
            continue
        if len(buf) + len(chunk) <= limit:
            buf += chunk
        else:
            if buf:
                parts.append(buf)
            if len(chunk) <= limit:
                buf = chunk
            else:  # hard-split overlong run (no boundary)
                for i in range(0, len(chunk), limit):
                    parts.append(chunk[i:i + limit])
                buf = ""
    if buf:
        parts.append(buf)
    return [p.strip() for p in parts if p.strip()]


def dashscope_synth(text: str, voice: str, language: str, out: str) -> None:
    api_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        fail("DASHSCOPE_API_KEY is not set. Get one at https://bailian.console.aliyun.com (API-KEY管理).")
    segments = split_text(text, DASHSCOPE_SEGMENT_CHARS)
    if len(segments) > 1:
        print(f"Note: text exceeds {DASHSCOPE_MAX_CHARS} chars; split into {len(segments)} segments.", file=sys.stderr)

    wav_urls = []
    t0 = time.time()
    for idx, seg in enumerate(segments, start=1):
        body = {
            "model": DASHSCOPE_MODEL,
            "input": {"text": seg, "voice": voice, "language_type": language},
        }
        req = urllib.request.Request(
            DASHSCOPE_ENDPOINT,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            fail(f"DashScope HTTP {exc.code}: {detail}")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            fail(f"DashScope request failed: {exc}. Retry once for transient network errors.")
        try:
            url = data["output"]["audio"]["url"]
            if data.get("output", {}).get("task_status", "").upper() == "FAILED":
                raise KeyError(data["output"].get("message", "task failed"))
        except (KeyError, TypeError):
            fail(f"DashScope response has no audio url: {json.dumps(data, ensure_ascii=False)[:500]}")
        wav_urls.append(url)
        if len(segments) > 1:
            print(f"  segment {idx}/{len(segments)} done", file=sys.stderr)
    gen_s = time.time() - t0

    # download segment 0 first to learn the format
    def fetch(url: str) -> bytes:
        try:
            with urllib.request.urlopen(url, timeout=300) as r:
                blob = r.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            fail(f"audio download failed: {exc}. The URL is valid 24h; retry soon.")
        if len(blob) <= 44:
            fail("downloaded audio is empty/trivial; retry")
        return blob

    blobs = [fetch(u) for u in wav_urls]

    import io
    base = wave.open(io.BytesIO(blobs[0]), "rb")
    params = base.getparams()
    frames = [base.readframes(base.getnframes())]
    base.close()
    pause = b"\x00" * int(params.framerate * DASHSCOPE_SEGMENT_PAUSE_S) * params.sampwidth * params.nchannels
    for blob in blobs[1:]:
        w = wave.open(io.BytesIO(blob), "rb")
        p = w.getparams()
        if (p.nchannels, p.sampwidth, p.framerate) != (params.nchannels, params.sampwidth, params.framerate):
            fail("segment format mismatch; refusing to concatenate. Use single-segment text.")
        frames.append(pause)
        frames.append(w.readframes(w.getnframes()))
        w.close()

    tmp = out + ".part"
    try:
        with wave.open(tmp, "wb") as w:
            w.setnchannels(params.nchannels)
            w.setsampwidth(params.sampwidth)
            w.setframerate(params.framerate)
            w.writeframes(b"".join(frames))
        os.replace(tmp, out)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    dur = sum(len(f) for f in frames) / (params.framerate * params.sampwidth * params.nchannels)
    print(f"load=0.0s gen={gen_s:.1f}s dur={dur:.1f}s rtf={gen_s / max(dur, 0.01):.2f}")
    print(f"OUTPUT={out}")


# ----------------------------------------------------------------- doctor ----

def cmd_backends() -> None:
    cfg = load_config()
    print(f"config: {CONFIG_PATH}" if cfg else f"config not found: {CONFIG_PATH}")
    for name in ("qwen3tts", "cosyvoice3", "dashscope"):
        ok, why = check_backend_ready(cfg, name)
        state = "READY" if ok else "unavailable"
        print(f"  {name:12} {state}")
        if not ok:
            print(f"               {why}")
    bank = BANK if os.path.isdir(BANK) else (LEGACY_BANK if os.path.isdir(LEGACY_BANK) else None)
    if bank:
        print(f"voice bank: {bank}")


# ------------------------------------------------------------------ main -----

def main() -> None:
    ap = argparse.ArgumentParser(description="poly-tts unified TTS CLI")
    ap.add_argument("text", nargs="?", default="", help="要合成的文本（或改用 --text-file）")
    ap.add_argument("command", nargs="?", default=None,
                    help="[backends] 子命令；与文本互斥")
    ap.add_argument("--text-file", help="从文件读长文本（UTF-8），覆盖位置参数")
    ap.add_argument("-o", "--output", help="输出 WAV 路径（默认临时目录）")
    ap.add_argument("--backend", default="auto",
                    help="qwen3tts | cosyvoice3 | dashscope | auto (default: auto)")
    ap.add_argument("--voice", help="本地音色库 ID；dashscope 后端下为 API 音色名（如 Cherry）")
    ap.add_argument("-r", "--reference", help="零样本克隆参考音频（2.5-20s，须有授权）")
    ap.add_argument("--reference-text", help="参考音频的逐字稿（用 --reference 时必填）")
    ap.add_argument("--speed", type=float, default=1.0, help="语速 0.5-2.0（仅 cosyvoice3 支持）")
    ap.add_argument("--language", default="auto", help="Chinese | English | auto（qwen3tts/dashscope）")
    args = ap.parse_args()

    if args.command == "backends" or args.text == "backends":
        cmd_backends()
        return
    if args.command:
        fail(f"unknown subcommand '{args.command}' (available: backends)")

    text = read_text_file(args.text_file) if args.text_file else args.text
    text = text.strip()
    if not text:
        fail("empty text (positional or --text-file)")
    if not (MIN_SPEED <= args.speed <= MAX_SPEED):
        fail(f"--speed must be between {MIN_SPEED} and {MAX_SPEED}")
    out = validate_output_path(
        args.output or os.path.join(
            tempfile.gettempdir(), f"poly_tts_{os.urandom(4).hex()}.wav")
    )

    cfg = load_config()

    # --voice semantics depend on backend; for auto, a bank hit steers local
    resolved_backend = None
    ref_path = ref_text = None
    register_speaker_id = None
    bank_voice_requested = bool(args.voice) and not (
        args.reference or args.reference_text
    )

    candidate = args.backend
    if bank_voice_requested and candidate in ("auto", "qwen3tts", "cosyvoice3"):
        bank_dirs = [BANK, LEGACY_BANK]
        hit = any(
            os.path.isfile(os.path.join(d, args.voice, "voice.json")) for d in bank_dirs
        )
        if hit:
            ref_path, ref_text, _ = load_bank_voice(args.voice)
            register_speaker_id = args.voice
            if candidate == "auto":
                # prefer an available local backend
                for name in PLATFORM_PREFERENCE.get(sys.platform, ["dashscope"]):
                    if name == "dashscope":
                        continue
                    ok, _ = check_backend_ready(cfg, name)
                    if ok:
                        candidate = name
                        break
    resolved_backend = pick_backend(cfg, candidate)

    if args.voice and (args.reference or args.reference_text):
        fail("--voice cannot be combined with --reference/--reference-text")
    if args.reference_text and not args.reference:
        fail("--reference-text requires --reference")
    if args.reference:
        if not args.reference_text:
            fail("--reference requires paired --reference-text (verbatim reference transcript)")
        ref_path = os.path.abspath(os.path.expanduser(args.reference))
        ref_text = args.reference_text.strip()
        if not ref_text:
            fail("--reference-text must not be empty")

    if ref_path and resolved_backend == "dashscope":
        fail("voice cloning is not supported by the dashscope backend (preset voices only)")

    if resolved_backend == "dashscope":
        voice = args.voice or "Cherry"
        language = args.language if args.language != "auto" else detect_language(text)
        dashscope_synth(text, voice, language, out)
        return

    # local backends share reference validation
    if ref_path:
        if not os.path.isfile(ref_path):
            fail(f"reference audio not found: {ref_path}")
        if not os.access(ref_path, os.R_OK):
            fail(f"reference audio is not readable: {ref_path}")
        dur = probe_duration(ref_path)
        if not (MIN_REF_SECONDS <= dur <= MAX_REF_SECONDS):
            fail(
                f"reference audio is {dur:.1f}s; hard range is "
                f"{MIN_REF_SECONDS:g}-{MAX_REF_SECONDS:g}s, optimal is 3-10s."
            )
        if not (3.0 <= dur <= 10.0):
            print(f"Warning: reference audio is {dur:.1f}s; optimal range is 3-10s.", file=sys.stderr)

    if args.speed != 1.0 and resolved_backend == "qwen3tts":
        print("Warning: --speed is not supported by the qwen3tts backend yet; ignored.", file=sys.stderr)

    entry = backend_config(cfg, resolved_backend)
    payload = {
        "text": text,
        "output": out,
        "ref_path": ref_path or "",
        "ref_text": ref_text or "",
        "register_speaker_id": register_speaker_id or "",
        "speed": args.speed,
        "language": args.language if args.language != "auto" else detect_language(text),
        "model_dir": entry.get("model_dir", ""),
        "repo": entry.get("repo", ""),
        "device": entry.get("device", ""),
    }

    lock_fd = acquire_lock() if resolved_backend != "dashscope" else None
    try:
        run_local_backend(resolved_backend, payload)
    finally:
        if lock_fd is not None:
            os.close(lock_fd)


if __name__ == "__main__":
    main()
