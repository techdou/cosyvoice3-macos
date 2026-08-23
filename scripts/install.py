#!/usr/bin/env python3
"""
poly-tts cross-platform installer (stdlib-only; run with any Python 3.9+).

Backends:
  qwen3tts    Windows/Linux + NVIDIA GPU (CUDA). venv + torch (cu128) + qwen-tts.
              Refuses on macOS (MPS unsupported upstream, issue #345).
  cosyvoice3  macOS Apple Silicon (delegates to install.sh, the field-verified
              path) / Linux CUDA or CPU. Not supported on native Windows
              (upstream pins torch 2.3.1, incompatible with Blackwell GPUs).
  dashscope   Cloud API — nothing to install; export DASHSCOPE_API_KEY.

Usage:
  python install.py qwen3tts --model-dir "E:/models/Qwen3-TTS"
  python install.py qwen3tts --download hf     # download model from HuggingFace
  python install.py cosyvoice3                  # macOS/Linux → runs install.sh
  python install.py doctor                      # environment health check

Layout written to ~/.poly-tts/config.json (merge-in-place, hand-editable):
  { "backends": { "qwen3tts": { "venv_python": ..., "model_dir": ..., "device": "auto" },
                    "cosyvoice3": { "venv_python": ..., "repo": ..., "model_dir": ... } } }
API keys are never written here — dashscope reads DASHSCOPE_API_KEY from env.
"""
import argparse
import json
import os
import platform
import shutil
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

POLY_HOME = os.environ.get("POLY_TTS_HOME", os.path.expanduser("~/.poly-tts"))
CONFIG_PATH = os.path.join(POLY_HOME, "config.json")
DEFAULT_VENV = os.path.join(POLY_HOME, "venvs")
QWEN_MODEL_SUBDIR = "Qwen3-TTS"
HF_REPO_ID = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
MS_REPO_ID = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
QWEN_REQUIRED_FILES = (
    "model.safetensors",
    "config.json",
    "preprocessor_config.json",
    os.path.join("speech_tokenizer", "model.safetensors"),
)


def die(msg: str) -> None:
    print(f"❌ {msg}", file=sys.stderr)
    sys.exit(1)


def info(msg: str) -> None:
    print(msg)


def have_cuda() -> bool:
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        return False
    try:
        return subprocess.run(
            [nvidia_smi], capture_output=True, timeout=20
        ).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def run(cmd, **kw) -> subprocess.CompletedProcess:
    info(f"  $ {' '.join(str(c) for c in cmd)}")
    return subprocess.run(cmd, **kw)


def find_uv() -> str:
    uv = shutil.which("uv")
    if not uv:
        die("uv not found. Install it first: "
            "Windows: winget install astral-sh.uv | macOS: brew install uv | "
            "Linux: curl -LsSf https://astral.sh/uv/install.sh | sh")
    return uv


def venv_python(venv_dir: str) -> str:
    inner = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    return os.path.join(venv_dir, inner)


def pip_install(uv: str, py: str, *spec: str, index_url: str = None) -> None:
    cmd = [uv, "pip", "install", "--python", py]
    if index_url:
        cmd += ["--index-url", index_url]
    cmd += list(spec)
    proc = run(cmd)
    if proc.returncode != 0:
        die(f"pip install failed: {' '.join(spec)}")


def update_config(backend: str, **fields) -> None:
    os.makedirs(POLY_HOME, exist_ok=True)
    cfg = {}
    if os.path.isfile(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                cfg = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            die(f"cannot parse existing config {CONFIG_PATH}: {exc}")
    cfg.setdefault("backends", {})
    cfg["backends"].setdefault(backend, {})
    cfg["backends"][backend].update(fields)
    tmp = CONFIG_PATH + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, CONFIG_PATH)
    info(f"✓ config updated: {CONFIG_PATH} → backends.{backend}")


def model_complete(model_dir: str) -> bool:
    return all(
        os.path.isfile(os.path.join(model_dir, rel)) and
        os.path.getsize(os.path.join(model_dir, rel)) > 0
        for rel in QWEN_REQUIRED_FILES
    )


# ------------------------------------------------------------- qwen3tts ------

def install_qwen3tts(args) -> None:
    if platform.system() == "Darwin":
        die("qwen3tts backend: macOS is not supported upstream (no MPS; "
            "github.com/QwenLM/Qwen3-TTS issue #345). Use the cosyvoice3 backend on macOS.")
    if platform.system() == "Windows":
        info("Platform: Windows (native). CosyVoice is not offered here; qwen3tts is the local backend.")

    cuda = have_cuda()
    if cuda:
        info("GPU detected (nvidia-smi OK) → installing CUDA wheels (cu128; "
             "required for RTX 50-series Blackwell, works on older cards too).")
    else:
        info("⚠️ No NVIDIA GPU detected → installing CPU torch. The 0.6B model "
             "will run but slowly; this path is experimental (upstream assumes CUDA).")

    venv_dir = os.path.abspath(os.path.expanduser(args.venv_dir or os.path.join(DEFAULT_VENV, "qwen3tts")))
    uv = find_uv()

    if not os.path.isfile(venv_python(venv_dir)):
        info(f"🐍 Creating Python 3.12 venv at {venv_dir} ...")
        proc = run([uv, "venv", "--python", "3.12", venv_dir])
        if proc.returncode != 0:
            die("uv venv creation failed")
    py = venv_python(venv_dir)

    info("🔥 Installing torch + torchaudio ...")
    if cuda:
        pip_install(uv, py, "torch", "torchaudio",
                    index_url="https://download.pytorch.org/whl/cu128")
    else:
        pip_install(uv, py, "torch", "torchaudio",
                    index_url="https://download.pytorch.org/whl/cpu")

    info("📦 Installing qwen-tts (pins transformers==4.57.3) ...")
    pip_install(uv, py, "qwen-tts")

    # sox: pysox needs the SoX binary; harmless warning if absent
    if shutil.which("sox"):
        info("✓ sox binary found")
    else:
        info("⚠️ sox binary not found on PATH. The 12Hz tokenizer path normally works "
             "without it; if synthesis errors mentioning sox appear, install SoX "
             "(Windows: winget install SoX.Sox / Linux: apt install sox).")

    # ---- model ----
    default_models_root = args.models_dir or os.path.join(POLY_HOME, "models")
    model_dir = os.path.abspath(os.path.expanduser(
        args.model_dir or os.path.join(default_models_root, QWEN_MODEL_SUBDIR)
    ))
    if model_complete(model_dir):
        info(f"✓ model already complete: {model_dir}")
    elif args.download:
        os.makedirs(model_dir, exist_ok=True)
        if args.download == "hf":
            info(f"📥 Downloading {HF_REPO_ID} from HuggingFace (~2.4GB) ...")
            pip_install(uv, py, "huggingface_hub")
            code = (
                "import sys; from huggingface_hub import snapshot_download; "
                f"snapshot_download('{HF_REPO_ID}', local_dir=sys.argv[1])"
            )
            proc = run([py, "-c", code, model_dir])
            if proc.returncode != 0:
                die("HuggingFace download failed; re-run to resume, or try --download ms")
        else:
            info(f"📥 Downloading {MS_REPO_ID} from ModelScope (~2.4GB) ...")
            pip_install(uv, py, "modelscope")
            code = (
                "import sys; from modelscope import snapshot_download; "
                f"snapshot_download('{MS_REPO_ID}', local_dir=sys.argv[1])"
            )
            proc = run([py, "-c", code, model_dir])
            if proc.returncode != 0:
                die("ModelScope download failed; re-run to resume")
        if not model_complete(model_dir):
            die("model still incomplete after download; re-run install.py qwen3tts --download ...")
    else:
        die(
            f"model not found at {model_dir}. Either pass --model-dir pointing to an "
            "existing Qwen3-TTS-12Hz-0.6B-Base directory, or use --download hf|ms "
            "to fetch it there automatically."
        )

    # ---- smoke import ----
    info("🔎 Verifying imports ...")
    proc = run([py, "-c",
                "import torch, qwen_tts; print('torch', torch.__version__, "
                "'cuda', torch.cuda.is_available())"])
    if proc.returncode != 0:
        die("import verification failed; see output above")

    update_config(
        "qwen3tts",
        venv_python=py,
        model_dir=model_dir,
        device="auto",
    )
    info("")
    info("=== qwen3tts backend ready ===")
    info(f"Smoke test:\n  python <skill-dir>/scripts/tts.py \"你好，测试。\" --backend qwen3tts -o test.wav")


# ----------------------------------------------------------- cosyvoice3 ------

def install_cosyvoice3(_args) -> None:
    if platform.system() == "Windows":
        die(
            "cosyvoice3 backend is not supported on native Windows: upstream pins "
            "torch==2.3.1/cu121 (no Blackwell sm_120 support) and has no official "
            "Windows path. On this machine use the qwen3tts backend, or WSL2 (untested)."
        )
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "install.sh")
    if not os.path.isfile(script):
        die(f"install.sh not found next to install.py: {script}")
    proc = run(["bash", script])
    if proc.returncode != 0:
        die("install.sh failed; see output above")

    repo = os.environ.get("COSYVOICE_REPO", os.path.expanduser("~/.cosyvoice3-repo"))
    update_config(
        "cosyvoice3",
        venv_python=os.path.join(repo, ".venv", "bin", "python"),
        repo=repo,
        model_dir=os.path.join(repo, "pretrained_models", "Fun-CosyVoice3-0.5B"),
    )
    info("=== cosyvoice3 backend ready ===")


# ---------------------------------------------------------------- doctor -----

def doctor(_args) -> None:
    info(f"poly-tts home : {POLY_HOME}")
    info(f"config        : {CONFIG_PATH} "
         + ("(exists)" if os.path.isfile(CONFIG_PATH) else "(missing)"))
    cfg = {}
    if os.path.isfile(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                cfg = json.load(f)
        except (json.JSONDecodeError, OSError):
            info("  ⚠️ config is unreadable JSON")
    for name in ("qwen3tts", "cosyvoice3", "dashscope"):
        entry = cfg.get("backends", {}).get(name, {})
        if name == "dashscope":
            has_key = bool(os.environ.get("DASHSCOPE_API_KEY"))
            info(f"dashscope     : {'READY (DASHSCOPE_API_KEY set)' if has_key else 'no DASHSCOPE_API_KEY in env'}")
            continue
        if not entry:
            info(f"{name:13}: not configured")
            continue
        py = entry.get("venv_python", "")
        md = entry.get("model_dir", "")
        info(f"{name:13}: venv {'✓' if os.path.isfile(py) else 'MISSING ' + py} | "
             f"model {'✓' if os.path.isdir(md) else 'MISSING ' + md}")
    info(f"GPU           : {'yes (nvidia-smi)' if have_cuda() else 'no/undetected'}")
    for tool in ("ffmpeg", "ffprobe", "sox", "uv"):
        info(f"{tool:14}: {'✓' if shutil.which(tool) else 'not found'}")


def main() -> None:
    ap = argparse.ArgumentParser(description="poly-tts installer")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("qwen3tts", help="local Qwen3-TTS backend (Win/Linux + CUDA)")
    p.add_argument("--venv-dir", help="venv location (default ~/.poly-tts/venvs/qwen3tts)")
    p.add_argument("--models-dir", help="default root for model dirs")
    p.add_argument("--model-dir", help="existing Qwen3-TTS model directory")
    p.add_argument("--download", choices=["hf", "ms"],
                   help="download the model if missing (HuggingFace / ModelScope)")
    p.set_defaults(fn=install_qwen3tts)

    p = sub.add_parser("cosyvoice3", help="local CosyVoice3 backend (macOS/Linux)")
    p.set_defaults(fn=install_cosyvoice3)

    p = sub.add_parser("doctor", help="environment health check")
    p.set_defaults(fn=doctor)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
