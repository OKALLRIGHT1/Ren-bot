#!/usr/bin/env bash
# Ren-bot Linux/macOS Environment Setup Script
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "========================================================"
echo "          Ren-bot Environment Setup (Linux / macOS)"
echo "========================================================"
echo ""

# Find suitable python binary
PYTHON=""
for cmd in python3.11 python3.10 python3.12 python3; do
    if command -v "$cmd" >/dev/null 2>&1; then
        VER=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        MAJOR=$(echo "$VER" | cut -d. -f1)
        MINOR=$(echo "$VER" | cut -d. -f2)
        if [ "$MAJOR" -eq 3 ] && [ "$MINOR" -ge 10 ]; then
            PYTHON="$cmd"
            echo "[Ren-bot] Found suitable Python: $PYTHON ($VER)"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo "[Error] Python 3.10+ was not found on your system."
    echo "Please install Python 3.10 or 3.11 (recommended):"
    echo "  Ubuntu/Debian: sudo apt update && sudo apt install -y python3.10 python3.10-venv python3-pip"
    echo "  Arch Linux:    sudo pacman -S python python-pip"
    echo "  macOS:         brew install python@3.11"
    exit 1
fi

# Optional: Prompt or create venv
VENV_DIR="$SCRIPT_DIR/.venv"
if [ ! -d "$VENV_DIR" ]; then
    echo "[Ren-bot] Creating virtual environment at $VENV_DIR..."
    "$PYTHON" -m venv "$VENV_DIR"
fi

# Activate venv
if [ -f "$VENV_DIR/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$VENV_DIR/bin/activate"
    PIP_BIN="$VENV_DIR/bin/pip"
    PY_RUN="$VENV_DIR/bin/python"
else
    PIP_BIN="$PYTHON -m pip"
    PY_RUN="$PYTHON"
fi

echo "[Ren-bot] Upgrading pip..."
$PIP_BIN install --upgrade pip

echo "[Ren-bot] Installing requirements from requirements.txt..."
$PIP_BIN install -r "$SCRIPT_DIR/requirements.txt"

# Optional PortAudio reminder for voice capture
if ! ldconfig -p 2>/dev/null | grep -q libportaudio; then
    echo ""
    echo "[Notice] libportaudio not detected in system libraries."
    echo "If you intend to use microphone voice input on this machine, install portaudio:"
    echo "  Ubuntu/Debian: sudo apt install -y libportaudio2"
    echo "  Arch Linux:    sudo pacman -S portaudio"
    echo "  macOS:         brew install portaudio"
    echo "If you are running in Headless / Server / QQ-Bot mode, this can be safely ignored."
fi

echo ""
echo "========================================================"
echo " [Success] Environment is ready!"
echo " Start Ren-bot via: ./run.sh"
echo "========================================================"
