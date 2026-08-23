#!/bin/bash
# CosyVoice3 install for macOS Apple Silicon (uv-based, idempotent)
# 原版 skill 的 install.sh 用 conda + 硬编码路径，本脚本为实测过的 uv 替代方案
set -euo pipefail

REPO="${COSYVOICE_REPO:-$HOME/.cosyvoice3-repo}"
REPO_URL="https://github.com/QwenAudio/CosyVoice.git"
COSYVOICE_COMMIT="${COSYVOICE_COMMIT:-074ca6d}"
MODEL_DIR="pretrained_models/Fun-CosyVoice3-0.5B"
MIN_FREE_GB="${COSYVOICE_MIN_FREE_GB:-13}"
REQUIRED_MODEL_FILES=(
    "cosyvoice3.yaml"
    "llm.pt"
    "flow.pt"
    "hift.pt"
    "campplus.onnx"
    "speech_tokenizer_v3.onnx"
    "CosyVoice-BlankEN/model.safetensors"
)

die() {
    echo "❌ $*" >&2
    exit 1
}

require_tool() {
    command -v "$1" >/dev/null || die "$1 not found. Install it first: $2"
}

check_platform() {
    [ "$(uname -s)" = "Darwin" ] || die "this installer is for macOS"
    [ "$(uname -m)" = "arm64" ] || die "this installer is for Apple Silicon (arm64)"
}

check_free_space() {
    local path="$1"
    local parent
    parent="$(dirname "$path")"
    mkdir -p "$parent"
    [ -w "$parent" ] || die "repository parent is not writable: $parent"
    local free_kb required_kb
    free_kb="$(df -Pk "$parent" | awk 'NR == 2 {print $4}')"
    required_kb=$((MIN_FREE_GB * 1024 * 1024))
    if [ "$free_kb" -lt "$required_kb" ]; then
        die "need at least ${MIN_FREE_GB}GB free under $parent; model is ~9.1GB and repo is ~11GB total"
    fi
}

model_missing_files() {
    local missing=0
    for rel in "${REQUIRED_MODEL_FILES[@]}"; do
        if [ ! -s "$MODEL_DIR/$rel" ]; then
            echo "  missing: $MODEL_DIR/$rel"
            missing=1
        fi
    done
    return "$missing"
}

check_platform
require_tool git "brew install git"
require_tool uv "brew install uv"
require_tool ffmpeg "brew install ffmpeg"
require_tool ffprobe "brew install ffmpeg"
check_free_space "$REPO"

if [ ! -d "$REPO/.git" ]; then
    echo "📥 Cloning CosyVoice repo..."
    git clone "$REPO_URL" "$REPO"
fi
cd "$REPO"
[ -w "$REPO" ] || die "repository is not writable: $REPO"

origin_url="$(git remote get-url origin 2>/dev/null || true)"
case "$origin_url" in
    *QwenAudio/CosyVoice*|*FunAudioLLM/CosyVoice*) ;;
    *) die "origin is not the expected QwenAudio/CosyVoice repository: $origin_url" ;;
esac

echo "📌 Checking out CosyVoice commit ${COSYVOICE_COMMIT}..."
if ! git cat-file -e "${COSYVOICE_COMMIT}^{commit}" 2>/dev/null; then
    git fetch origin "$COSYVOICE_COMMIT" || git fetch origin
fi
git checkout --detach "$COSYVOICE_COMMIT"
expected_commit="$(git rev-parse "${COSYVOICE_COMMIT}^{commit}")"
actual_commit="$(git rev-parse HEAD)"
[ "$actual_commit" = "$expected_commit" ] || die "checkout verification failed: expected $expected_commit, got $actual_commit"
git submodule update --init --recursive

if [ ! -d .venv ]; then
    echo "🐍 Creating Python 3.10 venv..."
    uv venv --python 3.10 .venv
fi
.venv/bin/python - <<'EOF' || die "existing .venv is not Python 3.10; move it aside and rerun install.sh"
import sys
assert sys.version_info[:2] == (3, 10), sys.version
EOF

echo "🔥 Installing torch (CPU wheels)..."
uv pip install --python .venv/bin/python torch==2.3.1 torchaudio==2.3.1 \
    --index-url https://download.pytorch.org/whl/cpu

echo "📦 Installing dependencies (strip CUDA-only index lines)..."
REQ_FILE="$(mktemp "${TMPDIR:-/tmp}/cv3-req-mac.XXXXXX")"
trap 'rm -f "$REQ_FILE"' EXIT
grep -v '^--extra-index-url' requirements.txt > "$REQ_FILE"
uv pip install --python .venv/bin/python -r "$REQ_FILE"

echo "⚠️ openai-whisper needs legacy setuptools (pkg_resources removed in setuptools 84)..."
uv pip install --python .venv/bin/python "setuptools==75.8.0" pip
.venv/bin/python -m pip install openai-whisper==20231117 --no-build-isolation

echo "⚠️ Pin numpy back to 1.26.4 (whisper drags it to 2.x, breaks torch 2.3.1)..."
uv pip install --python .venv/bin/python "numpy==1.26.4"

if model_missing_files; then
    echo "📥 Model manifest complete; skipping download."
else
    echo "📥 Downloading/resuming model (~9.1GB) from ModelScope..."
    .venv/bin/python - <<'EOF'
from modelscope import snapshot_download
snapshot_download(
    "FunAudioLLM/Fun-CosyVoice3-0.5B-2512",
    local_dir="pretrained_models/Fun-CosyVoice3-0.5B",
)
EOF
    echo "🔎 Validating model manifest..."
    if ! model_missing_files; then
        die "model download is still incomplete; re-run scripts/install.sh to resume"
    fi
    echo "model ready"
fi

echo ""
echo "=== Done ==="
echo "Smoke test:"
echo "  $REPO/.venv/bin/python <skill-dir>/scripts/tts.py '你好，测试。' -o /tmp/test.wav"
