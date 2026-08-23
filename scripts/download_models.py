#!/usr/bin/env python3
"""
Download pretrained models for poly-tts backends.

CosyVoice models  → ModelScope, into $COSYVOICE_REPO/pretrained_models/
Qwen3-TTS models  → HuggingFace or ModelScope, into ~/.poly-tts/models/ by default

Run inside the matching venv (the installer sets one up), or with any python
that has modelscope / huggingface_hub installed:
  python download_models.py --list
  python download_models.py qwen3-tts --source hf --dest E:/models/Qwen3-TTS
  python download_models.py cosyvoice3
"""
import argparse
import os
import sys

POLY_HOME = os.environ.get("POLY_TTS_HOME", os.path.expanduser("~/.poly-tts"))
CV_REPO = os.environ.get("COSYVOICE_REPO", os.path.expanduser("~/.cosyvoice3-repo"))

MODELS = {
    # ---- Qwen3-TTS (local cloning backend) ----
    "qwen3-tts": {
        "backend": "qwen3tts",
        "repo_id": "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
        "sources": ("hf", "ms"),
        "default_dir": os.path.join(POLY_HOME, "models", "Qwen3-TTS"),
        "desc": "Qwen3-TTS-12Hz-0.6B-Base (~2.4GB): voice cloning, used by the qwen3tts backend",
        "required_files": (
            "model.safetensors",
            "config.json",
            "preprocessor_config.json",
            os.path.join("speech_tokenizer", "model.safetensors"),
        ),
    },
    # ---- CosyVoice (macOS/Linux backend) ----
    "cosyvoice3": {
        "backend": "cosyvoice3",
        "repo_id": "FunAudioLLM/Fun-CosyVoice3-0.5B-2512",
        "sources": ("ms",),
        "default_dir": os.path.join(CV_REPO, "pretrained_models", "Fun-CosyVoice3-0.5B"),
        "desc": "Fun-CosyVoice3-0.5B (~9.1GB): main model of the cosyvoice3 backend",
        "required_files": (
            "cosyvoice3.yaml", "llm.pt", "flow.pt", "hift.pt",
            "campplus.onnx", "speech_tokenizer_v3.onnx",
            os.path.join("CosyVoice-BlankEN", "model.safetensors"),
        ),
    },
    "cosyvoice2": {
        "backend": "cosyvoice3",
        "repo_id": "iic/CosyVoice2-0.5B",
        "sources": ("ms",),
        "default_dir": os.path.join(CV_REPO, "pretrained_models", "CosyVoice2-0.5B"),
        "desc": "Manual/experimental: previous generation, not used by tts.py",
    },
    "cosyvoice-300m-sft": {
        "backend": "cosyvoice3",
        "repo_id": "iic/CosyVoice-300M-SFT",
        "sources": ("ms",),
        "default_dir": os.path.join(CV_REPO, "pretrained_models", "CosyVoice-300M-SFT"),
        "desc": "Manual/experimental: speaker-finetuned lightweight, not used by tts.py",
    },
    "cosyvoice-300m-instruct": {
        "backend": "cosyvoice3",
        "repo_id": "iic/CosyVoice-300M-Instruct",
        "sources": ("ms",),
        "default_dir": os.path.join(CV_REPO, "pretrained_models", "CosyVoice-300M-Instruct"),
        "desc": "Manual/experimental: instruct model, not used by tts.py",
    },
}


def nonempty_file(path: str) -> bool:
    try:
        return os.path.isfile(path) and os.path.getsize(path) > 0
    except OSError:
        return False


def missing_required(target: str, model: dict):
    return [
        rel for rel in model.get("required_files", ())
        if not nonempty_file(os.path.join(target, rel))
    ]


def download(model: dict, target: str, source: str) -> None:
    os.makedirs(target, exist_ok=True)
    if source == "hf":
        from huggingface_hub import snapshot_download
        snapshot_download(model["repo_id"], local_dir=target)
    else:
        from modelscope import snapshot_download
        snapshot_download(model["repo_id"], local_dir=target)


def main():
    ap = argparse.ArgumentParser(description="poly-tts model downloader")
    ap.add_argument("models", nargs="*", help="keys from --list (default: none)")
    ap.add_argument("--list", action="store_true", help="list available models")
    ap.add_argument("--source", choices=["hf", "ms"], default=None,
                    help="override the download source (default: per-model first source)")
    ap.add_argument("--dest", default=None,
                    help="override the target directory (applies to the single model given)")
    args = ap.parse_args()

    if args.list or not args.models:
        print("Available models:")
        for k, v in MODELS.items():
            print(f"  {k:24} [{','.join(v['sources'])}]  {v['desc']}")
            print(f"  {'':24} default dir: {v['default_dir']}")
        if not args.list:
            print("\nPass model keys as arguments to download.")
        return

    for key in args.models:
        if key not in MODELS:
            sys.exit(f"Unknown model '{key}'. Use --list.")
        model = MODELS[key]
        target = args.dest if (args.dest and len(args.models) == 1) else model["default_dir"]
        target = os.path.abspath(os.path.expanduser(target))
        source = args.source or model["sources"][0]
        if source not in model["sources"]:
            sys.exit(f"Model '{key}' does not support source '{source}'.")
        required = model.get("required_files")
        if required:
            missing = missing_required(target, model)
            if os.path.isdir(target) and not missing:
                print(f"✓ {key}: required-file manifest complete at {target}")
                continue
            if os.path.isdir(target):
                print(f"⚠️ {key}: incomplete at {target}; missing or empty: {', '.join(missing)}")
                print("Resuming download.")
        elif os.path.isdir(target) and os.listdir(target):
            print(f"⚠️ {key}: no manifest for this variant; downloading to verify/resume.")
        print(f"📥 {key} ({source}) -> {target}")
        try:
            download(model, target, source)
        except ImportError as exc:
            sys.exit(f"missing downloader package: {exc}. Install huggingface_hub / modelscope first.")
        if required:
            missing = missing_required(target, model)
            if missing:
                sys.exit(
                    f"{key}: download incomplete; missing or empty "
                    + ", ".join(missing)
                    + ". Re-run this command to resume."
                )
        print(f"✓ {key}: done")


if __name__ == "__main__":
    main()
