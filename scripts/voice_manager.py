#!/usr/bin/env python3
"""
CosyVoice3 custom voice bank manager.

Bank location: $COSYVOICE_REPO/voices/ (untracked dir inside the official repo;
survives git pull and skill reinstalls). Each voice:
  voices/<id>/ref.wav     # canonical 24kHz mono WAV
  voices/<id>/voice.json  # transcript + metadata

Commands:
  add <id> --wav <path> --text "<verbatim transcript>" [--notes "..."]
  list
  remove <id>
  test <id> "synthesis text"

Consent rule: only register voices you are authorized to use (your own
recordings, or explicit permission from the speaker). Never clone others'
voices for impersonation, and never cross-clone commercial preset voices.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

REPO = os.environ.get(
    "COSYVOICE_REPO",
    os.path.expanduser("~/.cosyvoice3-repo"),
)
BANK = os.path.join(REPO, "voices")
MODEL_SR = 24000
VALID_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def fail(message: str) -> None:
    sys.exit(f"Error: {message}")


def probe_duration(path: str) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        return float(out)
    except FileNotFoundError:
        fail("ffprobe not found. Install ffmpeg first: brew install ffmpeg")
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        fail(f"ffprobe could not read {path}: {detail or exc}")
    except ValueError:
        fail(f"ffprobe returned an invalid duration for {path}")


def validate_voice_dir(path: str, expected_id: str) -> None:
    ref = os.path.join(path, "ref.wav")
    meta_path = os.path.join(path, "voice.json")
    if not os.path.isfile(ref):
        fail(f"staged voice is missing ref.wav: {path}")
    if not os.path.isfile(meta_path):
        fail(f"staged voice is missing voice.json: {path}")
    try:
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        if meta["id"] != expected_id:
            fail("staged voice metadata id does not match the requested voice")
        if not meta["transcript"].strip():
            fail("staged voice metadata has an empty transcript")
    except json.JSONDecodeError as exc:
        fail(f"staged voice metadata is invalid JSON: {exc}")
    except KeyError as exc:
        fail(f"staged voice metadata is missing key: {exc}")
    duration = probe_duration(ref)
    if duration < 2.5 or duration > 20:
        fail(f"staged reference duration is invalid ({duration:.1f}s)")


def cmd_add(args):
    if not VALID_ID.match(args.id):
        fail(f"invalid id '{args.id}' (use ^[a-z0-9][a-z0-9_-]{{0,31}}$)")
    dest = os.path.join(BANK, args.id)
    if os.path.exists(dest):
        fail(f"voice '{args.id}' already exists ({dest}); remove it first or pick another id")
    if not os.path.isfile(args.wav):
        fail(f"wav not found: {args.wav}")
    text = args.text.strip()
    if not text:
        fail("--text (verbatim transcript) is required")

    dur = probe_duration(args.wav)
    if dur < 2.5:
        fail(f"too short ({dur:.1f}s). Reference hard range is 2.5-20s; optimal is 3-10s.")
    if dur > 20:
        fail(f"too long ({dur:.1f}s). Reference hard range is 2.5-20s; optimal is 3-10s.")
    if dur < 3 or dur > 10:
        print(
            f"Warning: {dur:.1f}s is accepted, but the optimal reference range is 3-10s.",
            file=sys.stderr,
        )

    try:
        os.makedirs(BANK, exist_ok=True)
        stage = tempfile.mkdtemp(prefix=f".{args.id}.", suffix=".tmp", dir=BANK)
    except OSError as exc:
        fail(f"cannot create a staging directory in voice bank {BANK}: {exc}")
    try:
        ref = os.path.join(stage, "ref.wav")
        # canonical form: model-native sample rate, mono — avoids any resample ambiguity later
        try:
            subprocess.run(
                ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", args.wav, "-ac", "1",
                 "-ar", str(MODEL_SR), ref],
                capture_output=True, text=True, check=True,
            )
        except FileNotFoundError:
            fail("ffmpeg not found. Install ffmpeg first: brew install ffmpeg")
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "").strip()
            fail(f"ffmpeg failed while converting reference audio: {detail or exc}")

        meta = {
            "id": args.id,
            "transcript": text,
            "source": os.path.abspath(args.wav),
            "duration_s": round(dur, 2),
            "sample_rate": MODEL_SR,
            "notes": args.notes or "",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        with open(os.path.join(stage, "voice.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())

        validate_voice_dir(stage, args.id)
        try:
            if os.path.exists(dest):
                fail(f"voice '{args.id}' was created by another process; pick another id")
            os.rename(stage, dest)
        except OSError as exc:
            if os.path.exists(dest):
                fail(f"voice '{args.id}' was created by another process; pick another id")
            fail(f"could not publish staged voice '{args.id}': {exc}")
    finally:
        if os.path.isdir(stage):
            shutil.rmtree(stage, ignore_errors=True)

    print(f"✓ voice '{args.id}' added ({dur:.1f}s source -> canonical {MODEL_SR}Hz mono)")
    print(f"  bank: {dest}")
    print(f"  use:  tts.py --voice {args.id} \"任意文本\" -o out.wav")


def cmd_list(_args):
    if not os.path.isdir(BANK):
        print(f"(no voice bank yet — {BANK})")
        return
    items = sorted(os.listdir(BANK))
    if not items:
        print(f"(voice bank empty — {BANK})")
        return
    print(f"voice bank: {BANK}")
    for i in items:
        if not VALID_ID.match(i) or not os.path.isdir(os.path.join(BANK, i)):
            continue
        vj = os.path.join(BANK, i, "voice.json")
        ref = os.path.join(BANK, i, "ref.wav")
        if not os.path.isfile(vj) or not os.path.isfile(ref):
            print(f"  {i:16} (incomplete; remove and re-add this voice)")
            continue
        try:
            with open(vj, encoding="utf-8") as f:
                m = json.load(f)
            note = f" — {m['notes']}" if m.get("notes") else ""
            print(f"  {i:16} {m['duration_s']:>5.1f}s  “{m['transcript'][:30]}”{note}")
        except json.JSONDecodeError as exc:
            print(f"  {i:16} (metadata invalid JSON: {exc}; remove and re-add)")
        except KeyError as exc:
            print(f"  {i:16} (metadata missing key: {exc}; remove and re-add)")
        except FileNotFoundError:
            print(f"  {i:16} (metadata disappeared; run list again)")
        except (TypeError, ValueError, OSError) as exc:
            print(f"  {i:16} (metadata unreadable: {exc}; remove and re-add)")


def _validate_id(id_str: str):
    """P0 fix: validate id before any path join/rmtree — blocks '..'/absolute traversal."""
    if not VALID_ID.match(id_str):
        fail(f"invalid voice id '{id_str}' (allowed: ^[a-z0-9][a-z0-9_-]{{0,31}}$)")


def cmd_remove(args):
    _validate_id(args.id)
    dest = os.path.join(BANK, args.id)
    if os.path.realpath(dest) == os.path.realpath(BANK) or not os.path.isdir(dest):
        fail(f"no such voice: {args.id}")
    shutil.rmtree(dest)
    print(f"✓ removed voice '{args.id}'")


def cmd_test(args):
    _validate_id(args.id)
    dest = os.path.join(BANK, args.id)
    vj = os.path.join(dest, "voice.json")
    if not os.path.isfile(vj):
        fail(f"no such voice: {args.id} (see: voice_manager.py list)")
    tts_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tts.py")
    out = os.path.join(tempfile.gettempdir(), f"voice_test_{args.id}.wav")
    try:
        subprocess.run(
            [os.path.join(REPO, ".venv/bin/python"), tts_py,
             args.text, "--voice", args.id, "-o", out],
            check=True,
        )
    except FileNotFoundError:
        fail(f"venv python not found under {REPO}; run scripts/install.sh")
    except subprocess.CalledProcessError as exc:
        fail(f"voice test synthesis failed with exit code {exc.returncode}; repair with voice_manager.py remove/add")
    print(f"listen: {out}")


def main():
    ap = argparse.ArgumentParser(description="CosyVoice3 custom voice bank manager")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add", help="register a new voice from authorized reference audio")
    p.add_argument("id")
    p.add_argument(
        "--wav",
        required=True,
        help="reference audio (2.5-20s hard range, 3-10s optimal, clean single speaker)",
    )
    p.add_argument("--text", required=True, help="verbatim transcript of the reference audio")
    p.add_argument("--notes", help="optional note, e.g. 'my own recording'")
    p.set_defaults(fn=cmd_add)

    p = sub.add_parser("list", help="list registered voices")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("remove", help="delete a voice")
    p.add_argument("id")
    p.set_defaults(fn=cmd_remove)

    p = sub.add_parser("test", help="quick synthesis test with a voice")
    p.add_argument("id")
    p.add_argument("text")
    p.set_defaults(fn=cmd_test)

    args = ap.parse_args()
    try:
        args.fn(args)
    except json.JSONDecodeError as exc:
        fail(f"voice metadata is invalid JSON: {exc}; remove and re-add the affected voice")
    except KeyError as exc:
        fail(f"voice metadata is missing key {exc}; remove and re-add the affected voice")
    except FileNotFoundError as exc:
        fail(f"required file or tool was not found: {exc.filename or exc}")
    except subprocess.CalledProcessError as exc:
        fail(f"external audio command failed with exit code {exc.returncode}")
    except OSError as exc:
        fail(f"voice-bank operation failed: {exc}")


if __name__ == "__main__":
    main()
