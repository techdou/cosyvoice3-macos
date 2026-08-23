#!/usr/bin/env python3
"""
Download CosyVoice pretrained models.

The tts.py wrapper hard-codes the main Fun-CosyVoice3-0.5B model. Extra variants
are manual/experimental downloads; this wrapper will not select them.

Run with the repo venv python:
  $REPO/.venv/bin/python <skill-dir>/scripts/download_models.py --list
  $REPO/.venv/bin/python <skill-dir>/scripts/download_models.py cosyvoice-300m-instruct
"""
import argparse
import os
import sys

REPO = os.environ.get(
    "COSYVOICE_REPO",
    os.path.expanduser("~/.cosyvoice3-repo"),
)

MODELS = {
    "cosyvoice3": {
        "model_id": "FunAudioLLM/Fun-CosyVoice3-0.5B-2512",
        "dir": "Fun-CosyVoice3-0.5B",
        "desc": "Main model used by tts.py, installed by default (~9.1GB)",
        "required_files": (
            "cosyvoice3.yaml",
            "llm.pt",
            "flow.pt",
            "hift.pt",
            "campplus.onnx",
            "speech_tokenizer_v3.onnx",
            os.path.join("CosyVoice-BlankEN", "model.safetensors"),
        ),
    },
    "cosyvoice2": {
        "model_id": "iic/CosyVoice2-0.5B",
        "dir": "CosyVoice2-0.5B",
        "desc": "Manual/experimental: previous generation, not used by tts.py",
    },
    "cosyvoice-300m-sft": {
        "model_id": "iic/CosyVoice-300M-SFT",
        "dir": "CosyVoice-300M-SFT",
        "desc": "Manual/experimental: speaker-finetuned lightweight, not used by tts.py",
    },
    "cosyvoice-300m-instruct": {
        "model_id": "iic/CosyVoice-300M-Instruct",
        "dir": "CosyVoice-300M-Instruct",
        "desc": "Manual/experimental: instruct model, not used by tts.py",
    },
}


def nonempty_file(path: str) -> bool:
    try:
        return os.path.isfile(path) and os.path.getsize(path) > 0
    except OSError:
        return False


def missing_required_files(target: str, model: dict):
    return [
        rel for rel in model.get("required_files", ())
        if not nonempty_file(os.path.join(target, rel))
    ]


def main():
    ap = argparse.ArgumentParser(description="CosyVoice model downloader")
    ap.add_argument("models", nargs="*", help="keys from --list (default: none)")
    ap.add_argument("--list", action="store_true", help="list available models")
    args = ap.parse_args()

    if args.list or not args.models:
        print("Available models (download target: $REPO/pretrained_models/):")
        for k, v in MODELS.items():
            print(f"  {k:24} {v['desc']}")
        if not args.list:
            print("\nPass model keys as arguments to download.")
        return

    try:
        from modelscope import snapshot_download
    except ImportError:
        sys.exit("modelscope not installed — run inside $REPO/.venv (see SKILL.md)")

    for key in args.models:
        if key not in MODELS:
            sys.exit(f"Unknown model '{key}'. Use --list.")
        model = MODELS[key]
        target = os.path.join(REPO, "pretrained_models", model["dir"])
        required_files = model.get("required_files")
        if required_files:
            missing = missing_required_files(target, model)
            if os.path.isdir(target) and not missing:
                print(f"✓ {key}: required-file manifest complete at {target}")
                continue
            if os.path.isdir(target):
                print(f"⚠️ {key}: incomplete model at {target}; missing or empty:")
                for rel in missing:
                    print(f"  - {rel}")
                print("Resuming download.")
        elif os.path.isdir(target) and os.listdir(target):
            print(
                f"⚠️ {key}: manual/experimental model has no wrapper manifest; "
                "asking ModelScope to verify/resume instead of skipping it"
            )
        print(f"📥 {key} -> {target}")
        snapshot_download(model["model_id"], local_dir=target)
        if required_files:
            missing = missing_required_files(target, model)
            if missing:
                sys.exit(
                    f"{key}: download incomplete; missing or empty "
                    + ", ".join(missing)
                    + ". Re-run this command to resume."
                )
        print(f"✓ {key}: done")


if __name__ == "__main__":
    main()
