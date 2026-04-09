#!/usr/bin/env bash
set -euo pipefail

# Pathfinder post-clone setup script
# Usage: ./scripts/setup.sh
#
# This script is idempotent — safe to run multiple times.
# It creates a virtual environment, installs dependencies,
# and verifies the installation.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_DIR="$PROJECT_ROOT/.venv"

# Minimum Python version
REQUIRED_PYTHON_MAJOR=3
REQUIRED_PYTHON_MINOR=10

# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

info()  { echo "==> $*"; }
error() { echo "ERROR: $*" >&2; exit 1; }

check_python() {
    # Try python3 first, fall back to python
    if command -v python3 &>/dev/null; then
        PYTHON=python3
    elif command -v python &>/dev/null; then
        PYTHON=python
    else
        error "Python not found. Please install Python >= ${REQUIRED_PYTHON_MAJOR}.${REQUIRED_PYTHON_MINOR}"
    fi

    # Check version
    local version
    version=$($PYTHON -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    local major minor
    major=$(echo "$version" | cut -d. -f1)
    minor=$(echo "$version" | cut -d. -f2)

    if (( major < REQUIRED_PYTHON_MAJOR )) || \
       (( major == REQUIRED_PYTHON_MAJOR && minor < REQUIRED_PYTHON_MINOR )); then
        error "Python >= ${REQUIRED_PYTHON_MAJOR}.${REQUIRED_PYTHON_MINOR} required (found $version)"
    fi

    info "Using $PYTHON ($version)"
}

check_adb() {
    if command -v adb &>/dev/null; then
        info "ADB found: $(adb version | head -1)"
    else
        echo "    WARNING: ADB not found. Android device interaction will not work."
        echo "    Install Android SDK Platform Tools to use the Android adapter."
    fi
}

# -------------------------------------------------------------------
# Main
# -------------------------------------------------------------------

cd "$PROJECT_ROOT"
info "Setting up Pathfinder in $PROJECT_ROOT"

# 1. Check prerequisites
check_python

# 2. Create venv if it doesn't exist
if [ -d "$VENV_DIR" ]; then
    info "Virtual environment already exists at $VENV_DIR"
else
    info "Creating virtual environment at $VENV_DIR"
    $PYTHON -m venv "$VENV_DIR"
fi

# 3. Activate and upgrade pip
info "Activating virtual environment"
source "$VENV_DIR/bin/activate"

info "Upgrading pip"
pip install --upgrade pip --quiet

# 4. Install the project and its dependencies
info "Installing pathfinder and dependencies"
pip install -e ".[dev]" --quiet

# 5. Install Playwright browsers
info "Installing Playwright browsers (chromium)"
python -m playwright install chromium 2>/dev/null || {
    echo "    WARNING: Playwright browser install failed."
    echo "    Run 'python -m playwright install chromium' manually for web exploration."
}

# 6. Verify installation
info "Verifying installation"
python -c "import pathfinder; print(f'  pathfinder package: OK')"
python -c "import anthropic; print(f'  anthropic: OK')"
python -c "import uiautomator2; print(f'  uiautomator2: OK')"
python -c "import pydantic; print(f'  pydantic: OK')"
python -c "import click; print(f'  click: OK')"
python -c "import pytest; print(f'  pytest: OK')"
python -c "import playwright; print(f'  playwright: OK')"

# 7. Check optional tools
check_adb

# 8. Remind about .env
if [ ! -f "$PROJECT_ROOT/.env" ]; then
    echo ""
    echo "    NOTE: No .env file found."
    echo "    Copy .env.example to .env and add your API key:"
    echo "      cp .env.example .env"
    echo ""
fi

# Done
echo ""
info "Setup complete!"
echo ""
echo "    To activate the environment:"
echo "      source .venv/bin/activate"
echo ""
echo "    To run pathfinder:"
echo "      pathfinder --help"
echo ""
echo "    To run tests:"
echo "      pytest"
echo ""
