#!/usr/bin/env bash
# Ren-bot Startup Script for Linux / macOS
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Activate venv if present
if [ -f "$SCRIPT_DIR/.venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$SCRIPT_DIR/.venv/bin/activate"
    PYTHON="$SCRIPT_DIR/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON="python"
else
    echo "[Error] Python not found. Please run ./setup_env.sh first."
    exit 1
fi

# Auto-detect display environment
if [ -z "$DISPLAY" ] && [ -z "$WAYLAND_DISPLAY" ]; then
    # No graphical display detected - automatically enable headless mode unless specified
    if [[ ! " $* " =~ " --headless " ]]; then
        echo "[Ren-bot] No graphical display detected (HEADLESS). Launching in headless/server mode..."
        exec "$PYTHON" "$SCRIPT_DIR/boot.py" --headless "$@"
    fi
fi

exec "$PYTHON" "$SCRIPT_DIR/boot.py" "$@"
