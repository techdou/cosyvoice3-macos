#!/usr/bin/env python3
"""
poly-tts voice bank manager — backend-agnostic cloned-voice registry.

Bank location: ~/.poly-tts/voices/ (first choice). If a legacy bank exists at
$COSYVOICE_REPO/voices/ (macOS v1 layout) it is read too; `migrate` copies it
to the new location. Each voice:
  voices/<id>/ref.wav     # canonical 24kHz mono WAV
  voices/<id>/voice.json  # transcript + metadata

The same voice works with every local cloning backend (qwen3tts / cosyvoice3).

Commands:
  add <id> --wav <path> --text "<verbatim transcript>" [--notes "..."]
  list
  remove <id>
  test <id> "synthesis text" [--backend qwen3tts|cosyvoice3|auto]
  migrate            # copy legacy ~/.cosyvoice3-repo/voices into ~/.poly-tts/voices

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

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

POLY_HOME = os.environ.get("POLY_TTS_HOME", os.path.expanduser("~/.poly-tts"))
BANK = os.path.join(POLY_HOME, "voices")
LEGACY_BANK = os.path.join(
    os.environ.get("COSYVOICE_REPO", os.path.expanduser("~/.cosyvoice3-repo")), "voices"
)
MODEL_SR = 24000
VALID_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def fail(message: str) -> None:
    sys.exit(f"Error: {message}")


def bank_dirs():
    dirs = []
    for d in (BANK, LEGACY_BANK):
        if os.path.isdir(d):
            dirs.append(d)
    return dirs


def probe_duration(path: str) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        return float(out)
    except FileNotFoundError:
        fail("ffprobe not found. Install ffmpeg first (winget install Gyan.FFmpeg / brew install ffmpeg)")
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


def _validate_id(id_str: str):
    if not VALID_ID.match(id_str):
        fail(f"invalid voice id '{id_str}' (allowed: ^[a-z0-9][a-z0-9_-]{{0,31}}$)")


def cmd_add(args):
    _validate_id(args.id)
    for d in bank_dirs():
        if os.path.exists(os.path.join(d, args.id)):
            fail(f"voice '{args.id}' already exists in {d}; remove it first or pick another id")
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
        print(f"Warning: {dur:.1f}s is accepted, but the optimal reference range is 3-10s.", file=sys.stderr)

    try:
        os.makedirs(BANK, exist_ok=True)
        stage = tempfile.mkdtemp(prefix=f".{args.id}.", suffix=".tmp", dir=BANK)
    except OSError as exc:
        fail(f"cannot create a staging directory in voice bank {BANK}: {exc}")
    try:
        ref = os.path.join(stage, "ref.wav")
        try:
            subprocess.run(
                ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", args.wav, "-ac", "1",
                 "-ar", str(MODEL_SR), ref],
                capture_output=True, text=True, check=True,
            )
        except FileNotFoundError:
            fail("ffmpeg not found. Install ffmpeg first.")
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
        dest = os.path.join(BANK, args.id)
        try:
            os.rename(stage, dest)
        except OSError as exc:
            if os.path.exists(dest):
                fail(f"voice '{args.id}' was created by another process; pick another id")
            fail(f"could not publish staged voice '{args.id}': {exc}")
    finally:
        if os.path.isdir(stage):
            shutil.rmtree(stage, ignore_errors=True)

    print(f"✓ voice '{args.id}' added ({dur:.1f}s source -> canonical {MODEL_SR}Hz mono)")
    print(f"  bank: {os.path.join(BANK, args.id)}")
    print(f"  use:  tts.py --voice {args.id} \"任意文本\" -o out.wav")


def cmd_list(_args):
    dirs = bank_dirs()
    if not dirs:
        print(f"(no voice bank yet — create one by adding a voice at {BANK})")
        return
    for d in dirs:
        tag = " (legacy)" if d == LEGACY_BANK and d != BANK else ""
        items = sorted(os.listdir(d)) if os.path.isdir(d) else []
        print(f"voice bank{tag}: {d}")
        found = False
        for i in items:
            if not VALID_ID.match(i) or not os.path.isdir(os.path.join(d, i)):
                continue
            found = True
            vj = os.path.join(d, i, "voice.json")
            ref = os.path.join(d, i, "ref.wav")
            if not (os.path.isfile(vj) and os.path.isfile(ref)):
                print(f"  {i:16} (incomplete; remove and re-add this voice)")
                continue
            try:
                with open(vj, encoding="utf-8") as f:
                    m = json.load(f)
                note = f" — {m['notes']}" if m.get("notes") else ""
                print(f"  {i:16} {m['duration_s']:>5.1f}s  “{m['transcript'][:30]}”{note}")
            except (json.JSONDecodeError, KeyError, TypeError, ValueError, OSError) as exc:
                print(f"  {i:16} (metadata unreadable: {exc}; remove and re-add)")
        if not found:
            print("  (empty)")
    if os.path.isdir(LEGACY_BANK) and LEGACY_BANK != BANK:
        print("\nTip: run `voice_manager.py migrate` to move legacy voices into the "
              "backend-agnostic bank.")


def cmd_remove(args):
    _validate_id(args.id)
    removed = False
    for d in bank_dirs():
        dest = os.path.join(d, args.id)
        if os.path.realpath(dest) != os.path.realpath(d) and os.path.isdir(dest):
            shutil.rmtree(dest)
            print(f"✓ removed voice '{args.id}' from {d}")
            removed = True
    if not removed:
        fail(f"no such voice: {args.id}")


def cmd_test(args):
    _validate_id(args.id)
    tts_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tts.py")
    out = os.path.join(tempfile.gettempdir(), f"voice_test_{args.id}.wav")
    cmd = [sys.executable, tts_py, args.text, "--voice", args.id, "-o", out]
    if args.backend and args.backend != "auto":
        cmd += ["--backend", args.backend]
    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError:
        fail(f"python entry not found: {sys.executable}")
    except subprocess.CalledProcessError as exc:
        fail(f"voice test synthesis failed with exit code {exc.returncode}")
    print(f"listen: {out}")


def cmd_migrate(_args):
    if not os.path.isdir(LEGACY_BANK):
        fail(f"legacy bank not found: {LEGACY_BANK}")
    if LEGACY_BANK == BANK:
        fail("legacy bank and new bank are the same path; nothing to migrate")
    os.makedirs(BANK, exist_ok=True)
    moved = skipped = 0
    for i in sorted(os.listdir(LEGACY_BANK)):
        src = os.path.join(LEGACY_BANK, i)
        dst = os.path.join(BANK, i)
        if not os.path.isdir(src) or not VALID_ID.match(i):
            continue
        if os.path.exists(dst):
            print(f"  skip {i} (already exists in new bank)")
            skipped += 1
            continue
        shutil.copytree(src, dst)
        print(f"  copied {i}")
        moved += 1
    print(f"✓ migrated {moved} voice(s), skipped {skipped}. "
          f"Legacy directory left untouched at {LEGACY_BANK}.")


def main():
    ap = argparse.ArgumentParser(description="poly-tts voice bank manager")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add", help="register a new voice from authorized reference audio")
    p.add_argument("id")
    p.add_argument("--wav", required=True,
                   help="reference audio (2.5-20s hard range, 3-10s optimal, clean single speaker)")
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
    p.add_argument("--backend", default="auto", help="force a backend for the test")
    p.set_defaults(fn=cmd_test)

    p = sub.add_parser("migrate", help="copy legacy ~/.cosyvoice3-repo/voices into the new bank")
    p.set_defaults(fn=cmd_migrate)

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
